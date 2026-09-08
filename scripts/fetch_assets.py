"""Download the models and test footage the real ANPR pipeline needs.

    python scripts/fetch_assets.py

Everything here is fetched from a public URL and written under models/ and
datasets/, both of which are gitignored — the repo stays clonable and
nobody commits a 20 MB video by accident.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ASSETS = [
    {
        "path": ROOT / "models" / "onnx" / "plate_yolo11n.pt",
        "url": "https://huggingface.co/morsetechlab/yolov11-license-plate-detection"
               "/resolve/main/license-plate-finetune-v1n.pt",
        "what": "YOLO11n licence-plate detector (~5 MB)",
        "why": "Without it the pipeline falls back to cropping the lower half of "
               "each vehicle, which feeds OCR bumpers and badges instead of plates.",
    },
    {
        "path": ROOT / "datasets" / "demo_clips" / "anpr_sample.mp4",
        "url": "https://raw.githubusercontent.com/computervisioneng"
               "/real-time-number-plate-recognition-anpr/main/sample_30fps_1440.mp4",
        "what": "ANPR test footage, 1440x960 @30fps (~21 MB)",
        "why": "UK traffic at a distance where plates are legible but individual "
               "frames are unreliable — exactly the case temporal voting exists for.",
    },
    {
        "path": ROOT / "datasets" / "demo_clips" / "vehicles.mp4",
        "url": "https://media.roboflow.com/supervision/video-examples/vehicles.mp4",
        "what": "4K highway traffic (~34 MB)",
        "why": "Good for vehicle detection and tracking. Plates are far too small "
               "to read at this distance — useful as an honest demonstration of "
               "where ANPR physically cannot work.",
    },
]


def download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1024:
        print(f"  already present: {dest.name} "
              f"({dest.stat().st_size / 1e6:.1f} MB)")
        return True
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        print(f"  downloading {dest.name} …", flush=True)
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
        tmp.replace(dest)
        print(f"  done: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return True
    except Exception as exc:                          # noqa: BLE001
        print(f"  FAILED {dest.name}: {exc}")
        tmp.unlink(missing_ok=True)
        return False


def main() -> int:
    print("Fetching NETRA assets\n")
    ok = True
    for a in ASSETS:
        print(f"{a['what']}\n  {a['why']}")
        ok &= download(a["url"], a["path"])
        print()

    print("Next:")
    print("  python scripts/seed_demo.py --reset")
    print("  uvicorn app.main:app --app-dir apps/api --port 8000")
    print("  cd apps/edge && NETRA_PLATE_REGION=UK python -m edge --mode anpr \\")
    print("      --source ../../datasets/demo_clips/anpr_sample.mp4 --camera cam-01")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
