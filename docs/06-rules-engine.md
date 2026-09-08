<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## The Spatio-Temporal Rules Engine

This is the component that converts a stream of `plate_reads` rows into defensible `alerts` rows. Everything upstream (YOLO, ByteTrack, PaddleOCR) is a sensor. Everything downstream (Next.js) is a viewer. The rules engine is the only place where the product makes a *claim about a crime*, so it is also the only place where being wrong is expensive.

### 1. Architecture: where do rules execute?

| Option | Latency | Testability | Failure mode | Verdict |
|---|---|---|---|---|
| PL/pgSQL `AFTER INSERT` triggers on `plate_reads` | ~0 ms | Poor — needs a live DB per test, no `pytest` unit tests, no debugger | A slow trigger holds the insert transaction open and back-pressures every edge worker; one exception rolls back the *sighting itself* | Rejected for rule logic |
| Polling service (`SELECT ... WHERE id > last_seen` every 2 s) | 0–2 s | Good | Silent lag under load; wasted queries at idle; cursor bookkeeping | Rejected as primary |
| Event-driven in the FastAPI ingest path | 5–40 ms | Excellent — pure Python, `pytest` with fixtures, replayable from a JSONL file | If the API dies mid-evaluation the read is stored but unevaluated | **Recommended** |

**Decision: event-driven Python, with a `LISTEN/NOTIFY` safety net and an idempotent claim.**

`POST /v1/reads` inserts the row and schedules `evaluate_read(read_id)` on FastAPI's `BackgroundTasks`. Separately, an `AFTER INSERT` trigger on `plate_reads` issues `pg_notify('plate_read_inserted', id::text)`, and a long-lived asyncio `LISTEN` task calls the same `evaluate_read`. Both paths are safe because evaluation begins by *claiming* the read:

```sql
UPDATE plate_reads SET rules_evaluated_at = now()
 WHERE id = $1 AND rules_evaluated_at IS NULL
RETURNING id;
```

No row returned means someone else already evaluated it; return immediately. This gives you exactly-once semantics without a queue broker, and it means the edge worker may write directly to Supabase (bypassing FastAPI) during the demo and rules still fire. A nightly/on-demand sweep `POST /v1/rules/replay` re-evaluates `WHERE rules_evaluated_at IS NULL`.

The **only** poller in the system is a 10 s `rule_followups` drainer for time-delayed checks (hit-and-run "did it come back?"). Triggers are used for exactly three things, all of them mechanical, none of them logic: `pg_notify`, `alert_events` audit rows, and `updated_at`.

**Concurrency.** Six camera workers can produce two reads of the same plate within milliseconds. Before evaluating, take a transaction-scoped advisory lock keyed on the plate so two evaluations of the same vehicle serialise:

```sql
SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0));
```

**Volume contract with the edge team (non-negotiable):** the edge worker emits **one consolidated read per (track_id, camera_id)** — best frame, per-character majority vote across the track — not one row per frame. Six replayed cameras produce 2–5 reads/s, not 60. The engine's p95 budget is **25 ms per read** across ~4 indexed queries; that fits comfortably in the FastAPI process on the 16 GB dev box with no GPU involvement at all.

### 2. Rule specification format

Rules are declarative rows; the Python class is only the executor. `rules.yaml` seeds `rule_config`; the dashboard `PATCH /v1/rules/{rule_id}` writes `rule_config` and the engine hot-reloads every 30 s. Judges love that thresholds are visible and tunable on stage.

```python
# apps/api/app/rules/spec.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, Sequence

class Severity(str, Enum):
    INFO = "info"; LOW = "low"; MEDIUM = "medium"; HIGH = "high"; CRITICAL = "critical"

class Trigger(str, Enum):
    ON_READ = "on_read"            # fired per new plate_reads row
    ON_VAD_EVENT = "on_vad_event"  # fired per new vad_events row
    DEFERRED = "deferred"          # fired by rule_followups drainer
    SCHEDULED = "scheduled"        # fired on a fixed interval (loitering, convoy)

@dataclass(frozen=True, slots=True)
class RuleSpec:
    id: str
    title: str
    trigger: Trigger
    severity: Severity
    enabled: bool = True
    shadow: bool = False              # write alert, do NOT notify — burn-in mode
    min_ocr_confidence: float = 0.65  # mean OCR conf gate
    min_char_confidence: float = 0.0  # min over char_confidences[]
    require_valid_format: bool = False
    cooldown_s: int = 900
    dedup_key: str = "{rule_id}|{plate_text}"
    auto_notify_min_confidence: float = 0.75
    params: dict[str, Any] = field(default_factory=dict)

    def p(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

@dataclass(slots=True)
class AlertDraft:
    rule_id: str
    severity: Severity
    confidence: float                 # 0..1, per-rule formula, see §8
    occurred_at: datetime
    camera_id: str | None
    plate_text: str | None
    primary_read_id: int | None
    related_read_ids: list[int]
    dedup_key: str
    payload: dict[str, Any]           # rule-specific evidence, rendered by the UI

class Rule(Protocol):
    spec_id: str
    async def evaluate(self, ctx: "ReadContext", spec: RuleSpec, conn) -> Sequence[AlertDraft]: ...
```

```yaml
# apps/api/app/rules/rules.yaml
version: 1
rules:
  - id: cloned_plate
    title: "Physically impossible transit (cloned / fake plate)"
    trigger: on_read
    severity: critical
    min_ocr_confidence: 0.90
    min_char_confidence: 0.80
    require_valid_format: true
    cooldown_s: 1800
    dedup_key: "cloned_plate|{plate_text}|{cam_lo}|{cam_hi}"
    params:
      max_plausible_speed_kmph: 150
      circuity_factor: 1.25
      clock_skew_tolerance_s: 5
      min_separation_m: 350
      min_elapsed_s: 2
      lookback_minutes: 30

  - id: speeding
    title: "Point-to-point average speed over limit"
    trigger: on_read
    severity: medium
    min_ocr_confidence: 0.85
    require_valid_format: true
    cooldown_s: 600
    dedup_key: "speeding|{plate_text}|{cam_lo}|{cam_hi}"
    params:
      timestamp_uncertainty_s: 1.5
      min_baseline_m: 800
      tolerance_pct: 0.10
      tolerance_floor_kmph: 5
      lookback_minutes: 30

  - id: hit_and_run
    title: "Vehicle left the scene of a detected collision"
    trigger: on_vad_event
    severity: critical
    min_ocr_confidence: 0.75
    cooldown_s: 3600
    dedup_key: "hit_and_run|{vad_event_id}|{plate_text}"
    params:
      pre_impact_s: 20
      post_impact_s: 10
      flee_window_s: 45
      followup_delay_s: 300
      accident_event_types: ["accident", "collision", "sudden_stop"]

  - id: trajectory_break
    title: "Vehicle vanished before every expected downstream camera"
    trigger: deferred
    severity: medium
    min_ocr_confidence: 0.85
    cooldown_s: 3600
    dedup_key: "trajectory_break|{plate_text}|{camera_id}"
    params:
      travel_time_multiplier: 3.0
      followup_delay_s: 600

  - id: loitering
    title: "Repeated presence in a sensitive zone (casing)"
    trigger: scheduled
    severity: medium
    min_ocr_confidence: 0.80
    cooldown_s: 3600
    dedup_key: "loitering|{plate_text}|{zone_id}"
    params:
      interval_s: 120
      window_s: 3600
      revisit_gap_s: 300
      min_sightings: 5
      min_dwell_s: 1200
      min_revisits: 3

  - id: watchlist_hit
    title: "Plate matches stolen / wanted list"
    trigger: on_read
    severity: high
    min_ocr_confidence: 0.70
    cooldown_s: 300
    dedup_key: "watchlist_hit|{plate_text}|{camera_id}"
    params:
      trigram_limit: 0.50
      max_edit_distance: 1
      fuzzy_severity_downgrade: true

  - id: no_plate
    title: "Vehicle with absent / obscured / unreadable plate"
    trigger: on_read
    severity: low
    min_ocr_confidence: 0.0
    cooldown_s: 1800
    dedup_key: "no_plate|{camera_id}|{track_id}"
    params:
      min_frames_without_plate: 8
      min_track_frames: 12

  - id: convoy
    title: "Two plates travelling together across 3+ cameras"
    trigger: scheduled
    severity: low
    shadow: true            # stretch goal, ships in burn-in
    min_ocr_confidence: 0.85
    cooldown_s: 3600
    dedup_key: "convoy|{plate_a}|{plate_b}"
    params:
      interval_s: 300
      window_minutes: 45
      max_delta_s: 20
      min_shared_cameras: 3
      max_delta_jitter_s: 6
```

