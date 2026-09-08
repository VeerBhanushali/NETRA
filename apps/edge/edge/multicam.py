"""Many cameras, one process.

Running a separate full pipeline per camera does not scale past three on a
CPU-only machine: each one loads its own YOLO and EasyOCR, and ten copies
thrash both RAM and cache. This module runs the whole network in one
process with **one** shared plate detector and **one** shared OCR reader.

The important structural idea is that display is decoupled from inference:

  * one decoder thread per camera, which reads frames and writes the
    annotated JPEG the dashboard polls. Decoding and JPEG encoding are
    cheap and release the GIL, so every camera shows smooth, continuously
    moving video.

  * one inference thread, which round-robins the cameras, takes whichever
    frame is current, and runs detection → tracking → plate → OCR → vote.
    Its results are handed back to the decoder as overlays.

So a camera keeps showing live video even in the moments the inference
thread is busy elsewhere. The alternative — inference inline with decode —
makes every tile stutter in lockstep, which looks broken.

Tracking state cannot be shared: Ultralytics keeps tracker state on the
model instance, so interleaving cameras through one instance would blend
their track IDs and corrupt voting. Each camera therefore gets its own
lightweight YOLO detector instance (a few MB of weights) while the two
expensive models stay shared.

    python -m edge --mode network
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .publisher import Publisher
from .snapshot import THREAT_CLASSES, threat_evidence
from .voting import PlateRead, TrackAccumulator, frame_quality

VEHICLE_CLASSES = {2, 3, 5, 7}
CLASS_NAME = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

PERSON_CLASS = 0
# A threat channel watches people and the objects near their hands, so it
# tracks a different class set than an ANPR channel. Sharing one detector
# instance across both would blend ByteTrack ids between vehicles and
# people, so each camera keeps its own — as it already does.
THREAT_TRACK_CLASSES = {PERSON_CLASS} | set(THREAT_CLASSES)
# Don't re-raise the same scene every pass. A person carrying scissors
# across a square would otherwise generate an alert every 150 ms.
THREAT_COOLDOWN_S = float(os.getenv("NETRA_THREAT_COOLDOWN", "45"))
THREAT_ALERT_MIN = float(os.getenv("NETRA_THREAT_MIN", "0.35"))

REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_DIR = Path(os.getenv("NETRA_LIVE_DIR", REPO_ROOT / "data" / "evidence" / "live"))
# Plate crops kept as evidence. A review screen that shows a placeholder
# instead of the actual image asks an operator to confirm a plate they
# cannot see — which is not review, it is rubber-stamping.
CROP_DIR = Path(os.getenv("NETRA_CROP_DIR", REPO_ROOT / "data" / "evidence" / "crops"))

DISPLAY_FPS = float(os.getenv("NETRA_DISPLAY_FPS", "8"))
MAX_OCR_PER_PASS = int(os.getenv("NETRA_MAX_OCR", "3"))
# How long a track may go unseen before we call the pass finished.
#
# Must exceed the interval between inference passes ON THE SAME CAMERA.
# With ten cameras round-robining, a camera is revisited every several
# seconds; at the old 2.5 s every track was closed after a single pass and
# voting never accumulated more than one frame — the network streamed
# beautifully and read almost nothing.
TRACK_IDLE_S = float(os.getenv("NETRA_TRACK_IDLE", "20"))
MIN_VEHICLE_PX = 55 * 55
REPUBLISH_COOLDOWN_S = float(os.getenv("NETRA_REPUBLISH_COOLDOWN", "180"))
# Longest edge fed to the detector. 4K frames cost a fortune on CPU for no
# accuracy gain once the plate is already below readable size.
INFER_MAX_W = int(os.getenv("NETRA_INFER_WIDTH", "1280"))
# Downscale at decode time, before the frame is ever stored.
#
# This is a memory fix, not a cosmetic one: a single 3840x2160 frame is
# ~24 MB uncompressed, and ten decoder threads each holding one pushed the
# machine to 95% RAM and starved the browser. Nothing downstream needs
# more than this width — the plate is already unreadable at the distances
# where 4K would matter.
DECODE_MAX_W = int(os.getenv("NETRA_DECODE_WIDTH", "1280"))
# Minimum seconds between inference passes.
#
# The inference loop is otherwise unbounded and will consume every spare
# cycle, which on a laptop means the desktop and the browser stutter and
# the whole app feels broken. Pacing it costs a little plate accuracy and
# buys back a usable machine. Set to 0 on a dedicated box or a GPU.
INFER_INTERVAL_S = float(os.getenv("NETRA_INFER_INTERVAL", "0.12"))
# How long a computed bounding box stays valid for drawing.
BOX_TTL_S = float(os.getenv("NETRA_BOX_TTL", "0.7"))
# A car at 60 km/h leaves its box behind in under a second, so an ANPR
# box must expire fast or it is drawn on empty road. A person walks at
# 5 km/h — the same TTL there just makes the boxes blink off between
# passes, which reads as a detector that keeps losing people.
THREAT_BOX_TTL_S = float(os.getenv("NETRA_THREAT_BOX_TTL", "3.0"))
# Conclude a vote as soon as it is settled and confident, rather than
# waiting for the vehicle to leave the frame.
#
# Waiting was a latency choice that turned into a correctness bug: on a
# looping clip — and on any real camera watching stationary or slow
# traffic — the vehicle never goes idle, so the track stayed open and the
# read was never published at all. Evidence piled up and nothing came out.
SETTLE_MIN_FRAMES = int(os.getenv("NETRA_SETTLE_FRAMES", "8"))
# Hard ceiling on how long one track may stay open before we conclude it
# regardless, so a permanently parked car cannot hold a slot for ever.
TRACK_MAX_AGE_S = float(os.getenv("NETRA_TRACK_MAX_AGE", "45"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CameraStream:
    """Decodes one camera's playlist and writes its annotated frame."""

    def __init__(self, cam_id: str, playlist: list[str], display_fps: float,
                 loop: bool = True, name: str = "",
                 box_ttl: float = BOX_TTL_S) -> None:
        self.id = cam_id
        self.name = name
        self.box_ttl = box_ttl
        self.playlist = playlist
        self.idx = 0
        self.loop = loop
        self.display_fps = display_fps
        self.cap = None
        self.latest = None            # most recent decoded frame (BGR)
        self.overlays: dict = {}      # track_id -> {box, plate_box, text, conf}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.frames = 0
        self.alive = False
        # (label, confidence). A None confidence means the label is a
        # count — a threat channel reports "25 people tracked", which is
        # not a percentage of anything.
        self.recent_reads: list[tuple[str, float | None]] = []

    # --- decoding -----------------------------------------------------

    def _open(self) -> bool:
        import cv2
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(self.playlist[self.idx])
        ok = self.cap.isOpened()
        if ok:
            print(f"[net] {self.id}: clip {self.idx + 1}/{len(self.playlist)} "
                  f"{Path(self.playlist[self.idx]).name}")
        return ok

    def run(self) -> None:
        import cv2

        if not self._open():
            print(f"[net] {self.id}: cannot open {self.playlist[self.idx]}")
            return
        self.alive = True
        interval = 1.0 / max(self.display_fps, 0.5)

        while not self.stop.is_set():
            started = time.monotonic()
            ok, frame = self.cap.read()
            if not ok:
                # Advance the playlist; wrap only if looping.
                if self.idx + 1 < len(self.playlist):
                    self.idx += 1
                elif self.loop:
                    self.idx = 0
                else:
                    break
                if not self._open():
                    break
                continue

            fh, fw = frame.shape[:2]
            if fw > DECODE_MAX_W:
                frame = cv2.resize(frame, (DECODE_MAX_W,
                                           int(fh * DECODE_MAX_W / fw)),
                                   interpolation=cv2.INTER_AREA)

            with self.lock:
                self.latest = frame
            self.frames += 1
            self._write(frame)

            # Pace to display_fps so a short clip does not race past in
            # two seconds and so we leave CPU for the inference thread.
            time.sleep(max(0.0, interval - (time.monotonic() - started)))

        self.alive = False
        if self.cap:
            self.cap.release()

    # --- drawing ------------------------------------------------------

    def _write(self, frame) -> None:
        import cv2

        img = frame.copy()
        now = time.time()
        with self.lock:
            overlays = list(self.overlays.values())
            recent = list(self.recent_reads)

        # Boxes are only valid for the frame they were computed on. The
        # vehicle keeps moving while the next inference pass is queued, so
        # a box older than this is drawn on the wrong part of the road —
        # which looked like the detector had missed the plate entirely.
        # Past the TTL the geometry is dropped and the read survives in
        # the corner readout, which does not depend on position.
        for ov in overlays:
            if now - ov.get("ts", 0) > self.box_ttl:
                continue
            x1, y1, x2, y2 = ov["box"]
            # Mid-grey at 1 px is right for a vehicle box on tarmac and
            # invisible for a person box on a white marble concourse, so
            # the channel supplies its own when it needs to.
            cv2.rectangle(img, (x1, y1), (x2, y2),
                          ov.get("box_colour", (150, 150, 150)),
                          ov.get("box_width", 1))
            if ov.get("plate_box"):
                p = ov["plate_box"]
                cv2.rectangle(img, (p[0], p[1]), (p[2], p[3]), (0, 220, 0), 2)
            if ov.get("text"):
                conf = ov.get("conf", 0.0)
                # A threat channel supplies its own colour: its confidence
                # scale means something different from a plate's, so the
                # green/amber rule for reads must not be applied to it.
                colour = ov.get("colour") or (
                    (0, 170, 0) if conf >= 0.90 else (0, 160, 235))
                label = f"{ov['text']}  {conf * 100:.0f}%"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)
                ty = max(th + 10, y1 - 8)
                cv2.rectangle(img, (x1, ty - th - 8), (x1 + tw + 12, ty + 5), colour, -1)
                cv2.putText(img, label, (x1 + 6, ty),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2,
                            cv2.LINE_AA)

        # --- CCTV burn-in ---------------------------------------------
        # Real surveillance footage carries its identity in the pixels,
        # not in the surrounding UI: the frame has to stay legible after
        # it is exported, screenshotted or handed to a court. So the
        # camera id, wall-clock and REC state are drawn into the image
        # itself, in the corner positions operators expect.
        h, w = img.shape[:2]
        stamp = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

        def shadowed(text, org, scale=0.5, colour=(240, 240, 240)):
            # A one-pixel offset drop shadow, not a thick outline. The
            # outline version smeared at these sizes and looked like the
            # text had been rendered twice.
            cv2.putText(img, text, (org[0] + 1, org[1] + 1),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 1, cv2.LINE_AA)
            cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale,
                        colour, 1, cv2.LINE_AA)

        shadowed(f"{self.id.upper()}   {self.name}", (12, 25), 0.5)
        shadowed(stamp, (12, h - 14), 0.5)

        # Blinking REC dot, once per second, like a real recorder.
        if int(time.time()) % 2 == 0:
            cv2.circle(img, (w - 68, 18), 5, (60, 60, 220), -1, cv2.LINE_AA)
        shadowed("REC", (w - 55, 25), 0.48, (90, 90, 240))

        shadowed(f"{w}x{h}  {self.display_fps:.0f}FPS", (w - 148, h - 14), 0.42,
                 (205, 205, 205))

        # Rolling readout of what this camera has actually read. Unlike a
        # bounding box it cannot drift, so it stays correct between passes.
        for i, (plate, conf) in enumerate(recent[:4]):
            y = 52 + i * 20
            # conf None = a count, not a confidence. "25 person 0%" read as
            # a failed detection when it was in fact 25 successful ones.
            if conf is None:
                shadowed(plate, (12, y), 0.46, (200, 200, 200))
                continue
            col = (120, 255, 120) if conf >= 0.90 else (120, 210, 255)
            shadowed(f"{plate}  {conf * 100:.0f}%", (12, y), 0.46, col)

        # Hairline frame, so the tile reads as a video feed rather than a
        # loose photograph.
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), (70, 70, 70), 1)

        tmp = LIVE_DIR / f".{self.id}.tmp.jpg"
        dst = LIVE_DIR / f"{self.id}.jpg"
        try:
            cv2.imwrite(str(tmp), img, [cv2.IMWRITE_JPEG_QUALITY, 70])
            for attempt in range(3):
                try:
                    os.replace(tmp, dst)
                    break
                except PermissionError:
                    # Windows refuses the rename while the browser reads it.
                    # Cosmetic, so never let it kill the stream.
                    time.sleep(0.04 * (attempt + 1))
        except Exception:                                  # noqa: BLE001
            pass

    def snapshot(self):
        with self.lock:
            return None if self.latest is None else self.latest.copy()


