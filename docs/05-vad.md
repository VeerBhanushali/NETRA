<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## Video Anomaly Detection — Crime & Behaviour

This component runs **beside** the ANPR pipeline on the same decoded frame stream and answers a different question. ANPR answers *"which vehicle, where, when"*. VAD answers *"is something happening here that a human operator should look at right now"*. It emits discrete `anomaly_events`, each with a clip, a thumbnail, a severity, a type label, and a list of vehicles that were in frame — so a single alert card in the UI reads: *"Fighting, Cam-07 (Sion Circle), 14:22:31, 18 s, peak 0.81 — vehicles in frame: MH12DE1433, MH02BK9021"*.

It is also the single highest-risk component in a 36-hour build. Section 12 below is the fallback ladder, and it is not optional reading.

---

### 1. The paradigm: weakly-supervised Multiple-Instance Learning

Nobody has frame-level labels for crime. UCF-Crime (Sultani et al., CVPR 2018) has **1,900 untrimmed real CCTV videos, 128 hours, 13 anomaly classes + Normal**, and the *training* split carries only one bit per video: *this video contains an anomaly somewhere* vs *this video is normal throughout*. The test split (290 videos) has frame-level start/end annotations, used **only** for evaluation.

MIL turns that weak label into a trainable objective:

1. Cut each video into `T` short **snippets** (we use 4 seconds each).
2. Embed each snippet into a fixed-length vector with a frozen, pretrained **feature extractor** (I3D / CLIP-ViT / VideoMAE). This extractor is never fine-tuned — that is what makes the whole thing trainable in minutes.
3. A tiny **score head** (an MLP) maps each snippet vector → a scalar anomaly score in `[0, 1]`.
4. Treat the video as a **bag** of snippets. Take the mean of the **top-k highest-scoring snippets** as the bag's prediction (`k = max(1, T // 16)`, following RTFM). Apply binary cross-entropy against the video-level label.

The gradient does the labelling for you: to make a *positive* bag score high, the head must push **at least a few** snippets high; to make a *negative* bag score low, it must push **every** snippet in every normal video low. Since normal videos contain traffic, pedestrians, occlusion, rain and headlight glare, "every snippet low" forces the head to learn that all of that is boring. What is left — the residual that only appears in anomalous videos — is the anomaly signal. Two regularisers keep it honest: an **L1 sparsity** term on positive-bag scores (anomalies are rare, so most snippets should stay near zero) and a **temporal-smoothness** term `Σ(s_t − s_{t−1})²` (a real event lasts seconds, not one snippet).

> **How does it detect a fight, in one paragraph you can say to a judge.** We do not hand-write a rule for "fight". We take a frozen CLIP vision encoder — a model already trained on 400M image–text pairs, so its 512-dimensional embedding already separates "two people standing" from "two people entangled and lunging" — and summarise every 4-second window by the **mean and the standard deviation** of its 16 frame embeddings. The mean captures *what is in the scene*; the standard deviation captures *how violently the scene's semantic content is changing*, which is the cheap motion channel. A 590k-parameter MLP, trained on ~800 UCF-Crime videos with nothing but a yes/no label per video, maps that 1024-d vector to a score. Fighting reliably produces high embedding variance plus a distinctive appearance cluster, so its score rises. We then require the score to stay high for 3 consecutive seconds and to be 3.5 robust-standard-deviations above *that camera's own* 5-minute baseline before we raise an alert.

---

### 2. Model choice on 4 GB AMD, no CUDA — what actually runs

The GPU is already committed. YOLOv8 (vehicle + plate) and PaddleOCR are the revenue-generating models and they own the DirectML device. VRAM accounting on an RX 6500M (4 GB, 64-bit bus, shared with the Windows compositor):

| Consumer | Approx. VRAM | Note |
|---|---|---|
| Windows 11 desktop / browser | 0.6 – 1.0 GB | unavoidable; the Next.js dev browser is on the same machine |
| YOLOv8s detector, ONNX fp16, DML | 250 – 400 MB | weights + activations |
| ORT DirectML arena overhead, per session | 200 – 500 MB | **per `InferenceSession`** — this is the killer |
| PaddleOCR det+rec, ONNX | 150 – 250 MB | |
| CLIP ViT-B/32 image encoder, fp16, DML | 300 – 450 MB | 88M params = 176 MB fp16 + arena |

**Rule: at most two DirectML sessions, and they must live in one process.** Two OS processes each holding a DML device on a 4 GB card will OOM or thrash under load.

**Default decision: the CLIP encoder runs on the CPU EP; the GPU stays with ANPR.** This is affordable because we only embed **4 frames per second of video**, not 25. Set `ANOMALY_EP=dml` only if you measure headroom.

> **Verify (day 1, 30 minutes):** run `bench_clip.py` on this laptop and record real numbers before trusting the table below.

| Stage | Model | Execution provider | Input shape | Calls / sec of video | Expected latency | 
|---|---|---|---|---|---|
| Frame embed | CLIP ViT-B/32 vision tower, ONNX fp32 | **ORT `CPUExecutionProvider`, 4 threads** | `[4,3,224,224]` | 1 | **60 – 110 ms** |
| Frame embed (alt) | same, ONNX fp16 | ORT `DmlExecutionProvider` | `[4,3,224,224]` | 1 | 20 – 40 ms |
| Score head | 590k-param MLP, ONNX | ORT CPU | `[1,1024]` | 1 | < 0.5 ms |
| Evidence ring | `cv2.imencode('.jpg')` @ q80, 1280×720 | CPU | — | 12 | 3 – 5 ms each |
| Clip mux | ffmpeg libx264 `-preset veryfast` | CPU | 360 frames | per event | 1.5 – 3 s |

Steady-state cost per camera ≈ **10–15 % of one CPU core**. Four simulated cameras is comfortable on this machine alongside the ANPR worker.

**Rejected alternatives, with reasons:**

| Candidate | Verdict |
|---|---|
| I3D RGB features (the UCF-Crime standard) | 3D convs, ~28 GFLOPs per 16-frame snippet, and ONNX export of the canonical Kinetics I3D is fiddly. **Rejected** — but note published AUCs are quoted against I3D, so our numbers are not apples-to-apples. |
| VideoMAE-Base | 87M params over a 16×224×224 *cube* ≈ 180 GFLOPs/snippet. Would run at maybe 2–4 snippets/s on this CPU. **Stretch only.** |
| CLIP ViT-L/14 | 4× the FLOPs of B/32 for a few AUC points. **No.** |
| **CLIP ViT-B/32 @ 4 fps + mean‖std pooling + MLP head** | **Chosen.** 4.4 GFLOPs/frame, trivially exportable, feature caching makes Colab training take minutes. |

> **Verify:** ViT-B/32 projected output dim is 512 for the standard OpenAI checkpoint; confirm against your export (`openai/clip-vit-base-patch32`) rather than assuming.

---

### 3. Streaming pipeline specification

```
RTSP → decode (shared with ANPR) → temporal subsample to 4 fps
     → CLIP embed, batch of 4 → 512-d, L2-normalised
     → rolling deque of 16 embeddings (= 4 s)
     → mean(16) ‖ std(16) = 1024-d snippet feature
     → ScoreHead ONNX → raw score s_t ∈ [0,1], emitted at 1 Hz
     → EMA smooth → robust-z vs 5-min baseline → hysteresis gate
     → anomaly_event → evidence clip → Supabase
```

