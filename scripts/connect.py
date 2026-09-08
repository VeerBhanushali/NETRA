"""Expose the running console to a phone, over HTTPS.

    python scripts/connect.py --usb           # USB cable, no network at all
    python scripts/connect.py                 # pick the best available method
    python scripts/connect.py --tailscale     # private mesh
    python scripts/connect.py --cloudflared   # public quick tunnel
    python scripts/connect.py --lan           # print the LAN URL only

Why this script exists at all: browsers only grant camera access in a
*secure context* — HTTPS, or localhost. Opening http://10.38.9.124:3000 on
a phone silently refuses getUserMedia with no useful error, which is the
single most common reason "the PWA camera doesn't work".

There are three ways to satisfy that, and they differ in who can reach
the console:

  USB (--usb)   `adb reverse` makes the phone's own localhost:3000 point
                down the cable at this laptop. localhost is a secure
                context by definition, so the camera opens with no
                certificate, no tunnel, and no network — the phone is
                wired to the laptop exactly like a real CCTV camera on a
                cable. This is the most faithful replication and the one
                that cannot fail on a conference network.

  Tailscale     the console stays on a private mesh. Only devices signed
                into your tailnet can reach it, and `tailscale serve`
                terminates HTTPS with a real MagicDNS certificate. This is
                the right default for a surveillance console.

  cloudflared   a *public* trycloudflare.com URL. Anyone who learns the
                URL reaches your command centre. Convenient for a demo on
                a hostile conference network; not something to leave up.

Both tunnel port 3000 only. The dashboard proxies /api to FastAPI, so the
API never needs to be exposed separately.
"""
from __future__ import annotations

import argparse
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                      # noqa: BLE001
        pass

PORT = 3000


