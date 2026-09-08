<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## The Vision Pipeline — Detection, Tracking, OCR

This section owns everything between an RTSP URL and a single JSON event posted to the API. It is written for **one specific machine**: Windows 11, 16 GB RAM, AMD Radeon RX 6500M (4 GB VRAM, RDNA2), no CUDA. Every performance claim assumes **ONNX Runtime 1.19.x with `DmlExecutionProvider` (DirectML)** unless stated otherwise, and every number marked `ESTIMATE` must be replaced by a real reading from `apps/edge/bench.py` before the pitch.

> **Verify:** exact `onnxruntime-directml` version with cp312 wheels. 1.17+ is the first line with Python 3.12 support; pin whatever `pip index versions onnxruntime-directml` actually resolves and record it in `apps/edge/requirements.lock`.

### 1. Process architecture: one process, N cameras

The obvious design — one OS process per camera — is wrong on 4 GB of VRAM. Each ORT+DML process allocates its own device heap; YOLOv8n weights are tiny (~6 MB FP16) but activations at 640×640 plus DirectML's greedy allocator land around **500–800 MB per process**. Four processes ≈ 3 GB, plus ~400 MB for the Windows desktop compositor, and you OOM mid-demo.

**Use a single process with one set of ORT sessions.** Threads are safe here because the two expensive calls both release the GIL: `cv2.VideoCapture.grab/retrieve` (OpenCV releases it around FFmpeg calls) and `InferenceSession.run`. Layout:

```
apps/edge/
  anpr_edge/
    __init__.py
    config.py        # CameraCfg, Thresholds, env parsing
    providers.py     # EP selection + session factory
    capture.py       # LatestFrameReader (1 thread per camera)
    detect.py        # YoloOnnx: letterbox, run, numpy NMS
    tracking.py      # supervision ByteTrack wrapper + TrackSession
    plate_ocr.py     # crop prep, rec model, character voting
    zones.py         # line crossing / OCR polygon
    attributes.py    # HSV colour, COCO class map
    emit.py          # EventSink: batching, retry, disk spool
    overlay.py       # DebugWriter -> annotated MP4
    worker.py        # main loop  (python -m anpr_edge.worker)
  models/
  config/cameras.yaml
  bench.py
```

Threads: `N` decoder threads (one per camera, blocking on the network), **one** pipeline thread that round-robins cameras and owns all ORT sessions, and one sender thread. Inference is serialised, which is correct — DirectML gives you one command queue on this GPU anyway, so parallel `run()` calls only add driver-level contention and jitter.

### 2. RTSP ingest and the stale-frame trap

| Option | Latency control | Effort | Verdict |
|---|---|---|---|
| `cv2.VideoCapture(url, cv2.CAP_FFMPEG)` | FFmpeg keeps an internal packet queue. **`CAP_PROP_BUFFERSIZE` is silently ignored by the FFmpeg backend** (it is honoured by DShow/V4L2 only). Controllable via `OPENCV_FFMPEG_CAPTURE_OPTIONS`. | Lowest | **Recommended for MVP** |
| PyAV | Real packet-level control, `low_delay`, can drop non-reference frames, exposes PTS. | Medium — you hand-manage decoder flush and stream discontinuities | Stretch |
| `ffmpeg` subprocess → `rawvideo bgr24` on stdout | Total control (`-fflags nobuffer -flags low_delay -rtsp_transport tcp -vf fps=12,scale=1280:-2`), and decode/resize/rate-limit run outside Python. Can try `-hwaccel d3d11va`. | Medium — process supervision, no PTS unless you parse stderr | Documented fallback if latency drifts |

**The trap:** naively calling `cap.read()` in a loop that takes 80 ms per iteration on a 25 FPS stream means you consume frame *n* while the encoder has already produced *n+2*; the queue grows without bound and after 60 seconds you are displaying a two-minute-old world. On stage this is fatal — the map lags the video.

**Fix:** a decoder thread that calls `grab()` for *every* frame in the source and `retrieve()` only for the frames you want. `grab()` decodes but skips colour conversion and the numpy copy — the expensive parts — so this drains the queue cheaply and gives you frame sampling for free.

```python
# apps/edge/anpr_edge/capture.py
from __future__ import annotations
import logging, os, threading, time
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("anpr.capture")

# MUST be set before the first cv2.VideoCapture(..., CAP_FFMPEG) in this process.
# Syntax: "key;value|key;value"  (semicolon between k/v, pipe between pairs).
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|reorder_queue_size;0|max_delay;300000",
)
# > Verify: the pipe/semicolon form is correct for OpenCV >= 4.5.4 on Windows.
#   If frames never arrive, drop to just "rtsp_transport;tcp" and re-test.


@dataclass(frozen=True)
class FrameMeta:
    seq: int          # monotonically increasing per camera, never reused
    wall_utc: float   # time.time() at retrieve()
    mono: float       # time.perf_counter(), for latency math
    src_index: int    # index in the source stream (for debug/overlay)


class LatestFrameReader:
    """One decoder thread per camera. Holds exactly the newest sampled frame.

    grab()  -> called for every source frame (cheap: decode, no BGR copy)
    retrieve() -> called every `stride`-th frame only
    """

    def __init__(self, url: str, camera_id: str, target_fps: float = 12.0,
                 source_fps_hint: float = 25.0, reconnect_s: float = 2.0):
        self.url, self.camera_id = url, camera_id
        self.target_fps, self.source_fps_hint = target_fps, source_fps_hint
        self.reconnect_s = reconnect_s
        self._lock = threading.Lock()
        self._slot: tuple[np.ndarray, FrameMeta] | None = None
        self._new = threading.Event()
        self._stop = threading.Event()
        self._seq = 0
        self.stats = {"grabbed": 0, "sampled": 0, "stride_skipped": 0,
                      "overrun_dropped": 0, "reconnects": 0, "decode_errors": 0}

    def start(self) -> "LatestFrameReader":
        self._t = threading.Thread(target=self._run, name=f"cap-{self.camera_id}", daemon=True)
        self._t.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def read(self, timeout: float = 1.0) -> tuple[np.ndarray, FrameMeta] | None:
        """Consume the newest frame. Returns None on timeout (camera down)."""
        if not self._new.wait(timeout):
            return None
        with self._lock:
            out, self._slot = self._slot, None
            self._new.clear()
        return out

    def _run(self) -> None:
        backoff = self.reconnect_s
        while not self._stop.is_set():
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # ignored by FFmpeg backend; harmless
            if not cap.isOpened():
                self.stats["reconnects"] += 1
                log.warning("capture_open_failed", extra={"camera_id": self.camera_id})
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            backoff = self.reconnect_s
            src_fps = cap.get(cv2.CAP_PROP_FPS) or self.source_fps_hint
            stride = max(1, round(src_fps / self.target_fps))
            log.info("capture_open", extra={"camera_id": self.camera_id,
                                            "src_fps": src_fps, "stride": stride})
            i = 0
            while not self._stop.is_set():
                if not cap.grab():
                    self.stats["decode_errors"] += 1
                    break                                   # EOF or socket died -> reconnect
                i += 1
                self.stats["grabbed"] += 1
                if i % stride:
                    self.stats["stride_skipped"] += 1
                    continue
                ok, frame = cap.retrieve()
                if not ok:
                    self.stats["decode_errors"] += 1
                    break
                self.stats["sampled"] += 1
                with self._lock:
                    if self._slot is not None:
                        self.stats["overrun_dropped"] += 1  # pipeline slower than target_fps
                    self._seq += 1
                    self._slot = (frame, FrameMeta(self._seq, time.time(),
                                                   time.perf_counter(), i))
                    self._new.set()
            cap.release()
            self.stats["reconnects"] += 1
```

`overrun_dropped` is your single most important health metric. If it climbs, the pipeline is not keeping up and you must lower `target_fps` or the input resolution — the system degrades gracefully instead of accumulating lag.

**Demo note:** never point the worker at a local `.mp4`. `VideoCapture` on a file decodes as fast as the CPU allows, which destroys all your timing assumptions and makes speed estimation nonsense. Always replay through MediaMTX so the stream is wall-clock paced, exactly like a real camera.