| Constant | Value | Reasoning |
|---|---|---|
| `feature_fps` | 4.0 | A punch or a collision spans ≥ 0.5 s; 4 fps captures it. 25 fps costs 6× more for no measured AUC gain in the CLIP-feature literature. |
| `snippet_frames` | 16 | = 4 s of wall-clock. Matches the snippet length used to train the head — **these must be identical or the head sees out-of-distribution input.** |
| `snippet_stride` | 4 sampled frames | ⇒ one score **per second**, 75 % overlap between consecutive snippets. |
| `embed_dim` | 512 | CLIP ViT-B/32 projected image embedding. |
| `head_input_dim` | 1024 | `concat(mean, std)` over the 16 frame embeddings. The `std` half is the motion/change channel — without it, CLIP is appearance-only and cannot tell a fight from two people hugging. |
| Input resolution | 224×224 | Resize shortest side to 224, centre-crop. |
| Normalisation | CLIP mean `(0.48145466, 0.4578275, 0.40821073)`, std `(0.26862954, 0.26130258, 0.27577711)` | Must match training exactly. |

---

### 4. Score post-processing — the part that stops alert spam

A raw 1 Hz sigmoid output will cross any fixed threshold dozens of times per minute on a busy junction. Four mechanisms, in order:

**(a) EMA smoothing.** `ema_t = α·s_t + (1−α)·ema_{t−1}`, `α = 0.35` ⇒ time constant ≈ 2.3 s. Chosen so a single bad second (a bus filling the frame, a headlight flare) cannot alone push the smoothed score over threshold, while a 3-second real event can.

**(b) Per-camera robust baseline.** Absolute score is *not* comparable across cameras — a narrow alley scores higher at rest than an empty flyover. Maintain a 300-sample (5-minute) deque of smoothed scores and compute `z = (ema − median) / (1.4826·MAD + 1e-3)`. Gate on `z ≥ 3.5` **AND** `ema ≥ τ_on`. The AND is deliberate: `z` alone fires on any quiet camera the moment a car appears; `τ_on` alone fires forever on a permanently busy camera.

**(c) Hysteresis with two thresholds.** `τ_on = 0.62`, `τ_off = 0.45`. A single threshold makes the event flap open/closed while the subject is momentarily occluded by a passing vehicle. The 0.17 gap bridges roughly 4–6 seconds of occlusion at typical decay rates. `τ_on = 0.62` is a **starting value** — it must be re-fit on the validation split (§11) to hit the false-alarm budget.

**(d) Duration, cooldown, cap.**

| Parameter | Value | Reasoning |
|---|---|---|
| `min_on_scores` | 3 | 3 consecutive seconds above `τ_on`. Kills sub-second spikes. Event `started_at` is **backdated** to the first crossing. |
| `off_consec` | 2 | 2 consecutive seconds below `τ_off` closes the event. |
| `cooldown_s` | 45 | Per camera, per type. A 2-minute brawl produces **one** alert with a long duration, not 120 rows. |
| `max_event_s` | 120 | Force-close and open a continuation event. Prevents a mis-calibrated camera producing one unbounded row. |
| `link_window_pad_s` | 3.0 | Vehicle-linking window is `[started_at − 3, ended_at + 3]` — a hit-and-run vehicle leaves frame exactly as the score peaks. |

**False-alarm budget:** tune `τ_on` so that on the *normal* validation videos the rate is **≤ 1 event per camera-hour**. An operator watching 40 cameras will tolerate ~40 alerts/hour; 400 means the system is switched off.

---

### 5. Alert taxonomy — and an honest statement about the 13 classes

**Do not claim 13-way classification.** Published video-level multi-class accuracy on UCF-Crime is in the **~30–40 %** range against a 14-way chance of ~7 %. > **Verify.** At CCTV resolution, `Shoplifting`, `Stealing`, `Burglary`, `Robbery`, `Vandalism` and `Arrest` are visually near-identical to a model that has no context about ownership or intent. Claiming otherwise in front of a judge who knows the dataset is a losing move.

What we ship is a **binary anomaly score** plus a **type-assignment stage** driven by cheap, auditable evidence detectors:

| `alert_type` | Assigned when | Confidence we claim |
|---|---|---|
| `fight` | anomaly fired **AND** ≥ 2 person tracks within 1.5 body-heights **AND** YOLOv8n-pose wrist/ankle speed > 2.5 body-heights/s | Good — this is the class VAD does best |
| `accident` | anomaly fired **AND** a tracked vehicle decelerates > 6 m/s² or stops abruptly, **OR** two vehicle boxes exceed IoU 0.35 with a track-ID merge | Good, on the demo footage |
| `weapon` | a dedicated YOLOv8n gun/knife head fires with conf ≥ 0.55 in the same window | Weak at CCTV range — **flag as low-confidence in the UI** |
| `crowd_surge` | person count > `crowd_n` **AND** mean optical-flow magnitude z > 3 | Reasonable |
| `arson` | fire/smoke colour-and-flicker heuristic, or a small fire classifier | **Stretch** |
| `abandoned_object` | static foreground blob persisting > 60 s with no owner track | **Stretch** |
| `anomaly_generic` | anomaly fired but no evidence detector agreed | **This is the default and it is fine.** Roughly half of true positives will land here. |

The two-stage split is the defensible design: the *learned* component is deliberately binary (where the weak supervision is trustworthy), and every *named* crime label is backed by a deterministic, explainable signal we can show in the evidence panel. That is also what a real procurement review would demand.

---

### 6. Fusion with ANPR

When an event closes, the worker `POST`s it. **The API performs the vehicle join server-side**, in one DB round trip, so the worker stays dumb and stateless:

```sql
-- executed inside POST /api/v1/anomaly/events, after the insert
insert into anomaly_event_vehicles (event_id, track_id, plate_text, plate_confidence,
                                    first_seen, last_seen, in_frame_seconds)
select $1, t.track_id, t.plate_text, t.plate_confidence, t.first_seen, t.last_seen,
       extract(epoch from (least(t.last_seen, $4) - greatest(t.first_seen, $3)))
from vehicle_tracks t
where t.camera_id = $2
  and tstzrange(t.first_seen, t.last_seen)
      && tstzrange($3::timestamptz - interval '3 seconds',
                   $4::timestamptz + interval '3 seconds')
order by t.plate_confidence desc nulls last
limit 25;
```

> **Contract with the ANPR section:** `vehicle_tracks(track_id uuid, camera_id text, first_seen timestamptz, last_seen timestamptz, plate_text text, plate_confidence real)` must exist with a GiST or btree index supporting `(camera_id, first_seen, last_seen)`. If ANPR names it differently, change it **here**, not there.

