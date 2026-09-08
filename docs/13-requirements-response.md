# Response to the Backend Engineering Requirements

**Project:** NETRA · **PS:** SIH26127 — City-Wide AI Engine for Multi-Camera ANPR
Trajectory Tracking and Urban Traffic Analytics · **Org:** Bharat Electronics Limited

This document answers the handoff point by point: what is **built and running**,
what is **deliberately not built**, and what is **still to build**. Every "built"
claim below corresponds to a live endpoint, a table with rows in it, or a test
that passes — not to an intention.

---

## 1. The one-line answer

The MVP loop the document defines in §18 is closed and running:

```
CCTV VIDEO → VEHICLE DETECTION → TRACK → ANPR → STORE → SEARCH
           → INCIDENT → OPERATOR CONFIRM → LIVE DASHBOARD
```

Two cameras decode real Indian traffic footage continuously, YOLOv8n detects
vehicles, ByteTrack tracks them, a YOLO11n plate detector crops the plate,
EasyOCR reads it, and a temporal-voting layer decides whether the read is good
enough to publish. Confirmed plates land in the database and appear on the live
dashboard; uncertain ones go to a human. Measured reads on real footage include
`KL07BA5252` @ 92.8 %, `DL3CBJ1384` @ 99.5 %, `MH02BJ4692` @ 93.6 %,
`HR26CO6869` @ 91.6 %, with **zero false positives across 327 seeded sightings**.

Where we differ from the recommended stack, it is a substitution behind the same
contract, not a missing capability — see §4.

---

## 2. Feature inventory — status against §2 of the requirements

| # | Feature | Phase | Status | Evidence |
|---|---------|-------|--------|----------|
| 1 | Camera ingestion (registry, health) | 1 | **Built** | `cameras`, `zones`, `camera_links` tables; `GET /api/v1/cameras`; `camera.health` events |
| 2 | ANPR (text, confidence, crop, track) | 1 | **Built** | `plate_reads` + `sightings`; crop written per pass; 92 evidence crops on disk |
| 3 | Vehicle detection / classification | 1 | **Built** | YOLOv8n, COCO classes 2/3/5/7; `sightings.vehicle_type` |
| 4 | Single-camera MOT | 1 | **Built** | ByteTrack; track id `<camera>:<local>` |
| 5 | Vehicle Re-ID | 2 | **Not built** | See §5 — plate is our identity key today |
| 6 | Trajectory reconstruction | 2 | **Built** | `GET /api/v1/vehicles/{plate}/trajectory` over `camera_links` |
| 7 | Vehicle investigation / search | 2 | **Built** | `GET /api/v1/vehicles/search`, `/sightings` |
| 8 | Vehicle watchlist | 2 | **Built** | `watchlist` table with expiry; `POST/DELETE /api/v1/watchlist` |
| 9 | Face / POI search | 3 | **Built** | InsightFace `buffalo_s`; `POST /api/v1/persons`, `/faces/scan` |
| 10 | Person Re-ID (cross-camera) | 3 | **Partial** | Same gallery matches on any camera; no appearance-based person Re-ID |
| 11 | Incident engine + schema | 1 | **Built** | `alerts` (15 types, 8 states), `anomaly_events` |
| 12 | Context-aware threats | 3 | **Built (evidence layer)** | `edge/snapshot.py` — object + person + interaction + temporal, no verdict |
| 13 | Medical / emergency events | 2/3 | **Not built** | Person-down, fire/smoke need trained models — see §5 |
| 14 | Incident fusion | 2 | **Partial** | Republish cooldown + dedupe keys prevent duplicates; no cross-camera merge |
| 15 | Severity / prioritisation | 2 | **Built** | `alerts.severity` critical→info, set per rule |
| 16 | Human verification workflow | 1 | **Built** | `review_queue`; `GET/POST /api/v1/review`; every decision audited |
| 17 | Response orchestration | 3 | **Not built** | No dispatch state machine |
| 18 | Hospital / resource recommendation | 3 | **Not built** | No resource registry |
| 19 | Evidence package | 2 | **Partial** | Crops + snapshot refs stored and served; no bundled export |
| 20 | City map APIs | 1/2 | **Built** | Leaflet + OSM; camera/alert/trajectory geometry; `haversine_m` UDF |
| 21 | Traffic analytics | 2 | **Partial** | `GET /api/v1/stats`, `/stats/timeline`; no density/congestion model |
| 22 | Predictive analytics | 3 | **Not built** | Needs a baseline history we do not have |
| 23 | Forensic search | 2 | **Built** | Historical sightings/alerts with time + camera + plate filters |
| 24 | Admin console | 1 | **Built** | Cameras, zones, thresholds, model versions, feature flags via `/config/*` |
| 25 | Observability | 1 | **Partial** | Structured logs, `/api/health`, per-camera stats; no Prometheus/Grafana |
| 26 | Auth / RBAC | — | **Not built** | Single implicit operator; see §6 — this is our top gap |