### 3. Frame sampling: 10–15 FPS, not 25–30

At 12 FPS a vehicle at 60 km/h (16.7 m/s) advances **1.4 m between sampled frames** — well under a 4 m car length, so ByteTrack's IoU association never breaks. A vehicle crossing a typical 20–30 m junction FOV is visible for 1.2–2.0 s, giving **14–24 sampled frames**, i.e. 14–24 independent OCR votes. That is far more than the ~8 votes you need for the character-consensus scheme in §8 to saturate.

Full-rate processing doubles GPU cost for frames that are ~97 % correlated with their neighbour, and it does **not** improve OCR: plate accuracy is dominated by the 2–4 frames where the plate is largest and most frontal, not by the count of frames. Sampling also does nothing about motion blur (that is exposure time, not frame rate), which is why we reject blurry crops in §7 instead of trying to fix them.

Set `target_fps` per camera in `cameras.yaml`. Use 10 for wide highway views (small plates, few useful frames anyway), 15 for tight junction/toll views where you want more votes.

### 4. Two-stage detection vs single-stage plate detection

| Design | Plate pixel width @ 25 m, 1080p source | Recall on far/small plates | GPU cost/frame | Verdict |
|---|---|---|---|---|
| **A.** Single YOLOv8n plate detector, 640 input, full frame | 1080p → 640 is a 3× downscale; a 30 cm plate at 25 m ≈ 40 px in the source → **~13 px at 640** | Poor. YOLO's finest head is stride-8; objects under ~10–14 px are at the practical floor. Loses the mid/far field entirely. | 1× | Reject |
| **B.** Single plate detector at 1280 input | ~26 px | Acceptable | ~3.5–4× (attention-free CNN scales ~quadratically with input area) | Too slow on this GPU |
| **C.** **Two-stage:** YOLOv8n COCO vehicles @ 640 → crop from the *full-resolution* frame → plate detector @ 384 on the crop | Vehicle box is 120–350 px tall and trivial to detect at 640. The crop is taken from the original 1080p pixels, so the plate is **40 px real** and, letterboxed into 384, appears at **60–110 px** | Good | 1× + (0.25× × k crops) | **Recommended** |

The whole argument for two-stage is **resolution recovery**: you never downscale the pixels the OCR will eventually see. Stage 1 answers "where should I look at full resolution?", stage 2 answers "where exactly is the plate?".

The cost objection ("k crops per frame") is solved by **gating**, not by a faster model. Stage 2 runs only for tracks that are (a) inside the camera's `ocr_zone` polygon, (b) below `MAX_VOTES` accumulated reads, and (c) above `MIN_BOX_AREA`. Sort the survivors by box area and take the top `MAX_OCR_TRACKS_PER_FRAME = 3`. In practice this means **0 plate-detector calls on most frames** and a batched call with 1–3 crops on the rest. Export with a dynamic batch axis so those 1–3 crops are one `run()`, not three.

Model sourcing:
- **Vehicle detector:** stock `yolov8n.pt` COCO. Zero training. Filter to classes `{2: car, 3: motorcycle, 5: bus, 7: truck}`. Use `yolov8s` only if you have benchmark headroom; on this GPU you will not.
- **Plate detector:** fine-tune `yolov8n` (1 class) on Colab/Kaggle T4. Roboflow has several 10–25k-image ANPR datasets with Indian subsets. `imgsz=640, epochs=60, batch=32` ≈ 45–75 min on a T4. Train on full images but **also** augment with tight vehicle crops so the domain matches inference.
- **Not COCO-shaped:** Indian traffic has auto-rickshaws, which COCO labels inconsistently as car/truck/motorcycle. MVP maps COCO → `{CAR, TWO_WHEELER, BUS, TRUCK}`. Stretch: fine-tune the vehicle detector on IDD (India Driving Dataset) and add `AUTO_RICKSHAW`.

**Licence note that matters for judging:** Ultralytics is AGPL-3.0. We use it only as an *offline export tool*; the runtime imports only `onnxruntime`, `opencv-python`, `numpy`, and `supervision` (MIT). Keep `ultralytics` in `requirements-dev.txt`, never in the runtime requirements.

### 5. ONNX Runtime + DirectML on Windows/AMD

```powershell
# apps/edge — runtime
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install onnxruntime-directml numpy opencv-python supervision pyyaml httpx python-dotenv
# OCR: pure-ORT PP-OCR (no paddlepaddle install pain on py3.12/Windows)
pip install rapidocr-onnxruntime
```

```powershell
# apps/edge — dev/export only, ideally a SECOND venv
pip install ultralytics onnx onnxslim onnxconverter-common onnxruntime
```

**Do not install `onnxruntime`, `onnxruntime-gpu`, `onnxruntime-directml`, or `onnxruntime-openvino` in the same environment.** They all publish the same `onnxruntime` module and the last one installed silently wins. This costs teams hours.

Export:

```bash
# Stage 1: vehicles (COCO, 80 classes -> output (1, 84, 8400))
yolo export model=yolov8n.pt format=onnx opset=17 imgsz=640 simplify=True dynamic=False nms=False

# Stage 2: plates (1 class -> output (1, 5, N))
yolo export model=runs/detect/plate/weights/best.pt format=onnx opset=17 imgsz=384 simplify=True dynamic=False nms=False
```

Two deliberate choices:

1. **`nms=False`.** ORT's `NonMaxSuppression` op has no DirectML kernel; including it forces a mid-graph fallback to CPU and a device sync on every frame. Do NMS yourself in numpy / `cv2.dnn.NMSBoxes` — it costs under 1 ms for a few hundred boxes.
2. **`dynamic=False`, then widen *only* the batch axis by hand.** Ultralytics' `dynamic=True` makes height and width dynamic too, and DirectML re-compiles kernels whenever the spatial shape changes — a large, invisible stall. Fixed spatial dims + dynamic batch gives you crop batching without the penalty.

```python
# apps/edge/tools/fix_dims.py  — run once per exported model
import sys, onnx
m = onnx.load(sys.argv[1])
for t in list(m.graph.input) + list(m.graph.output):
    t.type.tensor_type.shape.dim[0].Clear()
    t.type.tensor_type.shape.dim[0].dim_param = "batch"   # only axis 0
onnx.checker.check_model(m)
onnx.save(m, sys.argv[2])
```

FP16 (recommended for DML — roughly halves bandwidth on a 64-bit-bus GPU and RDNA2 has native FP16 throughput):

```python
# apps/edge/tools/to_fp16.py
import sys, onnx
from onnxconverter_common import float16
m = onnx.load(sys.argv[1])
# keep_io_types=True -> you still feed float32 NCHW; the cast happens in-graph.
onnx.save(float16.convert_float_to_float16(m, keep_io_types=True), sys.argv[2])
```
> **Verify:** `yolo export half=True` requires a CUDA device, which you do not have. Use the script above instead. Compare FP32 vs FP16 mAP on 200 held-out frames before trusting it; YOLOv8 FP16 loss is normally < 0.3 mAP but confirm.

INT8: use `onnxruntime.quantization.quantize_static` with a QDQ, per-channel config and a 300-image calibration reader — **for the CPU fallback only**. `quantize_dynamic` is near-useless for CNNs (it targets MatMul/GEMM, not Conv), and DirectML's INT8 QDQ coverage is partial, so on the AMD GPU you frequently end up slower than FP16 because of inserted Q/DQ nodes. Ship FP16 on DML, INT8 on CPU.

OpenVINO: needs `onnxruntime-openvino` (conflicting wheel) and its GPU plugin targets **Intel** silicon only. If the laptop pairs the RX 6500M with an Intel Iris Xe iGPU, a *second* venv running one camera on OpenVINO-GPU is a genuine free device. If the CPU is a Ryzen with a Radeon iGPU, OpenVINO buys you nothing over the CPU EP. MVP fallback is plain `CPUExecutionProvider`.

