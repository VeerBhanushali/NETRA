# Plan — Threat Detection & Wanted-Person Recognition

Extending NETRA from *what vehicle* to *what is happening* and *who is
there*. Written against measured numbers from this machine, not estimates.

---

## 0. The finding that changes everything: the GPU works

Until now the whole pipeline ran on CPU because the RX 6500M has no CUDA
and ROCm does not support gfx1034. **DirectML does.** Measured today:

| Model | CPU | DirectML | Gain |
|---|---|---|---|
| YOLOv8n — vehicles | 59.4 ms | **18.9 ms** | **3.2×** |
| YOLO11n — plates | 54.9 ms | **11.1 ms** | **5.0×** |

```bash
pip uninstall -y onnxruntime        # installing both silently kills DirectML
pip install onnxruntime-directml
```

Both models are already exported to `models/onnx/*.onnx`.

**What this buys us:** detection currently costs ~115 ms/pass on CPU
(vehicle + plate). On DirectML that becomes ~30 ms, and it runs on a GPU
that is otherwise **completely idle**. That freed CPU is exactly the
budget threat detection and face recognition need.

**The honest caveat:** EasyOCR is PyTorch, not ONNX, so it does *not*
move to the GPU by installing this. OCR is the real bottleneck at 379 ms
and stays on CPU until its recognition model is exported to ONNX too
(Phase 4). So the near-term win is: detection to GPU, CPU freed for
faces.

---

## 1. What we are adding

### A. Wanted-person recognition (face)
Detect faces, match against an enrolled watchlist of wanted or missing
persons, and raise an alert with evidence — the same lifecycle vehicles
already have.

### B. Threat detection
Weapons in frame, violence, and abandoned objects — behaviour rather than
identity.

Both reuse infrastructure that already exists: the alerts table, the
rules engine, evidence storage, the audit log, the review queue and the
SSE feed. **This is an extension, not a second system.**

---

## 2. Face recognition — the design

### The pipeline
```
frame ─▶ SCRFD face detector ─▶ align (5-point) ─▶ ArcFace embedding (512-d)
                                                        │
                                              cosine match vs gallery
                                                        │
                              ≥0.65 alert  ·  0.45–0.65 review  ·  <0.45 ignore
```

### Models — all ONNX, all DirectML-capable
| Role | Model | Size | Notes |
|---|---|---|---|
| Face detect | **SCRFD-500M** (InsightFace `buffalo_s`) | ~3 MB | ~15 ms on DirectML |
| Embedding | **ArcFace w600k_r50** (`buffalo_l`) | ~92 MB | 512-d, ~20 ms/face |

`pip install insightface onnxruntime-directml` — models auto-download.
No training required; ArcFace is already trained on millions of
identities and generalises to Indian faces.

### The differentiator: temporal voting on faces

This is the important design decision. **A single-frame face match is
unreliable at CCTV distance and angle** — exactly the problem we already
solved for plates.

So we reuse the same engine: accumulate embeddings across every frame of
one *tracked person*, weight each by face quality (size, sharpness,
yaw/pitch, detector confidence), and match the **averaged embedding**
rather than any single frame.

```python
# One tracked person, N frames -> one decision.
emb = normalise(sum(q_i * e_i for i in frames) / sum(q_i))
score = cosine(emb, gallery)
```

Averaging L2-normalised embeddings suppresses per-frame noise the same
way character voting suppresses OCR noise. It is the same idea, applied
to a different modality — and it is what lets us claim a *calibrated*
match rather than a raw similarity number.

### Person tracking
ByteTrack already runs. Add COCO class 0 (person) to the tracked classes
— zero extra model cost, since it is the same YOLO forward pass.

### Thresholds — to be calibrated, not guessed
Start at 0.65 cosine for alert and 0.45 for review, then calibrate on a
held-out set exactly as we did for plates. **Report false-accept rate,
not just accuracy** — a face system's failure mode is accusing the wrong
person.

---

## 3. Threat detection — the design

### Weapons
A YOLOv8 fine-tuned on gun/knife datasets (Roboflow Universe has several
with permissive licences). Runs as a third detector on the same frame.
DirectML cost ~15 ms.

**Expect a high false-positive rate.** Phones, tools and dark objects
read as handguns constantly. Mitigations: require the detection to
persist across N frames (temporal voting again), require a person box to
overlap it, and route every weapon alert to human confirmation — never
auto-escalate.

### Violence / fight detection
Two-tier, because the good version needs training we cannot finish:

1. **Heuristic (works now, no training):** optical-flow motion energy +
   YOLO-pose keypoints. Sudden high-magnitude, high-entropy motion among
   ≥2 overlapping person boxes. Cheap, explainable, catches obvious
   brawls.
2. **Learned (stretch):** VideoMAE/RTFM features + MIL head trained on
   UCF-Crime in Colab, exported to ONNX. The `anomaly_events` table and
   ingest endpoint already exist for this.

### Abandoned object
A static, person-free object box persisting >N seconds in a monitored
zone. Pure geometry over existing detections — nearly free.

---

## 4. Cost model — what actually fits

Per analysed frame, on DirectML:

| Stage | Cost | Notes |
|---|---|---|
| Vehicle + person detect | 19 ms | one YOLO pass, both classes |
| Plate detect | 11 ms | only on vehicle crops |
| Plate OCR | **379 ms** | CPU — the bottleneck |
| Face detect | ~15 ms | GPU |
| Face embed | ~20 ms/face | GPU |
| Weapon detect | ~15 ms | GPU |