### 3. Engine core

```python
# apps/api/app/rules/engine.py
import logging
from app.rules.spec import RuleSpec, Trigger, AlertDraft
from app.rules.registry import REGISTRY, load_specs
from app.rules.context import ReadContext, load_read_context
from app.rules.emit import emit

log = logging.getLogger("rules")

def _gate(ctx: ReadContext, spec: RuleSpec) -> bool:
    if ctx.plate_text is None:
        return spec.id == "no_plate"
    if (ctx.ocr_confidence or 0.0) < spec.min_ocr_confidence:
        return False
    if spec.min_char_confidence and ctx.char_confidences:
        if min(ctx.char_confidences) < spec.min_char_confidence:
            return False
    if spec.require_valid_format and not ctx.plate_valid:
        return False
    return True

async def evaluate_read(pool, read_id: int) -> list[str]:
    async with pool.acquire() as conn, conn.transaction():
        claimed = await conn.fetchval(
            "UPDATE plate_reads SET rules_evaluated_at = now() "
            "WHERE id = $1 AND rules_evaluated_at IS NULL RETURNING id", read_id)
        if claimed is None:
            return []
        ctx = await load_read_context(conn, read_id)
        if ctx.plate_text:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                               ctx.plate_text)

        specs = await load_specs(conn)
        drafts: list[AlertDraft] = []
        for rule in REGISTRY.for_trigger(Trigger.ON_READ):
            spec = specs[rule.spec_id]
            if not spec.enabled or not _gate(ctx, spec):
                continue
            try:
                drafts.extend(await rule.evaluate(ctx, spec, conn))
            except Exception:                      # one bad rule must never kill ingest
                log.exception("rule %s failed on read %s", rule.spec_id, read_id)

        out = []
        for d in drafts:
            alert_id = await emit(conn, d, specs[d.rule_id])
            if alert_id:
                out.append(alert_id)
        return out
```

### 4. `cloned_plate` — the flagship rule

A plate is cloned when the *same string* is observed at two places whose separation cannot be covered in the elapsed time by any vehicle. The whole game is stacking every assumption in the *innocent* direction, so that when it fires, it is not arguable.

**Guards, and the number behind each:**