```python
# apps/edge/anpr_edge/providers.py
from __future__ import annotations
import logging, os
import onnxruntime as ort

log = logging.getLogger("anpr.providers")
_PREFERENCE = ("DmlExecutionProvider", "OpenVINOExecutionProvider", "CPUExecutionProvider")


def select_providers(force: str | None = None) -> list[tuple[str, dict]]:
    available = ort.get_available_providers()
    force = force or os.getenv("ANPR_EP") or None
    if force:
        if force not in available:
            raise RuntimeError(f"ANPR_EP={force!r} unavailable; ORT offers {available}")
        order = [force]
    else:
        order = [p for p in _PREFERENCE if p in available] or ["CPUExecutionProvider"]

    providers: list[tuple[str, dict]] = []
    for p in order:
        if p == "DmlExecutionProvider":
            providers.append((p, {"device_id": int(os.getenv("ANPR_DML_DEVICE", "0"))}))
        elif p == "OpenVINOExecutionProvider":
            providers.append((p, {"device_type": "GPU_FP16"}))
        else:
            providers.append((p, {}))

    log.info("ep_selected", extra={"ort_version": ort.__version__,
                                   "available": available,
                                   "chosen": [p for p, _ in providers],
                                   "forced": bool(force)})
    return providers


def make_session(onnx_path: str, providers: list[tuple[str, dict]]) -> ort.InferenceSession:
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 3
    if providers[0][0] == "DmlExecutionProvider":
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.enable_mem_pattern = False          # required: DML does not support mem pattern
    sess = ort.InferenceSession(onnx_path, sess_options=so, providers=providers)
    log.info("session_ready", extra={"model": os.path.basename(onnx_path),
                                     "providers": sess.get_providers(),
                                     "inputs": [(i.name, i.shape) for i in sess.get_inputs()]})
    return sess
```

`sess.get_providers()` is the ground truth — log it at startup and put it in the debug-video HUD. If it prints `['CPUExecutionProvider']` on demo day you want to know in the first second, not from the frame rate.

### 6. Throughput budget on an RX 6500M (all values `ESTIMATE`)

Assumptions: 1280×720 replay (not 1080p), FP16 ONNX, DirectML EP, YOLOv8n both stages, sampled at 12 FPS, warm sessions (first `run()` after session creation includes kernel compilation and can take 1–3 s — always do 5 warmup runs at startup).

| Stage | Shape | ms `ESTIMATE` | Notes |
|---|---|---|---|
| H.264 decode + BGR (CPU, per sampled frame) | 720p | 4–8 | `grab()`-only frames cost ~2–4 |
| Letterbox + blob (numpy) | 640×640 | 1.5–3 | use `cv2.dnn.blobFromImage`, avoid python loops |
| YOLOv8n vehicles | 1×3×640×640 | **12–20** | YOLOv8s would be 28–45; do not use it |
| Numpy NMS + decode | 8400 boxes | 0.5–1.5 | vectorised, pre-filter by conf first |
| ByteTrack update | ~10 dets | < 1 | pure CPU, Kalman + LAP |
| HSV colour histogram | 1 crop | 0.3 | once per track, not per frame |
| YOLOv8n plates (gated) | 3×3×384×384 | **8–16** | batched; 0 ms on ungated frames |
| Crop prep (warp, CLAHE, resize) | 1–3 crops | 1–3 | CPU |
| PP-OCRv4 mobile rec (ORT) | 3×3×48×320 | **10–25** | CPU EP is fine here; DML has per-call overhead that eats the win on a model this small |
| Debug overlay + MP4 encode | 720p | 4–8 | debug builds only |

- Frame **without** plate work: **20–34 ms** → 30–50 FPS headroom.
- Frame **with** a 3-crop plate+OCR burst: **40–70 ms**.
- Gating means roughly 20–30 % of frames are "with". Weighted mean ≈ **28–45 ms/frame**.

Budget at 12 FPS is 83 ms per camera-frame, but all cameras share one pipeline thread, so the real constraint is **Σ(cameras × fps × ms_per_frame) < 1000 ms of wall clock per second**:

| Cameras @ 10 FPS, 720p | Load `ESTIMATE` | Verdict |
|---|---|---|
| 2 | 0.56–0.90 s/s | Comfortable |
| 3 | 0.84–1.35 s/s | Works with gating tuned; `overrun_dropped` starts appearing |
| 4 | 1.12–1.80 s/s | Only at 8 FPS, `MAX_OCR_TRACKS_PER_FRAME=2`, 960×540 |
| 6+ | — | Not on this machine. Pre-record the extra feeds. |

**Demo plan: 4 simulated cameras at 8–10 FPS, 720p.** Build `apps/edge/bench.py` in hour 3, not hour 30 — it replays 500 frames from a file and prints the p50/p95 table above for the actual machine. Every number in the pitch deck comes from that script.

VRAM: two FP16 sessions ≈ **600–900 MB** resident in one process. Fine. Two processes would not be.

### 7. Tracking: ByteTrack

| | ByteTrack | DeepSORT |
|---|---|---|
| Appearance model | none | ReID CNN (OSNet ≈ 90–150 MB VRAM, +4–9 ms/frame) |
| Association | IoU + Kalman, **two-pass**: high-confidence boxes first, then low-confidence leftovers against unmatched tracks | IoU + cosine appearance distance |
| Occlusion / re-entry | weaker | stronger |
| Cost on this GPU | ~0 | a third model competing for 4 GB |

**Use ByteTrack.** Our cross-camera identity is the *plate string*, not appearance — a ReID embedding buys us nothing the OCR does not already give us, and it costs VRAM we do not have. ByteTrack's second association pass is specifically valuable for Indian traffic: partially occluded two-wheelers and vehicles behind buses produce low-confidence boxes that DeepSORT's single-threshold pass throws away.

Use `supervision.ByteTrack` (MIT) rather than copying `ultralytics/trackers/byte_tracker.py` (AGPL).

```python
import supervision as sv

tracker = sv.ByteTrack(
    track_activation_threshold=0.50,   # a box below this can still be *matched*, not *started*
    lost_track_buffer=30,              # == max_age, in SAMPLED frames -> 30/12 = 2.5 s
    minimum_matching_threshold=0.80,   # IoU-distance gate
    frame_rate=12,                     # MUST equal target_fps, NOT source fps
    minimum_consecutive_frames=2,      # suppress single-frame false positives
)
```
> **Verify:** `sv.ByteTrack` kwarg names changed around supervision 0.20 (`track_thresh` → `track_activation_threshold`, `track_buffer` → `lost_track_buffer`). Pin the version and check the signature once.

`frame_rate` is the classic bug. The Kalman filter's velocity is in units of *per processed frame* and `lost_track_buffer` is scaled by it internally in most ports; feeding 30 while actually processing 12 makes tracks die too early and predicted positions drift ahead of reality.

**Thresholds, and why:**
- `lost_track_buffer=30` (2.5 s at 12 FPS) survives a vehicle passing behind a pole or a bus but expires before a *different* vehicle can plausibly occupy the same box.
- `minimum_matching_threshold=0.80` (IoU distance, so IoU ≥ 0.2 to match). Loose enough for 1.4 m/frame displacement, tight enough that adjacent lanes do not swap.
- `MIN_BOX_AREA = 900 px²` (30×30 at 720p). Below that a vehicle is too far to yield a readable plate; tracking it wastes association budget.

**Why `track_id` is the primary key of the accuracy strategy.** Per-character OCR accuracy on real Indian plates is roughly 92–96 % for a good frame. A 10-character plate read from a *single* frame is therefore exact only ~43–66 % of the time. Aggregating **per-character confidence-weighted votes across 8–20 frames of the same physical vehicle** pushes exact-plate accuracy above 95 %. That aggregation is only valid if you know the frames belong to one vehicle — which is exactly what `track_id` asserts. Everything downstream (one `track_sessions` row per camera pass, cloned-plate detection, point-to-point speed) is built on that assertion. If tracking IDs fragment, you get duplicate sessions, phantom "clones", and impossible speeds. Tune tracking before you tune OCR.

Track IDs are process-local integers that reset on restart, so the globally unique key is:

```
track_id = f"{camera_id}:{session_epoch}:{local_id}"     # e.g. "CAM_MH12_NH48_01:1725539400:117"
```

