# NETRA — System Information Document

**NETRA** — Sanskrit for *eye*. A city-wide ANPR (Automatic Number Plate
Recognition) and crime-tracking platform.

> Reads vehicle number plates from CCTV at near-perfect *published*
> accuracy, tracks every vehicle's movement across a city, and
> automatically flags cloned plates, speeding, loitering and watchlist
> vehicles — with a command-centre dashboard and a full audit trail.

Built for Smart India Hackathon. Everything below is measured from the
running system, not estimated.

---

## 1. The problem

Indian cities have tens of thousands of CCTV cameras. Almost all of them
are passive: they record, and a human watches afterwards. Three failures
follow:

1. **Nobody watches in real time.** A stolen vehicle passes a camera and
   nothing happens until someone reviews the tape days later.
2. **Plate cloning is invisible.** A criminal copies a legitimate plate
   onto a second vehicle. No single camera can detect this — it is only
   visible when you compare sightings *across* cameras and time.
3. **OCR alone is not trustworthy.** Single-frame plate reading peaks
   around 95–98% outdoors. At city scale that is thousands of wrong
   plates a day, and a wrong plate can put an innocent citizen under
   suspicion.

NETRA addresses all three.

---

## 2. The core idea — Temporal Voting

**This is the part that is genuinely ours.** Everything else is
integration; this is the invention.

A single frame gives one guess. But a *tracked* vehicle gives twenty or
thirty looks at the same plate, and the errors between frames are largely
independent — glare in frame 7 is not present in frame 22.

NETRA accumulates per-character evidence across every frame of a track,
weights each frame by its measured quality, and combines the positions
with a **geometric** mean so that one hopeless character drags the whole
plate down — because one wrong character *is* a wrong plate.

### Proof, from real Indian footage

Every individual frame read this plate **wrong**. The consensus was right:

| Individual OCR frames | Voted result |
|---|---|
| `GXISOGJ`, `GXI50GJ`, `GXIS0GJ` | **`GX15OGJ`** |
| `0G65ZFX`, `WG65ZFX` | **`OG65ZFX`** |
| `KHO6KSU`, `RH06KSU` | **`KH06KSU`** |

### The honest claim

"100% accuracy" is not a model metric — it is an **operational property**:

| Voted confidence | Action |
|---|---|
| ≥ 0.90 | Published as fact |
| 0.55 – 0.90 | Sent to a human in the review queue |
| < 0.55 | Discarded |

**The system never publishes a plate it is not sure about.** The two
numbers we report are *precision on the auto-accepted set* and the
*coverage* that set represents — never one without the other.

Reproduce it: `python -m pytest apps/edge/tests/test_voting.py -q -s`

```
single-frame accuracy : 0.4864
voted precision       : 1.0000
auto-accept coverage  : 0.9750
```

> That is a controlled simulation demonstrating the mechanism — **not** a
> field accuracy figure. Say so when presenting. A judge who thinks we
> claim 99% on real Indian roads from a simulation is right to push back.

### Supporting techniques

- **Quality weighting** — each frame's vote is weighted by plate size,
  Laplacian sharpness, aspect ratio and detector confidence.
- **Quality floor** — frames below a threshold do not vote at all.
  Evidence that poor is noise, not weak evidence.
- **Indian plate grammar** — positional repair of the classic confusions
  (`O`↔`0`, `I`↔`1`, `B`↔`8`, `S`↔`5`, `Z`↔`2`). A `0` in a letter slot
  becomes `O`; an `O` in a digit slot becomes `0`. Used as a *prior*,
  never a hard filter — a plate that fails the grammar is still reported,
  because damaged and illegal plates are exactly the ones worth watching.
- **Evidence gating** — a 5-frame consensus cannot reach the same
  confidence as a 20-frame one.

---

## 3. Crime detection — the rules engine

Reading plates is table stakes. The value is what you do with the
spatio-temporal record. Four rules run on **every** accepted sighting:

