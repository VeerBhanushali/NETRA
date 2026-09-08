"""Record a sample clip from this machine's camera, and wire it up as a channel.

    python scripts/record_clip.py knife_demo --seconds 20
    python scripts/record_clip.py knife_demo --seconds 20 --add-camera cam-03

Why this exists: NETRA's threat channel can report knife, scissors and
baseball bat, because those are COCO classes. What it cannot do is
demonstrate that on footage we do not have — and public CCTV of a knife
incident is either licensed behind a dataset registration or is real
footage of someone being attacked, which is not something to casually
download for a demo.

Recording your own is better anyway. It is genuine detection on genuine
footage, and a judge can hand you an object and watch you record it.

The recorder previews the detector's own view while recording, so you can
see whether YOLO is actually finding the object before you keep the take.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                      # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parents[1]
CLIPS = ROOT / "datasets" / "demo_clips"
CONFIG = ROOT / "infra" / "cameras.json"

# The classes the threat channel can honestly report, so the preview can
# tell you when you have a usable take.
WATCH = {0: "person", 34: "baseball bat", 43: "knife", 76: "scissors"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="clip name, without extension")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--device", type=int, default=0, help="camera index")
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--add-camera", metavar="ID",
                    help="also add the clip to infra/cameras.json as a threat channel")
    args = ap.parse_args()

    import cv2
    from ultralytics import YOLO

    cap = cv2.VideoCapture(args.device, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    if not cap.isOpened():
        print(f"[rec] cannot open camera {args.device}. "
              "Close anything else using it (including the browser tab).")
        return 1

    ok, probe = cap.read()
    if not ok:
        print("[rec] camera opened but returned no frame")
        return 1
    h, w = probe.shape[:2]

    CLIPS.mkdir(parents=True, exist_ok=True)
    dest = CLIPS / f"{args.name}.mp4"
    writer = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*"mp4v"),
                             args.fps, (w, h))

    model = YOLO("yolov8n.pt")
    print(f"[rec] recording {args.seconds:.0f}s to {dest.name} at {w}x{h}")
    print("[rec] hold the object clearly in view — press q in the window to stop early")

    seen: dict[str, int] = {}
    frames = 0
    started = time.time()
    interval = 1.0 / args.fps
    next_frame = started

    try:
        while time.time() - started < args.seconds:
            ok, frame = cap.read()
            if not ok:
                break
            writer.write(frame)
            frames += 1

            # Preview at a lower rate than we record: the point is to show
            # you whether the detector sees it, not to run at full speed.
            if frames % 3 == 0:
                shown = frame.copy()
                for b in model(frame, verbose=False, conf=0.35)[0].boxes:
                    cls = int(b.cls.item())
                    if cls not in WATCH:
                        continue
                    name = WATCH[cls]
                    seen[name] = seen.get(name, 0) + 1
                    x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
                    colour = (60, 180, 235) if cls == 0 else (0, 140, 235)
                    cv2.rectangle(shown, (x1, y1), (x2, y2), colour, 2)
                    cv2.putText(shown, f"{name} {b.conf.item() * 100:.0f}%",
                                (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                                0.6, colour, 2, cv2.LINE_AA)
                left = args.seconds - (time.time() - started)
                cv2.putText(shown, f"REC {left:4.1f}s", (12, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 235), 2, cv2.LINE_AA)
                cv2.imshow("NETRA recorder — press q to stop", shown)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            next_frame += interval
            time.sleep(max(0.0, next_frame - time.time()))
    finally:
        cap.release()
        writer.release()
        cv2.destroyAllWindows()

    print(f"[rec] wrote {frames} frames to {dest}")
    if seen:
        print("[rec] detector saw: " +
              ", ".join(f"{k} in {v} sampled frames" for k, v in sorted(seen.items())))
    else:
        print("[rec] the detector saw nothing it can report. Try better light, "
              "hold the object closer, and keep it unobstructed — YOLOv8n on COCO "
              "wants a fairly plain view of a knife.")

    if args.add_camera:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        rel = f"datasets/demo_clips/{dest.name}"
        cfg["cameras"] = [c for c in cfg["cameras"] if c["id"] != args.add_camera]
        cfg["cameras"].append({
            "id": args.add_camera,
            "name": f"Recorded — {args.name}",
            "mode": "threat",
            "sources": [rel],
            "note": "Recorded locally with scripts/record_clip.py",
            "display_fps": 6,
            "anpr_weight": 3,
        })
        CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"[rec] added {args.add_camera} to infra/cameras.json as a threat channel")
        print("[rec] restart the network to pick it up: python scripts/start.py")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