### 8. Plate crop preparation and OCR

```python
# apps/edge/anpr_edge/plate_ocr.py
from __future__ import annotations
import re
from collections import defaultdict

import cv2
import numpy as np

TARGET_H = 48            # PP-OCR rec input height is 3x48xW
MIN_PLATE_H_PX = 12      # below this, discard the read entirely
LOW_RES_H_PX = 24        # below this, upscale before OCR
MIN_SHARPNESS = 60.0     # variance of Laplacian on the 48px-normalised crop
LOW_CONTRAST_STD = 45.0  # apply CLAHE only under this


def _deskew_quad(gray: np.ndarray) -> np.ndarray | None:
    """Recover a rotated quad from an axis-aligned plate box via contours."""
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 25, 9)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((3, 15), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    rect = cv2.minAreaRect(max(cnts, key=cv2.contourArea))
    (w, h) = rect[1]
    if min(w, h) < 4:
        return None
    ar = max(w, h) / max(min(w, h), 1e-3)
    if not (1.6 <= ar <= 6.5):          # single-row IN plate ~4.7:1, two-row bike ~2:1
        return None
    if abs(rect[2]) > 25 and abs(abs(rect[2]) - 90) > 25:
        return None                      # implausible skew -> probably not the plate
    return cv2.boxPoints(rect).astype(np.float32)


def _order_quad(p: np.ndarray) -> np.ndarray:
    s, d = p.sum(1), np.diff(p, axis=1).ravel()
    return np.array([p[np.argmin(s)], p[np.argmin(d)],
                     p[np.argmax(s)], p[np.argmax(d)]], dtype=np.float32)


def prepare_plate(frame: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray | None:
    """frame MUST be the ORIGINAL full-resolution frame, never a resized copy."""
    x1, y1, x2, y2 = box
    pw, ph = x2 - x1, y2 - y1
    if ph < MIN_PLATE_H_PX:
        return None                                     # too small to be worth a vote
    px, py = int(pw * 0.07), int(ph * 0.14)             # pad: detectors clip characters
    x1, y1 = max(0, x1 - px), max(0, y1 - py)
    x2, y2 = min(frame.shape[1], x2 + px), min(frame.shape[0], y2 + py)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    quad = _deskew_quad(gray)
    if quad is not None:
        q = _order_quad(quad)
        w = int(max(np.linalg.norm(q[0] - q[1]), np.linalg.norm(q[3] - q[2])))
        h = int(max(np.linalg.norm(q[0] - q[3]), np.linalg.norm(q[1] - q[2])))
        if w > 8 and h > 6:
            dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
            crop = cv2.warpPerspective(crop, cv2.getPerspectiveTransform(q, dst), (w, h))

    crop = _strip_ind_band(crop)

    # upscale to the rec model's native height; INTER_CUBIC for upscale, AREA for downscale
    h0 = crop.shape[0]
    interp = cv2.INTER_CUBIC if h0 < TARGET_H else cv2.INTER_AREA
    scale = TARGET_H / h0
    crop = cv2.resize(crop, (max(16, int(crop.shape[1] * scale)), TARGET_H), interpolation=interp)

    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if cv2.Laplacian(g, cv2.CV_64F).var() < MIN_SHARPNESS:
        return None                                     # motion-blurred: skip, another frame will do
    if g.std() < LOW_CONTRAST_STD:
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
        crop = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return crop


def _strip_ind_band(crop: np.ndarray) -> np.ndarray:
    """Remove the blue 'IND' band on the left; it OCRs as leading garbage characters."""
    h, w = crop.shape[:2]
    band = crop[:, : max(1, int(w * 0.16))]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    blue = ((hsv[..., 0] > 100) & (hsv[..., 0] < 130) & (hsv[..., 1] > 80)).mean()
    return crop[:, int(w * 0.14):] if blue > 0.35 else crop
```

> **Over-processing warning.** PP-OCR's recognition model is trained on *natural* images. Otsu/adaptive binarisation, unsharp masking, and `fastNlMeansDenoising` all measurably **reduce** its accuracy — binarisation destroys anti-aliased stroke edges the model relies on, denoise smears thin strokes, and it costs 20+ ms. The only transforms that help are geometric (deskew, upscale) and mild contrast (conditional CLAHE). Do not binarise. Do not "enhance". If you find yourself adding a sharpening kernel, you are compensating for a bad crop — fix stage 2 instead.

**Two-row plates (motorcycles, ~30 % of Indian traffic):** if the warped crop's aspect ratio < 2.6, split it. Take the horizontal projection profile of the inverted binary image, find the minimum in the middle 40 % of rows, cut there, OCR both halves, concatenate top+bottom.

**Recognition config.** Use `rapidocr-onnxruntime` (PP-OCRv4 exported to ONNX — same weights, no `paddlepaddle` wheel problem on Python 3.12/Windows) with **detection disabled**: our crop is already tight, and running text detection on it wastes 15–30 ms and sometimes splits the plate into two lines.

```python
from rapidocr_onnxruntime import RapidOCR

_ocr = RapidOCR(det_use_cuda=False, rec_use_cuda=False,
                rec_img_shape=[3, 48, 320], rec_batch_num=8)

def recognise_batch(crops: list[np.ndarray]) -> list[tuple[str, list[float]]]:
    # use_det=False, use_cls=False, use_rec=True -> recognition only, batched internally
    out = []
    for c in crops:
        res, _ = _ocr(c, use_det=False, use_cls=False, use_rec=True)
        if not res:
            out.append(("", []))
            continue
        text, score = res[0][0], float(res[0][1])
        text = ALLOWED_RE.sub("", text.upper().replace(" ", "").replace("-", ""))
        out.append((text, [score] * len(text)))   # per-char scores need the ONNX path (stretch)
    return out
```
> **Verify:** `RapidOCR.__call__` kwargs (`use_det/use_cls/use_rec`) and the batch API changed between 1.3 and 1.4. If you prefer `paddleocr==2.7.x`, the equivalent is `PaddleOCR(use_angle_cls=False, lang='en', use_gpu=False, rec_batch_num=8)` called as `ocr.ocr(list_of_crops, det=False, cls=False)`.

**Character whitelist — the trap.** You cannot just point `rec_char_dict_path` at a 36-character A-Z0-9 file. The pretrained head's output layer is indexed against its original ~96-symbol dictionary; swapping the file misaligns every index and produces garbage. Two correct options:
- **MVP:** keep the original dictionary and filter/repair in post-processing (below).
- **Stretch:** load the rec ONNX yourself, mask the logits to the 36 allowed indices before argmax, then CTC-decode. This also gives you real per-character confidences, which the voting scheme wants.

**Angle classifier:** leave `cls` **off**. It only corrects 180° flips, which the deskew step already makes rare, and it costs ~2 ms/crop. Instead: if mean confidence < 0.45, retry once on `cv2.rotate(crop, ROTATE_180)` and keep the better result.

**Consensus voting and Indian plate grammar:**

