-- =====================================================================
--  NETRA — City-wide ANPR + Crime Tracking Platform
--  Canonical schema (SQLite dialect — the runnable demo store)
--
--  Production target is PostgreSQL 15 + PostGIS. This file is written so
--  the migration is mechanical:
--    - lat/lon REAL pairs      -> geography(Point,4326)
--    - haversine_m() UDF       -> ST_Distance(geography, geography)
--    - TEXT ISO-8601 UTC times -> timestamptz
--    - INTEGER PK AUTOINCREMENT-> bigserial
--  Nothing here relies on a SQLite-only feature beyond those four.
--
--  Time convention: every *_at / *_ts column is ISO-8601 UTC with a
--  trailing 'Z', lexicographically sortable. Never store local time.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Cameras: the physical (or replayed) sensor network.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cameras (
    id           TEXT PRIMARY KEY,             -- 'cam-01'
    name         TEXT    NOT NULL,             -- 'Sector 17 / Bank Square'
    lat          REAL    NOT NULL,
    lon          REAL    NOT NULL,
    bearing_deg  REAL,                         -- direction the lens faces, 0=N
    zone_id      TEXT REFERENCES zones(id),
    road_name    TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    rtsp_url     TEXT,                         -- never exposed to the browser
    created_at   TEXT    NOT NULL
);

-- Pairwise road distance between cameras. Straight-line distance badly
-- understates real travel distance, which is THE source of false
-- cloned-plate alerts. Where a pair is absent the rules engine falls
-- back to haversine * CIRCUITY_FACTOR.
CREATE TABLE IF NOT EXISTS camera_links (
    from_camera     TEXT NOT NULL REFERENCES cameras(id),
    to_camera       TEXT NOT NULL REFERENCES cameras(id),
    road_distance_m REAL NOT NULL,
    min_travel_s    REAL NOT NULL,             -- fastest legal traversal
    PRIMARY KEY (from_camera, to_camera)
);

-- ---------------------------------------------------------------------
-- Zones: geofences for loitering / restricted-area rules.
-- polygon_json is a GeoJSON-style [[lon,lat], ...] ring.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS zones (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'monitored'
                 CHECK (kind IN ('monitored','restricted','sensitive')),
    polygon_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Vehicle registry: one row per distinct plate ever seen.
-- Deliberately holds NO owner PII. Owner lookup is a separate, audited
-- system (see docs/12-privacy-ops.md) so a plate search cannot silently
-- become an identity lookup.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicles (
    plate          TEXT PRIMARY KEY,           -- normalised, no spaces: 'MH12DE1433'
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    sighting_count INTEGER NOT NULL DEFAULT 0,
    color          TEXT,
    vehicle_type   TEXT,                       -- car | truck | bus | motorcycle
    notes          TEXT
);

-- ---------------------------------------------------------------------
-- plate_reads: RAW per-frame OCR output. High volume, short retention.
-- This is the evidence trail behind a voted plate — without it the
-- confidence number is unauditable.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plate_reads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id   TEXT NOT NULL,                  -- '<camera_id>:<local track id>'
    camera_id  TEXT NOT NULL REFERENCES cameras(id),
    raw_text   TEXT NOT NULL,                  -- exactly what OCR returned
    confidence REAL NOT NULL,                  -- mean char confidence 0..1
    quality    REAL NOT NULL DEFAULT 1.0,      -- frame quality weight 0..1
    frame_ts   TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- sightings: ONE consolidated row per vehicle per camera pass.
-- This is the queryable spatio-temporal spine. The line-crossing rule in
-- the edge worker guarantees one row per pass, not one per frame — the
-- difference between 40 rows and 1 row for a single car.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sightings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plate         TEXT    NOT NULL,
    camera_id     TEXT    NOT NULL REFERENCES cameras(id),
    seen_at       TEXT    NOT NULL,
    confidence    REAL    NOT NULL,            -- post-vote confidence 0..1
    track_id      TEXT,
    frame_count   INTEGER NOT NULL DEFAULT 1,  -- frames that voted
    color         TEXT,
    vehicle_type  TEXT,
    speed_kmh     REAL,                        -- segment speed, if computable
    crop_path     TEXT,                        -- evidence image
    source        TEXT NOT NULL DEFAULT 'edge' -- edge | sim | manual
                  CHECK (source IN ('edge','sim','manual')),
    -- Idempotency: a retried publish must not double-insert a pass.
    dedupe_key    TEXT UNIQUE,
    created_at    TEXT    NOT NULL
);

