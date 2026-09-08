"""Size the camera network to this machine, by measuring it.

    python scripts/autotune.py            # measure and report
    python scripts/autotune.py --apply    # measure and rewrite cameras.json

Guessing a camera count from core count alone is how the network ended up
pinning a laptop at 90% CPU while reading almost nothing. This benchmarks
the three costs that actually matter — video decode, vehicle detection,
and plate OCR — and derives a configuration that leaves the machine
usable.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIPS = ROOT / "datasets" / "demo_clips"
CONFIG = ROOT / "infra" / "cameras.json"

# Fraction of the machine the pipeline is allowed to occupy. The rest is
# for the API, the dev server, the operator's browser and the desktop —
# all of which the demo depends on staying responsive.
CPU_BUDGET = float(os.getenv("NETRA_CPU_BUDGET", "0.45"))
# A clip must not outrun inference, so this is both the display rate and
# the pacing target.
TARGET_DISPLAY_FPS = 4

# Clips whose plates are genuinely legible, best first. Cameras beyond
# this list can still stream, but there is nothing for ANPR to read.
ANPR_CLIPS = [
    ("in_vip_720.mp4",      "Kerala plates, close range"),
    ("in_test1.mp4",        "Delhi and Haryana plates, toll-gate framing"),
    ("in_vid4_out_720.mp4", "Maharashtra street scene"),
    ("in_vid4_720.mp4",     "Maharashtra, wider framing"),
]
VIEW_CLIPS = [
    ("anpr_sample.mp4",    "Dense multi-lane traffic"),
    ("in_test2.mp4",       "Multi-lane approach"),
    ("car-detection.mp4",  "Wide road view"),
    ("vehicles_720.mp4",   "Overhead highway"),
]

PLACES = [
    ("cam-01", "Sector 17 Plaza North"), ("cam-02", "Sector 17 Bank Square"),
    ("cam-03", "Sector 17/22 Junction"), ("cam-04", "Matka Chowk"),
    ("cam-05", "ISBT-43 Approach"),      ("cam-06", "Sector 43/42 Light Pt"),
    ("cam-07", "Tribune Chowk"),         ("cam-08", "Industrial Ph-1 Gate"),
    ("cam-09", "Zirakpur Toll Approach"),("cam-10", "PGI Rotary"),
]


def bench() -> dict:
    import cv2
    import numpy as np
    from ultralytics import YOLO

    cores = os.cpu_count() or 4
    clip = next((CLIPS / n for n, _ in ANPR_CLIPS if (CLIPS / n).is_file()), None)
    if clip is None:
        raise SystemExit("no demo clips found — run: python scripts/fetch_assets.py")

    # --- decode + JPEG encode, the per-camera streaming cost ---
    cap = cv2.VideoCapture(str(clip))
    frames, t0 = [], time.perf_counter()
    for _ in range(40):
        ok, f = cap.read()
        if not ok:
            break
        if f.shape[1] > 1280:
            f = cv2.resize(f, (1280, int(f.shape[0] * 1280 / f.shape[1])))
        cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 70])
        frames.append(f)
    decode_ms = (time.perf_counter() - t0) / max(1, len(frames)) * 1000
    cap.release()
    if not frames:
        raise SystemExit(f"could not read frames from {clip}")

    # --- vehicle detection, the per-inference-pass cost ---
    #
    # Median of individual timings, not a mean over a batch. The first
    # version used a mean and, when run while the previous camera network
    # was still shutting down, reported 648 ms/frame against a true 65 ms
    # — and then sized the whole network off that. One contended sample
    # must not be able to distort the plan.
    det = YOLO(os.getenv("NETRA_VEHICLE_MODEL", "yolov8n.pt"))
    for _ in range(3):
        det(frames[0], verbose=False, classes=[2, 3, 5, 7], conf=0.35)
    times = []
    for f in frames[:10]:
        t0 = time.perf_counter()
        det(f, verbose=False, classes=[2, 3, 5, 7], conf=0.35)
        times.append((time.perf_counter() - t0) * 1000)
    detect_ms = statistics.median(times)
    spread = max(times) / max(min(times), 1e-6)

    # --- plate detect + OCR, the expensive tail ---
    ocr_ms = 0.0
    plate_w = ROOT / "models" / "onnx" / "plate_yolo11n.pt"
    if plate_w.is_file():
        import easyocr
        pm = YOLO(str(plate_w))
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        crop = frames[0][: max(40, frames[0].shape[0] // 4),
                         : max(120, frames[0].shape[1] // 4)]
        reader.readtext(crop, detail=1)                # warm up
        pm(frames[0], verbose=False, conf=0.25)         # warm up
        otimes = []
        for f in frames[:5]:
            t0 = time.perf_counter()
            pm(f, verbose=False, conf=0.25)
            reader.readtext(crop, detail=1,
                            allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
            otimes.append((time.perf_counter() - t0) * 1000)
        ocr_ms = statistics.median(otimes)

    return {"cores": cores, "decode_ms": decode_ms, "detect_ms": detect_ms,
            "ocr_ms": ocr_ms, "spread": spread}


def plan(b: dict) -> dict:
    """Turn measurements into a camera count.

    Streaming and inference are costed separately because they are
    separate threads: a camera that only streams is cheap, and adding one
    does not slow plate reading.
    """
    budget_ms_per_s = 1000.0 * b["cores"] * CPU_BUDGET

    # One streaming camera spends decode_ms per displayed frame.
    per_camera_ms = b["decode_ms"] * TARGET_DISPLAY_FPS
    # One inference pass is a detect plus, often, a plate read.
    per_pass_ms = b["detect_ms"] + b["ocr_ms"] * 0.7

    # Most of the budget goes to inference, not tiles.
    #
    # The first version of this split it the other way and produced ten
    # streaming cameras with two doing recognition — which published
    # nothing, because neither camera got enough passes for a vehicle to
    # accumulate the frames a confident vote needs. A wall of tiles that
    # reads no plates is not a working ANPR system, so cameras that can
    # actually be recognised are budgeted first and view-only tiles get
    # whatever is left.
    infer_ms = budget_ms_per_s * 0.6
    stream_ms = budget_ms_per_s - infer_ms

    passes_per_s = max(1.0, infer_ms / max(per_pass_ms, 1))
    # A vehicle is in view roughly 5 s. Voting wants ~10 frames to reach
    # auto-accept, so a camera needs ~2 passes/s to be worth inferring.
    anpr_cams = max(1, min(len(ANPR_CLIPS), int(passes_per_s // 2)))

    streamable = max(0, int(stream_ms // max(per_camera_ms, 1)))
    # Cap the extras: past a handful, more identical tiles add nothing to
    # the demo and only take CPU away from recognition.
    total_cams = max(anpr_cams, min(8, anpr_cams + min(3, streamable)))

    return {**b, "total_cameras": total_cams, "anpr_cameras": anpr_cams,
            "passes_per_s": passes_per_s,
            "infer_interval_s": round(1.0 / passes_per_s, 3)}


def build_config(p: dict) -> dict:
    cams = []
    available_anpr = [(n, d) for n, d in ANPR_CLIPS if (CLIPS / n).is_file()]
    available_view = [(n, d) for n, d in VIEW_CLIPS if (CLIPS / n).is_file()]

    for i in range(p["total_cameras"]):
        cam_id, place = PLACES[i]
        if i < p["anpr_cameras"] and available_anpr:
            clip, note = available_anpr[i % len(available_anpr)]
            weight = 4 if i < 2 else 3
        elif available_view:
            clip, note = available_view[(i - p["anpr_cameras"]) % len(available_view)]
            weight = 0
        else:
            clip, note = available_anpr[i % len(available_anpr)]
            weight = 0
        cams.append({
            "id": cam_id, "name": place,
            "sources": [f"datasets/demo_clips/{clip}"],
            "note": note,
            "display_fps": TARGET_DISPLAY_FPS,
            "anpr_weight": weight,
        })

    return {
        "_comment": [
            "GENERATED by scripts/autotune.py from measurements of this machine.",
            f"  cores {p['cores']}, decode {p['decode_ms']:.0f} ms/frame, "
            f"detect {p['detect_ms']:.0f} ms, ocr {p['ocr_ms']:.0f} ms",
            f"  -> {p['total_cameras']} cameras, {p['anpr_cameras']} of them "
            f"running plate recognition",
            "",
            "anpr_weight 0 = streams video but is never inferred.",
            "Re-run with --apply after changing hardware.",
            "",
            "Run everything with:  python scripts/start.py",
        ],
        "region": "IN",
        "loop": True,
        "infer_interval_s": p["infer_interval_s"],
        "cameras": cams,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="rewrite infra/cameras.json")
    args = ap.parse_args()

    print("Measuring this machine (about 30 seconds)…\n")
    p = plan(bench())

    print(f"  logical cores        {p['cores']}")
    print(f"  decode + encode      {p['decode_ms']:.0f} ms/frame")
    print(f"  vehicle detection    {p['detect_ms']:.0f} ms/frame")
    print(f"  plate detect + OCR   {p['ocr_ms']:.0f} ms/call")
    print(f"  CPU budget           {int(CPU_BUDGET * 100)}% of {p['cores']} cores\n")
    print(f"  -> {p['total_cameras']} cameras at {TARGET_DISPLAY_FPS} fps")
    print(f"  -> {p['anpr_cameras']} of them running plate recognition")
    print(f"  -> {p['passes_per_s']:.1f} inference passes/sec "
          f"(interval {p['infer_interval_s']}s)\n")

    if args.apply:
        CONFIG.write_text(json.dumps(build_config(p), indent=2) + "\n",
                          encoding="utf-8")
        print(f"Wrote {CONFIG.relative_to(ROOT)}")
        print("Start it with:  python scripts/start.py")
    else:
        print("Re-run with --apply to write this into infra/cameras.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