```python
ALLOWED_RE = re.compile(r"[^A-Z0-9]")
RE_STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")   # MH12DE1433, DL8CAF5030
RE_BH       = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")           # 22BH1234AA

L2D = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2",
       "A": "4", "S": "5", "G": "6", "T": "7", "B": "8"}
D2L = {"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "8": "B"}


class PlateVoter:
    """Per-track, per-position confidence-weighted character vote."""

    def __init__(self) -> None:
        self.by_len: dict[int, list[defaultdict]] = {}
        self.votes = 0
        self.best_single = ("", 0.0)

    def add(self, text: str, char_conf: list[float], sharpness: float, plate_h: int) -> None:
        if not (8 <= len(text) <= 11):
            return
        w = min(sharpness / 200.0, 1.0) * min(plate_h / 40.0, 1.0)   # quality weight
        slots = self.by_len.setdefault(len(text), [defaultdict(float) for _ in text])
        for i, ch in enumerate(text):
            slots[i][ch] += (char_conf[i] if i < len(char_conf) else 0.5) * w
        self.votes += 1
        mean = sum(char_conf) / max(len(char_conf), 1)
        if mean > self.best_single[1]:
            self.best_single = (text, mean)

    def consensus(self) -> tuple[str, float, list[float]]:
        if not self.by_len:
            return "", 0.0, []
        length = max(self.by_len, key=lambda k: sum(max(s.values(), default=0) for s in self.by_len[k]))
        slots = self.by_len[length]
        chars, confs = [], []
        for s in slots:
            tot = sum(s.values()) or 1e-6
            ch, sc = max(s.items(), key=lambda kv: kv[1])
            chars.append(ch)
            confs.append(sc / tot)
        text = repair_grammar("".join(chars))
        return text, (sum(confs) / len(confs)) * min(self.votes / 6.0, 1.0), confs


def repair_grammar(t: str) -> str:
    """Positional confusion repair using the RTO layout: AA NN X(X)(X) NNNN."""
    if RE_STANDARD.match(t) or RE_BH.match(t):
        return t
    out = list(t)
    for i in (0, 1):                                    # state code: always letters
        if i < len(out) and out[i].isdigit():
            out[i] = D2L.get(out[i], out[i])
    for i in range(len(out) - 4, len(out)):             # last four: always digits
        if 0 <= i < len(out) and out[i].isalpha():
            out[i] = L2D.get(out[i], out[i])
    return "".join(out)
```

`confs` is emitted as `char_confidences[]` so the UI can underline the low-confidence characters in a plate — a small feature that makes the demo look serious and honest.

### 9. Zones, line crossing, and one `track_session` per pass

Without this, a 15-frame track produces 15 database rows and the whole analytics layer breaks. Each camera declares, in normalised (0–1) coordinates so resolution changes do not invalidate the config:

```yaml
# apps/edge/config/cameras.yaml
cameras:
  - camera_id: CAM_MH12_NH48_01
    name: "Baner Road / NH-48 Junction"
    rtsp_url: "rtsp://127.0.0.1:8554/cam01"
    lat: 18.5591
    lon: 73.7868
    heading_deg: 275          # direction the camera faces; used for direction labelling
    target_fps: 10
    count_line: [[0.05, 0.62], [0.95, 0.58]]           # normalised, A -> B
    ocr_zone:   [[0.10, 0.40], [0.92, 0.38], [0.98, 0.92], [0.04, 0.95]]
    enabled: true
```

```python
# apps/edge/anpr_edge/zones.py
def side(a, b, p) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

class Zones:
    MIN_MOVE_PX = 8          # jitter guard: a parked car must not oscillate across the line
    MIN_FRAMES  = 3          # a track must exist for 3 frames before it can cross

    def crossed(self, s) -> bool:
        """Anchor = bottom-centre of the vehicle box (the tyre contact point)."""
        p = s.anchor
        cur = side(self.a_px, self.b_px, p)
        prev, self._prev[s.tid] = self._prev.get(s.tid), cur
        if prev is None or s.frames < self.MIN_FRAMES:
            return False
        if abs(p[0] - s.prev_anchor[0]) + abs(p[1] - s.prev_anchor[1]) < self.MIN_MOVE_PX:
            return False
        if (prev < 0) != (cur < 0):
            s.direction = "A_TO_B" if prev < 0 else "B_TO_A"
            return True
        return False
```

Emission rules — **each track emits at most one event**:
1. **`trigger="line"`** — the anchor crosses `count_line`. This is the normal path and it stamps `crossed_at_utc`, which is the timestamp every speed and trajectory calculation uses.
2. **`trigger="timeout"`** — the track dies (missing > `lost_track_buffer`) without crossing, but lived ≥ `MIN_TRACK_FRAMES = 5` and has ≥ 1 plate vote. Covers vehicles that stop, turn off, or are occluded at the line. `crossed_at_utc` falls back to `last_seen_utc` and `direction = "UNKNOWN"`.
3. Never both — `TrackSession.emitted` is a one-way latch.

`event_id = uuid5(NAMESPACE_URL, track_id)` makes the POST idempotent: the retry spool can replay safely and the API upserts on `event_id`.

### 10. Vehicle attributes

Colour from an HSV histogram is sufficient — it is a *search filter*, never evidence, and the UI must label it "estimated".

```python
# apps/edge/anpr_edge/attributes.py
HUE_BINS = [(0, 10, "RED"), (10, 25, "ORANGE"), (25, 35, "YELLOW"), (35, 85, "GREEN"),
            (85, 100, "CYAN"), (100, 130, "BLUE"), (130, 155, "VIOLET"),
            (155, 170, "PINK"), (170, 180, "RED")]

def dominant_colour(frame, box) -> str:
    x1, y1, x2, y2 = box
    h, w = y2 - y1, x2 - x1
    # lower-middle band = body panels; avoids sky/road background and glazing
    patch = frame[y1 + int(h * 0.45): y1 + int(h * 0.80), x1 + int(w * 0.20): x1 + int(w * 0.80)]
    if patch.size == 0:
        return "UNKNOWN"
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    hsv = hsv[(hsv[:, 2] > 40) & (hsv[:, 2] < 245)]      # drop shadow and specular highlights
    if len(hsv) < 50:
        return "UNKNOWN"
    s_med, v_med = np.median(hsv[:, 1]), np.median(hsv[:, 2])
    if s_med < 45:                                        # achromatic: the majority of IN fleet
        return "WHITE" if v_med > 190 else "GREY" if v_med > 70 else "BLACK"
    hue = int(np.bincount(hsv[hsv[:, 1] > 60, 0], minlength=180).argmax())
    return next((n for lo, hi, n in HUE_BINS if lo <= hue < hi), "UNKNOWN")

COCO_VEHICLE = {2: "CAR", 3: "TWO_WHEELER", 5: "BUS", 7: "TRUCK"}
```

Costs ~0.3 ms, run **once per track** on the largest observed box. Expect ~75–85 % agreement with a human label; WHITE vs GREY/SILVER is the dominant error and we deliberately merge silver into GREY rather than pretend to distinguish it.

### 11. The main loop

