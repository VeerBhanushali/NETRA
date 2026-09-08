"""Start the whole camera network at once, looping forever.

Each camera becomes its own OS process running the real ANPR pipeline on
its own clip, replayed on a loop so the dashboard behaves like a live
24/7 CCTV network.

    python scripts/run_cameras.py                # every camera in cameras.json
    python scripts/run_cameras.py --only cam-01  # just one
    python scripts/run_cameras.py --list

Separate processes rather than threads on purpose: torch and EasyOCR both
hold the GIL for long stretches, so threads would serialise and one
camera stalling would freeze the rest. Processes also mean a crashed
camera takes down only itself.

Stop everything with Ctrl-C.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "infra" / "cameras.json"
EDGE_DIR = ROOT / "apps" / "edge"


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="camera id (repeatable)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--threads", type=int, default=2,
                    help="torch threads per worker (default 2)")
    args = ap.parse_args()

    cfg = load_config()
    cams = cfg["cameras"]
    if args.only:
        cams = [c for c in cams if c["id"] in args.only]
    def sources_of(c: dict) -> list[str]:
        """A camera declares either a playlist ("sources") or, for backward
        compatibility, a single "source"."""
        return c.get("sources") or ([c["source"]] if c.get("source") else [])

    if args.list or not cams:
        for c in cfg["cameras"]:
            print(f"  {c['id']:<8} {', '.join(sources_of(c))}")
            print(f"           {c.get('note', '')}")
        return 0

    def resolve(src: str) -> str:
        # Stream URLs pass through untouched; relative paths resolve
        # against the repo root so the launcher works from anywhere.
        if "://" in src:
            return src
        return str(ROOT / src)

    missing = [(c["id"], s) for c in cams for s in sources_of(c)
               if "://" not in s and not (ROOT / s).is_file()]
    if missing:
        for cid, src in missing:
            print(f"[cameras] missing clip for {cid}: {src}")
        print("[cameras] run: python scripts/fetch_assets.py")
        return 1

    procs: list[tuple[str, subprocess.Popen]] = []
    print(f"[cameras] starting {len(cams)} camera(s), region={cfg.get('region','IN')}")
    print("[cameras] each clip loops forever — Ctrl-C to stop all\n")

    for c in cams:
        env = dict(os.environ)
        env.update({
            "NETRA_PLATE_REGION": cfg.get("region", "IN"),
            "NETRA_EDGE_FPS": str(c.get("fps", 3)),
            "NETRA_MAX_OCR": str(c.get("max_ocr", 3)),
            # Every worker shares one CPU. Left unbounded, each torch
            # process grabs all cores and they thrash each other.
            "OMP_NUM_THREADS": str(args.threads),
            "MKL_NUM_THREADS": str(args.threads),
        })
        cmd = [sys.executable, "-u", "-m", "edge", "--mode", "anpr",
               "--camera", c["id"]]
        for src in sources_of(c):
            cmd += ["--source", resolve(src)]
        if cfg.get("loop", True):
            cmd.append("--loop")
        p = subprocess.Popen(cmd, cwd=str(EDGE_DIR), env=env)
        procs.append((c["id"], p))
        clips = ", ".join(Path(x).name for x in sources_of(c))
        print(f"  started {c['id']}  pid {p.pid}  <- {clips}")
        # Stagger startup: three processes loading YOLO and EasyOCR
        # simultaneously will page-thrash a 16 GB machine.
        time.sleep(8)

    def shutdown(*_):
        print("\n[cameras] stopping…")
        for cid, p in procs:
            if p.poll() is None:
                p.terminate()
        for cid, p in procs:
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        print("[cameras] all stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("\n[cameras] all running. Open http://localhost:3000/live\n")
    try:
        while True:
            time.sleep(5)
            for cid, p in procs:
                if p.poll() is not None:
                    print(f"[cameras] WARNING {cid} exited (code {p.returncode})")
                    procs.remove((cid, p))
                    break
            if not procs:
                print("[cameras] every worker exited")
                return 1
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