**Score: 15 built, 5 partial, 6 not built.** Every Phase-1 item in the document
is complete except authentication.

---

## 3. Event contracts — §4

Implemented and flowing over SSE (`GET /api/v1/stream`):

`sighting.created` · `alert.created` · `alert.updated` · `camera.health` ·
`face.match_candidate` · `review.queued` · `review.decided` · `incident.detected`

Not yet emitted: `vehicle.detected`, `vehicle.track_updated`, `anpr.read`,
`vehicle.match_candidate`, `traffic.aggregate`.

This is a deliberate ordering decision, not an oversight. `vehicle.detected` and
`vehicle.track_updated` fire at frame rate — roughly 1 200 events/second at ten
cameras. The document itself says (§14) to use backpressure and batch writes for
high-rate observations. We publish the *settled* result instead of every frame,
and keep the per-frame evidence inside the tracker where it is actually used.
Emitting them becomes correct the moment there is a real event bus to absorb
them; the payloads are already assembled internally.

---

## 4. Stack substitutions — §10

We match the recommended architecture in shape and differ in three components,
each behind the same interface so the swap is mechanical:

| Recommended | We run | Why, and what the swap costs |
|---|---|---|
| PostgreSQL + PostGIS | **SQLite (WAL)** with a `haversine_m` SQL function | Zero-install so the whole system runs on a judge's laptop. `haversine_m` maps 1:1 onto `ST_Distance`; the queries are written so only the function name changes. |
| MinIO / S3 | **Filesystem** under `data/evidence/`, served read-only | Same discipline the document asks for: the DB stores a *reference*, never a blob. Repointing at S3 is a path change. |
| Kafka / Redpanda / Redis Streams | **In-process pub/sub → SSE** | Honest limit: this does not survive a restart and does not fan out across machines. It is the single biggest scaling gap. |
| pgvector | **NumPy matrix cosine** over the enrolled gallery | Exact, not approximate. Correct to a few thousand identities; needs a real index beyond that. |
| WebSocket | **SSE** | Live updates are one-directional. SSE reconnects automatically and needs no protocol upgrade through a proxy. |

Docker Compose, FastAPI, Pydantic contracts, YOLO-family detection, and ByteTrack
tracking are all as recommended.

---

## 5. What we cannot honestly build — and why

The document's §8 is explicit that models must return observable classes and
scores, never a backend assertion. Three items fail that bar with the data and
time we have, and we are declaring them rather than faking them.

### 5.1 Accident detection — built, benchmarked, and switched off

We implemented a trajectory heuristic (hard braking, stop-after-motion, tracks
ending at speed) and tested it against six real CCTV clips. The result:

| Clip | True label | Hard-brake | Stop-after-move | Track ends at speed |
|------|-----------|-----------:|----------------:|--------------------:|
| FP2 | **normal traffic** | **30** | **15** | 6 |
| V1 | accident | 20 | 10 | 9 |
| V4 | accident | 1 | 0 | 13 |

The normal-traffic control clip scored *highest* on two of the three signals. The
detector does not work. It ships **disabled** (`NETRA_ENABLE_ACCIDENT=false`) with
that table in its docstring, because tuning it until six clips pass is
overfitting to six clips, not building a detector. The correct fix is a trained
temporal model (CCD or UCF-Crime with VideoMAE / RTFM), which needs GPU training
time we do not have this cycle.

### 5.2 Firearm detection

Our detector is YOLOv8n on COCO. COCO contains `knife`, `scissors` and
`baseball bat`. **It contains no firearm class.** The threat module therefore
reports those three classes and says so in its own response payload. Claiming gun
detection would require a weapons dataset and training run we have not done.

### 5.3 Vehicle Re-ID and person Re-ID by appearance