**Escalation rule (this is the demo's money shot):** after linking, if any linked plate appears in `watchlist` or carries a `cloned_plate` flag from the cross-camera logic, set `priority = 1` and `escalated = true`. On stage: *"the anomaly detector flagged a collision on Cam-07; the ANPR layer independently flagged the vehicle in that frame as carrying a cloned plate; the platform merged them into one priority-1 alert without an operator touching anything."*

---

### 7. Evidence capture

A **ring buffer of JPEG-encoded frames**, not raw frames. Raw 1080p BGR is 6.2 MB/frame — 40 seconds would be 1.5 GB per camera and the machine has 16 GB total. JPEG at 1280×720 q80 is ~90 KB.

- Ring: `12 fps × 40 s = 480 frames ≈ 43 MB per camera`. Four cameras ≈ 173 MB. Acceptable.
- **Clip window is fixed at 30 s: `[started_at − 15 s, started_at + 15 s]`**, deliberately decoupled from event duration. The operator gets the clip ~15 s after the alert appears, regardless of whether the event runs for 8 s or 110 s, and the ring buffer size is bounded.
- Muxed with ffmpeg to **H.264 / yuv420p / +faststart** — `cv2.VideoWriter` with `mp4v` produces files that Chrome and Safari will not play inline. This bites teams every time.
- Thumbnail = the JPEG nearest the peak score, uploaded as-is.

Storage layout in bucket **`evidence`** (private; frontend reads via signed URL):

```
evidence/{camera_id}/{event_id}/clip.mp4
evidence/{camera_id}/{event_id}/thumb.jpg
```

> **DPDP Act 2023 note:** these clips contain uninvolved bystanders. Set a Storage lifecycle purge at **30 days** unless `anomaly_events.case_id is not null`. The VAD path uses **pose keypoints only — no face recognition, no biometric template** — which keeps this component outside the most sensitive category of processing. Say this before a judge asks.

---

### 8. Data contracts (DDL)

Text + `CHECK` rather than a PG enum — adding an enum value mid-hackathon requires `ALTER TYPE` and blocks.

```sql
create table anomaly_events (
  id             uuid primary key default gen_random_uuid(),
  camera_id      text not null references cameras(camera_id),
  started_at     timestamptz not null,
  ended_at       timestamptz,
  duration_s     real generated always as
                   (extract(epoch from (ended_at - started_at))) stored,
  peak_score     real not null,
  mean_score     real not null,
  peak_z         real,
  alert_type     text not null default 'anomaly_generic'
                 check (alert_type in ('fight','accident','arson','weapon',
                        'abandoned_object','crowd_surge','anomaly_generic')),
  type_confidence real,
  priority       smallint not null default 2 check (priority between 1 and 3),
  escalated      boolean not null default false,
  status         text not null default 'new'
                 check (status in ('new','ack','dismissed','confirmed')),
  score_source   text not null default 'live' check (score_source in ('live','replay')),
  model_version  text not null,
  clip_path      text,
  thumb_path     text,
  geom           geography(Point, 4326),
  case_id        uuid references cases(id),
  created_at     timestamptz not null default now()
);
create index anomaly_events_started_idx  on anomaly_events (started_at desc);
create index anomaly_events_camera_idx   on anomaly_events (camera_id, started_at desc);
create index anomaly_events_open_idx     on anomaly_events (status) where status = 'new';
create index anomaly_events_geom_idx     on anomaly_events using gist (geom);

create table anomaly_event_vehicles (
  event_id         uuid not null references anomaly_events(id) on delete cascade,
  track_id         uuid not null,
  plate_text       text,
  plate_confidence real,
  first_seen       timestamptz,
  last_seen        timestamptz,
  in_frame_seconds real,
  primary key (event_id, track_id)
);
create index aev_plate_idx on anomaly_event_vehicles (plate_text);

-- 1 Hz score trace for the UI sparkline. 6-hour retention.
create table anomaly_scores (
  camera_id text not null,
  ts        timestamptz not null,
  score     real not null,
  smoothed  real not null,
  zscore    real,
  primary key (camera_id, ts)
);
```

**Realtime:** publish `anomaly_events` on the Supabase Realtime `postgres_changes` channel, `INSERT` and `UPDATE`, so the alert rail streams without polling. `anomaly_scores` is **not** published — 1 Hz × N cameras over websockets will drown the browser; the sparkline polls `GET /api/v1/anomaly/scores` every 5 s instead.

**Endpoints** (owned by the FastAPI section, shape defined here):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/anomaly/events` | worker → API; inserts event, runs vehicle join, returns `{id}` |
| `PATCH` | `/api/v1/anomaly/events/{event_id}/evidence` | worker → API; attaches `clip_path`, `thumb_path` |
| `PATCH` | `/api/v1/anomaly/events/{event_id}` | operator → API; `status`, `case_id` |
| `GET` | `/api/v1/anomaly/events?since=&camera_id=&type=&min_priority=&limit=` | alert list |
| `GET` | `/api/v1/anomaly/events/{event_id}` | detail incl. linked vehicles + signed clip URL |
| `POST` | `/api/v1/anomaly/scores:batch` | worker → API; 1 Hz score batch, flushed every 10 s |
| `GET` | `/api/v1/anomaly/scores?camera_id=&since=` | sparkline data |

**Environment variables:** `ANOMALY_ENABLED`, `ANOMALY_EP` (`cpu`\|`dml`), `ANOMALY_SCORE_SOURCE` (`live`\|`replay`), `ANOMALY_REPLAY_PATH`, `ANOMALY_MODEL_VERSION`, `CLIP_ONNX_PATH`, `ANOMALY_HEAD_ONNX_PATH`, `ANOMALY_TAU_ON`, `ANOMALY_TAU_OFF`, `EVIDENCE_BUCKET`.

> **Packaging gotcha, real and costly:** `onnxruntime`, `onnxruntime-directml` and `onnxruntime-openvino` all install a module named `onnxruntime` and **conflict**. Install **only `onnxruntime-directml`** — the CPU EP is included in that wheel, so `providers=["CPUExecutionProvider"]` works without a second package. OpenVINO INT8 is a stretch experiment in a separate venv.

---

### 9. The anomaly worker

`apps/worker/anomaly/config.py`

```python
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class AnomalyConfig:
    feature_fps: float = 4.0
    snippet_frames: int = 16          # 4 s of wall-clock at feature_fps
    snippet_stride: int = 4           # -> one score per second
    embed_dim: int = 512
    head_input_dim: int = 1024        # mean || std

    ema_alpha: float = 0.35
    tau_on: float = float(os.getenv("ANOMALY_TAU_ON", 0.62))
    tau_off: float = float(os.getenv("ANOMALY_TAU_OFF", 0.45))
    z_gate: float = 3.5
    baseline_window_s: int = 300
    baseline_min_samples: int = 60
    min_on_scores: int = 3
    off_consec: int = 2
    cooldown_s: float = 45.0
    max_event_s: float = 120.0

    ring_fps: float = 12.0
    ring_seconds: float = 40.0
    ring_width: int = 1280
    jpeg_quality: int = 80
    clip_pre_s: float = 15.0
    clip_post_s: float = 15.0

CFG = AnomalyConfig()
```

`apps/worker/anomaly/features.py`

```python
import cv2, numpy as np, onnxruntime as ort

CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
CLIP_STD  = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)

def _providers(ep: str):
    return ["DmlExecutionProvider", "CPUExecutionProvider"] if ep == "dml" \
           else ["CPUExecutionProvider"]

