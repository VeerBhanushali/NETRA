"""Start the whole system with one command.

    python scripts/start.py                 # API + dashboard + cameras
    python scripts/start.py --no-cameras    # just the server and UI
    python scripts/start.py --reset         # wipe and reseed the demo city first

Brings up, in order:
  1. the FastAPI service          http://localhost:8000
  2. the Next.js dashboard        http://localhost:3000
  3. the camera network           one process, every camera in cameras.json

Ctrl-C stops all three. Anything already listening on a port is reused
rather than fought over, so re-running this is safe.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Windows consoles default to cp1252 and raise on any character
# outside it. Reconfigure rather than dumbing down the output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "infra" / "cameras.json"


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:                                  # noqa: BLE001
            time.sleep(1.0)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cameras", action="store_true")
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="wipe and reseed the demo city before starting")
    ap.add_argument("--only", action="append", help="limit to these camera ids")
    ap.add_argument("--no-restart", action="store_true",
                    help="report crashes instead of restarting them")
    args = ap.parse_args()

    # name -> (how to spawn it, the running process, restarts so far)
    procs: list[tuple[str, subprocess.Popen]] = []
    spawn: dict[str, callable] = {}

    if args.reset:
        print("[start] reseeding demo data…")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_demo.py"),
                        "--reset"], cwd=str(ROOT), check=False)

    # --- 1. API -------------------------------------------------------
    if port_busy(8000):
        print("[start] API already listening on :8000, reusing it")
    else:
        print("[start] starting API on :8000")
        spawn["api"] = lambda: subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "8000"],
            cwd=str(ROOT / "apps" / "api"))
        procs.append(("api", spawn["api"]()))
        if not wait_for("http://127.0.0.1:8000/api/health", 60):
            print("[start] API did not come up — check apps/api/requirements.txt")
            return 1
    print("[start] API ready        http://localhost:8000/docs")

    # --- 2. dashboard -------------------------------------------------
    if not args.no_web:
        if port_busy(3000):
            print("[start] dashboard already listening on :3000, reusing it")
        else:
            web = ROOT / "apps" / "web"
            if not (web / "node_modules").is_dir():
                print("[start] installing web dependencies (first run only)…")
                subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                               cwd=str(web), shell=(os.name == "nt"), check=False)
            print("[start] starting dashboard on :3000")
            spawn["web"] = lambda: subprocess.Popen(
                ["npx", "next", "dev", "-p", "3000"],
                cwd=str(web), shell=(os.name == "nt"))
            procs.append(("web", spawn["web"]()))
            wait_for("http://127.0.0.1:3000/dashboard", 90)
        print("[start] dashboard ready  http://localhost:3000")

    # --- 3. cameras ---------------------------------------------------
    if not args.no_cameras:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        missing = [s for c in cfg["cameras"] for s in c["sources"]
                   if "://" not in s and not (ROOT / s).is_file()]
        if missing:
            print(f"[start] missing footage: {missing[0]}")
            print("[start] run: python scripts/fetch_assets.py")
        else:
            n = len(cfg["cameras"])
            anpr = sum(1 for c in cfg["cameras"] if c.get("anpr_weight", 1) > 0)
            print(f"[start] starting {n} cameras ({anpr} running plate recognition)")
            env = dict(os.environ)
            # Leave cores for the API, the dev server and the browser.
            env.setdefault("OMP_NUM_THREADS", "3")
            env.setdefault("MKL_NUM_THREADS", "3")
            if cfg.get("infer_interval_s"):
                env.setdefault("NETRA_INFER_INTERVAL", str(cfg["infer_interval_s"]))
            cmd = [sys.executable, "-u", "-m", "edge", "--mode", "network"]
            for cam in (args.only or []):
                cmd += ["--only", cam]
            spawn["cameras"] = lambda: subprocess.Popen(
                cmd, cwd=str(ROOT / "apps" / "edge"), env=env)
            procs.append(("cameras", spawn["cameras"]()))

    print("\n" + "─" * 62)
    print("  NETRA is running")
    print("    dashboard   http://localhost:3000")
    print("    live wall   http://localhost:3000/live")
    print("    API docs    http://localhost:8000/docs")
    print("  Ctrl-C to stop everything")
    print("─" * 62 + "\n")

    def shutdown(*_):
        print("\n[start] stopping…")
        for name, p in procs:
            if p.poll() is None:
                p.terminate()
        for name, p in procs:
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        print("[start] stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # A crashed camera process used to take the live wall down until
    # somebody noticed. Restart it instead — but with a backoff and a cap,
    # because a process that dies instantly every time is a bug to read,
    # not a loop to spin on.
    MAX_RESTARTS = 5
    restarts: dict[str, int] = {}
    due: dict[str, float] = {}          # name -> earliest restart time

    try:
        while True:
            time.sleep(5)
            now = time.time()

            for entry in list(procs):
                name, p = entry
                if p.poll() is None:
                    continue
                print(f"[start] {name} exited (code {p.returncode})")
                procs.remove(entry)
                if args.no_restart or name not in spawn:
                    continue
                n = restarts.get(name, 0)
                if n >= MAX_RESTARTS:
                    print(f"[start] {name} crashed {n} times — not restarting."
                          " Read its output above.")
                    continue
                due[name] = now + min(60.0, 2.0 * (2 ** n))

            for name, at in list(due.items()):
                if now < at:
                    continue
                del due[name]
                n = restarts[name] = restarts.get(name, 0) + 1
                print(f"[start] restarting {name} (attempt {n}/{MAX_RESTARTS})")
                procs.append((name, spawn[name]()))
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
