# NETRA — City-wide ANPR & Crime Tracking Platform

Reads vehicle number plates from CCTV at near-perfect published accuracy,
tracks every vehicle's movement across a city, and automatically flags
cloned plates, speeding, loitering, watchlist vehicles and behavioural
anomalies — with a command-centre dashboard and an audit trail.

Built for Smart India Hackathon.

---

## The accuracy claim, stated honestly

Single-frame plate OCR peaks at roughly 95–98% outdoors. Mud, rain,
glare, motion blur and shallow angles are physical facts, not model
defects. NETRA does not pretend otherwise.

Instead, a tracked vehicle gives many independent looks at the same
plate. **Temporal voting** accumulates per-character evidence across
those frames, weighted by frame quality, and produces a consensus plus a
calibrated confidence. Then:

| Voted confidence | What happens |
|---|---|
| ≥ 0.90 | published as fact |
| 0.55 – 0.90 | sent to a human in the review queue |
| < 0.55 | discarded |

So "production 100%" is an **operational property, not a model metric**:
the system never publishes a plate it is not sure about. The measurable
claims are precision on the auto-accepted set *and* the coverage that set
represents — neither is ever reported without the other.

Reproduce the numbers yourself:

```bash
python -m pytest apps/edge/tests/test_voting.py -q -s
```

On simulated 30-frame tracks at a 12% per-character error rate:

```
single-frame accuracy : 0.4864
voted precision       : 1.0000
auto-accept coverage  : 0.9750
```

That is a controlled simulation demonstrating the mechanism — **not** a
field accuracy figure. Say so when you present it; a judge who thinks you
are claiming 99% on real Indian roads from a simulation is right to push
back.

---

## Run it

Nothing here needs a cloud account, an API key, or a GPU.

```bash
pip install -r apps/api/requirements.txt
python scripts/seed_demo.py --reset
```

```bash
python -m uvicorn app.main:app --app-dir apps/api --port 8000
```

```bash
cd apps/web && npm install && npm run dev
```

Open **http://localhost:3000**. API docs at http://localhost:8000/docs.

### Run the whole camera network

```bash
python scripts/fetch_assets.py
python scripts/run_cameras.py
```

Every camera in `infra/cameras.json` starts as its own process running real
ANPR. Each has a **playlist** of clips played back to back, so the wall
shows changing traffic rather than one clip on repeat. Open **/live** for
the wall and click any tile for the full-screen camera view.

On the frame itself: a **green box marks the located plate** and the label
above the vehicle shows the plate string with its current confidence —
amber while it is still being voted on, green once it crosses the
threshold and is written to the database.

To use your own footage or a real camera, add it to `infra/cameras.json`:

```json
{ "id": "cam-07",
  "sources": ["D:/recordings/gate_2h.mp4",
              "rtsp://user:pass@10.0.0.5:554/stream1"] }
```

Relative paths resolve from the repo root; anything with `://` is passed
straight to OpenCV, so an RTSP or HTTP stream needs no other change. Set
`"loop": false` once your footage is long enough to fill the demo.

### Run one camera by hand

```bash
pip install opencv-python ultralytics easyocr
python scripts/fetch_assets.py
```

```bash
cd apps/edge && NETRA_PLATE_REGION=UK NETRA_EDGE_FPS=7 python -m edge --mode anpr --source ../../datasets/demo_clips/anpr_sample.mp4 --camera cam-01
```

YOLOv8 detects and tracks vehicles, a YOLO11n licence-plate detector
localises the plate, EasyOCR reads it, and temporal voting fuses the
reads. Open **/live** to watch the annotated frames the model is actually
producing, and **/review** to see the plates it was not confident enough
to publish.

This is where the voting engine earns its place. On real footage the
per-frame reads are frequently wrong, and the consensus is right anyway:

| What individual frames read | What voting published |
|---|---|
| `GXISOGJ`, `GXI50GJ`, `GXIS0GJ` | **`GX15OGJ`** |
| `0G65ZFX`, `WG65ZFX` | **`OG65ZFX`** |
| `KHO6KSU`, `RH06KSU` | **`KH06KSU`** |

`NETRA_PLATE_REGION` selects the plate grammar: `IN` (default, Indian
RTO/Bharat), `UK` (the test footage), or `ANY`. Getting this wrong makes
every correct read look malformed, so set it to match your footage.

### Stream synthetic traffic (no models needed)

```bash
cd apps/edge && python -m edge --mode simulate --rate 3 --incident clone
```

Synthetic pixels, but the *real* voting engine and the *real* ingest API.
Watch the dashboard update live and the cloned-plate alert fire on cue.

### Using the console