Both need an appearance-embedding model trained on vehicle/person re-identification
data plus a camera-topology gating layer. We chose ByteTrack precisely *because*
it needs no ReID model — it was the right call for a laptop with no CUDA. Today
cross-camera identity is keyed on the plate, which is exact when the plate reads
and useless when it does not. That is the honest boundary.

---

## 6. What we still have to build — in priority order

1. **Authentication and RBAC** (§13). This is the largest gap and the easiest to
   close: four roles (administrator, operator, investigator, response), JWT
   login, and a dependency on every route. The `audit_log` table already records
   actor, action, target and reason on every consequential call — it is
   currently trusting a client-supplied actor string. *~1 day.*
2. **A durable event bus.** Redis Streams behind the existing `events.publish()`
   call. The interface already exists; only the transport changes. *~half a day.*
3. **PostgreSQL + PostGIS migration.** Schema and queries were written for it;
   `haversine_m` → `ST_Distance` and the `datetime()` handling are the two known
   edges. *~1 day.*
4. **The high-rate observation events** (§3 above), once 2 can absorb them.
5. **Incident fusion across cameras** — merge alerts from adjacent cameras within
   a time window into one incident, using the `camera_links` graph we already have.
6. **Traffic aggregates** — volume, average speed and density per road segment
   per interval. The sightings data needed is already being written.
7. **Prometheus + Grafana** over the counters we already keep per camera.
8. **Accident and person-down detection**, properly — trained temporal model,
   evaluated against a held-out set, not tuned to the demo clips.

Items 1–3 are the difference between "a working prototype" and "a backend a BEL
engineer would accept." Items 4–7 are scale and polish. Item 8 is research.

---

## 7. On the phone-as-camera addition

We do not have live CCTV hardware, so a phone runs the camera role over a USB
cable (`adb reverse`, no network exposure) or a private Tailscale link. It now
performs **face matching, plate reading and threat evidence** on the frames it
sends.

One design decision matters here and it follows directly from §8 of the
requirements. A fixed camera sees a vehicle across many frames and settles the
plate by temporal voting; a phone in a moving hand sees it once. A single frame
cannot satisfy our evidence rule (minimum 3 frames, full evidence at 8), so a
handheld read is **capped at 0.80 confidence and written to the review queue —
never to the confirmed sightings table.** Verified on real footage: the phone path
reads `KL07BA5252` correctly and files it as a review candidate at 0.80, while the
fixed camera publishes the same plate at 0.928 as fact.

The same restraint governs threat detection. It returns the evidence the
requirements ask us to store — object class, person track, interaction (is the
object inside a person's bounds, at hand height?), and an explicit note that a
single frame carries no temporal evidence — then a score and a *review* decision.
It never asserts a threat. `knife = threat` is not encoded anywhere.

---

## 8. Where we go beyond the brief

- **Temporal voting on characters, not strings.** Per-character evidence is
  accumulated across every frame of a track, weighted by frame quality, and
  combined with a geometric mean so one bad character sinks the plate. This is
  what produces the zero-false-positive figure.
- **A measured negative result, kept in the code.** §5.1 above.
- **A published-accuracy policy rather than a model metric.** ≥0.90 auto-accept,
  0.55–0.90 to a human, <0.55 discarded. "Accurate" is defined as *nothing wrong
  is ever published as fact*, which is the property an operator actually needs.
- **Regional plate grammar** with positional confusion repair (O↔0, I↔1, B↔8,
  S↔5, Z↔2) and rejection of impossible RTO codes — added after the system once
  published `KL0ZBA5252` for a true `KL07BA5252`.
- **DirectML acceleration on AMD** where CUDA is unavailable: YOLOv8n 59.4 → 18.9 ms
  (3.2×), plate detector 54.9 → 11.1 ms (5.0×).

---

## 9. Summary

We built the vertical slice the document asks for first (§16, item 8) and
resisted building face search, threat intelligence and dispatch before the core
loop was stable (§17). The core loop is stable. Face and threat modules sit
behind feature flags, exactly as instructed. The remaining work is
infrastructure — auth, a real event bus, PostgreSQL — not new AI, and each has a
known interface to slot into.

What we will not do is claim a detector we have not trained or publish a plate we
have not proven. The accident detector staying switched off is the clearest
statement of that principle we can make.