```python
# apps/edge/anpr_edge/worker.py
from __future__ import annotations
import logging, signal, time, uuid
import numpy as np
import supervision as sv

from .attributes import COCO_VEHICLE, dominant_colour
from .capture import LatestFrameReader
from .config import load_cameras, T          # T = threshold constants
from .detect import YoloOnnx
from .emit import EventSink
from .metrics import StageTimers
from .overlay import DebugWriter
from .plate_ocr import PlateVoter, prepare_plate, recognise_batch
from .providers import make_session, select_providers
from .tracking import TrackSession, iter_tracked
from .zones import Zones

log = logging.getLogger("anpr.worker")
SHUTDOWN = __import__("threading").Event()
NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")   # fixed namespace, do not change


def run_camera(cam, veh: YoloOnnx, plate: YoloOnnx, sink: EventSink, dbg: DebugWriter | None):
    reader = LatestFrameReader(cam.rtsp_url, cam.camera_id, target_fps=cam.target_fps).start()
    tracker = sv.ByteTrack(track_activation_threshold=T.TRACK_THRESH,
                           lost_track_buffer=T.TRACK_BUFFER,
                           minimum_matching_threshold=T.MATCH_THRESH,
                           frame_rate=int(cam.target_fps),
                           minimum_consecutive_frames=2)
    zones = Zones.from_cfg(cam)
    timers = StageTimers(cam.camera_id)
    sessions: dict[int, TrackSession] = {}
    session_epoch = int(time.time())
    frame_idx = 0

    while not SHUTDOWN.is_set():
        got = reader.read(timeout=1.0)
        if got is None:
            log.warning("no_frame", extra={"camera_id": cam.camera_id, **reader.stats})
            continue
        frame, meta = got
        frame_idx += 1
        zones.bind(frame.shape[1], frame.shape[0])       # normalised -> pixel, once per size

        with timers("detect_vehicle"):
            dets = veh.infer(frame, conf=T.VEHICLE_CONF, iou=T.NMS_IOU,
                             classes=tuple(COCO_VEHICLE))
        with timers("track"):
            tracked = tracker.update_with_detections(dets)

        live: set[int] = set()
        for box, cls_id, conf, tid in iter_tracked(tracked):
            live.add(tid)
            s = sessions.get(tid)
            if s is None:
                s = sessions[tid] = TrackSession(tid=tid, camera_id=cam.camera_id,
                                                 first_seen_utc=meta.wall_utc,
                                                 voter=PlateVoter())
            s.update(box, cls_id, conf, meta.wall_utc, frame_idx)
            if s.colour is None and s.box_area >= T.COLOUR_MIN_AREA:
                with timers("colour"):
                    s.colour = dominant_colour(frame, box)

        # ---------- gated plate stage ----------
        cands = [sessions[t] for t in live
                 if zones.in_ocr_zone(sessions[t].anchor)
                 and sessions[t].voter.votes < T.MAX_VOTES
                 and sessions[t].box_area >= T.MIN_BOX_AREA]
        cands.sort(key=lambda s: -s.box_area)
        cands = cands[: T.MAX_OCR_TRACKS_PER_FRAME]

        if cands:
            with timers("detect_plate"):
                crops, offsets = plate.build_crops(frame, [c.box for c in cands], pad=0.04)
                pboxes = plate.infer_batch(crops, conf=T.PLATE_CONF, iou=T.NMS_IOU)
            owners, imgs = [], []
            with timers("prep_ocr"):
                for s, pb, off in zip(cands, pboxes, offsets):
                    if pb is None:
                        continue
                    img = prepare_plate(frame, plate.map_to_frame(pb, off))
                    if img is None:
                        continue
                    owners.append((s, plate.map_to_frame(pb, off)))
                    imgs.append(img)
            if imgs:
                with timers("ocr"):
                    results = recognise_batch(imgs)
                for (s, pbox), img, (text, cconf) in zip(owners, imgs, results):
                    if not text:
                        continue
                    sharp = float(np.var(np.gradient(img.mean(axis=2))[0])) * 100.0
                    s.voter.add(text, cconf, sharp, pbox[3] - pbox[1])
                    s.plate_box = pbox
                    s.best_crop = img if s.best_crop is None or img.shape[1] > s.best_crop.shape[1] else s.best_crop

        # ---------- emission ----------
        for tid in live:
            s = sessions[tid]
            if not s.emitted and zones.crossed(s):
                s.crossed_at_utc = meta.wall_utc
                sink.put(build_event(cam, s, session_epoch, "line"), s.best_crop)
                s.emitted = True

        for tid in list(sessions):
            if tid in live:
                continue
            s = sessions[tid]
            s.missing += 1
            if s.missing > T.TRACK_BUFFER:
                if not s.emitted and s.frames >= T.MIN_TRACK_FRAMES and s.voter.votes >= 1:
                    s.crossed_at_utc = s.last_seen_utc
                    sink.put(build_event(cam, s, session_epoch, "timeout"), s.best_crop)
                del sessions[tid]

        if dbg is not None:
            with timers("overlay"):
                dbg.write(frame, tracked, sessions, zones, timers, meta)
        if frame_idx % 100 == 0:
            timers.flush(extra=reader.stats)


def build_event(cam, s, session_epoch: int, trigger: str) -> dict:
    track_id = f"{cam.camera_id}:{session_epoch}:{s.tid}"
    text, conf, char_conf = s.voter.consensus()
    return {
        "schema_version": 1,
        "event_id": str(uuid.uuid5(NS, track_id)),
        "track_id": track_id,
        "camera_id": cam.camera_id,
        "trigger": trigger,                       # "line" | "timeout"
        "direction": s.direction,                 # "A_TO_B" | "B_TO_A" | "UNKNOWN"
        "first_seen_utc": s.first_seen_utc,       # epoch seconds, float
        "last_seen_utc": s.last_seen_utc,
        "crossed_at_utc": s.crossed_at_utc,
        "frames_tracked": s.frames,
        "vehicle_class": COCO_VEHICLE.get(s.cls_id, "OTHER"),
        "vehicle_class_conf": round(s.cls_conf, 3),
        "vehicle_color": s.colour or "UNKNOWN",
        "plate_text": text or None,
        "plate_confidence": round(conf, 3),
        "char_confidences": [round(c, 3) for c in char_conf],
        "plate_votes": s.voter.votes,
        "plate_best_single": s.voter.best_single[0] or None,
        "plate_valid_format": bool(text) and s.voter.grammar_ok(text),
        "bbox_vehicle": [int(v) for v in s.box],
        "bbox_plate": [int(v) for v in s.plate_box] if s.plate_box else None,
        "frame_w": s.frame_w, "frame_h": s.frame_h,
        "evidence_plate_key": None,               # filled by EventSink after upload
        "worker_version": "edge-1.0.0",
        "provider": s.provider,
    }
```

`EventSink` uploads `best_crop` as JPEG (quality 92) to the Supabase `evidence` bucket at
`plates/{camera_id}/{YYYY}/{MM}/{DD}/{event_id}.jpg`, sets `evidence_plate_key`, then batches up to 16 events every 250 ms to `POST /api/v1/ingest/sightings`. On failure it appends to `logs/spool/{camera_id}.jsonl` and a background task replays the spool — the demo must survive the API restarting.

### 12. Logging, metrics, debug video

**Structured logging.** Standard `logging` with a JSON formatter; every record carries `camera_id`, and where relevant `track_id`, `stage`, `ms`. INFO = one line per emitted event plus a metrics line every 100 frames; DEBUG = per-frame. Never log at DEBUG during the demo — the file I/O alone costs frames.

```python
# apps/edge/anpr_edge/metrics.py
import collections, logging, statistics, time
log = logging.getLogger("anpr.metrics")

class StageTimers:
    def __init__(self, camera_id, window=300):
        self.camera_id = camera_id
        self.d = collections.defaultdict(lambda: collections.deque(maxlen=window))
        self._t0 = None; self._name = None

    def __call__(self, name): self._name = name; return self
    def __enter__(self): self._t0 = time.perf_counter(); return self
    def __exit__(self, *a): self.d[self._name].append((time.perf_counter() - self._t0) * 1e3)

    def p(self, name, q):
        v = sorted(self.d[name])
        return round(v[min(len(v) - 1, int(len(v) * q))], 2) if v else 0.0

    def flush(self, extra=None):
        log.info("stage_timing", extra={"camera_id": self.camera_id,
            "p50": {k: self.p(k, .50) for k in self.d},
            "p95": {k: self.p(k, .95) for k in self.d},
            "capture": extra or {}})
```

Also POST a heartbeat every 10 s to `POST /api/v1/ingest/heartbeat` with `{camera_id, worker_version, provider, fps_actual, overrun_dropped, p95_ms, uptime_s}` — that is what the frontend "System Health" panel reads, and it is what proves the pipeline is alive when a replay stream has no traffic in it.

**Debug overlay — you need this video for the pitch.** `--debug-video out/CAM_XX.mp4` writes an annotated MP4:

- vehicle boxes, 1 px, near-black `#111111`, label `#<track_id> CAR/WHITE`
- plate box in the single accent colour `#1A4FD6`, with the *current consensus* plate and vote count (`MH12DE1433 · 7`) so the audience sees the vote converge in real time
- the `count_line` drawn hairline grey, flashing accent for 6 frames on each crossing
- the `ocr_zone` polygon as a 1 px dashed outline
- a top-left HUD: `EP=DmlExecutionProvider · 11.4 fps · det 16.1/24.8ms · ocr 18.2/31.0ms · dropped 3`

Match the product's Swiss/minimal palette — white HUD panel, hairline borders, uppercase micro-labels, `cv2.FONT_HERSHEY_SIMPLEX` at 0.45 — so the demo video looks like the same system as the dashboard, not a debug tool.

```python
w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), cam.target_fps, (W, H))
```
`mp4v` is what OpenCV can write without extra codecs, but browsers and PowerPoint often refuse it. Always re-encode before the deck:

```bash
ffmpeg -i out/CAM_MH12_NH48_01.mp4 -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart out/demo_cam01.mp4
```

Overlay costs 4–8 ms/frame plus encode, so it is a `--debug-video` flag, off by default, and the recording run is separate from the live-demo run.

---

## Appendix — Interface Contracts Declared by This Section