class ClipFeatureExtractor:
    """Frozen CLIP ViT-B/32 vision tower, ONNX. Output: L2-normalised [N,512]."""

    def __init__(self, onnx_path: str, ep: str = "cpu", threads: int = 4):
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(onnx_path, so, providers=_providers(ep))
        self.iname = self.sess.get_inputs()[0].name

    @staticmethod
    def preprocess(frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        s = 224 / min(h, w)
        img = cv2.resize(frame_bgr, (int(round(w * s)), int(round(h * s))),
                         interpolation=cv2.INTER_AREA)
        h2, w2 = img.shape[:2]
        y0, x0 = (h2 - 224) // 2, (w2 - 224) // 2
        img = img[y0:y0 + 224, x0:x0 + 224]
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = (img - CLIP_MEAN) / CLIP_STD
        return np.transpose(img, (2, 0, 1))                      # CHW

    def embed(self, frames_bgr: list) -> np.ndarray:
        batch = np.stack([self.preprocess(f) for f in frames_bgr]).astype(np.float32)
        emb = self.sess.run(None, {self.iname: batch})[0]         # [N,512]
        return emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-8)
```

`apps/worker/anomaly/scorer.py`

```python
from collections import deque
import numpy as np, onnxruntime as ort
from .config import CFG
from .features import ClipFeatureExtractor, _providers

class SnippetScorer:
    """Feeds sampled frames in, yields one anomaly score per second."""

    def __init__(self, extractor: ClipFeatureExtractor, head_onnx: str):
        self.ex = extractor
        so = ort.SessionOptions(); so.intra_op_num_threads = 2
        self.head = ort.InferenceSession(head_onnx, so,
                                         providers=["CPUExecutionProvider"])
        self.hname = self.head.get_inputs()[0].name
        self.pending = []                                    # frames awaiting embed
        self.embeds = deque(maxlen=CFG.snippet_frames)       # rolling 16 embeddings
        self.last_sample_ts = -1e9

    def push(self, frame_bgr, ts: float):
        """ts = monotonic seconds of the stream. Returns score or None."""
        if ts - self.last_sample_ts < 1.0 / CFG.feature_fps:
            return None
        self.last_sample_ts = ts
        self.pending.append(frame_bgr)
        if len(self.pending) < CFG.snippet_stride:
            return None

        for e in self.ex.embed(self.pending):
            self.embeds.append(e)
        self.pending.clear()

        if len(self.embeds) < CFG.snippet_frames:
            return None                                      # warming up (~4 s)

        arr = np.stack(self.embeds)                          # [16,512]
        feat = np.concatenate([arr.mean(0), arr.std(0)]).astype(np.float32)
        assert feat.shape[0] == CFG.head_input_dim
        return float(self.head.run(None, {self.hname: feat[None, :]})[0].ravel()[0])
```

`apps/worker/anomaly/gate.py`

```python
from collections import deque
from dataclasses import dataclass, field
import statistics, uuid
from .config import CFG

@dataclass
class AnomalyEvent:
    id: str
    camera_id: str
    started_at: float           # stream seconds; converted to timestamptz by caller
    ended_at: float | None = None
    peak_score: float = 0.0
    peak_z: float = 0.0
    peak_ts: float = 0.0
    scores: list = field(default_factory=list)
    @property
    def mean_score(self): return sum(self.scores) / max(1, len(self.scores))

class EventGate:
    """EMA -> robust-z -> hysteresis -> min-duration -> cooldown."""

    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self.ema = None
        self.hist = deque(maxlen=CFG.baseline_window_s)
        self.on_run = 0
        self.off_run = 0
        self.active: AnomalyEvent | None = None
        self.last_close_ts = -1e9

    def _z(self, v: float) -> float:
        if len(self.hist) < CFG.baseline_min_samples:
            return 99.0                       # unknown baseline -> do not block
        med = statistics.median(self.hist)
        mad = statistics.median([abs(x - med) for x in self.hist])
        return (v - med) / (1.4826 * mad + 1e-3)

    def update(self, ts: float, raw: float):
        """Returns ('open'|'close', event) or (None, None). Call at 1 Hz."""
        self.ema = raw if self.ema is None else \
                   CFG.ema_alpha * raw + (1 - CFG.ema_alpha) * self.ema
        z = self._z(self.ema)
        self.hist.append(self.ema)

        if self.active is None:
            hot = self.ema >= CFG.tau_on and z >= CFG.z_gate
            self.on_run = self.on_run + 1 if hot else 0
            if self.on_run >= CFG.min_on_scores and \
               ts - self.last_close_ts >= CFG.cooldown_s:
                ev = AnomalyEvent(
                    id=str(uuid.uuid4()), camera_id=self.camera_id,
                    started_at=ts - (CFG.min_on_scores - 1),   # backdate to 1st cross
                    peak_score=self.ema, peak_z=z, peak_ts=ts, scores=[self.ema])
                self.active, self.on_run, self.off_run = ev, 0, 0
                return "open", ev
            return None, None

        ev = self.active
        ev.scores.append(self.ema)
        if self.ema > ev.peak_score:
            ev.peak_score, ev.peak_z, ev.peak_ts = self.ema, z, ts

        self.off_run = self.off_run + 1 if self.ema < CFG.tau_off else 0
        expired = (ts - ev.started_at) >= CFG.max_event_s
        if self.off_run >= CFG.off_consec or expired:
            ev.ended_at = ts
            self.active, self.last_close_ts, self.off_run = None, ts, 0
            return "close", ev
        return None, None
```

`apps/worker/anomaly/evidence.py`

```python
from collections import deque
import subprocess, cv2, numpy as np
from .config import CFG

class JpegRing:
    """Bounded ring of JPEG-encoded frames. ~43 MB per camera at defaults."""

    def __init__(self):
        self.buf = deque(maxlen=int(CFG.ring_fps * CFG.ring_seconds))
        self.last_ts = -1e9

    def push(self, frame_bgr, ts: float):
        if ts - self.last_ts < 1.0 / CFG.ring_fps:
            return
        self.last_ts = ts
        h, w = frame_bgr.shape[:2]
        if w != CFG.ring_width:
            k = CFG.ring_width / w
            frame_bgr = cv2.resize(frame_bgr, (CFG.ring_width, int(round(h * k))),
                                   interpolation=cv2.INTER_AREA)
        ok, enc = cv2.imencode(".jpg", frame_bgr,
                               [cv2.IMWRITE_JPEG_QUALITY, CFG.jpeg_quality])
        if ok:
            self.buf.append((ts, enc.tobytes()))

    def slice(self, t0: float, t1: float):
        return [(t, b) for (t, b) in self.buf if t0 <= t <= t1]

    def nearest(self, t: float):
        return min(self.buf, key=lambda kv: abs(kv[0] - t))[1] if self.buf else None