**The conclusion this forces:** GPU work is nearly free; **OCR dominates
everything**. So faces and threats should run on cameras that are *not*
doing plate recognition, or on the same camera at a lower cadence.

### Recommended configuration
| Camera | Role |
|---|---|
| cam-01, cam-02 | ANPR (as today) |
| cam-03 | **Face + threat**, no ANPR |

Adding a face camera costs ~50 ms/pass on the GPU — it does not compete
with OCR at all. This is why the GPU finding matters: it makes the
feature affordable *without* taking anything away from plates.

---

## 5. Schema additions

```sql
-- Enrolled persons. Deliberately minimal: no address, no phone, no
-- free-text notes that could become an unaccountable dossier.
CREATE TABLE persons (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    case_ref    TEXT,                    -- FIR / missing-person reference
    category    TEXT NOT NULL CHECK (category IN ('wanted','missing','person_of_interest')),
    added_by    TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    expires_at  TEXT,                    -- enrolment is time-bounded
    is_active   INTEGER NOT NULL DEFAULT 1
);

-- One row per enrolled photo. The embedding is stored, the photo is not
-- required to be: a template cannot be reversed into a face.
CREATE TABLE person_faces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT NOT NULL REFERENCES persons(id),
    embedding   BLOB NOT NULL,           -- 512 float32, L2-normalised
    quality     REAL NOT NULL,
    source      TEXT,
    created_at  TEXT NOT NULL
);

-- A match, before a human has judged it.
CREATE TABLE face_matches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT REFERENCES persons(id),
    camera_id   TEXT NOT NULL REFERENCES cameras(id),
    track_id    TEXT NOT NULL,
    score       REAL NOT NULL,           -- cosine similarity
    frame_count INTEGER NOT NULL,        -- frames that voted
    crop_path   TEXT,
    seen_at     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','confirmed','rejected')),
    reviewed_by TEXT,
    reviewed_at TEXT
);
```

`alert_type` gains: `wanted_person`, `missing_person`, `weapon`,
`violence`, `abandoned_object`.

---

## 6. Privacy — non-negotiable for biometrics

Number plates identify a *vehicle*. Faces identify a *person*, and that
is a categorically bigger intrusion. Under India's DPDP Act 2023 facial
templates are sensitive personal data. The safeguards are not decoration;
without them this feature should not ship.

1. **No face is matched unless it is on the watchlist.** We do not build
   a general face database. Non-matching faces produce an embedding that
   is compared and immediately discarded — never stored, never logged.
2. **Enrolment is authorised and expiring.** Every person carries a case
   reference, an enroller and an expiry. No open-ended enrolment.
3. **Every match is human-confirmed before it becomes an alert.** A face
   match is a *lead*. No automated enforcement, ever.
4. **Faces of non-matching bystanders are blurred** in any retained
   evidence clip or crop.
5. **The full audit trail applies** — who enrolled whom, who viewed a
   match, who confirmed it, with a stated reason.
6. **Report the false-accept rate** alongside accuracy, and state the
   known demographic bias of face recognition rather than hiding it.

A judge asking "what stops this becoming mass surveillance?" should get
points 1 and 3 as the answer.

---

## 7. Phased plan

| Phase | Work | Effort | Risk |
|---|---|---|---|
| **1** | Move detection to DirectML — export done, swap the session factory | 2 h | low — measured, 3-5× |
| **2** | Person tracking (add COCO class 0) + SCRFD face detect, drawn on the live wall | 3 h | low |
| **3** | ArcFace embeddings + gallery matching + temporal voting on embeddings | 5 h | medium — needs threshold calibration |
| **4** | `persons` / `person_faces` / `face_matches` schema, enrolment UI, match review screen | 5 h | low — mirrors the plate review queue |
| **5** | Weapon detector + persistence gating | 3 h | **high** — false positives |
| **6** | Violence heuristic (optical flow + pose) | 4 h | medium |
| **7** | *Stretch:* export EasyOCR recognition to ONNX → OCR on GPU | 6 h | high — would remove the 379 ms bottleneck entirely |
| **8** | *Stretch:* trained VAD (VideoMAE + MIL on UCF-Crime, Colab) | 8 h+ | high |

**Recommended for the hackathon: Phases 1–4 plus 6.** That gives working
wanted-person recognition with the temporal-voting differentiator, a
believable violence heuristic, and an honest "weapons detection is
next" line — rather than a weapon detector that fires on a mobile phone
during the demo.

---

## 8. What we can and cannot claim

**Can:**
- Real face detection and matching against an enrolled watchlist, on GPU
- Temporal voting on face embeddings — the same idea that makes our plate
  reading defensible, applied to a second modality
- Full accountability: enrolment, audit, human confirmation, expiry
- Violence detection by motion heuristic, explainable in one sentence

**Cannot (and should say so):**
- Recognise arbitrary people — only enrolled persons, by design
- Reliable weapon detection without a curated dataset and calibration
- Trained video anomaly detection inside the hackathon window
- Face recognition at the distances our current traffic clips were shot
  at — faces need ~80+ px between the eyes, which means a camera placed
  for people, not for cars

**The last point is important and easy to miss:** the ANPR footage we
have is shot for vehicles. Face recognition needs different footage. We
will need a separate clip — a pedestrian or entrance camera — to
demonstrate it honestly.