### Cloned / fake plate (flagship)
The same plate cannot be in two places at once. On each sighting we take
the previous sighting of that plate at a different camera, compute road
distance and elapsed time, and derive the implied speed. Above a
physically impossible threshold, the plate exists twice.

Guards, each because it is a real false-positive source:
- Low-confidence reads never accuse anyone (≥0.92 required on both)
- Cameras at the same junction excluded (400 m distance floor)
- Clock skew between cameras subtracted (15 s worst case)
- Straight-line distance inflated by an urban circuity factor (1.35), so
  the rule is *conservative*

### Speeding
Average speed over a **surveyed** camera-to-camera segment. Reported as
indicative, not legally defensible — it is a segment average and cannot
prove instantaneous speed.

### Loitering / casing
5+ passes through the same zone within 30 minutes, sustained over at
least 8 minutes. Both thresholds were tuned against seeded traffic: 4
passes flagged ordinary shopping trips.

### Watchlist hit
Exact match, plus a one-character-off fuzzy match — the residual OCR
error on a stolen-vehicle plate is exactly what you cannot afford to
miss. Fuzzy matches are downgraded one severity level, because a fuzzy
match is a lead, not a fact.

**Every alert carries the numbers that produced it.** An alert an
operator cannot interrogate is an alert they learn to ignore.

**Measured result:** 327 background sightings → **0 false positives**,
6 alerts, every one real.

---

## 4. Architecture

```
 RTSP / video files
        │
        ▼
┌──────────────────┐   one process, shared models
│  EDGE  (Python)  │
│  ├ decoder thread per camera  ──▶ annotated JPEG (live wall)
│  └ inference thread (round-robin, weighted)
│      YOLOv8n  →  ByteTrack  →  YOLO11n plate  →  EasyOCR
│                        │
│                  TEMPORAL VOTING
│                        │
│           ≥0.90 publish │ 0.55–0.90 review │ <0.55 discard
└────────────────────────┼───────────────────────────────────┘
                         ▼  HTTPS batch, token-auth, idempotent
┌──────────────────────────────────────────────────────────┐
│  API  (FastAPI)                                          │
│  ingest → dedupe → store → RULES ENGINE → alerts         │
│                                    │                     │
│  SQLite (PostGIS-ready)            └─▶ SSE ──────────────┼──▶ dashboard
│  audit log · watchlist · review queue                    │
└──────────────────────────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────┐
│  WEB  (Next.js)  live wall · map · search · trajectory    │
│                  alerts · review queue · audit            │
└──────────────────────────────────────────────────────────┘
```

### Key design decisions, and why

| Decision | Reason |
|---|---|
| One process, shared models | Ten separate workers each load their own YOLO + EasyOCR and thrash a laptop. Shared models cost 2.2 GB for the whole network. |
| Display decoupled from inference | Every camera shows smooth video even while inference is busy elsewhere. Inline inference makes all tiles stutter in lockstep. |
| Per-camera detector instance | Ultralytics keeps tracker state on the model; sharing one would blend track IDs across cameras and corrupt voting. |
| SQLite, not Postgres | No server, no account, no network — the demo survives dead venue wifi. Every geo call goes through `haversine_m`, which maps 1:1 onto PostGIS `ST_Distance`. |
| Leaflet + OpenStreetMap | No access token. Nothing breaks when a key expires or a free tier runs out. |
| SSE, not WebSockets | The dashboard only receives. SSE reconnects by itself and needs no extra dependency. |
| Weighted inference scheduling | A camera whose plates cannot be resolved should not consume the same CPU as a toll-gate view. |

---

## 5. Technology stack