def mux_mp4(jpegs: list[bytes], out_path: str, fps: float = CFG.ring_fps) -> bool:
    """H.264 yuv420p +faststart -- required for inline <video> in Chrome/Safari."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for j in jpegs:
        p.stdin.write(j)
    p.stdin.close()
    return p.wait() == 0
```

`apps/worker/anomaly/worker.py` — the loop that ties it together.

```python
import os, time, json, tempfile, datetime as dt, httpx
from .config import CFG
from .features import ClipFeatureExtractor
from .scorer import SnippetScorer
from .gate import EventGate
from .evidence import JpegRing, mux_mp4
from .taxonomy import classify_event

API = os.environ["API_BASE_URL"]

class AnomalyWorker:
    def __init__(self, camera_id: str, wall_epoch: float):
        self.cam, self.epoch = camera_id, wall_epoch
        self.ring, self.gate = JpegRing(), EventGate(camera_id)
        self.pending_clips: list[dict] = []
        self.score_batch: list[dict] = []
        self.http = httpx.Client(base_url=API, timeout=8.0)
        self.replay = os.getenv("ANOMALY_SCORE_SOURCE", "live") == "replay"
        self.mv = os.getenv("ANOMALY_MODEL_VERSION", "clip-b32-mlp-v1")

        if self.replay:                       # ladder level 4 -- see section 12
            with open(os.environ["ANOMALY_REPLAY_PATH"]) as f:
                self.trace = {round(t, 1): s for t, s in json.load(f)[camera_id]}
        else:
            ex = ClipFeatureExtractor(os.environ["CLIP_ONNX_PATH"],
                                      ep=os.getenv("ANOMALY_EP", "cpu"))
            self.scorer = SnippetScorer(ex, os.environ["ANOMALY_HEAD_ONNX_PATH"])

    def _iso(self, ts): return dt.datetime.fromtimestamp(
        self.epoch + ts, dt.timezone.utc).isoformat()

    def on_frame(self, frame_bgr, ts: float, tracks: list[dict], poses: list[dict]):
        """Called by the shared decode loop for every decoded frame.
        `tracks` / `poses` are the current-frame outputs of the ANPR worker."""
        self.ring.push(frame_bgr, ts)

        score = self.trace.get(round(ts, 1)) if self.replay \
                else self.scorer.push(frame_bgr, ts)
        if score is None:
            self._drain_clips(ts); return

        self.score_batch.append({"camera_id": self.cam, "ts": self._iso(ts),
                                 "score": score, "smoothed": self.gate.ema or score,
                                 "zscore": None})
        if len(self.score_batch) >= 10:
            self._flush_scores()

        kind, ev = self.gate.update(ts, score)
        if kind == "open":
            ev.signals = {"tracks": list(tracks), "poses": list(poses)}
            self.pending_clips.append({
                "event": ev,
                "cut_start": ev.started_at - CFG.clip_pre_s,
                "cut_end":   ev.started_at + CFG.clip_post_s})
        elif kind == "close":
            self._post_event(ev)
        self._drain_clips(ts)

    def _post_event(self, ev):
        atype, conf = classify_event(ev)
        r = self.http.post("/api/v1/anomaly/events", json={
            "id": ev.id, "camera_id": self.cam,
            "started_at": self._iso(ev.started_at),
            "ended_at":   self._iso(ev.ended_at),
            "peak_score": round(ev.peak_score, 4),
            "mean_score": round(ev.mean_score, 4),
            "peak_z": round(ev.peak_z, 2),
            "alert_type": atype, "type_confidence": conf,
            "priority": 1 if atype in ("weapon", "accident", "fight") else 2,
            "score_source": "replay" if self.replay else "live",
            "model_version": self.mv})
        r.raise_for_status()

    def _drain_clips(self, now: float):
        """Cut + upload evidence 15 s after the event opened, not at close."""
        still = []
        for job in self.pending_clips:
            if now < job["cut_end"]:
                still.append(job); continue
            ev = job["event"]
            frames = [b for _, b in self.ring.slice(job["cut_start"], job["cut_end"])]
            if not frames:
                continue
            mp4 = os.path.join(tempfile.gettempdir(), f"{ev.id}.mp4")
            if not mux_mp4(frames, mp4):
                continue
            thumb = self.ring.nearest(ev.peak_ts)
            with open(mp4, "rb") as fh:
                self.http.patch(
                    f"/api/v1/anomaly/events/{ev.id}/evidence",
                    files={"clip":  ("clip.mp4",  fh.read(), "video/mp4"),
                           "thumb": ("thumb.jpg", thumb,     "image/jpeg")})
            os.remove(mp4)
        self.pending_clips = still

    def _flush_scores(self):
        try:
            self.http.post("/api/v1/anomaly/scores:batch",
                           json={"rows": self.score_batch})
        except httpx.HTTPError:
            pass                              # score trace is cosmetic, never block
        finally:
            self.score_batch.clear()
```

`apps/worker/anomaly/taxonomy.py`

```python
import math
from .config import CFG

def classify_event(ev) -> tuple[str, float]:
    """Deterministic, explainable type assignment. Never invents a crime label
    without a corroborating signal -- unlabelled events stay 'anomaly_generic'."""
    sig = getattr(ev, "signals", {}) or {}
    tracks = sig.get("tracks", [])
    poses  = sig.get("poses", [])

    if any(t.get("cls") in ("gun", "knife") and t.get("conf", 0) >= 0.55
           for t in tracks):
        return "weapon", 0.55

    # collision: hard deceleration, or two vehicle boxes fusing
    for t in tracks:
        if t.get("cls") in ("car", "truck", "bus", "motorcycle"):
            if abs(t.get("accel_mps2", 0.0)) > 6.0:
                return "accident", 0.6
    vehicles = [t for t in tracks if t.get("cls") in ("car", "truck", "motorcycle")]
    for i, a in enumerate(vehicles):
        for b in vehicles[i + 1:]:
            if _iou(a["bbox"], b["bbox"]) > 0.35:
                return "accident", 0.5

    # fight: >=2 people, close, with fast limbs
    people = [p for p in poses if p.get("bbox")]
    for i, a in enumerate(people):
        ha = max(1.0, a["bbox"][3] - a["bbox"][1])
        for b in people[i + 1:]:
            d = math.dist(_c(a["bbox"]), _c(b["bbox"]))
            if d < 1.5 * ha and max(a.get("limb_speed_bh", 0),
                                    b.get("limb_speed_bh", 0)) > 2.5:
                return "fight", 0.65

    if len(people) >= 12 and sig.get("flow_z", 0) > 3.0:
        return "crowd_surge", 0.5
    return "anomaly_generic", float(min(1.0, ev.peak_score))

def _c(b): return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua > 0 else 0.0
```

---

### 10. What to train on Colab, and in what budget

**Do not download all 128 hours of UCF-Crime.** Take the **6-class subset** that matches our taxonomy: `Fighting`, `Assault`, `Abuse`, `RoadAccidents`, `Explosion`, `Arson` (~400 videos) plus ~400 `Normal_Videos` — roughly 800 videos, ~25 GB. > **Verify:** UCF-Crime is distributed as per-class Dropbox/OneDrive folders; grab only the class folders you need.

| Phase | Where | Wall-clock budget |
|---|---|---|
| Download the 6-class subset + Normal | Colab, `wget` into `/content` | 60 – 90 min |
| Export CLIP ViT-B/32 vision tower → ONNX (`opset=17`, dynamic batch) | Colab, once | 10 min |
| Decode @ 4 fps + CLIP-embed → cache `.npy` per video (`[T,1024]` snippet features) | Colab T4 | **90 – 120 min** — decode is the bottleneck, not the GPU. Use PyAV/decord, 8 worker threads. |
| Train the MLP head on cached features | T4 or even CPU | **< 5 min per run.** This is the point of caching — you can do 20 hyper-parameter runs in an hour. |
| Evaluate frame-level AUC on the official 290-video test split | Colab | 20 min (features must be extracted for the test split too) |
| Export head → ONNX, download both `.onnx` files | — | 5 min |

Training code (the part that matters):

```python
import torch, torch.nn as nn, torch.nn.functional as F

class ScoreHead(nn.Module):                      # 590k params
    def __init__(self, d=1024):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d, 512), nn.GELU(), nn.Dropout(0.6),
            nn.Linear(512, 128), nn.GELU(), nn.Dropout(0.6),
            nn.Linear(128, 1), nn.Sigmoid())
    def forward(self, x): return self.net(x)

LAM_SPARSE, LAM_SMOOTH = 8e-5, 8e-5              # Sultani et al. starting values

def mil_step(head, batch, opt):
    """batch = [{'f': FloatTensor[T,1024], 'y': 0|1}, ...]  (T varies per video)"""
    feats = torch.cat([b["f"] for b in batch]).cuda()
    s_all = head(feats).squeeze(-1).clamp(1e-6, 1 - 1e-6)
    off, loss = 0, 0.0
    for b in batch:
        T = b["f"].shape[0]
        s = s_all[off:off + T]; off += T
        k = max(1, T // 16)                                   # RTFM top-k rule
        bag = torch.topk(s, min(k, T)).values.mean()
        y = torch.tensor(float(b["y"]), device=s.device)
        loss = loss + F.binary_cross_entropy(bag, y)
        if b["y"] == 1:                                       # regularise positives
            loss = loss + LAM_SPARSE * s.sum() \
                        + LAM_SMOOTH * ((s[1:] - s[:-1]) ** 2).sum()
    loss = loss / len(batch)
    opt.zero_grad(); loss.backward(); opt.step()
    return loss.item()

# Adam(lr=1e-3, weight_decay=5e-4) -- bags balanced 1:1 normal/abnormal per batch.
# 32 bags/batch, 200 epochs over ~800 videos. Export with dynamic batch axis:
# torch.onnx.export(head.cpu().eval(), torch.randn(1,1024), "anomaly_head_v1.onnx",
#                   opset_version=17, input_names=["feat"], output_names=["score"],
#                   dynamic_axes={"feat": {0: "n"}, "score": {0: "n"}})
```

**Metric to report: frame-level ROC-AUC on the UCF-Crime test split**, computed by expanding each snippet score across its 4-second frame span and scoring against `Temporal_Anomaly_Annotation_for_Testing_Videos.txt`. Also report **FAR on normal test videos** at your chosen `τ_on`.

Published reference points, so the team knows what "good" means — **all of these are figures to verify before quoting them to a judge**, and all are measured on the full 13-class training set with I3D features, not our 6-class CLIP subset:

| Method | Features | Frame-level AUC (%) |
|---|---|---|
| Chance | — | 50.0 |
| Sultani et al. 2018 (MIL) | C3D | ≈ 75.4 |
| Sultani et al. | I3D | ≈ 77 |
| RTFM (ICCV'21) | I3D | ≈ 84.3 |
| S3R / MGFN (2022) | I3D | ≈ 86 – 87 |
| CLIP-feature methods (CLIP-TSA / VadCLIP) | CLIP ViT | ≈ 87 – 88 |

> **Verify: every number in the table above.**

**Decision rule for the team.** `AUC ≥ 0.80` → ship level 1 with the full taxonomy. `0.70 ≤ AUC < 0.80` → ship level 1 but raise `τ_on` to hit the FAR budget and label everything `anomaly_generic` unless an evidence detector corroborates. `AUC < 0.70` → **drop to level 2 immediately, do not spend more hours on it.**

---

### 11. Fallback ladder — with hard time triggers

Measure from T-0 = start of the 36-hour build.

| Level | What ships | Fall back when | What the demo still proves | Switch cost |
|---|---|---|---|---|
| **1 — Weakly-supervised VAD** *(MVP target)* | CLIP feature extractor + MIL-trained MLP head, live scores | — | Full claim: learned, generalising anomaly detection from weak labels | — |
| **2 — Supervised action classifier** | Same code path, same 1024-d features, but the head is retrained as a **supervised binary fight/no-fight classifier on RWF-2000** (2,000 five-second clips, ~2 GB, downloads and trains in ~40 min). > **Verify** published RWF-2000 accuracy ~87 %. | **T-20h**: Colab val AUC < 0.70, or ONNX export of CLIP fails | Real learned violence detection, live, with honest scope: "trained for violence, not 13 crime types". Every downstream system is unchanged. | ~2 h |
| **3 — Classical motion heuristics** | Farnebäck optical flow on 160×90 grayscale @ 8 fps → motion energy `E_t`; robust-z of `E_t` **AND** YOLOv8n-pose limb-speed rule from `taxonomy.py`. `SnippetScorer.push` returns `sigmoid(0.8·z_flow − 2)` instead of a network score. | **T-8h**: no trained head that beats chance, or the CLIP session will not fit alongside YOLO on this machine | The alerting pipeline is real and live; detection is explicitly rule-based. Fights and crowd surges still fire. Say "heuristic baseline" out loud. | ~3 h |
| **4 — Replayed pre-computed scores** | Score the demo videos **offline the night before** with whatever model you have, dump `{camera_id: [[t, score], ...]}` JSON, set `ANOMALY_SCORE_SOURCE=replay`. | **T-4h**: live scoring is unstable, or the demo machine cannot hold ANPR + VAD simultaneously | **Everything except the score source is genuine live code**: hysteresis, event creation, evidence cut, ffmpeg mux, Supabase upload, ANPR vehicle join, Realtime push, UI. That is ~85 % of the engineering. | ~30 min |

Two rules about level 4. First, **build it on day 1, not at T-4h** — it is 30 lines (already in `worker.py`) and it is your integration test harness for the entire downstream pipeline while the model is still training. Second, **`score_source` is a column in `anomaly_events` and it is surfaced in the UI**. If a judge asks whether scores are live, the answer is visible on screen. Presenting replayed scores as live inference is the one thing that will actually disqualify you.

---

### 12. UI surface (conforming to the project's visual law)

Two screens, both white, hairline-bordered, no colour except severity.

- **Alert rail** (right column of the map view, 380 px): a single-column list of `anomaly_events`, newest first, streamed over Supabase Realtime. Each row: uppercase 11 px letter-spaced type label (`FIGHT`), tabular-numeral timestamp, camera name, duration, peak score to 2 dp. A **4 px left border** carries the only colour in the row — priority 1 red, 2 amber, 3 grey. No badges, no pills, no icons.
- **Event detail**: 30-second `<video>` on the left (signed Supabase URL, `preload="metadata"`, poster = thumbnail); on the right a 1 Hz score sparkline (1 px stroke, near-black, with a hairline horizontal rule at `τ_on` and a light grey band marking the event window), then a dense table of linked plates — `PLATE`, `CONF`, `IN FRAME (S)`, `TRACK` — with plate text in a monospace face. Escalated events show a single-line rule above the table: `ESCALATED — CLONED PLATE MATCH`. Status control is a three-item segmented text control (`ACK / DISMISS / CONFIRM`), not buttons with fills.
- Below the video, one italic line of small grey type when `score_source = 'replay'`: `Scores replayed from offline pass — model version clip-b32-mlp-v1`.

---

## Appendix — Interface Contracts Declared by This Section

- `table: anomaly_events(id uuid pk, camera_id text fk->cameras.camera_id, started_at timestamptz, ended_at timestamptz, duration_s real generated, peak_score real, mean_score real, peak_z real, alert_type text check, type_confidence real, priority smallint 1..3, escalated boolean, status text check(new|ack|dismissed|confirmed), score_source text check(live|replay), model_version text, clip_path text, thumb_path text, geom geography(Point,4326), case_id uuid fk->cases.id, created_at timestamptz)`
- `table: anomaly_event_vehicles(event_id uuid fk->anomaly_events.id on delete cascade, track_id uuid, plate_text text, plate_confidence real, first_seen timestamptz, last_seen timestamptz, in_frame_seconds real, pk(event_id,track_id))`
- `table: anomaly_scores(camera_id text, ts timestamptz, score real, smoothed real, zscore real, pk(camera_id,ts)) - 6 hour retention`
- `CHECK constraint values for anomaly_events.alert_type: fight | accident | arson | weapon | abandoned_object | crowd_surge | anomaly_generic`
- `CHECK constraint values for anomaly_events.status: new | ack | dismissed | confirmed`
- `CHECK constraint values for anomaly_events.score_source: live | replay`
- `index: anomaly_events_started_idx (started_at desc); anomaly_events_camera_idx (camera_id, started_at desc); anomaly_events_open_idx partial where status='new'; anomaly_events_geom_idx gist(geom)`
- `DEPENDS ON (owned by ANPR section): table vehicle_tracks(track_id uuid, camera_id text, first_seen timestamptz, last_seen timestamptz, plate_text text, plate_confidence real) with index on (camera_id, first_seen, last_seen)`
- `DEPENDS ON: table cameras(camera_id text pk) - anomaly_events.camera_id is a FK to it`
- `DEPENDS ON: table cases(id uuid pk) - anomaly_events.case_id is a nullable FK to it`
- `DEPENDS ON: watchlist / cloned-plate flag reachable from plate_text, used to set anomaly_events.escalated and priority=1`
- `endpoint: POST /api/v1/anomaly/events -> body {id, camera_id, started_at, ended_at, peak_score, mean_score, peak_z, alert_type, type_confidence, priority, score_source, model_version}; server-side performs the vehicle overlap join into anomaly_event_vehicles`
- `endpoint: PATCH /api/v1/anomaly/events/{event_id}/evidence -> multipart files clip=clip.mp4 (video/mp4), thumb=thumb.jpg (image/jpeg); sets clip_path, thumb_path`
- `endpoint: PATCH /api/v1/anomaly/events/{event_id} -> body {status, case_id} (operator action)`
- `endpoint: GET /api/v1/anomaly/events?since=&camera_id=&type=&min_priority=&limit=`
- `endpoint: GET /api/v1/anomaly/events/{event_id} -> event + linked vehicles + signed clip URL`
- `endpoint: POST /api/v1/anomaly/scores:batch -> body {rows:[{camera_id, ts, score, smoothed, zscore}]}, flushed every 10 rows`
- `endpoint: GET /api/v1/anomaly/scores?camera_id=&since=`
- `Supabase Storage bucket: evidence (private); paths evidence/{camera_id}/{event_id}/clip.mp4 and evidence/{camera_id}/{event_id}/thumb.jpg; 30-day lifecycle purge unless case_id is not null`
- `Supabase Realtime: postgres_changes on anomaly_events (INSERT + UPDATE) is published; anomaly_scores is NOT published (frontend polls the REST endpoint every 5 s)`
- `env: ANOMALY_ENABLED, ANOMALY_EP (cpu|dml), ANOMALY_SCORE_SOURCE (live|replay), ANOMALY_REPLAY_PATH, ANOMALY_MODEL_VERSION, CLIP_ONNX_PATH, ANOMALY_HEAD_ONNX_PATH, ANOMALY_TAU_ON, ANOMALY_TAU_OFF, EVIDENCE_BUCKET, API_BASE_URL`
- `model artifact: models/clip_vitb32_image.onnx - CLIP ViT-B/32 vision tower, input [N,3,224,224] float32 NCHW named by sess.get_inputs()[0].name, output [N,512] pre-L2-norm`
- `model artifact: models/anomaly_head_v1.onnx - input 'feat' [n,1024] float32, output 'score' [n,1] sigmoid, opset 17, dynamic batch axis`
- `constant: feature_fps=4.0, snippet_frames=16, snippet_stride=4, embed_dim=512, head_input_dim=1024 (mean||std concat)`
- `constant: ema_alpha=0.35, tau_on=0.62, tau_off=0.45, z_gate=3.5, baseline_window_s=300, baseline_min_samples=60, min_on_scores=3, off_consec=2, cooldown_s=45, max_event_s=120, link_window_pad_s=3.0`
- `constant: ring_fps=12.0, ring_seconds=40.0, ring_width=1280, jpeg_quality=80, clip_pre_s=15.0, clip_post_s=15.0 (fixed 30 s evidence clip, cut at started_at+15 s regardless of event duration)`
- `python module paths: apps/worker/anomaly/{config,features,scorer,gate,evidence,taxonomy,worker}.py`
- `python API: AnomalyWorker.on_frame(frame_bgr, ts: float, tracks: list[dict], poses: list[dict]) - called by the shared ANPR decode loop for every decoded frame`
- `python API: track dict fields consumed by taxonomy.classify_event -> {cls: str, conf: float, bbox: [x0,y0,x1,y1], accel_mps2: float}; pose dict fields -> {bbox: [x0,y0,x1,y1], limb_speed_bh: float}`
- `replay trace file format: JSON {camera_id: [[stream_seconds, score], ...]} loaded when ANOMALY_SCORE_SOURCE=replay`
- `pip: install onnxruntime-directml ONLY - onnxruntime, onnxruntime-directml and onnxruntime-openvino all provide the module 'onnxruntime' and conflict; CPUExecutionProvider is included in the directml wheel`
- `evidence mux: ffmpeg image2pipe/mjpeg in -> libx264 -preset veryfast -crf 26 -pix_fmt yuv420p -movflags +faststart (cv2.VideoWriter mp4v is NOT browser-playable)`

## Appendix — MVP vs Stretch

- MVP: CLIP ViT-B/32 vision tower exported to ONNX and running on ORT CPUExecutionProvider at 4 fps per camera (GPU stays reserved for YOLO + PaddleOCR)
- MVP: SnippetScorer producing exactly one anomaly score per second from a 16-embedding rolling window, feature = concat(mean, std) = 1024-d
- MVP: EventGate with EMA smoothing, per-camera robust-z baseline, hysteresis (tau_on 0.62 / tau_off 0.45), 3-second minimum duration, 45-second cooldown, 120-second cap
- MVP: JpegRing evidence buffer + ffmpeg H.264 mux + upload of a fixed 30 s clip and a peak-frame thumbnail to the Supabase 'evidence' bucket
- MVP: anomaly_events, anomaly_event_vehicles and anomaly_scores tables created, with the server-side vehicle overlap join running inside POST /api/v1/anomaly/events
- MVP: alert_type assignment limited to fight, accident and anomaly_generic - the three that can be corroborated with signals that certainly exist by demo time
- MVP: ANOMALY_SCORE_SOURCE=replay path built and working on DAY 1, used as the integration harness for the whole downstream pipeline while the model trains
- MVP: score_source column surfaced in the UI so replayed scores are never presented as live inference
- MVP: MIL head trained on the 6-class UCF-Crime subset (Fighting, Assault, Abuse, RoadAccidents, Explosion, Arson) + ~400 Normal videos, with frame-level AUC reported on the official 290-video test split
- MVP: Supabase Realtime subscription on anomaly_events driving the alert rail; anomaly_scores polled, not streamed
- MVP: escalation to priority 1 when a linked plate carries a watchlist or cloned-plate flag
- STRETCH: ANOMALY_EP=dml - move the CLIP session onto DirectML after measuring VRAM headroom alongside YOLO and PaddleOCR
- STRETCH: temporal-context score head (1D conv or self-attention over the last 8 snippet features) instead of the per-snippet MLP
- STRETCH: RTFM feature-magnitude loss on top of the top-k BCE objective
- STRETCH: weapon alert_type backed by a dedicated YOLOv8n gun/knife head
- STRETCH: arson (fire/smoke flicker) and abandoned_object (static blob persistence) detectors
- STRETCH: VideoMAE-Base features replacing CLIP, trained on Colab and benchmarked against the CLIP baseline AUC
- STRETCH: OpenVINO INT8 quantisation of the CLIP tower in a separate venv (blocked by the onnxruntime wheel conflict in the main env)
- STRETCH: XD-Violence as a second training corpus, reporting AP rather than AUC

## Appendix — Risks

- The single 4 GB AMD GPU cannot hold YOLOv8 + PaddleOCR + CLIP DirectML sessions plus the Windows compositor. Mitigation: CLIP runs on the CPU EP by default (only 4 frames/s to embed, ~10-15% of one core per camera); at most two DirectML sessions and both must live in one process, never two.
- onnxruntime, onnxruntime-directml and onnxruntime-openvino all install a module named 'onnxruntime' and silently break each other. Mitigation: install onnxruntime-directml only - CPUExecutionProvider ships inside that wheel; keep OpenVINO experiments in a separate venv.
- Frame-level AUC on the reduced 6-class CLIP-feature setup may land well below the published I3D numbers, making the learned scores useless. Mitigation: the decision rule is explicit - AUC >= 0.80 ship full taxonomy, 0.70-0.80 ship with raised tau_on and anomaly_generic only, < 0.70 drop to ladder level 2 immediately and stop spending hours.
- UCF-Crime download is ~90 GB in total and will eat the entire Colab session. Mitigation: pull only the six class folders plus a 400-video Normal sample (~25 GB), and cache snippet features as .npy so head retraining costs under 5 minutes per run.
- Video decode, not GPU inference, is the Colab bottleneck for feature extraction. Mitigation: budget 90-120 minutes for the decode+embed pass, use PyAV/decord with 8 worker threads, and never re-decode - all hyper-parameter search runs on the cached features.
- Sigmoid scores are not calibrated across cameras: a narrow alley baselines higher than an empty flyover, so a global threshold either spams one camera or silences another. Mitigation: AND-gate the absolute threshold with a per-camera robust z-score (median/MAD over a 300-sample window), and require both.
- A single fixed threshold makes events flap open and closed while the subject is occluded, producing dozens of rows per incident. Mitigation: hysteresis with a 0.17 gap between tau_on and tau_off, 3-second minimum duration, 2-second off-run, and a 45-second per-camera-per-type cooldown.
- Raw-frame evidence buffering would consume 1.5 GB per camera on a 16 GB machine. Mitigation: ring buffer stores JPEG-encoded 1280-wide frames at 12 fps (~43 MB per camera, ~173 MB for four).
- cv2.VideoWriter with the mp4v fourcc produces MP4s that Chrome and Safari refuse to play inline, which silently kills the evidence panel. Mitigation: mux through ffmpeg with libx264, -pix_fmt yuv420p and -movflags +faststart.
- Clip length tied to event duration means a 110-second event holds 145 seconds of ring buffer per camera and the operator waits two minutes for evidence. Mitigation: fixed 30-second clip cut at started_at + 15 s, decoupled from event close.
- Claiming 13-way UCF-Crime classification in front of a judge who knows the dataset (published video-level accuracy is roughly 30-40%) destroys credibility. Mitigation: ship a binary learned score plus a deterministic, explainable type-assignment stage, and default honestly to anomaly_generic.
- Presenting replayed pre-computed scores as live inference is a disqualifying misrepresentation. Mitigation: score_source is a first-class column, rendered on the event detail screen, and the fallback level is stated aloud during the demo.
- The vehicle-linking join returns nothing if the ANPR section names its table or columns differently. Mitigation: vehicle_tracks(track_id, camera_id, first_seen, last_seen, plate_text, plate_confidence) is a named cross-section contract; reconcile it on day 1, not at integration time.
- Streaming 1 Hz scores for N cameras over Supabase Realtime will saturate the browser websocket. Mitigation: publish only anomaly_events over Realtime; the score sparkline polls GET /api/v1/anomaly/scores every 5 s.
- Evidence clips capture uninvolved bystanders, creating a DPDP Act 2023 exposure. Mitigation: 30-day storage lifecycle purge unless the event is attached to a case, pose keypoints only with no face recognition or biometric template anywhere in the VAD path, and state this proactively.
- Snippet length or normalisation constants drifting between the Colab training script and the worker puts the head permanently out of distribution with no error message. Mitigation: snippet_frames=16 at feature_fps=4.0 and the CLIP mean/std triples are frozen in config.py and imported by both the training notebook and the worker.

## Appendix — Open Questions

- Does the ANPR worker expose per-track kinematics (accel_mps2) and YOLOv8-pose keypoints on the same decoded frame the anomaly worker sees, or does the anomaly worker need to run its own pose pass? taxonomy.classify_event assumes the former; if not, fight/accident typing degrades to anomaly_generic.
- Who owns the shared decode loop that calls AnomalyWorker.on_frame - the ANPR worker process or a separate frame-fanout service? This determines whether CLIP and YOLO can share one DirectML device or must contend across processes.
- What is the actual measured CLIP ViT-B/32 latency on this RX 6500M / laptop CPU (bench_clip.py, day 1)? Every FPS claim in this section is an estimate until that runs.
- Is there a cases table and a watchlist/cloned-plate flag by the time VAD integrates, or should escalation be deferred and anomaly_events.case_id left nullable-unused for the demo?
- Does the demo footage actually contain a fight, a collision and a normal-traffic baseline on separate simulated camera_ids with usable time offsets? If not, the score trace has nothing to fire on and the whole taxonomy is untestable.
- Should anomaly_scores be written at all during the demo, or is the sparkline worth dropping to save a write path? 1 Hz x N cameras x 6 hours is small but the endpoint is pure cosmetics.
- Confirm the exact supabase-py v2 storage upload signature (file_options vs a plain dict) before wiring the evidence PATCH handler.
- Is RWF-2000 downloadable without a request form? If the ladder level 2 dataset needs manual approval, level 2 is not actually reachable inside 36 hours and the ladder collapses from 4 rungs to 3.