class Network:
    """Shared models + round-robin inference across every camera."""

    def __init__(self, cameras: list[dict], region: str, loop: bool) -> None:
        from ultralytics import YOLO
        import easyocr

        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        CROP_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[net] region={region}, {len(cameras)} cameras")

        plate_path = REPO_ROOT / "models" / "onnx" / "plate_yolo11n.pt"
        self.plate_model = YOLO(str(plate_path)) if plate_path.is_file() else None
        print(f"[net] plate detector: "
              f"{'loaded' if self.plate_model else 'MISSING — accuracy will be poor'}")

        print("[net] loading EasyOCR (shared by every camera)…")
        self.reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        self.allow = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

        self.pub = Publisher(batch_size=2)
        self.streams: list[CameraStream] = []
        self.detectors: dict[str, object] = {}
        self.tracks: dict[str, dict[int, TrackAccumulator]] = {}
        self.last_seen: dict[str, dict[int, float]] = {}
        self.kinds: dict[str, dict[int, int]] = {}
        self.published: dict[str, set] = {}
        self.opened: dict[str, dict[int, float]] = {}
        self.last_plate_at: dict[str, float] = {}
        # Best (highest quality) plate crop per track, kept in memory
        # until the track is published so we only write one file per pass.
        self.best_crop: dict[str, tuple[float, object]] = {}
        self.stop = threading.Event()
        self.stats = {"passes": 0, "ocr": 0, "published": 0, "review": 0}
        # Weighted schedule. Cameras whose plates are actually legible earn
        # more inference passes; a 4K overhead shot where no plate can be
        # resolved should not consume the same CPU as a toll-gate view.
        self.schedule: list[int] = []

        # "anpr" reads plates; "threat" watches people and the objects
        # near their hands. A camera does one job: the two need different
        # class sets, different framing and different evidence rules, and
        # a channel that claims to do both does neither convincingly.
        self.modes: dict[str, str] = {}
        self.last_threat_at: dict[str, float] = {}

        for c in cameras:
            cam_id = c["id"]
            self.modes[cam_id] = c.get("mode", "anpr")
            srcs = c.get("sources") or [c["source"]]
            srcs = [s if "://" in s else str(REPO_ROOT / s) for s in srcs]
            self.streams.append(
                CameraStream(cam_id, srcs, c.get("display_fps", DISPLAY_FPS), loop,
                             name=c.get("name", ""),
                             box_ttl=(THREAT_BOX_TTL_S
                                      if self.modes[cam_id] == "threat"
                                      else BOX_TTL_S)))
            # Its own detector instance, so ByteTrack IDs never blend
            # between cameras.
            self.detectors[cam_id] = YOLO(
                os.getenv("NETRA_VEHICLE_MODEL", "yolov8n.pt"))
            self.tracks[cam_id] = {}
            self.last_seen[cam_id] = {}
            self.kinds[cam_id] = {}
            self.published[cam_id] = set()
            self.opened[cam_id] = {}
            idx = len(self.streams) - 1
            # anpr_weight 0 = stream video only, never inferred. That is
            # not a downgrade: a 4K overhead shot cannot resolve a plate at
            # any budget, so spending inference on it only starves the
            # cameras that can. Every camera still shows live video.
            self.schedule += [idx] * max(0, int(c.get("anpr_weight", 1)))

        if not self.schedule:
            self.schedule = list(range(len(self.streams)))
        # Interleave so a weight-3 camera is spread through the cycle
        # rather than inferred three times back to back.
        self.schedule.sort(key=lambda k: (self.schedule.count(k), k))
        inferred = {self.streams[i].id for i in self.schedule}
        anpr_ids = sorted(i for i in inferred if self.modes[i] == "anpr")
        threat_ids = sorted(i for i in inferred if self.modes[i] == "threat")
        video_only = [s.id for s in self.streams if s.id not in inferred]
        print(f"[net] ANPR on: {', '.join(anpr_ids) or 'none'}")
        if threat_ids:
            print(f"[net] threat watch: {', '.join(threat_ids)}")
        if video_only:
            print(f"[net] video only: {', '.join(video_only)}")

    # --- OCR ----------------------------------------------------------

    def read_plate(self, crop) -> tuple[str, list[float]]:
        import cv2
        h, w = crop.shape[:2]
        if w < 240:
            s = 240.0 / max(w, 1)
            crop = cv2.resize(crop, (int(w * s), max(1, int(h * s))),
                              interpolation=cv2.INTER_CUBIC)
        try:
            found = self.reader.readtext(crop, allowlist=self.allow,
                                         detail=1, paragraph=False)
        except Exception:                                  # noqa: BLE001
            return "", []
        if not found:
            return "", []
        found.sort(key=lambda f: f[0][0][0])
        text = "".join(ch for f in found for ch in str(f[1]).upper() if ch.isalnum())
        conf = sum(float(f[2]) for f in found) / len(found)
        return (text, [conf] * len(text)) if len(text) >= 4 else ("", [])

    # --- publishing ---------------------------------------------------

    def finish(self, cam_id: str, tid: int) -> None:
        acc = self.tracks[cam_id].pop(tid, None)
        self.last_seen[cam_id].pop(tid, None)
        if acc is None:
            self.best_crop.pop(f"{cam_id}:{tid}", None)
        cls = self.kinds[cam_id].pop(tid, None)
        self.opened[cam_id].pop(tid, None)
        if not acc or len(acc) == 0 or tid in self.published[cam_id]:
            return
        r = acc.consensus()
        if r.decision == "discard":
            return
        self.published[cam_id].add(tid)

        key = f"{cam_id}:{r.plate}"
        last = self.last_plate_at.get(key)
        if last is not None and (time.time() - last) < REPUBLISH_COOLDOWN_S:
            return
        self.last_plate_at[key] = time.time()

        seen = now_iso()

        # Persist the single best crop of this pass as evidence.
        crop_path = None
        key = f"{cam_id}:{tid}"
        best = self.best_crop.pop(key, None)
        if best is not None and best[1] is not None:
            try:
                import cv2
                safe = r.plate or "unread"
                fname = f"{cam_id}_{safe}_{int(time.time())}_{tid}.jpg"
                cv2.imwrite(str(CROP_DIR / fname), best[1],
                            [cv2.IMWRITE_JPEG_QUALITY, 92])
                crop_path = f"crops/{fname}"
            except Exception as exc:                       # noqa: BLE001
                # Evidence capture must never block the sighting itself.
                print(f"[net] crop write failed for {key}: {exc!r}")

        self.pub.add({
            "plate": r.plate, "camera_id": cam_id, "seen_at": seen,
            "crop_path": crop_path,
            "confidence": r.confidence, "track_id": acc.track_id,
            "frame_count": r.frame_count,
            "vehicle_type": CLASS_NAME.get(cls),
            "source": "edge", "dedupe_key": f"{acc.track_id}:{seen}",
            "reads": [
                {"raw_text": rd.text,
                 "confidence": round(sum(rd.char_confidences) / len(rd.char_confidences), 3)
                               if rd.char_confidences else 0.5,
                 "quality": rd.quality, "frame_ts": rd.timestamp or seen}
                for rd in acc.reads[:12]
            ],
        })
        self.stats["published" if r.decision == "auto_accept" else "review"] += 1
        print(f"  [{cam_id}] {r.plate:<12} {r.confidence * 100:5.1f}%  "
              f"{r.frame_count:>2}f  {r.decision}")

    # --- one inference pass over one camera ---------------------------

    def infer(self, stream: CameraStream) -> None:
        if self.modes.get(stream.id) == "threat":
            self.infer_threat(stream)
            return
        self.infer_anpr(stream)

    def infer_threat(self, stream: CameraStream) -> None:
        """One pass over a threat channel.

        Reuses the same evidence assembly as the mobile camera
        (snapshot.threat_evidence) so a knife seen by a fixed camera and a
        knife seen by a phone are scored identically. One rule, one place.
        """
        frame = stream.snapshot()
        if frame is None:
            return
        cam_id = stream.id

        h, w = frame.shape[:2]
        scale = 1.0
        if w > INFER_MAX_W:
            import cv2
            scale = INFER_MAX_W / w
            small = cv2.resize(frame, (INFER_MAX_W, int(h * scale)))
        else:
            small = frame

        results = self.detectors[cam_id].track(
            small, persist=True, verbose=False, tracker="bytetrack.yaml",
            classes=list(THREAT_TRACK_CLASSES), conf=0.35)
        boxes = results[0].boxes if results else []
        inv = 1.0 / scale
        self.stats["passes"] += 1

        people: list[tuple] = []
        objects: list[dict] = []
        overlays: dict = {}

        for box in boxes:
            cls = int(box.cls.item())
            conf = float(box.conf.item())
            x1, y1, x2, y2 = (int(v * inv) for v in box.xyxy[0].tolist())
            tid = int(box.id.item()) if box.id is not None else -(len(overlays) + 1)

            if cls == PERSON_CLASS:
                people.append((x1, y1, x2, y2))
                overlays[tid] = {"box": (x1, y1, x2, y2),
                                 # BGR: amber, matching --warn in the console.
                                 "box_colour": (60, 180, 235), "box_width": 2}
            else:
                objects.append({"class": THREAT_CLASSES[cls],
                                "confidence": round(conf, 3),
                                "box": [x1, y1, x2, y2]})
                overlays[tid] = {"box": (x1, y1, x2, y2),
                                 "box_colour": (0, 140, 235), "box_width": 3,
                                 "text": THREAT_CLASSES[cls], "conf": conf,
                                 "colour": (0, 140, 235)}

        ev = threat_evidence(people, objects)

        stamped = time.time()
        for ov in overlays.values():
            ov["ts"] = stamped
        with stream.lock:
            stream.overlays = overlays
            label = f"{len(people)} person tracked" if len(people) == 1 \
                else f"{len(people)} people tracked"
            stream.recent_reads = [(label, None)] + (
                [(o["class"], o["confidence"]) for o in objects][:3])
            if not objects:
                stream.recent_reads.append(("no threat object in frame", None))

        if ev["score"] >= THREAT_ALERT_MIN and (
                stamped - self.last_threat_at.get(cam_id, 0.0) > THREAT_COOLDOWN_S):
            self.last_threat_at[cam_id] = stamped
            self.publish_threat(cam_id, ev)

    def publish_threat(self, cam_id: str, ev: dict) -> None:
        """Send threat evidence to the API as an anomaly observation."""
        import json
        import urllib.request

        detail = "; ".join(f"{c['kind']}: {c['detail']}"
                           for c in ev["contributions"])
        body = json.dumps({
            "camera_id": cam_id,
            "occurred_at": now_iso(),
            "kind": "weapon",
            "score": ev["score"],
            "detail": detail,
            "source": "heuristic",
        }).encode()
        from .publisher import API_URL, INGEST_TOKEN

        req = urllib.request.Request(
            f"{API_URL}/api/v1/ingest/anomaly", body,
            {"content-type": "application/json",
             "x-ingest-token": INGEST_TOKEN})
        try:
            urllib.request.urlopen(req, timeout=5).read()
            print(f"  [{cam_id}] threat evidence {ev['score']:.2f} — {detail}")
        except Exception as exc:                          # noqa: BLE001
            # The API being down must not stop the camera watching.
            print(f"[net] {cam_id} could not publish threat: {exc!r}")

    def infer_anpr(self, stream: CameraStream) -> None:
        import cv2

        frame = stream.snapshot()
        if frame is None:
            return
        cam_id = stream.id

        h, w = frame.shape[:2]
        scale = 1.0
        if w > INFER_MAX_W:
            scale = INFER_MAX_W / w
            small = cv2.resize(frame, (INFER_MAX_W, int(h * scale)))
        else:
            small = frame

        results = self.detectors[cam_id].track(
            small, persist=True, verbose=False, tracker="bytetrack.yaml",
            classes=list(VEHICLE_CLASSES), conf=0.35)
        boxes = results[0].boxes if results else []
        now = time.time()
        inv = 1.0 / scale
        overlays: dict = {}
        candidates = []

        for box in boxes:
            if box.id is None:
                continue
            tid = int(box.id.item())
            x1, y1, x2, y2 = (int(v * inv) for v in box.xyxy[0].tolist())
            self.last_seen[cam_id][tid] = now
            self.kinds[cam_id][tid] = int(box.cls.item())
            overlays[tid] = {"box": (x1, y1, x2, y2)}
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area < MIN_VEHICLE_PX or tid in self.published[cam_id]:
                continue
            acc = self.tracks[cam_id].setdefault(
                tid, TrackAccumulator(f"{cam_id}:{tid}", cam_id))
            self.opened[cam_id].setdefault(tid, now)
            if acc.stable:
                continue
            candidates.append((area, tid, (x1, y1, x2, y2), float(box.conf.item())))

        candidates.sort(reverse=True, key=lambda c: c[0])
        for area, tid, (x1, y1, x2, y2), det_conf in candidates[:MAX_OCR_PER_PASS]:
            vehicle = frame[max(0, y1):y2, max(0, x1):x2]
            if vehicle.size == 0:
                continue
            if self.plate_model is not None:
                pres = self.plate_model(vehicle, verbose=False, conf=0.25)
                pb = pres[0].boxes if pres else []
                if not len(pb):
                    continue
                best = max(pb, key=lambda b: float(b.conf.item()))
                px1, py1, px2, py2 = (int(v) for v in best.xyxy[0].tolist())
                m = 2
                crop = vehicle[max(0, py1 - m):py2 + m, max(0, px1 - m):px2 + m]
                det_conf = float(best.conf.item())
                overlays.setdefault(tid, {})["plate_box"] = (
                    x1 + max(0, px1 - m), y1 + max(0, py1 - m),
                    x1 + px2 + m, y1 + py2 + m)
            else:
                vh, vw = vehicle.shape[:2]
                crop = vehicle[int(vh * 0.5):vh, int(vw * 0.1):int(vw * 0.9)]

            if crop.size == 0 or crop.shape[1] < 30:
                continue
            grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            sharp = float(cv2.Laplacian(grey, cv2.CV_64F).var())
            text, confs = self.read_plate(crop)
            self.stats["ocr"] += 1
            if not text:
                continue
            ph, pw = crop.shape[:2]
            q = frame_quality(bbox_area_px=ph * pw, sharpness=sharp,
                              aspect=(pw / ph) if ph else 0.0,
                              detector_conf=det_conf)
            self.tracks[cam_id][tid].add(
                PlateRead(text, confs, quality=q, timestamp=now_iso()))
            key = f"{cam_id}:{tid}"
            if q > self.best_crop.get(key, (0.0, None))[0]:
                self.best_crop[key] = (q, crop.copy())
            vote = self.tracks[cam_id][tid].consensus()
            overlays.setdefault(tid, {})["text"] = vote.plate or text
            overlays[tid]["conf"] = vote.confidence

        # Keep the last known plate label on a vehicle across passes, so
        # the overlay does not flicker off between OCR opportunities.
        stamped = time.time()
        for ov in overlays.values():
            ov["ts"] = stamped
        with stream.lock:
            for tid, ov in overlays.items():
                prev = stream.overlays.get(tid, {})
                if "text" not in ov and prev.get("text"):
                    ov["text"] = prev["text"]
                    ov["conf"] = prev.get("conf", 0.0)
                if "plate_box" not in ov and prev.get("plate_box"):
                    ov["plate_box"] = prev["plate_box"]
            stream.overlays = overlays
            for ov in overlays.values():
                if not ov.get("text"):
                    continue
                stream.recent_reads = (
                    [(ov["text"], ov.get("conf", 0.0))]
                    + [r for r in stream.recent_reads if r[0] != ov["text"]]
                )[:6]

        # 1. votes that have settled — publish now, do not wait for the
        #    vehicle to leave.
        settled = []
        for tid, acc in list(self.tracks[cam_id].items()):
            if tid in self.published[cam_id] or len(acc) < SETTLE_MIN_FRAMES:
                continue
            if acc.consensus().decision == "auto_accept":
                settled.append(tid)

        # 2. tracks that have gone quiet, or overstayed their welcome.
        gone = [t for t, seen in list(self.last_seen[cam_id].items())
                if now - seen > TRACK_IDLE_S]
        stale = [t for t, opened in list(self.opened[cam_id].items())
                 if now - opened > TRACK_MAX_AGE_S]

        for tid in dict.fromkeys(settled + gone + stale):
            self.finish(cam_id, tid)
        self.stats["passes"] += 1

    # --- lifecycle ----------------------------------------------------

    def run(self) -> int:
        threads = [threading.Thread(target=s.run, daemon=True, name=f"dec-{s.id}")
                   for s in self.streams]
        for t in threads:
            t.start()
        time.sleep(2.0)          # let the decoders produce a first frame

        print(f"\n[net] all {len(self.streams)} cameras streaming. "
              f"Open http://localhost:3000/live\n")
        i = 0
        last_report = time.time()
        try:
            while not self.stop.is_set():
                pass_started = time.monotonic()
                stream = self.streams[self.schedule[i % len(self.schedule)]]
                i += 1
                if stream.alive:
                    try:
                        self.infer(stream)
                    except Exception as exc:              # noqa: BLE001
                        # One bad frame must never stop the network.
                        print(f"[net] {stream.id} inference error: {exc!r}")
                # Yield the CPU so decoding, the API and the operator's
                # browser all keep running smoothly.
                time.sleep(max(0.0, INFER_INTERVAL_S - (time.monotonic() - pass_started)))
                if time.time() - last_report > 60:
                    live = sum(1 for s in self.streams if s.alive)
                    open_tracks = sum(len(t) for t in self.tracks.values())
                    buffered = sum(len(a) for t in self.tracks.values()
                                   for a in t.values())
                    dropped = sum(a.rejected for t in self.tracks.values()
                                  for a in t.values())
                    print(f"[net] {live}/{len(self.streams)} streaming · "
                          f"{self.stats['passes']} passes · {self.stats['ocr']} OCR · "
                          f"{self.stats['published']} published · "
                          f"{self.stats['review']} to review · "
                          f"tracks {open_tracks} · reads buffered {buffered} · "
                          f"low-quality dropped {dropped}")
                    last_report = time.time()
        except KeyboardInterrupt:
            print("\n[net] stopping…")
        finally:
            self.stop.set()
            for s in self.streams:
                s.stop.set()
            for cam_id in list(self.tracks):
                for tid in list(self.tracks[cam_id]):
                    self.finish(cam_id, tid)
            self.pub.drain()
            print(f"[net] published {self.stats['published']}, "
                  f"{self.stats['review']} to review")
        return 0


def run_network(config_path: Path | None = None, only: list[str] | None = None) -> int:
    import json

    cfg_path = config_path or (REPO_ROOT / "infra" / "cameras.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cams = cfg["cameras"]
    if only:
        cams = [c for c in cams if c["id"] in only]
    if not cams:
        print("[net] no cameras selected")
        return 1

    os.environ.setdefault("NETRA_PLATE_REGION", cfg.get("region", "IN"))
    return Network(cams, cfg.get("region", "IN"), cfg.get("loop", True)).run()
