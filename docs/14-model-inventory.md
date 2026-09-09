# NETRA — Model Inventory

**SIH26127 · Bharat Electronics Limited**

Every model in the system, what it does, why it was chosen over the alternative,
and the number to quote if a judge asks. All figures measured on the development
machine (AMD Radeon RX 6500M, no CUDA).

---

## 1. The six models at a glance

| # | Role | Model | Weights | Framework | Runs on |
|---|------|-------|---------|-----------|---------|
| 1 | Vehicle detection | **YOLOv8n** | `yolov8n.pt` — 6.3 MB | Ultralytics 8.4.142 | GPU (DirectML) |
| 2 | Multi-object tracking | **ByteTrack** | none (algorithmic) | Ultralytics | CPU |
| 3 | Plate detection | **YOLO11n** (licence-plate) | `plate_yolo11n.pt` — 5.3 MB | Ultralytics | GPU (DirectML) |
| 4 | Plate OCR | **EasyOCR** (CRAFT + CRNN) | ~100 MB | PyTorch 2.14 | CPU |
| 5 | Face detection | **SCRFD-500M** | `det_500m.onnx` — 2.5 MB | ONNX Runtime | GPU (DirectML) |
| 6 | Face recognition | **ArcFace / MobileFaceNet** | `w600k_mbf.onnx` — 13 MB | ONNX Runtime | GPU (DirectML) |

Total weights actually loaded: **≈ 128 MB**. The whole stack runs on a laptop
with no dedicated AI hardware.

---

## 2. Model-by-model detail

### 2.1 YOLOv8n — vehicle and object detection

* **Trained on:** COCO (80 classes). We use classes **2 car, 3 motorcycle,
  5 bus, 7 truck** for ANPR, **0 person** for the threat channel, and all 80 when
  the operator camera is asked to report everything.
* **Why the *n* (nano) variant:** 6.3 MB and 18.9 ms per frame on this GPU. A
  larger variant buys accuracy we do not need — the bottleneck in this pipeline
  is OCR legibility, not whether we found the car.
* **Confidence threshold:** 0.35 for tracking, 0.30 for object reporting.

### 2.2 ByteTrack — multi-object tracking

* **Not a neural network.** It associates detections across frames using IoU and
  detection score, including the *low*-confidence detections most trackers throw
  away — which is where it gets its accuracy on partially occluded vehicles.
* **Why ByteTrack over DeepSORT:** DeepSORT needs a separate appearance-embedding
  network, which is more VRAM, another model to ship and another failure mode.
  ByteTrack needs no ReID model at all. On a laptop with no CUDA that was the
  deciding factor.
* Each camera gets its **own** tracker instance so track IDs never blend between
  cameras. Local track ID is `<camera_id>:<n>`, deliberately distinct from any
  cross-camera identity — as §8 of the requirements demands.

### 2.3 YOLO11n licence-plate detector

* **Source:** `morsetechlab/yolov11-license-plate-detection` (Hugging Face).
* **Job:** find the plate *inside* the vehicle box, so OCR reads a tight crop
  instead of the whole car.
* **Measured impact:** without it we fall back to "the lower-central band of the
  vehicle box", which works for a smoke test and is poor for accuracy. The code
  says so in its own comment rather than pretending the fallback is equivalent.
* **Threshold:** 0.25, with a 2 px margin around the crop.

### 2.4 EasyOCR — plate text recognition

* **Architecture:** CRAFT text detector + CRNN recogniser, English model.
* **Character allowlist:** `A–Z0–9` only. Without it the reader returns
  punctuation and lowercase noise that the voting layer then has to discount.
* **Pre-processing:** crops narrower than 240 px are upscaled with cubic
  interpolation — measurably better reads. Heavier processing (binarisation,
  denoise) made results **worse** in testing and was removed.
* **Runs on CPU.** This is the honest limitation of the stack: EasyOCR is PyTorch,
  not ONNX, so it does not move to DirectML. It is the slowest stage.
* Fragments are joined left-to-right across boxes, because the recogniser often
  splits one plate into two detections and taking only the best one silently
  truncates the plate.

### 2.5 SCRFD-500M — face detection

* Part of the InsightFace **`buffalo_s`** pack. 500M-FLOP variant.
* Detection size 640×640; faces narrower than **60 px** are rejected outright
  rather than matched badly.

### 2.6 ArcFace (MobileFaceNet, `w600k_mbf`) — face recognition

* **Training set:** WebFace600K. **Output:** a 512-dimension L2-normalised
  embedding.
* **Matching:** cosine similarity against the enrolled gallery held as one
  matrix — a single matrix-vector product, so a watchlist of thousands costs the
  same as a watchlist of ten.
* **We load only detection + recognition.** The `buffalo_s` pack also ships
  landmark (`1k3d68`, 137 MB) and `genderage` models. We deliberately do **not**
  load them: NETRA does not estimate age or gender, and a surveillance system
  should not infer attributes it has no operational need for.

---

## 3. The layer that is ours — temporal voting

This is the part to lead with. It is not a downloaded model; it is the algorithm
that makes the downloaded models trustworthy.