- `Ctrl-K` — command palette; type a partial plate to jump to its trajectory
- `g` then `d` / `l` / `m` / `a` / `s` / `r` / `c` / `u` — jump to a section
- Density control (bottom-left) switches between laptop and wall-display sizing

### Deploy anywhere

```bash
docker compose up --build
```

Two images — API and dashboard — that run unchanged on a laptop, a VM,
Render, Railway, Fly.io or Cloud Run. Attach a persistent volume at
`/data` so the sighting history survives redeploys.

---

## What is real, and what is not

| Component | Status |
|---|---|
| Temporal voting engine + plate grammar | **Working**, unit tested |
| Rules engine (clone, speed, loiter, watchlist) | **Working**, zero false positives on 327 seeded sightings |
| REST API, SSE live feed, audit log | **Working** |
| Dashboard, map, trajectory playback, review queue | **Working** |
| Demo seed with one planted instance of every alert type | **Working** |
| Live simulator (real voting, synthetic pixels) | **Working** |
| YOLOv8 + ByteTrack + plate detector + EasyOCR on real video | **Working** — reads real UK plates from test footage |
| Live wall showing annotated model output | **Working** |
| Video anomaly detection (VAD) | **Ingest endpoint + alerting done**; detector not trained |
| Cloud LLM/VLM arbitration | **Code complete**, off by default, needs an API key |

Nothing above is overstated. The gaps are listed because a demo that
claims more than it does is a demo that dies under questioning.

---

## Hardware reality

Developed against an **AMD Radeon RX 6500M (4 GB), no CUDA**. That rules
out PyTorch-CUDA, TensorRT and DeepStream; ROCm does not support this GPU
(gfx1034) either. The viable path is ONNX Runtime + DirectML, with CPU
fallback, and any training done on a free Colab/Kaggle GPU then exported
to ONNX.

```bash
python -m edge --mode providers   # what will inference actually run on?
```

One trap: installing both `onnxruntime` and `onnxruntime-directml` leaves
a broken install where DirectML silently disappears and everything runs
on CPU at a few FPS. Install exactly one.

---

## Architecture

```
RTSP / video  ──▶  edge worker  ──▶  ingest API  ──▶  SQLite (+PostGIS-ready)
                   │  YOLOv8            │  auth            │
                   │  ByteTrack         │  idempotent      ├─▶ rules engine ─▶ alerts
                   │  PaddleOCR         │  dedupe          │
                   └─ temporal voting   └─ confidence      └─▶ SSE ─▶ dashboard
                                           routing
```

Why SQLite: it needs no server, no account and no network, so the demo
survives a dead venue wifi. Every geographic operation goes through a
`haversine_m` SQL function that maps one-to-one onto PostGIS
`ST_Distance`, and the schema comments document the mechanical migration
to Postgres when multiple API replicas are actually needed.

Why Leaflet + OpenStreetMap rather than Mapbox: no access token, so
nothing breaks when a key expires or a free tier runs out.

```
apps/api     FastAPI: ingest, rules engine, REST, SSE, AI arbitration
apps/edge    Vision pipeline, temporal voting, simulator
apps/web     Next.js dashboard
infra        schema.sql
scripts      seed_demo.py
docs         13-section technical blueprint
```

---

## Privacy

This is a surveillance system, and it is engineered as though that
matters:

- The vehicle registry holds **no owner PII**. Plate → owner is a
  separate, audited lookup.
- Every plate search records the operator, the query and a stated reason
  **before** results are returned.
- Watchlist entries are purpose-bound and expire.
- Alerts are advisory. No automated enforcement action, ever — a human
  decides.
- Cloned-plate accusations require high-confidence reads on both
  sightings, a distance floor, and a clock-skew allowance.

See `docs/12-privacy-ops.md` for the DPDP Act 2023 posture.

---

## Documentation

`docs/` holds the full technical blueprint: architecture, schema,
temporal voting, vision pipeline, VAD, rules engine, API contract,
frontend, design system, datasets and training, execution plan, privacy,
and the cloud-AI layer.
# 🛡️ Project Netra Backend Architecture

<!-- CUSTOM VISUAL LOGOS & BADGES -->
<p align="left">
  <a href="./LICENSE">
    <img src="https://shields.io" alt="License Badge">
  </a>
  <a href="./COPYRIGHT">
    <img src="https://shields.io" alt="Copyright Badge">
  </a>
  <a href="https://veerbhanushali.com">
    <img src="https://shields.io" alt="Designer Badge">
  </a>
</p>

---

## 🚀 About the Project
This repository contains the completely secure backend architecture developed exclusively by **Team Netra** for the **Smart India Hackathon (SIH)**. 

* **Backend & Security Architect:** [Veer Bhanushali](https://veerbhanushali.com)
* **Official Website:** [veerbhanushali.com](https://veerbhanushali.com)