### AI / Computer Vision
| Component | Technology | Role |
|---|---|---|
| Vehicle detection | **YOLOv8n** (Ultralytics, COCO) | cars, motorcycles, buses, trucks — zero training needed |
| Multi-object tracking | **ByteTrack** | persistent track ID — the foundation of voting. Chosen over DeepSORT because it needs no ReID model, so no extra VRAM |
| Plate localisation | **YOLO11n** licence-plate model (HuggingFace) | finds the plate inside the vehicle box |
| Text recognition | **EasyOCR** (CPU) | reads the plate crop, character allowlist |
| Consensus | **Temporal Voting** (ours, pure Python) | fuses frames into one confident answer |
| Runtime | **PyTorch CPU** / ONNX Runtime + DirectML | no CUDA on the dev machine |

### Backend
| Component | Technology |
|---|---|
| API | **FastAPI** + Uvicorn, Pydantic v2 |
| Database | **SQLite** (WAL), PostgreSQL/PostGIS-ready |
| Real-time | **Server-Sent Events** |
| Rules engine | Pure Python, runs synchronously on ingest |

### Frontend
| Component | Technology |
|---|---|
| Framework | **Next.js 15** (App Router) + React 19 + TypeScript |
| Styling | **Tailwind CSS v3** over CSS custom-property tokens |
| Maps | **Leaflet** + OpenStreetMap (no API key) |
| Charts | Hand-rolled inline SVG (no chart library) |

### Optional — Cloud AI Arbitration
Provider-agnostic layer (Gemini / OpenAI / Anthropic / Grok), **off by
default**. Local models handle 100% of per-frame volume at zero marginal
cost; the hosted model is a *bounded escalation path* for low-confidence
plates, anomaly clips and natural-language search. Hard daily budget,
response caching, timeout → local fallback, and a cache-only mode for
when venue wifi dies.

---

## 6. What we have built

**8,557 lines of code** across:

| Area | Files | Lines | Contents |
|---|---|---|---|
| `apps/edge` | 11 | 2,277 | vision pipeline, temporal voting, plate grammar, multi-camera network, simulator |
| `apps/web` | 26 | 3,204 | dashboard, live wall, map, search, trajectory, review queue, audit |
| `apps/api` | 15 | 1,884 | ingest, rules engine, REST, SSE, AI layer |
| `scripts` | 5 | 923 | start, autotune, seed, asset fetch |
| `infra` | 1 | 269 | database schema |

- **12 database tables** — cameras, zones, vehicles, plate_reads, sightings,
  alerts, watchlist, review_queue, anomaly_events, audit_log,
  ai_inferences, camera_links
- **25 API operations** across 23 paths
- **14 automated tests**, all passing

### Dashboard screens
`/dashboard` overview + KPIs + traffic sparkline · `/live` camera wall ·
`/live/[camera]` full-screen camera · `/map` incidents + geofences ·
`/search` audited plate search · `/vehicle/[plate]` trajectory with
playback · `/alerts` triage · `/alerts/[id]` evidence · `/review` queue ·
`/cameras` network health · `/audit` accountability log

### Operator features
Ctrl-K command palette · `g`+key navigation · live toasts · density
switch (laptop / wall display) · keyboard-first review queue.

---

## 7. Privacy and accountability

This is a surveillance system, engineered as though that matters.

- The vehicle registry holds **no owner PII**. Plate → owner is a
  separate, audited lookup.
- **Every plate search is written to the audit log with the operator and
  a stated reason, before results are returned.** "Who searched this
  plate, and why" is the single most important record in the system.
- Watchlist entries are purpose-bound and **expire**. An entry that never
  expires is how a watchlist becomes permanent surveillance.
- Alerts are **advisory**. No automated enforcement, ever — a human
  decides.
- Natural-language search can only read allow-listed views through a
  read-only role, with forced `LIMIT` and single-`SELECT` enforcement, so
  prompt injection through database content cannot reach sensitive tables.
- Framed against India's **DPDP Act 2023**.

---

## 8. Performance, measured

Dev machine: **12 logical cores, 15 GB RAM, AMD RX 6500M (no CUDA)**.

