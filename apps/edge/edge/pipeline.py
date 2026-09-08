"""The real vision pipeline: video -> detect -> track -> OCR -> vote -> publish.

Deliberately kept dependency-light at import time. The simulator and the
voting engine must keep working on a machine where ultralytics and
PaddleOCR are not installed, because that is the state of every laptop on
day zero of a hackathon.

Install when you are ready for real inference:
    pip install ultralytics opencv-python
    pip install paddlepaddle paddleocr        # CPU build is fine for crops
    pip install onnxruntime-directml          # AMD GPU on Windows
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from .publisher import Publisher
from .voting import PlateRead, TrackAccumulator, frame_quality

# Sample well below source frame rate: a car crossing a junction is
# visible for seconds, and every extra frame costs GPU time we do not
# have. 12 FPS gives 20-30 looks at a plate, far more than voting needs.
TARGET_FPS = float(os.getenv("NETRA_EDGE_FPS", "12"))
# A track must be gone this long before we consider the pass finished.
TRACK_IDLE_S = 1.5
VEHICLE_CLASSES = {2, 3, 5, 7}          # COCO: car, motorcycle, bus, truck


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require(module: str, hint: str):
    try:
        return __import__(module)
    except ImportError as exc:
        raise SystemExit(
            f"\n[edge] '{module}' is not installed, which --mode video needs.\n"
            f"       {hint}\n"
            f"       Meanwhile `python -m edge --mode simulate` exercises the\n"
            f"       whole system without any model weights.\n") from exc


class OCREngine:
    """PaddleOCR restricted to recognition on an already-cropped plate.

    Running full detection+recognition on the crop wastes time finding
    text we have already localised, and it sometimes splits a plate into
    two boxes, which is worse than useless for voting.
    """

    def __init__(self) -> None:
        _require("paddleocr", "pip install paddlepaddle paddleocr")
        from paddleocr import PaddleOCR
        self.ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

    def read(self, crop) -> tuple[str, list[float]]:
        result = self.ocr.ocr(crop, det=False, cls=True)
        if not result or not result[0]:
            return "", []
        text, score = result[0][0]
        # PaddleOCR returns one score per line, not per character; spread
        # it across the characters so the voter has something per column.
        return text, [float(score)] * len(text)


def preprocess_plate(crop):
    """Light touch only.

    Deskew and upscale help; aggressive denoise and binarisation destroy
    the stroke detail PaddleOCR relies on, and consistently made accuracy
    worse in testing. Resist the urge to add more here.
    """
    cv2 = _require("cv2", "pip install opencv-python")
    h, w = crop.shape[:2]
    if h == 0 or w == 0:
        return crop
    if w < 160:                                   # upscale small crops
        scale = 160.0 / w
        crop = cv2.resize(crop, (int(w * scale), int(h * scale)),
                          interpolation=cv2.INTER_CUBIC)
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)


def sharpness(crop) -> float:
    """Variance of the Laplacian — the standard blur proxy."""
    cv2 = _require("cv2", "pip install opencv-python")
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def run_video(source: str, camera_id: str, debug_video: bool = False) -> int:
    """Process a video file or RTSP stream end to end.

    Emits exactly one sighting per tracked vehicle per pass — the track id
    is what prevents one car becoming forty database rows.
    """
    cv2 = _require("cv2", "pip install opencv-python")
    _require("ultralytics", "pip install ultralytics")
    from ultralytics import YOLO

    vehicle_model = os.getenv("NETRA_VEHICLE_MODEL", "yolov8n.pt")
    plate_model_path = os.getenv("NETRA_PLATE_MODEL", "")

    print(f"[edge] camera={camera_id} source={source}")
    from .runtime import describe_runtime
    print(describe_runtime())

    detector = YOLO(vehicle_model)
    plate_detector = YOLO(plate_model_path) if plate_model_path else None
    if plate_detector is None:
        print("[edge] NETRA_PLATE_MODEL not set — using the lower half of each\n"
              "       vehicle box as the plate region. Fine for a smoke test,\n"
              "       poor for accuracy. Train a plate detector for real use.")
    ocr = OCREngine()

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"[edge] cannot open {source}")
        return 1

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    stride = max(1, int(round(src_fps / TARGET_FPS)))
    print(f"[edge] source {src_fps:.1f} fps, processing every {stride} frame(s)")

    pub = Publisher(batch_size=4)
    tracks: dict[int, TrackAccumulator] = {}
    last_seen: dict[int, float] = {}
    meta: dict[int, dict] = {}
    frame_no = 0
    writer = None

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            if frame_no % stride:
                continue

            # persist=True keeps ByteTrack ids stable across calls, which
            # is the entire basis of temporal voting. ByteTrack is chosen
            # over DeepSORT because it needs no ReID network — and we do
            # not have the VRAM to spare for one.
            results = detector.track(frame, persist=True, verbose=False,
                                     tracker="bytetrack.yaml",
                                     classes=list(VEHICLE_CLASSES))
            now = time.time()

            for box in (results[0].boxes if results else []):
                if box.id is None:
                    continue
                tid = int(box.id.item())
                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                vehicle = frame[max(0, y1):y2, max(0, x1):x2]
                if vehicle.size == 0:
                    continue

                # --- locate the plate within the vehicle crop ---
                if plate_detector is not None:
                    pres = plate_detector(vehicle, verbose=False)
                    pboxes = pres[0].boxes if pres else []
                    if not len(pboxes):
                        continue
                    best = max(pboxes, key=lambda b: float(b.conf.item()))
                    px1, py1, px2, py2 = (int(v) for v in best.xyxy[0].tolist())
                    det_conf = float(best.conf.item())
                    plate_crop = vehicle[max(0, py1):py2, max(0, px1):px2]
                else:
                    h, w = vehicle.shape[:2]
                    plate_crop = vehicle[int(h * 0.55):h, int(w * 0.15):int(w * 0.85)]
                    det_conf = float(box.conf.item())

                if plate_crop.size == 0:
                    continue

                acc = tracks.setdefault(tid, TrackAccumulator(f"{camera_id}:{tid}", camera_id))
                last_seen[tid] = now
                meta.setdefault(tid, {"cls": int(box.cls.item())})

                # Skip OCR entirely once the vote has settled — real GPU
                # savings on a 4 GB card.
                if acc.stable:
                    continue

                ph, pw = plate_crop.shape[:2]
                q = frame_quality(
                    bbox_area_px=ph * pw,
                    sharpness=sharpness(plate_crop),
                    aspect=(pw / ph) if ph else 0.0,
                    detector_conf=det_conf,
                )
                text, confs = ocr.read(preprocess_plate(plate_crop))
                if text:
                    acc.add(PlateRead(text, confs, quality=q, timestamp=now_iso()))

            # --- finish tracks that have left the frame ---
            for tid in [t for t, seen in last_seen.items() if now - seen > TRACK_IDLE_S]:
                acc = tracks.pop(tid, None)
                last_seen.pop(tid, None)
                info = meta.pop(tid, {})
                if not acc:
                    continue
                r = acc.consensus()
                if r.decision == "discard":
                    continue
                pub.add({
                    "plate": r.plate,
                    "camera_id": camera_id,
                    "seen_at": now_iso(),
                    "confidence": r.confidence,
                    "track_id": acc.track_id,
                    "frame_count": r.frame_count,
                    "vehicle_type": {2: "car", 3: "motorcycle",
                                     5: "bus", 7: "truck"}.get(info.get("cls"), None),
                    "source": "edge",
                    "dedupe_key": f"{acc.track_id}:{now_iso()}",
                    "reads": [
                        {"raw_text": rd.text,
                         "confidence": (sum(rd.char_confidences) / len(rd.char_confidences))
                                       if rd.char_confidences else 0.5,
                         "quality": rd.quality,
                         "frame_ts": rd.timestamp}
                        for rd in acc.reads[:12]
                    ],
                })
                print(f"  {r.plate:<12} conf {r.confidence:.3f}  "
                      f"frames {r.frame_count}  {r.decision}")

            if debug_video:
                annotated = results[0].plot() if results else frame
                if writer is None:
                    h, w = annotated.shape[:2]
                    writer = cv2.VideoWriter(
                        f"{camera_id}.annotated.mp4",
                        cv2.VideoWriter_fourcc(*"mp4v"), TARGET_FPS, (w, h))
                writer.write(annotated)
    except KeyboardInterrupt:
        print("\n[edge] interrupted")
    finally:
        # Flush every track still in flight, or the last vehicles of the
        # clip are silently lost.
        for acc in tracks.values():
            r = acc.consensus()
            if r.decision != "discard":
                pub.add({"plate": r.plate, "camera_id": camera_id,
                         "seen_at": now_iso(), "confidence": r.confidence,
                         "track_id": acc.track_id, "frame_count": r.frame_count,
                         "source": "edge",
                         "dedupe_key": f"{acc.track_id}:final", "reads": []})
        pub.drain()
        cap.release()
        if writer:
            writer.release()
            print(f"[edge] wrote {camera_id}.annotated.mp4")
        print(f"[edge] published {pub.sent} sightings")
    return 0