- `module: apps/edge/anpr_edge/{config,providers,capture,detect,tracking,plate_ocr,zones,attributes,emit,metrics,overlay,worker}.py`
- `entrypoint: python -m anpr_edge.worker --config apps/edge/config/cameras.yaml [--debug-video DIR] [--cameras CAM_A,CAM_B]`
- `bench harness: apps/edge/bench.py (prints p50/p95 per stage for the real machine)`
- `export tools: apps/edge/tools/fix_dims.py, apps/edge/tools/to_fp16.py`
- `model file: apps/edge/models/yolov8n_vehicle_fp16.onnx (input images: float32 [batch,3,640,640] NCHW RGB 0-1, output0: [batch,84,8400])`
- `model file: apps/edge/models/yolov8n_plate_fp16.onnx (input images: float32 [batch,3,384,384], output0: [batch,5,N], 1 class)`
- `model dir: apps/edge/models/ppocr_rec_en/ (PP-OCRv4 mobile rec, rec_img_shape [3,48,320])`
- `charset file: apps/edge/models/plate_charset.txt (36 lines: 0-9 then A-Z) — stretch/logit-masking path only`
- `config file: apps/edge/config/cameras.yaml, root key `cameras`, per-camera keys: camera_id, name, rtsp_url, lat, lon, heading_deg, target_fps, count_line, ocr_zone, enabled`
- `config: count_line = [[x1,y1],[x2,y2]] normalised 0-1; ocr_zone = [[x,y],...] normalised 0-1 polygon`
- `env var: ANPR_API_BASE (e.g. http://127.0.0.1:8000)`
- `env var: ANPR_WORKER_KEY (sent as header X-Worker-Key)`
- `env var: ANPR_EP (force one of DmlExecutionProvider|OpenVINOExecutionProvider|CPUExecutionProvider)`
- `env var: ANPR_DML_DEVICE (DirectML device_id, default 0)`
- `env var: ANPR_MODEL_DIR (default apps/edge/models)`
- `env var: ANPR_LOG_LEVEL (default INFO)`
- `env var: ANPR_DEBUG_VIDEO (output dir; enables overlay writer)`
- `env var: OPENCV_FFMPEG_CAPTURE_OPTIONS = rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|reorder_queue_size;0|max_delay;300000 (set before first VideoCapture)`
- `endpoint consumed: POST /api/v1/ingest/sightings — body {events: SightingEvent[]} (batch <= 16), header X-Worker-Key, must be idempotent on event_id (ON CONFLICT (event_id) DO UPDATE)`
- `endpoint consumed: POST /api/v1/ingest/heartbeat — body {camera_id, worker_version, provider, fps_actual, overrun_dropped, p95_ms, uptime_s}`
- `endpoint consumed (optional): GET /api/v1/cameras — remote camera config pull, same shape as cameras.yaml entries`
- `payload: SightingEvent v1 fields = schema_version(int), event_id(uuid str), track_id(text), camera_id(text), trigger('line'|'timeout'), direction('A_TO_B'|'B_TO_A'|'UNKNOWN'), first_seen_utc(float epoch s), last_seen_utc(float), crossed_at_utc(float), frames_tracked(int), vehicle_class, vehicle_class_conf(float), vehicle_color, plate_text(text|null), plate_confidence(float 0-1), char_confidences(float[]), plate_votes(int), plate_best_single(text|null), plate_valid_format(bool), bbox_vehicle(int[4] x1y1x2y2), bbox_plate(int[4]|null), frame_w(int), frame_h(int), evidence_plate_key(text|null), worker_version(text), provider(text)`
- `id format: track_id = '{camera_id}:{session_epoch}:{local_track_id}' e.g. 'CAM_MH12_NH48_01:1725539400:117'`
- `id format: event_id = uuid5(namespace 6f9619ff-8b86-d011-b42d-00c04fc964ff, track_id) — deterministic, retry-safe`
- `enum vehicle_class: CAR | TWO_WHEELER | BUS | TRUCK | OTHER (COCO ids 2,3,5,7 respectively)`
- `enum vehicle_color: RED | ORANGE | YELLOW | GREEN | CYAN | BLUE | VIOLET | PINK | WHITE | GREY | BLACK | UNKNOWN`
- `enum trigger: line | timeout`
- `enum direction: A_TO_B | B_TO_A | UNKNOWN`
- `storage bucket: evidence; key pattern plates/{camera_id}/{YYYY}/{MM}/{DD}/{event_id}.jpg`
- `storage bucket: evidence; key pattern vehicles/{camera_id}/{YYYY}/{MM}/{DD}/{event_id}.jpg (stretch)`
- `db expectation: track_sessions gets exactly ONE row per camera pass, keyed by event_id, holding track_id, camera_id, first_seen_utc, last_seen_utc, crossed_at_utc, direction, trigger, vehicle_class, vehicle_color, bbox_vehicle`
- `db expectation: plate_reads(track_session_id uuid fk, plate_text text, plate_confidence real, char_confidences real[], plate_votes int, plate_valid_format bool, evidence_plate_key text)`
- `db expectation: cameras(camera_id text pk, name text, rtsp_url text, geom geography(Point,4326), heading_deg int, count_line jsonb, ocr_zone jsonb, enabled bool)`
- `threshold constant: VEHICLE_CONF=0.35`
- `threshold constant: PLATE_CONF=0.40`
- `threshold constant: NMS_IOU=0.50`
- `threshold constant: TRACK_THRESH=0.50 (ByteTrack track_activation_threshold)`
- `threshold constant: MATCH_THRESH=0.80 (ByteTrack minimum_matching_threshold)`
- `threshold constant: TRACK_BUFFER=30 sampled frames (ByteTrack lost_track_buffer; 2.5 s at 12 FPS)`
- `threshold constant: MIN_BOX_AREA=900 px^2`
- `threshold constant: COLOUR_MIN_AREA=2500 px^2`
- `threshold constant: MAX_VOTES=15`
- `threshold constant: MAX_OCR_TRACKS_PER_FRAME=3`
- `threshold constant: MIN_TRACK_FRAMES=5`
- `threshold constant: MIN_PLATE_H_PX=12 (discard read)`
- `threshold constant: LOW_RES_H_PX=24 (upscale before OCR)`
- `threshold constant: TARGET_H=48 (PP-OCR rec input height)`
- `threshold constant: MIN_SHARPNESS=60.0 (variance of Laplacian)`
- `threshold constant: LOW_CONTRAST_STD=45.0 (CLAHE gate)`
- `threshold constant: Zones.MIN_MOVE_PX=8, Zones.MIN_FRAMES=3`
- `threshold constant: target_fps default 10 (highway) / 15 (junction), source assumed 25-30`
- `regex: RE_STANDARD = ^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$`
- `regex: RE_BH = ^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$`
- `runtime deps: onnxruntime-directml, numpy, opencv-python, supervision, pyyaml, httpx, python-dotenv, rapidocr-onnxruntime`
- `dev-only deps (AGPL isolation): ultralytics, onnx, onnxslim, onnxconverter-common, onnxruntime (CPU)`
- `ORT provider names: DmlExecutionProvider, OpenVINOExecutionProvider, CPUExecutionProvider; ONNX opset 17`
- `spool file: apps/edge/logs/spool/{camera_id}.jsonl (failed POST replay queue)`
- `debug output: apps/edge/out/{camera_id}.mp4 (mp4v; re-encode to H.264 yuv420p for the deck)`

## Appendix — MVP vs Stretch