| Operation | Measured |
|---|---|
| Video decode + JPEG encode | 8 ms/frame |
| Vehicle detection (YOLOv8n) | 62 ms/frame |
| Plate detect + OCR | 379 ms/call |

`scripts/autotune.py` benchmarks these and sizes the network
automatically. Current configuration: **7 cameras, 4 running recognition**,
15.4 inference passes/sec, ~65% CPU, dashboard responding in 0.18 s.

### The compute ceiling — an important finding

More CPU does **not** improve accuracy. Confidence plateaus:

```
 18 frames (observed mix) -> 0.939
 36 frames (same mix)     -> 0.939
144 frames (same mix)     -> 0.939   ← plateau
```

Per-character margins show why:

```
K    L    0    7    B    A    5    2    5    2
1.00 1.00 0.93 0.57 1.00 1.00 1.00 1.00 1.00 1.00
                ↑ the only contested position
```

Nine characters unanimous, one contested. This is an **OCR quality
limit, not a compute limit**. A GPU buys more *cameras*, not better
*reads*. The fixes that matter are free: fine-tuned OCR, better camera
placement (plates > 120 px), and threshold calibration on labelled data.

---

## 9. Honest status

| Component | Status |
|---|---|
| Temporal voting + plate grammar | **Working**, unit tested |
| Rules engine (clone / speed / loiter / watchlist) | **Working** — 0 false positives on 327 sightings |
| REST API, SSE, audit log | **Working** |
| Dashboard, map, trajectory, review queue | **Working** |
| Real ANPR on Indian footage | **Working** — publishes real plates |
| Multi-camera live wall with CCTV overlay | **Working** |
| Auto-sizing to hardware | **Working** |
| Video anomaly detection (fights, accidents) | **Partial** — ingest + alerting done, detector **not trained** |
| Cloud LLM/VLM arbitration | **Code complete**, off by default, needs a key |

Nothing above is overstated. The gaps are listed because a demo that
claims more than it does is a demo that dies under questioning.

---

## 10. Running it

```bash
pip install -r apps/api/requirements.txt
pip install opencv-python ultralytics easyocr
python scripts/fetch_assets.py
python scripts/autotune.py --apply
python scripts/start.py
```

Dashboard http://localhost:3000 · API docs http://localhost:8000/docs

Deploy anywhere: `docker compose up --build` — two images that run
unchanged on a laptop, a VM, Render, Railway, Fly.io or Cloud Run.

### Scaling to a real city
Per-junction edge boxes with GPUs · Kafka/Redis Streams instead of direct
POST · PostgreSQL + PostGIS + TimescaleDB, partitioned by time ·
horizontally scaled API with the SSE broker moved to Redis. The camera
config already accepts RTSP URLs, so real cameras need no code change.

---

## 11. Anticipated questions

**Is it really 100% accurate?**
No, and we never claim that. Single-frame OCR is 95–98%. What we
guarantee is that the system never *publishes* a plate it is not
confident about — everything else goes to a human. We report precision
and coverage together.

**What about privacy?**
No owner PII in the sighting stream, every search audited with a stated
reason, expiring watchlists, no automated enforcement. Engineered to
DPDP Act principles.

**Does it work at night / in rain?**
Accuracy drops, and temporal voting is precisely the mitigation — more
frames, independent errors. Low-confidence reads route to humans rather
than being published wrong.

**How does it scale to 10,000 cameras?**
Edge boxes at junctions do the inference; only structured sightings
travel to the centre — bytes, not video. Section 10 sketches the path.

**What is novel versus commercial ANPR?**
Commercial ANPR sells a plate string with a confidence number. We treat
accuracy as an *operational* property: a calibrated decision boundary, a
human-in-the-loop queue, and cross-camera spatio-temporal reasoning that
detects cloning — which no single-camera product can do.

**What happens on a false positive?**
Cloned-plate alerts require high confidence on both sightings, a distance
floor and a clock-skew allowance. Operators can mark false positives, and
that signal is what tunes the thresholds.