> Every frame of a tracked vehicle contributes **per-character** evidence.
> Each vote is weighted by frame quality (plate area, sharpness via variance of
> Laplacian, aspect ratio, detector confidence). Per-position margins are combined
> with a **geometric mean**, so one bad character sinks the whole plate instead of
> being averaged away by nine good ones.

| Constant | Value | Meaning |
|----------|-------|---------|
| `MIN_FRAMES` | 3 | Below this, no vote is published at all |
| `FULL_EVIDENCE_FRAMES` | 8 | Frames needed before full confidence is reachable |
| `MIN_READ_QUALITY` | 0.45 | Below this a frame is **noise**, not weak evidence |
| `AUTO_ACCEPT` | 0.90 | At or above, published as fact |
| `REVIEW_MIN` | 0.55 | Between the two, a human decides |
| `SINGLE_FRAME_CEILING` | 0.80 | Cap for handheld cameras — cannot reach auto-accept |

**Two results worth quoting:**

1. *More frames made confidence worse.* 28 frames scored 0.825 where 20 frames
   scored 0.928 — because bad frames were voting. Adding `MIN_READ_QUALITY` fixed
   it. Evidence that poor is noise, not weak evidence.
2. *More CPU does not improve accuracy.* 18, 36, 72 and 144 frames all converge on
   0.939 when one character position is contested. It is an OCR-quality limit,
   not a compute limit.

---

## 4. Measured accuracy

| Plate | Confidence | Frames | Source |
|-------|-----------:|-------:|--------|
| `DL3CBJ1384` | 99.5 % | — | Delhi, toll-gate framing |
| `MH02BJ4692` | 93.6 % | — | Maharashtra |
| `KL07BA5252` | 92.8 % | 20 | Kerala, close range |
| `HR26CO6869` | 91.6 % | — | Haryana |

**Zero false positives across 327 seeded sightings.**

The same plate read from a phone (single frame) returns **0.800** and goes to the
review queue — never published as fact. That contrast is the demo.

---

## 5. Hardware acceleration — DirectML

The development machine is an **AMD Radeon RX 6500M (gfx1034)**: no CUDA, and
ROCm does not support it. We used **`onnxruntime-directml`**, which runs ONNX
models through DirectX 12 on any Windows GPU — AMD, Intel or NVIDIA.

| Model | CPU | DirectML | Speed-up |
|-------|----:|---------:|---------:|
| YOLOv8n | 59.4 ms | **18.9 ms** | **3.2×** |
| Plate YOLO11n | 54.9 ms | **11.1 ms** | **5.0×** |

Execution providers active: `DmlExecutionProvider`, `CPUExecutionProvider`.

*Gotcha worth mentioning if asked:* `pip install insightface` pulls plain
`onnxruntime`, which silently overrides the DirectML build and drops everything
back to CPU with no error message. The fix is to reinstall
`onnxruntime-directml` afterwards.

---

## 6. Decision thresholds

| Subsystem | Threshold | Value |
|-----------|-----------|------:|
| ANPR | auto-accept / review floor | 0.90 / 0.55 |
| Face match | confirm / review | 0.65 / 0.45 |
| Face | minimum face width | 60 px |
| Face | frames before an identity settles | 5 |
| Single-frame plate | confidence cap | 0.80 |
| Single-frame plate | minimum OCR confidence | 0.35 |
| Single-frame plate | minimum plate length | 7 chars |
| Threat | person-contact IoU | 0.20 |
| Cloned plate | max plausible speed | 150 km/h |
| Cloned plate | min separation / min read confidence | 400 m / 0.92 |
| Speeding | limit + margin | 60 + 15 km/h |
| Loitering | window / passes / dwell | 30 min / 5 / 480 s |

---

## 7. What we did NOT use, and why

| Not used | Reason |
|----------|--------|
| DeepSORT / StrongSORT | Needs a ReID network; ByteTrack does not. No CUDA on this machine. |
| YOLOv8m / l / x | The bottleneck is OCR legibility, not detection. Larger models cost CPU for no gain. |
| PaddleOCR / TrOCR | EasyOCR's per-detection confidence feeds the voting layer directly. |
| `buffalo_l` (large face pack) | 5× the size for accuracy we cannot demonstrate on webcam-quality frames. |
| genderage model | Downloaded with the pack, deliberately not loaded — no operational need. |
| A weapon/firearm model | Not trained. COCO has **no firearm class**, and we say so rather than claim gun detection. |
| A trained accident model | The heuristic failed its benchmark; the fix is CCD/UCF-Crime + VideoMAE or RTFM, which needs GPU training time we did not have. |

---

## 8. If a judge asks one question, answer this one

> **"Which model gives you your accuracy?"**

None of them. The models are small, off-the-shelf and replaceable — YOLOv8n and
EasyOCR are what everyone uses. The accuracy comes from **what we do with their
output**: accumulating per-character evidence across frames, weighting it by image
quality, combining it with a geometric mean so one weak character cannot hide
behind nine strong ones, and refusing to publish anything below 0.90.

A better OCR model would raise the numbers. It would not change the design — and
the design is what makes a wrong plate never reach an operator as fact.