- MVP: single process, N decoder threads + one pipeline thread owning all ORT sessions (do NOT run one process per camera on 4 GB VRAM)
- MVP: OpenCV VideoCapture(CAP_FFMPEG) + LatestFrameReader grab/retrieve thread with OPENCV_FFMPEG_CAPTURE_OPTIONS forcing TCP and nobuffer
- MVP: frame sampling to 10-12 FPS via grab-all / retrieve-every-Nth; overrun_dropped counter as the primary health metric
- MVP: two-stage detection — stock yolov8n COCO (classes 2,3,5,7) at 640, then fine-tuned 1-class yolov8n plate detector at 384 on the full-resolution vehicle crop
- MVP: ONNX export at opset 17 with nms=False, dynamic=False, then batch-axis widening via tools/fix_dims.py and FP16 via tools/to_fp16.py
- MVP: provider selection with logging (DML -> OpenVINO -> CPU), enable_mem_pattern=False and ORT_SEQUENTIAL under DirectML, 5 warmup runs at startup
- MVP: numpy/cv2.dnn.NMSBoxes post-processing outside the graph
- MVP: supervision.ByteTrack (MIT) with frame_rate == target_fps, lost_track_buffer=30
- MVP: globally unique track_id = camera_id:session_epoch:local_id
- MVP: gated plate stage (in ocr_zone AND votes<15 AND area>=900, top 3 by area) with batched crop inference
- MVP: plate crop prep — 7%/14% pad, minAreaRect deskew + 4-point warp, IND blue-band strip, resize to 48px height, blur rejection, conditional CLAHE. No binarisation, no denoise, no sharpening.
- MVP: recognition-only OCR (detection off, angle classifier off) via rapidocr-onnxruntime PP-OCRv4, batched
- MVP: per-track per-position confidence-weighted character voting + positional confusion repair against the RTO grammar regexes
- MVP: two-row (motorcycle) plate split by horizontal projection profile when aspect ratio < 2.6
- MVP: line-crossing emission with jitter guard, plus timeout emission on track death — exactly one event (one track_sessions row) per track
- MVP: HSV lower-body-band colour estimate + COCO class -> {CAR,TWO_WHEELER,BUS,TRUCK}
- MVP: EventSink with JPEG evidence upload, batched POST to /api/v1/ingest/sightings, uuid5 idempotency, disk spool retry
- MVP: JSON structured logging, StageTimers p50/p95 every 100 frames, heartbeat POST every 10 s
- MVP: --debug-video annotated MP4 in the product palette, plus the ffmpeg H.264 re-encode command
- MVP: apps/edge/bench.py built in the first few hours so every quoted latency is measured, not assumed
- STRETCH: ffmpeg subprocess raw-pipe ingest with -hwaccel d3d11va if OpenCV latency drifts
- STRETCH: run the PP-OCR rec model as raw ONNX so logits can be masked to the 36-character plate charset and real per-character confidences are available
- STRETCH: YOLOv8n-OBB plate detector to get the rotated quad directly instead of contour-based deskew
- STRETCH: vehicle detector fine-tuned on IDD to add AUTO_RICKSHAW
- STRETCH: INT8 static QDQ quantisation for the CPU fallback path only
- STRETCH: second venv with onnxruntime-openvino to offload one camera to an Intel iGPU (useless if the iGPU is AMD)
- STRETCH: adaptive FPS backpressure — drop target_fps when overrun_dropped rises, burst to full rate while a track is inside the OCR zone
- STRETCH: prometheus_client exporter on :9101 instead of log-line metrics
- STRETCH: synthetic Indian-plate rec fine-tuning on Colab with the RTO font

## Appendix — Risks

- onnxruntime-directml, onnxruntime, onnxruntime-gpu and onnxruntime-openvino all install the same `onnxruntime` module — installing two silently breaks provider availability. Mitigation: separate runtime and export venvs, and log sess.get_providers() at startup plus in the debug-video HUD so a silent CPU fallback is visible in second one.
- CAP_PROP_BUFFERSIZE is ignored by OpenCV's FFmpeg backend, so a naive read() loop accumulates unbounded lag and the map desynchronises from the video on stage. Mitigation: the grab-all/retrieve-on-demand reader thread plus the overrun_dropped counter surfaced in the heartbeat.
- Feeding ByteTrack frame_rate=30 while processing at 12 FPS makes tracks die early and Kalman predictions overshoot, fragmenting track_ids and producing duplicate track_sessions and phantom cloned-plate alerts. Mitigation: frame_rate is read from the same cam.target_fps value used by the reader; assert equality at startup.
- Swapping rec_char_dict_path to a 36-char plate charset misaligns the pretrained head's output indices and produces garbage text. Mitigation: MVP keeps the original dictionary and filters/repairs in post-processing; logit masking only on the raw-ONNX stretch path.
- Over-processing the plate crop (Otsu/adaptive binarisation, unsharp, fastNlMeansDenoising) measurably lowers PP-OCR accuracy while costing 20+ ms. Mitigation: geometric transforms plus conditional CLAHE only, documented as a hard rule, and an A/B on 200 held-out crops before changing it.
- Ultralytics is AGPL-3.0; shipping it in the runtime creates a source-disclosure obligation that a judge could raise. Mitigation: ONNX-only runtime, ultralytics confined to requirements-dev.txt, supervision (MIT) for tracking.
- Single-stage plate detection at 640 on a downscaled 1080p frame leaves plates at ~13 px and silently loses the mid/far field, which looks like an OCR problem but is a detection problem. Mitigation: two-stage with crops taken from the original full-resolution frame; log plate box height distribution so the failure is diagnosable.
- DirectML re-compiles kernels when input spatial dims change, so fully dynamic ONNX axes cause invisible per-frame stalls. Mitigation: fixed H/W, dynamic batch axis only, via tools/fix_dims.py.
- paddlepaddle wheels for Python 3.12 on Windows are historically unreliable. Mitigation: rapidocr-onnxruntime (the same PP-OCRv4 weights exported to ONNX) as the default, with paddleocr==2.7.x documented as the alternative.
- First inference after session creation includes DirectML kernel compilation and can take 1-3 s, which looks like a hang or a dropped first vehicle on stage. Mitigation: 5 warmup runs per session during startup, before any camera thread begins.
- COCO has no auto-rickshaw class and labels them inconsistently, so vehicle_class will be wrong for a visible fraction of Indian traffic. Mitigation: treat vehicle_class as a coarse filter, never as evidence; IDD fine-tune as stretch.
- Colour estimation is only ~75-85% accurate and WHITE/GREY/SILVER is the dominant confusion. Mitigation: silver merged into GREY, field labelled 'estimated' in the UI, never used as an alert trigger.
- Four cameras on one RX 6500M is at the edge of the budget; a busy frame with three simultaneous OCR bursts can exceed the frame budget and cascade. Mitigation: MAX_OCR_TRACKS_PER_FRAME cap, 720p replay, and a documented fallback to 8 FPS / 960x540 during the live demo.
- Reading demo footage from local .mp4 files instead of MediaMTX decodes as fast as the CPU allows, invalidating every timing assumption and making inter-camera speed estimates nonsense. Mitigation: the worker refuses file:// and non-rtsp URLs unless --allow-file is passed.

## Appendix — Open Questions

- Does the dev laptop pair the RX 6500M with an Intel Iris Xe iGPU or an AMD Radeon iGPU? If Intel, a second venv running one camera on OpenVINO GPU_FP16 adds a free device and raises the camera ceiling from ~3-4 to ~5.
- Which plate dataset is the team actually using for the stage-2 fine-tune, and what fraction of it is Indian-format? This determines whether we need a Colab synthetic-plate augmentation pass on day one.
- Does the DB section want first_seen_utc/last_seen_utc/crossed_at_utc as float epoch seconds (what the worker emits) or as timestamptz? The worker will send whichever, but the contract must be fixed before either side writes code.
- Does the backend own the evidence-crop upload (worker POSTs multipart) or does the worker upload directly to the Supabase Storage bucket with a service key? Direct upload is fewer hops but puts a Supabase key on the edge box, which has DPDP implications the compliance section should rule on.
- Is inter-camera speed estimation computed in the backend from crossed_at_utc deltas (my assumption) or does the worker need to emit an in-frame pixel-velocity estimate as well?
- Should the plate-detector stage also run on the two-wheeler rear view, where the plate is often the only readable region and the 'vehicle crop' is mostly rider? A dedicated lower-third crop heuristic may be needed if two-wheeler recall is poor in benchmarking.
- What is the acceptable end-to-end latency target for the demo (RTSP frame -> row visible on the map)? I have budgeted ~1.5-3 s including network and Realtime; if the pitch promises sub-second, the emission trigger has to move earlier than the line crossing.
