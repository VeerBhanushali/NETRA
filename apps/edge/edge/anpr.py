"""Real ANPR on real video.

YOLOv8 detects and tracks vehicles, EasyOCR reads the plate region of each
tracked vehicle, temporal voting fuses the reads across frames, and the
consensus is published to the API. Also writes an annotated frame per
camera so the dashboard's live wall shows what the model actually sees.

    python -m edge --mode anpr --source datasets/demo_clips/vehicles.mp4 --camera cam-01

On this machine (AMD RX 6500M, no CUDA) torch runs on CPU. YOLOv8n on
640px CPU frames is roughly 60-120 ms, and EasyOCR another 40-90 ms per
crop, so we sample a few frames per second rather than pretending to do
30 FPS. Vehicles remain in frame for seconds, which is ample for voting.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .publisher import Publisher
from .voting import PlateRead, TrackAccumulator, frame_quality

VEHICLE_CLASSES = {2, 3, 5, 7}          # COCO: car, motorcycle, bus, truck
CLASS_NAME = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# Frames per second to actually analyse. Higher is not better here: it
# burns CPU for near-duplicate frames that add no independent evidence.
TARGET_FPS = float(os.getenv("NETRA_EDGE_FPS", "4"))
# Seconds a track can go unseen before we consider the pass complete.
TRACK_IDLE_S = float(os.getenv("NETRA_TRACK_IDLE", "1.2"))
# Don't waste OCR on a vehicle too small to carry a legible plate.
MIN_VEHICLE_PX = 60 * 60
# Cap OCR calls per analysed frame so one crowded frame cannot stall the loop.
MAX_OCR_PER_FRAME = int(os.getenv("NETRA_MAX_OCR", "4"))
# Refuse to publish the same plate from the same camera again inside this
# window. Two reasons: a vehicle idling in view keeps being re-detected and
# is not making a new pass, and a looped demo clip would otherwise emit the
# same plate every few seconds and manufacture loitering alerts.
REPUBLISH_COOLDOWN_S = float(os.getenv("NETRA_REPUBLISH_COOLDOWN", "180"))

REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_DIR = Path(os.getenv("NETRA_LIVE_DIR", REPO_ROOT / "data" / "evidence" / "live"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PlateReader:
    """EasyOCR restricted to plate-shaped text.

    An allowlist of uppercase Latin + digits stops the reader returning
    punctuation and lowercase noise that voting would then have to
    discount. Confidence comes back per detection, not per character, so
    it is spread across the string.
    """

    def __init__(self) -> None:
        import easyocr
        print("[anpr] loading EasyOCR (first run downloads ~100 MB of weights)…")
        self.reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        self.allow = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

    def read(self, crop) -> tuple[str, list[float]]:
        import cv2

        # Plate crops off a distant vehicle are 60-90 px wide, well below
        # what the recogniser wants. Upscaling with cubic interpolation
        # measurably improves reads; heavier processing (binarisation,
        # denoise) made them worse in testing.
        h, w = crop.shape[:2]
        if w < 240:
            scale = 240.0 / max(w, 1)
            crop = cv2.resize(crop, (int(w * scale), max(1, int(h * scale))),
                              interpolation=cv2.INTER_CUBIC)
        try:
            found = self.reader.readtext(crop, allowlist=self.allow,
                                         detail=1, paragraph=False)
        except Exception:                              # noqa: BLE001
            return "", []
        if not found:
            return "", []
        # Join every fragment left-to-right: the recogniser often splits a
        # plate into two boxes, and taking only the best one silently
        # truncates the plate.
        found.sort(key=lambda f: f[0][0][0])
        text = "".join(
            ch for f in found for ch in str(f[1]).upper() if ch.isalnum())
        conf = sum(float(f[2]) for f in found) / len(found)
        if len(text) < 4:
            return "", []
        return text, [conf] * len(text)


def plate_region(vehicle):
    """Crop the part of a vehicle most likely to hold the plate.

    A dedicated plate detector would be better and the pipeline supports
    one via NETRA_PLATE_MODEL. Absent trained weights, the lower-central
    band of the vehicle box is a reasonable prior for rear-facing traffic
    footage, and it is honest to say so rather than claim a detector we
    do not have.
    """
    h, w = vehicle.shape[:2]
    return vehicle[int(h * 0.50):h, int(w * 0.10):int(w * 0.90)]


def run_anpr(source, camera_id: str, max_seconds: float | None = None,
             annotate: bool = True, loop: bool = False) -> int:
    """`source` is a path/URL, or a list of them played back to back.

    A playlist is what makes the demo feel like real CCTV: the camera
    shows continuously changing traffic for hours instead of the same
    six seconds on repeat, which is immediately obvious to anyone
    watching.
    """
    import cv2
    from ultralytics import YOLO

    from .plate_norm import REGION

    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[anpr] camera={camera_id} source={source} region={REGION}")

    model = YOLO(os.getenv("NETRA_VEHICLE_MODEL", "yolov8n.pt"))

    # A trained plate detector is what makes this real ANPR rather than
    # OCR-on-a-guess. Without one we fall back to a crop heuristic, and
    # the log says so plainly instead of quietly degrading.
    # Resolve against the repo root, not the process working directory —
    # the worker is normally launched from apps/edge, where a repo-relative
    # path silently misses and degrades the pipeline without failing.
    plate_weights = os.getenv("NETRA_PLATE_MODEL", "")
    if not plate_weights:
        plate_weights = str(REPO_ROOT / "models" / "onnx" / "plate_yolo11n.pt")
    elif not Path(plate_weights).is_absolute():
        plate_weights = str(REPO_ROOT / plate_weights)

    plate_model = None
    if Path(plate_weights).is_file():
        plate_model = YOLO(plate_weights)
        print(f"[anpr] plate detector: {plate_weights}")
    else:
        print(f"[anpr] no plate detector at {plate_weights} — falling back to "
              f"the lower-vehicle crop heuristic (much weaker)")

    reader = PlateReader()
    pub = Publisher(batch_size=2)

    playlist = [source] if isinstance(source, (str, bytes)) else list(source)
    if not playlist:
        print("[anpr] no sources given")
        return 1
    clip_idx = 0

    def open_clip(i: int):
        c = cv2.VideoCapture(playlist[i])
        if not c.isOpened():
            return None, 1
        fps = c.get(cv2.CAP_PROP_FPS) or 25.0
        st = max(1, int(round(fps / TARGET_FPS)))
        name = Path(str(playlist[i])).name
        print(f"[anpr] [{camera_id}] clip {i + 1}/{len(playlist)}: {name} "
              f"({fps:.0f} fps, analysing every {st})")
        return c, st

    cap, stride = open_clip(clip_idx)
    if cap is None:
        print(f"[anpr] cannot open {playlist[clip_idx]}")
        return 1

    tracks: dict[int, TrackAccumulator] = {}
    last_seen: dict[int, float] = {}
    kinds: dict[int, int] = {}
    published: set[int] = set()
    # Latest plate box + current consensus per track, purely for drawing.
    plate_boxes: dict[int, dict] = {}
    last_plate_at: dict[str, float] = {}
    loops = 0
    frame_no = 0
    started = time.time()
    stats = {"frames": 0, "ocr": 0, "published": 0, "review": 0}

    def finish(tid: int) -> None:
        acc = tracks.pop(tid, None)
        last_seen.pop(tid, None)
        cls = kinds.pop(tid, None)
        plate_boxes.pop(tid, None)
        if not acc or tid in published or len(acc) == 0:
            return
        r = acc.consensus()
        if r.decision == "discard":
            return
        published.add(tid)

        last = last_plate_at.get(r.plate)
        if last is not None and (time.time() - last) < REPUBLISH_COOLDOWN_S:
            return
        last_plate_at[r.plate] = time.time()

        seen = now_iso()
        pub.add({
            "plate": r.plate,
            "camera_id": camera_id,
            "seen_at": seen,
            "confidence": r.confidence,
            "track_id": acc.track_id,
            "frame_count": r.frame_count,
            "vehicle_type": CLASS_NAME.get(cls),
            "source": "edge",
            "dedupe_key": f"{acc.track_id}:{seen}",
            "reads": [
                {"raw_text": rd.text,
                 "confidence": round(sum(rd.char_confidences) / len(rd.char_confidences), 3)
                               if rd.char_confidences else 0.5,
                 "quality": rd.quality,
                 "frame_ts": rd.timestamp or seen}
                for rd in acc.reads[:12]
            ],
        })
        stats["published" if r.decision == "auto_accept" else "review"] += 1
        print(f"  [{camera_id}] {r.plate:<10} conf {r.confidence:.3f} "
              f"frames {r.frame_count:<3} {r.decision}")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                # Close out every in-flight track before switching clips,
                # otherwise the last vehicles of the clip are lost and their
                # accumulators leak across the boundary.
                for t in list(tracks):
                    finish(t)
                published.clear()
                cap.release()

                if clip_idx + 1 < len(playlist):
                    clip_idx += 1
                elif loop:
                    clip_idx = 0
                    loops += 1
                    print(f"  [{camera_id}] playlist pass {loops + 1}")
                else:
                    break

                cap, stride = open_clip(clip_idx)
                if cap is None:
                    break
                continue
            frame_no += 1
            if frame_no % stride:
                continue
            if max_seconds and (time.time() - started) > max_seconds:
                break

            stats["frames"] += 1
            results = model.track(frame, persist=True, verbose=False,
                                  tracker="bytetrack.yaml",
                                  classes=list(VEHICLE_CLASSES), conf=0.35)
            now = time.time()
            boxes = results[0].boxes if results else []

            # OCR the biggest vehicles first: they carry the most legible
            # plates, and the per-frame cap means the rest can wait for a
            # later frame rather than blocking this one.
            candidates = []
            for box in boxes:
                if box.id is None:
                    continue
                tid = int(box.id.item())
                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                area = max(0, x2 - x1) * max(0, y2 - y1)
                last_seen[tid] = now
                kinds[tid] = int(box.cls.item())
                if area < MIN_VEHICLE_PX or tid in published:
                    continue
                acc = tracks.setdefault(
                    tid, TrackAccumulator(f"{camera_id}:{tid}", camera_id))
                if acc.stable:
                    continue
                candidates.append((area, tid, (x1, y1, x2, y2), float(box.conf.item())))

            candidates.sort(reverse=True, key=lambda c: c[0])
            for area, tid, (x1, y1, x2, y2), det_conf in candidates[:MAX_OCR_PER_FRAME]:
                vehicle = frame[max(0, y1):y2, max(0, x1):x2]
                if vehicle.size == 0:
                    continue

                px1 = py1 = px2 = py2 = 0
                m = 2
                if plate_model is not None:
                    # Localise the plate inside the vehicle box, so OCR
                    # sees a plate and not a bumper, a badge and a shadow.
                    pres = plate_model(vehicle, verbose=False, conf=0.25)
                    pboxes = pres[0].boxes if pres else []
                    if not len(pboxes):
                        continue
                    best = max(pboxes, key=lambda b: float(b.conf.item()))
                    px1, py1, px2, py2 = (int(v) for v in best.xyxy[0].tolist())
                    # A small margin keeps the characters off the crop edge.
                    crop = vehicle[max(0, py1 - m):py2 + m, max(0, px1 - m):px2 + m]
                    det_conf = float(best.conf.item())
                else:
                    crop = plate_region(vehicle)

                if crop.size == 0:
                    continue
                ph, pw = crop.shape[:2]
                if pw < 30:
                    continue
                grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                sharp = float(cv2.Laplacian(grey, cv2.CV_64F).var())
                text, confs = reader.read(crop)
                stats["ocr"] += 1
                if text:
                    q = frame_quality(bbox_area_px=ph * pw, sharpness=sharp,
                                      aspect=(pw / ph) if ph else 0.0,
                                      detector_conf=det_conf)
                    tracks[tid].add(PlateRead(text, confs, quality=q,
                                              timestamp=now_iso()))
                    # Remember where the plate was and what we currently
                    # believe it says, so the live frame can show it.
                    vote = tracks[tid].consensus()
                    entry = {"text": vote.plate or text, "conf": vote.confidence}
                    if plate_model is not None:
                        entry["box"] = (x1 + max(0, px1 - m), y1 + max(0, py1 - m),
                                        x1 + px2 + m, y1 + py2 + m)
                    plate_boxes[tid] = entry

            for tid in [t for t, seen in list(last_seen.items())
                        if now - seen > TRACK_IDLE_S]:
                finish(tid)

            if annotate:
                # Drawn by hand rather than results[0].plot(): the stock
                # plotter shows "car 0.83", which tells an operator nothing.
                # What they need to see is the plate box and the string the
                # system currently believes — that is the whole product.
                img = frame.copy()
                for box in boxes:
                    if box.id is None:
                        continue
                    tid = int(box.id.item())
                    x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                    cv2.rectangle(img, (x1, y1), (x2, y2), (120, 120, 120), 1)

                    acc = tracks.get(tid)
                    label = plate_boxes.get(tid, {}).get("text")
                    conf = plate_boxes.get(tid, {}).get("conf", 0.0)
                    pbox = plate_boxes.get(tid, {}).get("box")

                    if pbox:
                        cv2.rectangle(img, (pbox[0], pbox[1]), (pbox[2], pbox[3]),
                                      (0, 200, 0), 2)
                    if label:
                        # Auto-accept green, review amber: the operator can
                        # tell at a glance which reads are being published.
                        colour = (0, 170, 0) if conf >= 0.90 else (0, 160, 235)
                        text = f"{label} {conf * 100:.0f}%"
                        (tw, th), _ = cv2.getTextSize(
                            text, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
                        ty = max(th + 8, y1 - 6)
                        cv2.rectangle(img, (x1, ty - th - 6), (x1 + tw + 10, ty + 4),
                                      colour, -1)
                        cv2.putText(img, text, (x1 + 5, ty),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)

                banner = f"{camera_id}   {now_iso()}   tracks {len(tracks)}"
                cv2.rectangle(img, (0, 0), (img.shape[1], 34), (20, 20, 20), -1)
                cv2.putText(img, banner, (12, 23),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

                # Temp name then rename: the browser polls this file
                # constantly and must never receive a half-written JPEG.
                #
                # On Windows the rename fails outright if the browser has
                # the destination open at that instant. Annotation is
                # cosmetic, so a failure here must never take down ingest —
                # this crashed a camera in testing. Retry briefly, then
                # skip the frame.
                tmp = LIVE_DIR / f".{camera_id}.tmp.jpg"
                dst = LIVE_DIR / f"{camera_id}.jpg"
                try:
                    cv2.imwrite(str(tmp), img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    for attempt in range(3):
                        try:
                            os.replace(tmp, dst)
                            break
                        except PermissionError:
                            time.sleep(0.05 * (attempt + 1))
                except Exception as exc:                  # noqa: BLE001
                    print(f"  [{camera_id}] frame write skipped: {exc!r}")
    except KeyboardInterrupt:
        print("\n[anpr] interrupted")
    finally:
        for tid in list(tracks):
            finish(tid)
        pub.drain()
        cap.release()
        elapsed = time.time() - started
        print(f"\n[anpr] {stats['frames']} frames analysed in {elapsed:.1f}s "
              f"({stats['frames']/max(elapsed,1):.1f} fps), {stats['ocr']} OCR calls")
        print(f"[anpr] published {stats['published']} auto-accepted, "
              f"{stats['review']} sent to review")
    return 0