-- Serves: "where has this plate been?" (the hero query) — plate first,
-- time descending, so the trajectory read is a single index range scan.
CREATE INDEX IF NOT EXISTS idx_sightings_plate_time ON sightings(plate, seen_at DESC);
-- Serves: the live feed and per-camera activity panels.
CREATE INDEX IF NOT EXISTS idx_sightings_time       ON sightings(seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_sightings_camera     ON sightings(camera_id, seen_at DESC);

-- ---------------------------------------------------------------------
-- alerts: output of the rules engine + VAD. Every alert must be
-- explainable, so evidence_json carries the numbers that fired it.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type    TEXT NOT NULL CHECK (alert_type IN
                  ('cloned_plate','speeding','loitering','watchlist_hit',
                   'hit_and_run','anomaly','no_plate','convoy',
                   -- Phase 3 safety-intelligence types.
                   'wanted_person','missing_person','weapon','violence',
                   'person_down','abandoned_object','accident')),
    severity      TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low','info')),
    -- Full incident lifecycle (SIH26127 §1):
    -- DETECTED -> UNDER REVIEW -> CONFIRMED/FALSE POSITIVE
    --          -> DISPATCHED -> RESPONDING -> RESOLVED
    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN
                  ('new','acknowledged','investigating','confirmed',
                   'dispatched','responding','resolved','false_positive')),
    plate         TEXT,
    camera_id     TEXT REFERENCES cameras(id),
    occurred_at   TEXT NOT NULL,
    title         TEXT NOT NULL,
    detail        TEXT NOT NULL,               -- human-readable "why"
    evidence_json TEXT NOT NULL DEFAULT '{}',  -- the numbers behind the call
    confidence    REAL NOT NULL DEFAULT 1.0,
    -- Suppresses duplicate alerts for the same underlying event.
    dedupe_key    TEXT UNIQUE,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    resolved_at   TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_status_time ON alerts(status, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_plate       ON alerts(plate, occurred_at DESC);

-- ---------------------------------------------------------------------
-- watchlist: stolen / wanted / BOLO plates.
-- Purpose-bound and expiring by design — an entry that never expires is
-- how a watchlist becomes permanent unaccountable surveillance.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlist (
    plate      TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    severity   TEXT NOT NULL DEFAULT 'high'
               CHECK (severity IN ('critical','high','medium','low')),
    case_ref   TEXT,                           -- FIR / case number
    added_by   TEXT NOT NULL,
    added_at   TEXT NOT NULL,
    expires_at TEXT,                           -- NULL only for stolen-vehicle
    is_active  INTEGER NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------------
-- review_queue: the human-in-the-loop fallback. Every plate whose voted
-- confidence lands below the auto-accept threshold arrives here rather
-- than being silently published as fact.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        TEXT NOT NULL,
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    best_guess      TEXT NOT NULL,
    confidence      REAL NOT NULL,
    candidates_json TEXT NOT NULL DEFAULT '[]', -- [{plate, score}, ...]
    crop_path       TEXT,
    frame_count     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','confirmed','corrected','rejected')),
    corrected_plate TEXT,
    reviewed_by     TEXT,
    reviewed_at     TEXT,
    seen_at         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status, created_at DESC);

-- ---------------------------------------------------------------------
-- anomaly_events: raw VAD / behavioural model output, before it is
-- promoted to an alert by hysteresis + cooldown.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS anomaly_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id  TEXT NOT NULL REFERENCES cameras(id),
    occurred_at TEXT NOT NULL,
    kind       TEXT NOT NULL,                  -- fight | accident | weapon | crowd | generic
    score      REAL NOT NULL,                  -- 0..1
    clip_path  TEXT,
    detail     TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'heuristic' -- heuristic | model | vlm
);

-- ---------------------------------------------------------------------
-- audit_log: append-only. "Who searched this plate, and why" is the
-- single most important record in a surveillance system.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,                   -- plate_search | evidence_view | watchlist_add ...
    target    TEXT,
    reason    TEXT,                            -- operator-stated justification
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);

-- ---------------------------------------------------------------------
-- ai_inferences: ledger for every hosted LLM/VLM call. Powers the cost
-- meter and answers "what did you send to a third party, and when".
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_inferences (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    provider   TEXT NOT NULL,                  -- gemini | grok | openai | anthropic | stub
    use_case   TEXT NOT NULL,                  -- plate_arbitration | anomaly_clip | nl_query ...
    input_hash TEXT NOT NULL,                  -- content hash -> cache key
    cached     INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    cost_usd   REAL NOT NULL DEFAULT 0.0,
    ok         INTEGER NOT NULL DEFAULT 1,
    detail     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ai_ts ON ai_inferences(ts DESC);

-- =====================================================================
--  Read-only views.
--
--  These exist for two reasons: the dashboard gets its joins done once,
--  and the natural-language query feature is restricted to querying
--  ONLY these. Generated SQL can therefore never reach rtsp_url,
--  audit_log or the watchlist, no matter what the model is told to do by
--  text it reads out of the database.
-- =====================================================================

CREATE VIEW IF NOT EXISTS v_sightings AS
SELECT s.id, s.plate, s.camera_id, c.name AS camera_name, c.lat, c.lon,
       s.seen_at, s.confidence, s.color, s.vehicle_type, s.speed_kmh,
       c.zone_id, s.frame_count, s.source
FROM sightings s JOIN cameras c ON c.id = s.camera_id;

CREATE VIEW IF NOT EXISTS v_alerts AS
SELECT a.id, a.alert_type, a.severity, a.status, a.plate, a.camera_id,
       c.name AS camera_name, a.occurred_at, a.title, a.detail, a.confidence
FROM alerts a LEFT JOIN cameras c ON c.id = a.camera_id;

CREATE VIEW IF NOT EXISTS v_cameras AS
SELECT c.id, c.name, c.lat, c.lon, c.zone_id, z.name AS zone_name,
       c.road_name, c.is_active
FROM cameras c LEFT JOIN zones z ON z.id = c.zone_id;