| Guard | Value | Why this number |
|---|---|---|
| `max_plausible_speed_kmph` | 150 | Indian expressway car limit is 120 km/h; 150 gives 25 % headroom over the legal ceiling for a genuinely fast night run. Note this is an **average over a multi-kilometre hop including junction stops** — real urban averages are 20–45 km/h — so 150 only catches near-teleportation. |
| `circuity_factor` | 1.25 | Route distance ÷ straight-line distance. Indian metros run 1.3–1.6 (radial layouts, one-ways, few river/rail crossings). We deliberately pick the **low end**, because the factor sits in the numerator of implied speed and an over-estimate manufactures false positives. Used **only** when `camera_pair_distance` has no routed distance for the pair. |
| `clock_skew_tolerance_s` | 5 | Edge boxes have independent clocks. We **add** it to elapsed time (longer elapsed → lower implied speed → fewer alerts). Covers w32time drift plus RTSP jitter-buffer timestamp assignment. On the demo rig all replays share one clock, so it costs nothing. |
| `min_separation_m` | 350 | Derived: for the test to be more than timestamp noise we need `d·C / v_max ≥ skew + min_elapsed + 3·u` ≈ 10 s with `u` = 1.5 s per-read timestamp uncertainty. `d ≥ 10 × 41.67 / 1.25 = 333 m` → round up to 350 m. Below this the rule can *only* fire on measurement noise, so we skip it entirely. Junction camera pairs (typically < 150 m apart) are structurally excluded. |
| `min_elapsed_s` | 2 | Divide-by-zero and sub-frame-ordering guard. |
| `lookback_minutes` | 30 | City span ≈ 40 km → `40000 × 1.25 / 41.67 ≈ 1200 s`. Beyond 20 min no pair in the city can be "impossible", so 30 min is generous and bounds the index scan. |
| Same `camera_id` | skipped | Track loss and re-acquisition on one camera yields two reads metres apart, seconds apart → infinite implied speed. Never a clone signal. |
| Same `cluster_id` | skipped | `cameras.cluster_id` (NOT NULL, defaults to the camera's own id) groups every camera on one junction into one logical node. |
| Confidence gate | mean ≥ 0.90, min char ≥ 0.80, format valid, **both** reads | A cloned-plate alert accuses a *specific registered owner*. One OCR character error turns an innocent plate into a "clone". Gating on the mean alone lets a single weak character through, hence the per-character floor over `char_confidences[]`. |

**The SQL** (single indexed scan; requires `CREATE INDEX plate_reads_plate_time_idx ON plate_reads (plate_text, captured_at DESC) WHERE plate_text IS NOT NULL;`):

```sql
-- apps/api/app/rules/impl/cloned_plate.sql   $1 = read_id, $2 = min_ocr_confidence, $3 = lookback_minutes
WITH cur AS (
    SELECT r.id, r.plate_text, r.camera_id, r.captured_at, r.geom,
           r.ocr_confidence, r.vehicle_class, r.vehicle_color, c.cluster_id
      FROM plate_reads r
      JOIN cameras c USING (camera_id)
     WHERE r.id = $1
)
SELECT p.id                                             AS prev_read_id,
       p.camera_id                                      AS prev_camera_id,
       p.captured_at                                    AS prev_captured_at,
       p.ocr_confidence                                 AS prev_confidence,
       p.vehicle_class                                  AS prev_class,
       p.vehicle_color                                  AS prev_color,
       ST_Distance(p.geom, cur.geom)                    AS straight_m,   -- geography -> metres
       EXTRACT(EPOCH FROM (cur.captured_at - p.captured_at)) AS elapsed_s,
       cpd.road_distance_m                              AS routed_m
  FROM cur
  JOIN plate_reads p
    ON p.plate_text  = cur.plate_text
   AND p.id         <> cur.id
   AND p.captured_at <= cur.captured_at
   AND p.captured_at  > cur.captured_at - make_interval(mins => $3)
  JOIN cameras pc ON pc.camera_id = p.camera_id
  LEFT JOIN camera_pair_distance cpd
         ON cpd.from_camera_id = p.camera_id AND cpd.to_camera_id = cur.camera_id
 WHERE p.camera_id  <> cur.camera_id
   AND pc.cluster_id <> cur.cluster_id
   AND p.ocr_confidence >= $2
   AND p.plate_valid
 ORDER BY p.captured_at DESC
 LIMIT 1;
```

**The Python:**

```python
# apps/api/app/rules/impl/cloned_plate.py
from pathlib import Path
from app.rules.spec import AlertDraft, Severity, RuleSpec
from app.rules.context import ReadContext

SQL = (Path(__file__).with_suffix(".sql")).read_text()
KMPH = 3.6

class ClonedPlateRule:
    spec_id = "cloned_plate"

    async def evaluate(self, ctx: ReadContext, spec: RuleSpec, conn):
        row = await conn.fetchrow(SQL, ctx.read_id, spec.min_ocr_confidence,
                                  spec.p("lookback_minutes", 30))
        if row is None:
            return []

        elapsed = float(row["elapsed_s"])
        if elapsed < spec.p("min_elapsed_s", 2.0):
            return []

        straight_m = float(row["straight_m"])
        if straight_m < spec.p("min_separation_m", 350.0):
            return []                                   # junction pair — noise floor

        if row["routed_m"] is not None:                 # exact routed distance wins
            road_m, distance_source = float(row["routed_m"]), "routed"
        else:
            road_m = straight_m * spec.p("circuity_factor", 1.25)
            distance_source = "circuity"

        effective_s = elapsed + spec.p("clock_skew_tolerance_s", 5.0)
        implied_kmph = (road_m / effective_s) * KMPH
        v_max = spec.p("max_plausible_speed_kmph", 150.0)
        if implied_kmph <= v_max:
            return []

        # confidence: OCR quality (both reads) + how far past impossible + physical corroboration
        ocr = min(ctx.ocr_confidence, float(row["prev_confidence"]))
        margin = min((implied_kmph - v_max) / (0.5 * v_max), 1.0)   # saturates at 1.5 x v_max
        corroboration, evidence = 0.0, []
        if row["prev_class"] and ctx.vehicle_class and row["prev_class"] != ctx.vehicle_class:
            corroboration, evidence = 0.15, ["vehicle_class_mismatch"]
        elif row["prev_color"] and ctx.vehicle_color and row["prev_color"] != ctx.vehicle_color:
            corroboration, evidence = 0.08, ["vehicle_color_mismatch"]
        confidence = min(0.45 * ocr + 0.40 * margin + corroboration, 0.99)

        cam_lo, cam_hi = sorted([row["prev_camera_id"], ctx.camera_id])
        return [AlertDraft(
            rule_id=self.spec_id, severity=Severity.CRITICAL, confidence=round(confidence, 3),
            occurred_at=ctx.captured_at, camera_id=ctx.camera_id, plate_text=ctx.plate_text,
            primary_read_id=ctx.read_id, related_read_ids=[int(row["prev_read_id"])],
            dedup_key=f"cloned_plate|{ctx.plate_text}|{cam_lo}|{cam_hi}",
            payload={
                "prev_camera_id": row["prev_camera_id"], "curr_camera_id": ctx.camera_id,
                "prev_captured_at": row["prev_captured_at"].isoformat(),
                "curr_captured_at": ctx.captured_at.isoformat(),
                "straight_line_m": round(straight_m, 1),
                "assumed_road_m": round(road_m, 1), "distance_source": distance_source,
                "elapsed_s": round(elapsed, 2), "effective_elapsed_s": round(effective_s, 2),
                "implied_speed_kmph": round(implied_kmph, 1),
                "max_plausible_speed_kmph": v_max,
                "prev_vehicle": {"class": row["prev_class"], "color": row["prev_color"]},
                "curr_vehicle": {"class": ctx.vehicle_class, "color": ctx.vehicle_color},
                "corroboration": evidence,
            })]
```

**Worked example (this is the on-stage demo).** `MH12DE1433` read at `CAM-014` Andheri (19.1197, 72.8468) at `21:04:11`, then at `CAM-031` Chembur (19.0522, 72.8999) at `21:07:38`.

```
ST_Distance(geography)   = 9 352.5 m
elapsed                  = 207 s
effective (207 + 5 skew) = 212 s
road (9 352.5 x 1.25)    = 11 690.7 m
implied                  = 11 690.7 / 212 = 55.15 m/s = 198.5 km/h  >  150  -> ALERT
```

Robustness check: with **no** circuity factor at all it is still 158.8 km/h — over the limit. The alert does not depend on the modelling assumption. Confidence: `ocr = 0.95`, `margin = (198.5-150)/75 = 0.647`, class mismatch (`car` vs `motorcycle`) `= 0.15` → **0.836**, above `auto_notify_min_confidence`, so it sirens.

Near-miss for the same pair at `elapsed = 420 s`: `11 690.7 / 425 = 99.0 km/h` → no alert. A marginal 160 km/h read at `ocr = 0.90` with no corroboration scores `0.458` → alert is written but queued silently rather than sirened.

**Demo prep:** for 6–8 cameras there are only 30–56 ordered pairs. Hand-populate `camera_pair_distance.road_distance_m` from a routing service for every pair — 30 minutes of work — and the circuity factor becomes a fallback you can honestly describe rather than a number you rely on.

### 5. `speeding`

Never use straight-line distance for a speed accusation. This rule fires only on pairs present in `camera_pair_distance` with a real routed `road_distance_m` and a `speed_limit_kmph`.

```python
elapsed = (ctx.captured_at - prev_at).total_seconds()
u = spec.p("timestamp_uncertainty_s", 1.5)
v_kmph = (road_distance_m / (elapsed + 2 * u)) * 3.6          # +2u => under-states speed
threshold = limit + max(spec.p("tolerance_floor_kmph", 5), spec.p("tolerance_pct", 0.10) * limit)
fires = road_distance_m >= spec.p("min_baseline_m", 800) and v_kmph > threshold
confidence = min(0.5 * min(ctx.ocr_confidence, prev_conf)
                 + 0.5 * min((v_kmph - threshold) / max(0.25 * limit, 1), 1.0), 0.99)
```

The `+2u` in the denominator is the same conservatism trick as clock skew: it makes the reported speed the *lowest* speed consistent with the measurement.

**Why `min_baseline_m = 800`.** Relative speed error equals relative time error, `Δv/v = Δt/t`:

| Baseline | Elapsed @ 60 km/h | Speed error at Δt = 3 s |
|---|---|---|
| 500 m | 30 s | ±10 % (±6 km/h) |
| 800 m | 48 s | ±6.3 % |
| 2 km | 120 s | ±2.5 % |
| 5 km | 300 s | ±1.0 % |

**In-frame (single-camera) speed estimation is not offered, deliberately.** It requires a per-camera homography from four surveyed ground points. Even with one, the dominant error is along the optical axis: at 30 m range on a 1080p camera with a ~50° horizontal FOV, one pixel of vertical bounding-box jitter maps to tens of centimetres of ground distance, and the plate-centroid jitter across consecutive frames is ±2–3 px. Over a 1 s in-frame baseline that is a speed uncertainty of the order of ±20 km/h — worthless. Point-to-point averaging over 800 m+ moves the error from *pixels* into *clock*, where we can bound it.

**Legally defensible vs indicative.** Our number is **indicative only**: a triage signal that ranks a vehicle for human review. Prosecutable speed evidence in India requires a type-approved, periodically calibrated measuring instrument and a documented chain of custody, which our replayed-video pipeline is not. The UI must label these alerts `INDICATIVE — NOT EVIDENTIARY` and the API returns `"evidentiary": false` in the payload. Saying this to judges *strengthens* the pitch; claiming otherwise invites a question you cannot answer.

> **Verify:** the exact Legal Metrology / MoRTH approval regime for average-speed (point-to-point) enforcement in India, and whether any state has notified ANPR average-speed corridors, before altering that wording.

### 6. `hit_and_run` and `trajectory_break`

Two independent signals, correlated.

**Signal A — flight from a detected collision.** A `vad_events` row with `event_type IN ('accident','collision','sudden_stop')` and `score >= 0.6` at camera `C`, time `t0`, triggers:

```sql
-- who was on scene?   $1 = camera_id, $2 = t0, $3 = pre_impact_s, $4 = post_impact_s
SELECT DISTINCT ON (r.plate_text)
       r.id, r.plate_text, r.captured_at, r.ocr_confidence, r.vehicle_class
  FROM plate_reads r
 WHERE r.camera_id = $1
   AND r.plate_text IS NOT NULL
   AND r.captured_at BETWEEN $2 - make_interval(secs => $3)
                         AND $2 + make_interval(secs => $4)
 ORDER BY r.plate_text, r.captured_at;

-- did it stay?        $1 = camera_id, $2 = plate_text, $3 = t0
SELECT max(captured_at) AS last_at
  FROM plate_reads
 WHERE camera_id = $1 AND plate_text = $2 AND captured_at > $3;
```

A plate whose `last_at` is null or earlier than `t0 + flee_window_s` (45 s) **left the scene**. 45 s is chosen because a driver who stops lawfully is still in frame after a minute; a fleeing vehicle clears a typical 60–80 m camera field in under 10 s. Every on-scene plate that left is a **candidate**, severity `critical`, human triage mandatory — this rule is intentionally high-recall, because missing a hit-and-run is worse than a queued false alarm, and volume is bounded by how rare accident VAD events are.

A `rule_followups` row is queued at `t0 + 300 s` to re-score: if the plate reappeared at `C` inside 5 minutes (driver stopped further down and walked back) the alert is auto-transitioned to `false_positive` with actor `system`.

**Signal B — trajectory break.** After a vehicle is seen at `C`, every adjacent camera has an expected minimum travel time. If it appears at *none* of them within `3 × min_travel_time_s`, the trajectory broke — plate removed, plate covered, or the vehicle was garaged.

```sql
-- $1 = camera_id, $2 = plate_text, $3 = captured_at, $4 = multiplier
SELECT d.to_camera_id, d.min_travel_time_s
  FROM camera_pair_distance d
 WHERE d.from_camera_id = $1
   AND d.is_adjacent
   AND NOT EXISTS (
        SELECT 1 FROM plate_reads r2
         WHERE r2.plate_text = $2
           AND r2.camera_id  = d.to_camera_id
           AND r2.captured_at BETWEEN $3
                                  AND $3 + make_interval(secs => d.min_travel_time_s * $4));
```

If the query returns *every* adjacent camera, the break is total. Alone this is weak (people park), so its standalone severity is `medium` and confidence caps at 0.45. **Correlated** with a `hit_and_run` candidate on the same plate within 10 minutes, `emit()` promotes the hit-and-run alert's confidence by +0.20 and appends `"trajectory_break"` to `payload.corroboration`. That correlation is the demo money-shot: *"the vehicle fled the collision and then vanished from the road network — it did not simply drive on."*

### 7. `loitering` / casing, `convoy`, `watchlist_hit`, `no_plate`

**Loitering.** Zone membership is precomputed once, not per read:

```sql
CREATE TABLE camera_zone (
  camera_id text REFERENCES cameras(camera_id),
  zone_id   text REFERENCES zones(zone_id),
  PRIMARY KEY (camera_id, zone_id)
);
INSERT INTO camera_zone (camera_id, zone_id)
SELECT c.camera_id, z.zone_id
  FROM cameras c JOIN zones z ON ST_Covers(z.polygon, c.geom)
ON CONFLICT DO NOTHING;
```

The detector, run every 120 s per zone (`Trigger.SCHEDULED`):

```sql
-- $1 zone_id, $2 window_s, $3 min_ocr_confidence, $4 revisit_gap_s,
-- $5 min_sightings, $6 min_dwell_s, $7 min_revisits
WITH w AS (
    SELECT r.id, r.plate_text, r.camera_id, r.captured_at,
           LAG(r.captured_at) OVER (PARTITION BY r.plate_text ORDER BY r.captured_at) AS prev_at
      FROM plate_reads r
      JOIN camera_zone cz ON cz.camera_id = r.camera_id AND cz.zone_id = $1
     WHERE r.captured_at > now() - make_interval(secs => $2)
       AND r.plate_text IS NOT NULL
       AND r.ocr_confidence >= $3
)
SELECT plate_text,
       count(*)                                       AS sightings,
       count(DISTINCT camera_id)                      AS cameras,
       min(captured_at)                               AS first_at,
       max(captured_at)                               AS last_at,
       EXTRACT(EPOCH FROM (max(captured_at) - min(captured_at)))          AS dwell_s,
       count(*) FILTER (WHERE prev_at IS NOT NULL
                          AND captured_at - prev_at > make_interval(secs => $4)) AS revisits,
       array_agg(id ORDER BY captured_at)             AS read_ids
  FROM w
 GROUP BY plate_text
HAVING count(*) >= $5
   AND EXTRACT(EPOCH FROM (max(captured_at) - min(captured_at))) >= $6
   AND count(*) FILTER (WHERE prev_at IS NOT NULL
                          AND captured_at - prev_at > make_interval(secs => $4)) >= $7;
```

Thresholds and their reasoning: **window 60 min**, **≥ 5 sightings**, **dwell ≥ 20 min**, **≥ 3 revisits with ≥ 5 min gaps**. A resident leaving and returning produces 2 sightings and 1 revisit. A delivery rider produces many sightings but with sub-5-minute gaps, so `revisits` stays low and dwell is short. A vehicle casing a target makes repeated *separated* passes — the `revisits` counter, not raw count, is what isolates it. All three conditions must hold; any one alone is a rich false-positive source. Confidence = `0.3·(sightings/10 capped) + 0.4·(revisits/6 capped) + 0.3·(dwell/3600 capped)`.

**Convoy / tailing (stretch, ships in `shadow: true`).**

```sql
WITH pairs AS (
    SELECT LEAST(a.plate_text, b.plate_text)  AS p1,
           GREATEST(a.plate_text, b.plate_text) AS p2,
           a.camera_id,
           abs(EXTRACT(EPOCH FROM (a.captured_at - b.captured_at))) AS dt
      FROM plate_reads a
      JOIN plate_reads b
        ON b.camera_id = a.camera_id
       AND b.captured_at BETWEEN a.captured_at - make_interval(secs => $1)
                             AND a.captured_at + make_interval(secs => $1)
       AND a.plate_text < b.plate_text
     WHERE a.captured_at > now() - make_interval(mins => $2)
       AND a.ocr_confidence >= 0.85 AND b.ocr_confidence >= 0.85
)
SELECT p1, p2, count(DISTINCT camera_id) AS shared_cameras,
       avg(dt) AS avg_gap_s, stddev_pop(dt) AS gap_jitter_s
  FROM pairs
 GROUP BY p1, p2
HAVING count(DISTINCT camera_id) >= $3
   AND stddev_pop(dt) < $4;
```

`stddev_pop(dt) < 6 s` is the anti-false-positive core: coincidental co-travel on a busy corridor has a *jittery* gap, while a deliberate tail holds a near-constant following distance. Without that guard, every pair of vehicles on a one-way arterial matches.

**Watchlist.** Requires `CREATE EXTENSION pg_trgm; CREATE EXTENSION fuzzystrmatch;` and `CREATE INDEX watchlist_norm_trgm ON watchlist USING gin (plate_norm gin_trgm_ops);`.

```sql
SELECT set_limit($3);   -- trigram prefilter, e.g. 0.50
SELECT w.id, w.plate_norm, w.reason, w.severity, w.source_ref,
       levenshtein($1, w.plate_norm) AS edits,
       similarity($1, w.plate_norm)  AS trg
  FROM watchlist w
 WHERE w.active
   AND (w.expires_at IS NULL OR w.expires_at > now())
   AND length(w.plate_norm) BETWEEN length($1) - 1 AND length($1) + 1
   AND w.plate_norm % $1                                   -- index-backed prefilter
   AND levenshtein($1, w.plate_norm) <= $2
 ORDER BY edits ASC, trg DESC
 LIMIT 5;
```

`edits = 0` → exact hit, confidence `0.95 × ocr_confidence`, severity from `watchlist.severity`. `edits = 1` → OCR-confusion-aware scoring, because not all one-character errors are equally likely:

```python
CONFUSION_GROUPS = [set("0ODQ"), set("1IL7"), set("2Z"), set("5S"),
                    set("8B"), set("6G"), set("4A"), set("MN")]

def _confusable(a: str, b: str) -> bool:
    return any(a in g and b in g for g in CONFUSION_GROUPS)

def fuzzy_confidence(read: str, wl: str, ocr: float) -> float:
    diffs = [(x, y) for x, y in zip(read, wl) if x != y]
    if len(read) == len(wl) and len(diffs) == 1 and _confusable(*diffs[0]):
        return 0.70 * ocr      # plausible OCR slip -> real candidate
    return 0.35 * ocr          # probably a genuinely different vehicle
```

Fuzzy hits are downgraded one severity step and the payload carries `"match_type": "fuzzy"` plus the differing character pair, so the operator sees *why* it was flagged.

> **Verify:** that `levenshtein_less_equal` (cheaper, bounded) exists in the Supabase-provisioned `fuzzystrmatch` build; if so, substitute it for `levenshtein` in the WHERE clause.

Format validation used by `require_valid_format`:

```python
import re
STD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")   # MH12DE1433, DL8CAF5030
BH  = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")           # 22BH1234AA
STATE_CODES = {"AN","AP","AR","AS","BR","CG","CH","DD","DL","GA","GJ","HP","HR","JH","JK",
               "KA","KL","LA","LD","MH","ML","MN","MP","MZ","NL","OD","PB","PY","RJ","SK",
               "TN","TR","TS","UK","UP","WB"}

def plate_valid(p: str) -> bool:
    p = re.sub(r"[^A-Z0-9]", "", (p or "").upper())
    if BH.fullmatch(p):
        return True
    return bool(STD.fullmatch(p)) and p[:2] in STATE_CODES
```

> **Verify:** the state-code set against the current MoRTH list before the demo (CG/OD/TS/UK/LA renames, plus armed-forces and CD/UN series which do not match either regex and must be allowlisted rather than flagged).

**`no_plate`.** The edge worker must emit a `plate_reads` row with `plate_text IS NULL`, `no_plate_reason IN ('not_detected','unreadable','occluded','obscured')`, `track_frames int`, `frames_without_plate int`. The rule fires only when `track_frames >= 12 AND frames_without_plate >= 8` — a vehicle tracked for a dozen frames with no readable plate in two-thirds of them is not transient occlusion. Severity `low`, no notification, purely a queue entry, because tarpaulin, a following vehicle, or heavy rain all produce it legitimately. Its real value is a per-camera **rate**: a sudden rise in `no_plate` at one camera means that camera has drifted out of focus or the glare angle changed, which is an operational alert, not a crime alert.

### 8. Alert emission, deduplication and confidence

```python
# apps/api/app/rules/emit.py
DEDUP_CLAIM = """
INSERT INTO alert_dedup (dedup_key, rule_id, last_fired_at, hit_count)
VALUES ($1, $2, now(), 1)
ON CONFLICT (dedup_key) DO UPDATE
   SET last_fired_at = now(),
       hit_count     = alert_dedup.hit_count + 1
 WHERE alert_dedup.last_fired_at < now() - make_interval(secs => $3)
RETURNING hit_count;
"""

async def emit(conn, draft, spec) -> str | None:
    if await conn.fetchval("SELECT 1 FROM plate_allowlist "
                           "WHERE plate_norm = $1 AND active", draft.plate_text or ""):
        return None                                    # emergency / govt fleet
    hits = await conn.fetchval(DEDUP_CLAIM, draft.dedup_key, draft.rule_id, spec.cooldown_s)
    if hits is None:
        return None                                    # inside cooldown -> suppressed
    alert_id = await conn.fetchval(
        """INSERT INTO alerts (rule_id, severity, status, shadow, confidence, plate_text,
                               camera_id, occurred_at, primary_read_id, related_read_ids,
                               dedup_key, repeat_count, payload, geom)
           VALUES ($1,$2,'new',$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                   (SELECT geom FROM cameras WHERE camera_id = $6))
           RETURNING id""",
        draft.rule_id, draft.severity.value, spec.shadow, draft.confidence, draft.plate_text,
        draft.camera_id, draft.occurred_at, draft.primary_read_id, draft.related_read_ids,
        draft.dedup_key, hits, json.dumps(draft.payload))
    if not spec.shadow and draft.confidence >= spec.auto_notify_min_confidence:
        await conn.execute(
            "INSERT INTO notification_outbox (alert_id, channel, payload) "
            "SELECT $1, s.id, $2 FROM webhook_subscriptions s "
            "WHERE s.active AND ($1 IS NOT NULL) AND s.min_severity_rank <= severity_rank($3)",
            alert_id, json.dumps(draft.payload), draft.severity.value)
    return str(alert_id)
```

The `ON CONFLICT DO UPDATE ... WHERE` is the whole cooldown mechanism and it is **atomic**: if the guard fails, no row is returned, no exception is raised, and two concurrent workers cannot both win. `repeat_count` records how many times the same condition recurred inside the cooldown, which the UI shows as `×7` on the alert card rather than seven cards.

**Cooldown table:**

| Rule | Cooldown | Reason |
|---|---|---|
| `cloned_plate` | 1800 s per (plate, camera-pair) | The clone keeps driving; one alert per half hour per corridor is actionable, thirty are not |
| `speeding` | 600 s per (plate, camera-pair) | Same corridor, same offence |
| `watchlist_hit` | 300 s per (plate, camera) | You want the *trajectory*, so short |
| `hit_and_run` | 3600 s per (vad_event, plate) | One incident, one alert |
| `loitering` | 3600 s per (plate, zone) | The rolling window is 60 min; a shorter cooldown re-fires the same window |
| `no_plate` | 1800 s per (camera, track) | Suppresses a stuck detection |

**Why a command centre that cries wolf gets switched off.** This is worth saying explicitly to judges, with arithmetic. A 200-camera city at realistic density produces on the order of 1.5 M `plate_reads` per day. A rule with a 0.1 % firing rate produces **1 500 alerts/day**. One operator triages an alert in 60–90 s including pulling the evidence crop, so a full 8-hour shift is ~150–200 alerts. At 1 500/day the queue is unservable, and the documented human response to an unservable alarm queue is **bulk dismissal** — at which point the true positives are dismissed along with the noise and the system's measured value drops to zero. The engineering answers, all implemented above:

1. **Confidence gating, not binary firing.** Every rule emits a 0–1 score; only `≥ auto_notify_min_confidence` (0.75) escalates. Everything below is written and searchable but silent.
2. **Cooldown + dedup keys** collapse repetition into `repeat_count`.
3. **Allowlist** (`plate_allowlist`) for ambulances, fire, police and government fleet, checked before insert.
4. **Shadow mode.** Every new or re-tuned rule ships `shadow: true`: alerts are written with `shadow = true`, excluded from the operator queue and from Realtime, and reviewed offline for a burn-in period. Only then is it promoted.
5. **Self-measurement.** Every `false_positive` transition writes a `rule_feedback` row, so `GET /v1/rules/metrics` returns precision per rule per day. A rule whose 7-day precision drops below 0.5 is auto-demoted to shadow by the scheduled job. The system knows when it is lying.
6. **Volume budget.** Target ≤ 150 notifying alerts per operator per day, city-wide. If a rule exceeds its `alerts/day` budget it gets tightened, not accepted.

### 9. Alert lifecycle state machine

```
                 ┌──────────────── supervisor reopen ────────────────┐
                 v                                                   │
  new ──ack──> acknowledged ──assign──> investigating ──resolve──> resolved
   │                │                        │                       │
   └───────┬────────┴────────────────────────┘                       │
           v                                                         │
     false_positive <───────────── supervisor reopen ────────────────┘
```

```sql
CREATE TYPE alert_status AS ENUM
  ('new','acknowledged','investigating','resolved','false_positive');

CREATE TABLE alert_transitions_allowed (
  from_status   alert_status NOT NULL,
  to_status     alert_status NOT NULL,
  min_role      text         NOT NULL,   -- viewer < operator < investigator < supervisor < admin
  note_required boolean      NOT NULL DEFAULT false,
  PRIMARY KEY (from_status, to_status)
);

INSERT INTO alert_transitions_allowed VALUES
  ('new','acknowledged','operator',false),
  ('new','false_positive','supervisor',true),
  ('acknowledged','investigating','investigator',false),
  ('acknowledged','false_positive','supervisor',true),
  ('investigating','resolved','investigator',true),
  ('investigating','false_positive','supervisor',true),
  ('resolved','investigating','supervisor',true),
  ('false_positive','investigating','supervisor',true);

CREATE OR REPLACE FUNCTION role_rank(r text) RETURNS int LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE r WHEN 'viewer' THEN 0 WHEN 'operator' THEN 1 WHEN 'investigator' THEN 2
                WHEN 'supervisor' THEN 3 WHEN 'admin' THEN 4 ELSE -1 END $$;

CREATE OR REPLACE FUNCTION alert_transition(p_alert_id uuid, p_to alert_status,
                                            p_note text DEFAULT NULL)
RETURNS alerts LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE v_from alert_status; v_rule text; v_role text;
        v_allowed alert_transitions_allowed%ROWTYPE; v_row alerts;
BEGIN
  SELECT role INTO v_role FROM user_roles WHERE user_id = auth.uid();
  IF v_role IS NULL THEN
    RAISE EXCEPTION 'no role assigned' USING ERRCODE = '42501';
  END IF;

  SELECT status, rule_id INTO v_from, v_rule FROM alerts WHERE id = p_alert_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'alert % not found', p_alert_id USING ERRCODE = 'P0002'; END IF;

  SELECT * INTO v_allowed FROM alert_transitions_allowed
   WHERE from_status = v_from AND to_status = p_to;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'illegal transition % -> %', v_from, p_to USING ERRCODE = '23514';
  END IF;
  IF role_rank(v_role) < role_rank(v_allowed.min_role) THEN
    RAISE EXCEPTION 'role % may not perform % -> %', v_role, v_from, p_to USING ERRCODE = '42501';
  END IF;
  IF v_allowed.note_required AND coalesce(btrim(p_note), '') = '' THEN
    RAISE EXCEPTION 'note is required for % -> %', v_from, p_to USING ERRCODE = '23514';
  END IF;

  PERFORM set_config('app.transition_ok', '1', true);   -- true = transaction-local
  UPDATE alerts
     SET status = p_to,
         status_changed_at = now(),
         assigned_to = CASE WHEN p_to = 'investigating' THEN auth.uid() ELSE assigned_to END
   WHERE id = p_alert_id
  RETURNING * INTO v_row;

  INSERT INTO alert_events (alert_id, from_status, to_status, actor_id, actor_role, note)
  VALUES (p_alert_id, v_from, p_to, auth.uid(), v_role, p_note);

  IF p_to = 'false_positive' THEN
    INSERT INTO rule_feedback (rule_id, alert_id, label) VALUES (v_rule, p_alert_id, 'fp');
  ELSIF p_to = 'resolved' THEN
    INSERT INTO rule_feedback (rule_id, alert_id, label) VALUES (v_rule, p_alert_id, 'tp');
  END IF;
  RETURN v_row;
END $$;

-- status may ONLY move through the function
CREATE OR REPLACE FUNCTION guard_alert_status() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status
     AND coalesce(current_setting('app.transition_ok', true), '0') <> '1' THEN
    RAISE EXCEPTION 'use alert_transition() to change alert status';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER alerts_status_guard BEFORE UPDATE ON alerts
FOR EACH ROW EXECUTE FUNCTION guard_alert_status();
```

Exposed as `POST /v1/alerts/{alert_id}/transition` with body `{"to": "investigating", "note": "..."}`; the API forwards the user's Supabase JWT so `auth.uid()` resolves. Only `false_positive` and `resolved` require a supervisor/investigator, because those two are the labels that feed rule tuning — letting an operator mark FPs would let alarm fatigue silently retune the engine. No alert ever triggers automated enforcement; a human transition is mandatory before any downstream action, which is both the DPDP Act 2023 accountability posture and the answer to "what if your model is wrong?".

**DPDP note:** a plate is personal data. `alert_events` is the processing audit trail (who looked, when, why). Retention: `plate_reads` 90 days, `alerts` and their evidence objects 1 year or until case closure, `alert_events` 3 years. Purpose limitation is enforced structurally — `rule_config.enabled` is the list of notified purposes, and adding a rule is a reviewable change, not a code deploy.

### 10. Fan-out to the dashboard

Two channels, one atomic write.

**Realtime (in-app, primary).** Supabase Realtime replicates `public.alerts` INSERT/UPDATE. RLS on `alerts` restricts rows to the operator's `district_code`, and Realtime honours RLS, so the filter is enforced server-side.

```ts
// apps/web/lib/realtime.ts
const channel = supabase
  .channel("alerts:live")
  .on("postgres_changes",
      { event: "INSERT", schema: "public", table: "alerts", filter: "shadow=eq.false" },
      ({ new: alert }) => store.upsert(alert))
  .on("postgres_changes",
      { event: "UPDATE", schema: "public", table: "alerts" },
      ({ new: alert }) => store.upsert(alert))
  .subscribe();
```

Realtime only delivers events after subscription, so the dashboard must seed with `GET /v1/alerts?status=new&shadow=false&limit=100&order=occurred_at.desc` on mount and reconcile by `id`. On `CHANNEL_ERROR` / disconnect, re-seed rather than resuming — cheap, and it prevents a silent gap.

**Webhooks (external, transactional outbox).** `notification_outbox` is written *in the same transaction* as the alert, so a webhook is never lost if the sender process dies. A 5 s asyncio drainer claims work with `FOR UPDATE SKIP LOCKED`:

```sql
UPDATE notification_outbox SET status = 'sending', attempts = attempts + 1, claimed_at = now()
 WHERE id IN (SELECT id FROM notification_outbox
               WHERE status = 'pending' AND next_attempt_at <= now()
               ORDER BY next_attempt_at LIMIT 25 FOR UPDATE SKIP LOCKED)
RETURNING *;
```

```python
body = json.dumps(envelope, separators=(",", ":")).encode()
ts   = str(int(time.time()))
sig  = hmac.new(sub.secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
headers = {"Content-Type": "application/json",
           "X-ANPR-Timestamp": ts,
           "X-ANPR-Delivery": str(row["id"]),
           "X-ANPR-Signature": f"sha256={sig}"}
```

The timestamp is inside the signed string to prevent replay; receivers must reject a skew > 300 s. Backoff schedule `[0, 5, 30, 300, 1800]` seconds, then `status = 'dead'` and a `webhook_dead_letter` counter that itself surfaces as an operational alert. Same drainer pattern serves `rule_followups` (deferred re-scoring) — one loop, two queues.

### 11. Build order for 36 hours

| Hours | Deliverable |
|---|---|
| 0–3 | Migrations: `alerts`, `alert_dedup`, `alert_events`, `alert_transitions_allowed`, `rule_config`, `camera_pair_distance`, `zones`, `camera_zone`, `watchlist`, `plate_allowlist`, `notification_outbox`, `rule_followups`, `rule_feedback` + indexes + `alert_transition()` |
| 3–8 | `spec.py`, `registry.py`, `engine.py`, `emit.py`; `cloned_plate` with the pytest fixture that reproduces the worked example exactly |
| 8–12 | `watchlist_hit`, `speeding`; `POST /v1/alerts/{id}/transition`; Realtime wired to the dashboard |
| 12–18 | `loitering`, `no_plate`; the outbox drainer; `POST /v1/rules/replay` |
| 18–24 | `hit_and_run` + `trajectory_break` against synthetic `vad_events` |
| 24–30 | Populate `camera_pair_distance` by hand for all demo pairs; tune thresholds against replayed footage; shadow-mode review |
| 30–36 | `convoy` if time remains; rehearse the deterministic cloned-plate scenario end to end |

A rule that has not been run against replayed footage before hour 30 does not go into the demo. The scripted stage sequence is: watchlist hit (instant, always works) → speeding (visible number) → **cloned plate with the map showing two pins and the 198.5 km/h arithmetic** → hit-and-run with the VAD clip. That order climbs in impressiveness and puts the most fragile rule after two guaranteed wins.

---

## Appendix — Interface Contracts Declared by This Section

- `table: plate_reads(id bigserial pk, track_id uuid, camera_id text, plate_text text, plate_text_raw text, plate_valid boolean, ocr_confidence real, char_confidences real[], vehicle_class text, vehicle_color text, captured_at timestamptz, ingested_at timestamptz, geom geography(Point,4326), crop_url text, no_plate_reason text, track_frames int, frames_without_plate int, rules_evaluated_at timestamptz)`
- `column REQUIRED by rules engine: plate_reads.rules_evaluated_at timestamptz NULL - idempotent evaluation claim`
- `column REQUIRED by rules engine: plate_reads.geom geography(Point,4326) denormalized from cameras.geom at insert`
- `column REQUIRED by rules engine: plate_reads.char_confidences real[] - per-character OCR confidence, used for min_char_confidence gate`
- `column REQUIRED by rules engine: plate_reads.plate_valid boolean - RTO/BH regex validation result`
- `columns REQUIRED for no_plate rule: plate_reads.no_plate_reason text IN ('not_detected','unreadable','occluded','obscured'), plate_reads.track_frames int, plate_reads.frames_without_plate int`
- `INGEST CONTRACT: edge worker emits exactly ONE consolidated plate_reads row per (track_id, camera_id), not one per frame`
- `table: cameras(camera_id text pk, name text, geom geography(Point,4326), cluster_id text NOT NULL DEFAULT camera_id, direction_deg smallint, road_name text, district_code text, is_active boolean)`
- `column REQUIRED by rules engine: cameras.cluster_id text NOT NULL - junction grouping, defaults to the camera's own id`
- `table: camera_pair_distance(from_camera_id text, to_camera_id text, road_distance_m integer, min_travel_time_s integer, speed_limit_kmph integer, is_adjacent boolean, source text, PRIMARY KEY (from_camera_id, to_camera_id)) - directed pairs`
- `table: alerts(id uuid pk default gen_random_uuid(), rule_id text, severity text, status alert_status default 'new', shadow boolean default false, confidence real, plate_text text, camera_id text, occurred_at timestamptz, created_at timestamptz default now(), status_changed_at timestamptz, primary_read_id bigint, related_read_ids bigint[], dedup_key text, repeat_count int, assigned_to uuid, district_code text, payload jsonb, geom geography(Point,4326))`
- `type: alert_status AS ENUM ('new','acknowledged','investigating','resolved','false_positive')`
- `table: alert_dedup(dedup_key text pk, rule_id text, last_fired_at timestamptz, hit_count int)`
- `table: alert_events(id bigserial pk, alert_id uuid, from_status alert_status, to_status alert_status, actor_id uuid, actor_role text, note text, created_at timestamptz default now())`
- `table: alert_transitions_allowed(from_status alert_status, to_status alert_status, min_role text, note_required boolean, PRIMARY KEY (from_status,to_status))`
- `table: rule_config(rule_id text pk, enabled boolean, shadow boolean, severity text, cooldown_s int, min_ocr_confidence real, min_char_confidence real, require_valid_format boolean, auto_notify_min_confidence real, params jsonb, updated_at timestamptz)`
- `table: rule_feedback(id bigserial pk, rule_id text, alert_id uuid, label text CHECK (label IN ('tp','fp')), created_at timestamptz)`
- `table: rule_followups(id bigserial pk, rule_id text, due_at timestamptz, status text, attempts int, claimed_at timestamptz, payload jsonb)`
- `table: zones(zone_id text pk, name text, kind text, polygon geography(Polygon,4326), risk_level smallint)`
- `table: camera_zone(camera_id text, zone_id text, PRIMARY KEY (camera_id, zone_id)) - materialized via ST_Covers(zones.polygon, cameras.geom)`
- `table: watchlist(id uuid pk, plate_text text, plate_norm text, reason text, severity text, source_ref text, active boolean, added_by uuid, added_at timestamptz, expires_at timestamptz)`
- `table: plate_allowlist(id uuid pk, plate_norm text unique, category text, active boolean, note text) - emergency/govt fleet, checked before every alert insert`
- `table: vad_events(id uuid pk, camera_id text, event_type text, score real, started_at timestamptz, ended_at timestamptz, clip_url text, geom geography(Point,4326))`
- `VAD CONTRACT: vad_events.event_type must include 'accident','collision','sudden_stop' for the hit_and_run rule; score is 0..1`
- `table: notification_outbox(id bigserial pk, alert_id uuid, channel uuid, payload jsonb, status text, attempts int, next_attempt_at timestamptz, claimed_at timestamptz)`
- `table: webhook_subscriptions(id uuid pk, url text, secret text, min_severity_rank int, active boolean)`
- `table: user_roles(user_id uuid pk, role text IN ('viewer','operator','investigator','supervisor','admin'), district_code text)`
- `sql function: alert_transition(p_alert_id uuid, p_to alert_status, p_note text) RETURNS alerts - SECURITY DEFINER, only legal way to change alerts.status`
- `sql function: role_rank(text) RETURNS int - viewer 0, operator 1, investigator 2, supervisor 3, admin 4`
- `sql function: severity_rank(text) RETURNS int - info 0, low 1, medium 2, high 3, critical 4`
- `sql trigger: alerts_status_guard BEFORE UPDATE ON alerts - blocks direct status writes unless app.transition_ok='1'`
- `sql trigger: AFTER INSERT ON plate_reads issuing pg_notify('plate_read_inserted', id::text)`
- `postgres channel: 'plate_read_inserted' carrying plate_reads.id as text`
- `index: plate_reads_plate_time_idx ON plate_reads (plate_text, captured_at DESC) WHERE plate_text IS NOT NULL`
- `index: watchlist_norm_trgm ON watchlist USING gin (plate_norm gin_trgm_ops)`
- `postgres extensions required: postgis, pg_trgm, fuzzystrmatch, pgcrypto`
- `endpoint: POST /v1/reads - ingest; inserts plate_reads and schedules evaluate_read(read_id)`
- `endpoint: GET /v1/alerts?status=&severity=&rule_id=&plate_text=&shadow=&from=&to=&limit=&order=`
- `endpoint: GET /v1/alerts/{alert_id}`
- `endpoint: POST /v1/alerts/{alert_id}/transition  body {to: alert_status, note?: string}`
- `endpoint: GET /v1/rules`
- `endpoint: PATCH /v1/rules/{rule_id}  body {enabled?, shadow?, severity?, cooldown_s?, params?}`
- `endpoint: GET /v1/rules/metrics?days=7 - precision per rule from rule_feedback`
- `endpoint: POST /v1/rules/replay  body {from, to, rule_ids?} - re-evaluates plate_reads with rules_evaluated_at IS NULL`
- `endpoint: POST /v1/watchlist, DELETE /v1/watchlist/{id}`
- `endpoint: POST /v1/webhooks - register webhook_subscriptions`
- `realtime channel: 'alerts:live' via supabase postgres_changes on public.alerts, INSERT filter shadow=eq.false, plus UPDATE`
- `webhook headers: X-ANPR-Signature: sha256=<hmac_sha256(secret, f"{ts}."+body)>, X-ANPR-Timestamp, X-ANPR-Delivery`
- `rule ids (stable strings): cloned_plate, speeding, hit_and_run, trajectory_break, loitering, watchlist_hit, no_plate, convoy`
- `config file: apps/api/app/rules/rules.yaml - seeds rule_config`
- `python module paths: apps/api/app/rules/{spec,registry,engine,context,emit,followups}.py and apps/api/app/rules/impl/*.py`
- `python entrypoint: evaluate_read(pool, read_id: int) -> list[str]`
- `python dataclasses: RuleSpec, AlertDraft, ReadContext; enums Severity(info|low|medium|high|critical), Trigger(on_read|on_vad_event|deferred|scheduled)`
- `threshold constant: cloned_plate.max_plausible_speed_kmph = 150`
- `threshold constant: cloned_plate.circuity_factor = 1.25 (fallback only when camera_pair_distance.road_distance_m is NULL)`
- `threshold constant: cloned_plate.clock_skew_tolerance_s = 5 (ADDED to elapsed)`
- `threshold constant: cloned_plate.min_separation_m = 350`
- `threshold constant: cloned_plate.min_elapsed_s = 2`
- `threshold constant: cloned_plate.lookback_minutes = 30`
- `threshold constant: cloned_plate gates min_ocr_confidence=0.90, min_char_confidence=0.80, require_valid_format=true`
- `threshold constant: speeding.min_baseline_m = 800, timestamp_uncertainty_s = 1.5, tolerance = max(5 kmph, 10% of limit)`
- `threshold constant: hit_and_run pre_impact_s=20, post_impact_s=10, flee_window_s=45, followup_delay_s=300`
- `threshold constant: trajectory_break.travel_time_multiplier = 3.0`
- `threshold constant: loitering window_s=3600, revisit_gap_s=300, min_sightings=5, min_dwell_s=1200, min_revisits=3`
- `threshold constant: convoy max_delta_s=20, min_shared_cameras=3, max_delta_jitter_s=6`
- `threshold constant: watchlist max_edit_distance=1, trigram set_limit=0.50`
- `threshold constant: no_plate min_track_frames=12, min_frames_without_plate=8`
- `threshold constant: global auto_notify_min_confidence = 0.75`
- `payload jsonb shape (cloned_plate): {prev_camera_id, curr_camera_id, prev_captured_at, curr_captured_at, straight_line_m, assumed_road_m, distance_source, elapsed_s, effective_elapsed_s, implied_speed_kmph, max_plausible_speed_kmph, prev_vehicle{class,color}, curr_vehicle{class,color}, corroboration[]}`
- `payload jsonb shape (speeding): {from_camera_id, to_camera_id, road_distance_m, elapsed_s, avg_speed_kmph, speed_limit_kmph, threshold_kmph, evidentiary: false}`
- `payload jsonb shape (watchlist_hit): {watchlist_id, match_type: exact|fuzzy, edits, diff_chars, reason, source_ref}`
- `retention policy: plate_reads 90 days, alerts 1 year, alert_events 3 years (DPDP Act 2023)`

## Appendix — MVP vs Stretch

- MVP: event-driven evaluate_read() called from POST /v1/reads via BackgroundTasks, plus pg_notify LISTEN safety net, with rules_evaluated_at as the idempotent claim
- MVP: RuleSpec dataclass + rules.yaml + rule_config table with 30 s hot reload
- MVP: cloned_plate rule complete with all five guards (same-camera skip, cluster skip, 350 m floor, 2 s min elapsed, 0.90/0.80 confidence gate) and the worked Andheri-Chembur pytest
- MVP: watchlist_hit with exact + one-edit fuzzy match via pg_trgm prefilter and levenshtein, OCR-confusion-aware scoring
- MVP: speeding using camera_pair_distance.road_distance_m only, labelled indicative-not-evidentiary
- MVP: loitering scheduled rule with the LAG() window-function revisit query
- MVP: no_plate low-severity flag
- MVP: alerts + alert_dedup atomic cooldown claim (ON CONFLICT DO UPDATE ... WHERE) + plate_allowlist suppression
- MVP: alert_transition() SQL function, alert_transitions_allowed matrix, alert_events audit, alerts_status_guard trigger, POST /v1/alerts/{id}/transition
- MVP: Supabase Realtime subscription on public.alerts with initial GET /v1/alerts seed and re-seed on reconnect
- MVP: hand-populated camera_pair_distance for all demo camera pairs (30-56 rows)
- MVP: hit_and_run stage-one rule fired from vad_events (on-scene query + flee_window check)
- STRETCH: trajectory_break deferred rule and its correlation bonus into hit_and_run confidence
- STRETCH: convoy / tailing rule with the stddev_pop gap-jitter guard (ships shadow: true)
- STRETCH: notification_outbox drainer with HMAC-signed webhooks, exponential backoff and dead-letter
- STRETCH: GET /v1/rules/metrics precision dashboard and auto-demotion of rules below 0.5 precision to shadow mode
- STRETCH: POST /v1/rules/replay over a historical window for offline threshold tuning
- STRETCH: PATCH /v1/rules/{rule_id} live threshold tuning from the dashboard UI

## Appendix — Risks

- Circuity factor over-estimation manufactures cloned-plate false positives, because the factor multiplies into the numerator of implied speed. Mitigation: 1.25 is chosen at the low end of the 1.3-1.6 empirical urban range, and it is bypassed entirely whenever camera_pair_distance has a real routed distance - which for the demo is every pair.
- Junction camera clusters generating phantom cloned-plate alerts. Mitigation: cameras.cluster_id skip plus the 350 m min_separation_m floor derived from timestamp uncertainty; below that distance the rule is mathematically incapable of firing on anything but noise.
- A single OCR character error naming an innocent registered owner as a plate cloner. Mitigation: dual gate of mean ocr_confidence >= 0.90 AND min(char_confidences) >= 0.80 AND RTO format validity, applied to BOTH sightings, not just the new one.
- Alarm fatigue: a rule that fires 1500 times a day gets bulk-dismissed and the true positives die with the noise. Mitigation: confidence gating at 0.75 for notification, per-rule cooldowns, plate_allowlist, shadow mode burn-in for every new rule, and rule_feedback-driven precision metrics with auto-demotion below 0.5.
- Double alerts from concurrent evaluation of the same plate by different camera workers. Mitigation: pg_advisory_xact_lock(hashtextextended(plate_text,0)) at the top of evaluation plus the atomic alert_dedup ON CONFLICT DO UPDATE ... WHERE cooldown claim, which cannot be won twice.
- Reads inserted directly into Supabase by an edge worker bypassing FastAPI would never be evaluated. Mitigation: pg_notify trigger + LISTEN task calling the same entrypoint, made safe by the rules_evaluated_at claim.
- A slow or crashing rule blocking ingest. Mitigation: rules run in BackgroundTasks outside the insert transaction, each rule is individually try/except-wrapped, and the 25 ms p95 budget is enforced by evaluating once per track rather than once per frame.
- If the edge worker emits one plate_reads row per frame instead of per track, alert volume and DB load rise by ~30x and the cloned-plate same-camera guard becomes the only thing standing between the demo and a flood. This contract must be verified before hour 12.
- Claiming point-to-point speed as prosecutable evidence invites a judge question that cannot be answered. Mitigation: payload carries evidentiary:false and the UI labels it INDICATIVE.
- Realtime only delivers post-subscription events, so a dashboard that does not seed from GET /v1/alerts silently misses everything that fired before the operator opened the tab. Mitigation: mandatory seed on mount and full re-seed on CHANNEL_ERROR rather than resume.
- Trajectory-break alone has a high natural false-positive rate (people park). Mitigation: standalone confidence capped at 0.45 and severity medium; it only escalates when correlated with a hit_and_run candidate on the same plate within 10 minutes.
- Convoy detection matches every pair of vehicles on a busy one-way arterial. Mitigation: the stddev_pop(dt) < 6 s gap-jitter guard, plus shipping the rule in shadow mode so it never reaches the operator queue during the demo.

## Appendix — Open Questions

- Does the schema section name the sightings table plate_reads (assumed here) or sightings? Every SQL block in this section must be renamed if it is the latter.
- Is plate_reads.geom denormalized from cameras at insert time, or must every rule join cameras? This section assumes denormalized geography(Point,4326) on plate_reads and a join only for cluster_id.
- Who owns vad_events - the edge/AI section or the API section? The hit_and_run rule needs an insert trigger or an explicit POST /v1/vad-events call to fire.
- Does the auth section provide user_roles(user_id, role, district_code), or does role live in the Supabase JWT app_metadata? alert_transition() currently reads a user_roles table.
- Is RLS on alerts scoped by district_code? If the demo runs single-district this can be deferred, but Realtime filtering depends on it.
- Confirm whether the Supabase project allows CREATE EXTENSION fuzzystrmatch (needed for levenshtein); if not, the one-edit fuzzy match must fall back to pure trigram similarity with a 0.85 cutoff.
- Does the edge worker have an NTP sync step on the Windows box? If not, clock_skew_tolerance_s may need to rise from 5 s to 30 s for a real deployment (irrelevant for the single-machine demo).
- Speed limit source: is speed_limit_kmph per camera pair (assumed) or per road segment from an external GIS layer?