def lan_ip() -> str:
    """The address other devices on this network would use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))          # no packet is sent
        return s.getsockname()[0]
    except Exception:                                      # noqa: BLE001
        return "127.0.0.1"
    finally:
        s.close()


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def qr(text: str) -> None:
    """Print a scannable QR code, if the optional dependency is present."""
    try:
        import qrcode                                       # type: ignore
    except ImportError:
        print("\n  (pip install qrcode  to print a scannable QR code here)")
        return
    q = qrcode.QRCode(border=1)
    q.add_data(text)
    q.make(fit=True)
    q.print_ascii(invert=True)


def announce(url: str, note: str) -> None:
    capture = url.rstrip("/") + "/capture"
    print("\n" + "─" * 66)
    print("  Phone camera ready")
    print(f"    open on the phone   {capture}")
    print(f"    console             {url}")
    print(f"    {note}")
    print("─" * 66)
    qr(capture)
    print("\n  On the phone: open the link, tap Start camera, then\n"
          "  'Add to Home Screen' to install it as an app.\n"
          "  Ctrl-C here to close the tunnel.\n")


# --- USB cable -------------------------------------------------------------

def try_usb() -> bool:
    """Wire the phone to this laptop with `adb reverse`.

    No cloud, no certificate, no Wi-Fi. The phone opens
    http://localhost:3000, which the browser already trusts as a secure
    context, and adb forwards that back down the cable.
    """
    exe = shutil.which("adb")
    if not exe:
        print("[connect] adb not found. It ships with scrcpy and with the "
              "Android platform-tools.")
        return False

    devices = subprocess.run([exe, "devices"], capture_output=True, text=True)
    lines = [l for l in devices.stdout.splitlines()[1:] if l.strip()]
    ready = [l.split()[0] for l in lines if l.split()[-1] == "device"]
    unauthorised = [l.split()[0] for l in lines if l.split()[-1] == "unauthorized"]

    if unauthorised:
        print(f"[connect] {unauthorised[0]} is connected but not authorised — "
              "unlock the phone and accept the USB debugging prompt.")
        return False
    if not ready:
        print("[connect] no phone over USB. On the phone: Settings → About → "
              "tap Build number 7 times, then Developer options → USB debugging.")
        return False

    # Reverse, not forward: traffic flows phone → laptop.
    r = subprocess.run([exe, "reverse", f"tcp:{PORT}", f"tcp:{PORT}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[connect] adb reverse failed: {r.stdout}{r.stderr}")
        return False

    print(f"[connect] {ready[0]} wired to this laptop over USB")
    announce(f"http://localhost:{PORT}",
             "wired: no network involved, nothing is exposed off this laptop")
    print("  Type that address on the phone directly — a QR code scanner would\n"
          "  send its own browser to its own localhost, which is the same thing,\n"
          "  but typing it is less confusing.\n")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[connect] removing the adb reverse route…")
        subprocess.run([exe, "reverse", "--remove", f"tcp:{PORT}"], check=False)
    return True


# --- Tailscale -------------------------------------------------------------

def try_tailscale() -> bool:
    exe = shutil.which("tailscale")
    if not exe:
        return False
    st = subprocess.run([exe, "status", "--json"], capture_output=True, text=True)
    if st.returncode != 0:
        print("[connect] tailscale is installed but not logged in — run: tailscale up")
        return False

    import json
    try:
        dns = json.loads(st.stdout).get("Self", {}).get("DNSName", "").rstrip(".")
    except Exception:                                      # noqa: BLE001
        dns = ""
    if not dns:
        print("[connect] could not read this machine's MagicDNS name")
        return False

    # `serve` fronts the dev server with a real certificate for the
    # MagicDNS name, which is what makes it a secure context.
    print(f"[connect] tailscale serve → https://{dns}")
    proc = subprocess.Popen([exe, "serve", "--bg", f"http://localhost:{PORT}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    out, _ = proc.communicate(timeout=60)
    if proc.returncode != 0:
        print(f"[connect] tailscale serve failed:\n{out}")
        print("[connect] HTTPS certificates need to be enabled once for the "
              "tailnet in the admin console (DNS → HTTPS Certificates).")
        return False

    announce(f"https://{dns}",
             "private: reachable only from devices signed into your tailnet")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[connect] removing the tailscale serve route…")
        subprocess.run([exe, "serve", "--https=443", "off"], check=False)
    return True


# --- cloudflared -----------------------------------------------------------

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def try_cloudflared() -> bool:
    exe = shutil.which("cloudflared")
    if not exe:
        return False

    print("[connect] opening a Cloudflare quick tunnel…")
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)

    found: list[str] = []

    def read() -> None:
        # The URL arrives on stderr amid a banner; scrape rather than parse.
        for line in proc.stdout:                            # type: ignore[union-attr]
            m = URL_RE.search(line)
            if m and not found:
                found.append(m.group(0))

    threading.Thread(target=read, daemon=True).start()

    deadline = time.time() + 45
    while time.time() < deadline and not found and proc.poll() is None:
        time.sleep(0.5)

    if not found:
        proc.terminate()
        print("[connect] cloudflared did not report a URL")
        return False

    announce(found[0],
             "PUBLIC: anyone with this URL reaches the console. Close it after the demo.")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", action="store_true")
    ap.add_argument("--tailscale", action="store_true")
    ap.add_argument("--cloudflared", action="store_true")
    ap.add_argument("--lan", action="store_true")
    args = ap.parse_args()

    if not port_busy(PORT):
        print(f"[connect] nothing is listening on :{PORT}."
              " Start the stack first: python scripts/start.py")
        return 1

    if args.lan:
        print(f"\n  http://{lan_ip()}:{PORT}/capture\n"
              "\n  Note: the camera will NOT open on this address — browsers"
              "\n  require HTTPS. Use --tailscale or --cloudflared for capture;"
              "\n  the LAN URL is fine for viewing the dashboard.\n")
        qr(f"http://{lan_ip()}:{PORT}/capture")
        return 0

    if args.usb:
        return 0 if try_usb() else 1
    if args.cloudflared:
        return 0 if try_cloudflared() else 1
    if args.tailscale:
        return 0 if try_tailscale() else 1

    # Default order is deliberate: nothing exposed, then private, then public.
    if try_usb() or try_tailscale() or try_cloudflared():
        return 0

    print("\n[connect] nothing available. Any one of these works:")
    print("    USB cable                plug the phone in, enable USB debugging")
    print("    Tailscale                https://tailscale.com/download")
    print("    cloudflared              npm i -g cloudflared")
    print(f"\n  Meanwhile the dashboard is reachable at http://{lan_ip()}:{PORT}")
    print("  (viewing only — the phone camera needs HTTPS).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
