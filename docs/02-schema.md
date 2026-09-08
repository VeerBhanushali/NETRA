<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## Spatio-Temporal Database Schema

This is the spine. Every other component — the ONNX/DirectML worker, the FastAPI gateway, the Next.js command centre — is a client of these tables. Build this first, in one migration run, before anyone writes inference code, because the worker's output contract is defined here.

Files live at `E:/D_Drive/Alll Websites/SIH HACKATHON/infra/supabase/migrations/`, applied with `supabase db push` (or pasted into the Supabase SQL editor in order — that is the 36-hour-safe path).

```
0000_extensions.sql
0001_enums.sql
0002_core_tables.sql      -- profiles, zones, cameras, camera_links, vehicles, watchlist
0003_event_tables.sql     -- track_sessions, plate_reads, sightings, vehicle_transits
0004_analytics_tables.sql -- anomaly_events, alerts, evidence, review_queue, audit_log
0005_indexes.sql
0006_functions_views.sql
0007_triggers.sql
0008_rls.sql
0009_realtime_cron.sql
0010_seed_pune.sql
```

---

### 1. The central modelling decision: three tiers of truth

An ANPR pipeline produces one OCR result *per frame per track*. A vehicle crossing a junction at 12 fps is visible for 1.5–4 s, so it yields **18–48 plate strings**, most of which disagree with each other (`MH12DE1433`, `MH12DE1433`, `MH12OE1433`, `MH12DE143`, `MHI2DE1433`). Collapsing those into one row at the point of ingest is the single most common architectural mistake, and modelling them as the queryable event table is the second.

We store all three tiers:

| Tier | Table | Cardinality per vehicle pass | Lifetime | Purpose |
|---|---|---|---|---|
| Raw | `plate_reads` | 18–48 rows | 48 h | Forensic audit + input to temporal voting. Proves in court/jury why the system said `MH12DE1433`. |
| Consolidated | `track_sessions` | exactly 1 row | 90 d | One tracked vehicle, one camera pass. Holds the temporally-voted final plate, the vote margin, entry/exit times. |
| Queryable event | `sightings` | exactly 1 row | 90 d | Denormalised, indexed spatio-temporal fact: *this plate was at this point at this instant*. Everything the map, the trajectory query and the rules engine touch. |

**Why `plate_reads` must exist:** temporal voting is the entire reason this system hits "near-perfect" accuracy on a 4 GB AMD card. A single INT8 PaddleOCR pass on a motion-blurred 40×120 px crop is maybe 82–90% string-exact. Position-wise weighted voting across 25 frames pushes that to 97–99%, because errors are uncorrelated across frames but the truth is not. You cannot vote on data you threw away. It is also the evidence trail: when an operator disputes a plate, you show the 25 candidates and the per-character confidences.

**Why `sightings` must exist separately from `track_sessions`:** `track_sessions` is write-heavy and mutable (it is UPDATEd as the track lives — `ended_at`, `frame_count`, `plate_text` all change). `sightings` is append-only and immutable, which is exactly what BRIN indexes, time partitioning, Realtime replication and a 100 M-row trajectory scan want. Mixing a hot mutable row with a cold analytical row in one table wrecks both. `sightings` is also denormalised with the camera's `geog` copied in, so the map query is a single-table GiST scan with no join.

Rule for the team: **the worker writes `plate_reads` continuously, writes `track_sessions` on track start and updates it on track end, and inserts exactly one `sightings` row when the track closes.** Nothing else writes `sightings`.

---

### 2. Geography vs geometry, and SRID

- `geography(Point,4326)` for `cameras.location`, `sightings.geog`, `zones.area`. Reason: every question we ask is metric — *"cameras within 800 m"*, *"how far apart were these two sightings"*, *"implied speed in km/h"*. With `geography`, `ST_Distance(a, b)` returns **metres on the spheroid** and `ST_DWithin(a, b, 800)` takes metres directly and is index-accelerated by GiST. With `geometry(Point,4326)`, `ST_Distance` returns **degrees**, which is meaningless (a degree of longitude is 105.7 km at Pune's latitude and 111.3 km of latitude), and every student on the team will get it wrong at 3 a.m.
- Do **not** store everything in UTM. The temptation is EPSG:32643 (WGS84 / UTM 43N, correct for Pune/Mumbai at 73.8° E). It is faster and exact for planar maths, but Mapbox GL, GeoJSON, the RTSP camera metadata and every phone GPS speak 4326. One SRID end-to-end beats a 12% speed gain. If a specific query needs planar geometry (e.g. buffering a corridor polygon), cast at query time: `ST_Buffer(geog::geometry::geography, 50)` or `ST_Transform(g::geometry, 32643)`.
- India spans UTM 42N–47N, so a single projected SRID cannot cover the country anyway. `geography/4326` is the only nationally-correct choice.
- Zones are `geography(Polygon,4326)`. `ST_Covers(zone.area, sighting.geog)` is the point-in-polygon test and is GiST-accelerated.

> **Verify:** on Supabase, PostGIS installs into the `extensions` schema. `extensions` is on the default `search_path` for `anon`/`authenticated`/`service_role`, so unqualified `ST_*` calls work; but inside `SECURITY DEFINER` functions we pin `set search_path = public, extensions` explicitly (done below) because that path is not inherited.

---

### 3. Extensions and enums

```sql
-- 0000_extensions.sql
create extension if not exists postgis      with schema extensions;
create extension if not exists pg_trgm      with schema extensions;  -- fuzzy plate search
create extension if not exists btree_gist   with schema extensions;  -- exclusion constraints (stretch)
create extension if not exists pg_cron;                              -- retention jobs
-- gen_random_uuid() is built into PG13+; no pgcrypto needed.
```

```sql
-- 0001_enums.sql
create type camera_status  as enum ('online','degraded','offline','maintenance');
create type vehicle_class  as enum ('car','motorcycle','auto_rickshaw','bus','truck','tractor','other','unknown');
create type plate_engine   as enum ('paddleocr','lprnet','manual','imported');
create type severity_level as enum ('info','low','medium','high','critical');
create type alert_status   as enum ('new','acknowledged','investigating','resolved','false_positive','expired');
create type alert_kind     as enum (
  'watchlist_hit','cloned_plate','overspeed','wrong_way','red_light_jump',
  'hit_and_run','loitering','abandoned_vehicle','route_deviation','convoy',
  'fight','accident','weapon','crowd_surge','camera_offline','low_confidence_read');
create type anomaly_kind   as enum ('fight','accident','weapon','fall','crowd_surge','loitering','object_abandoned','generic');
create type review_status  as enum ('pending','confirmed','corrected','rejected','expired');
create type app_role       as enum ('viewer','operator','investigator','admin');
create type watch_reason   as enum ('stolen','wanted','fir_linked','expired_permit','bolo','vip_escort','test_planted');
```

**Enum vs CHECK.** Enums for `alert_kind`, `severity_level`, `alert_status`, `app_role`: they are closed, small, ordered (severity sorts correctly out of the box — `order by severity desc` gives critical-first with no CASE), 4 bytes wide instead of a variable-length text, and they give the TypeScript codegen a real union type via `supabase gen types typescript`. The cost is that `alter type ... add value` cannot run inside a transaction block with other DDL in older PG and cannot be rolled back — acceptable, because these vocabularies are frozen before the demo. Use a **CHECK constraint** for anything open-ended or operator-editable (`vehicle_color`, `stream_kind`) so it can be widened in a plain migration.

---

### 4. Core reference tables

```sql
-- 0002_core_tables.sql
create table public.profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  full_name   text not null,
  badge_no    text unique,
  role        app_role not null default 'viewer',
  unit        text,                        -- 'Pune City Traffic', 'Crime Branch'
  is_active   boolean not null default true,
  created_at  timestamptz not null default now()
);
comment on table public.profiles is 'App-level identity mirrored from auth.users. RLS role source of truth.';

create table public.zones (
  id          uuid primary key default gen_random_uuid(),
  code        text not null unique,        -- 'Z-SHIVAJINAGAR'
  name        text not null,
  kind        text not null default 'ward'
              check (kind in ('ward','police_station','corridor','restricted','school','toll','custom')),
  area        geography(Polygon,4326) not null,
  speed_limit_kmph smallint check (speed_limit_kmph between 5 and 150),
  is_restricted boolean not null default false,   -- entry here is itself an alert
  created_at  timestamptz not null default now()
);
comment on column public.zones.is_restricted is 'True => any sighting inside raises a route_deviation/restricted-entry alert.';

create table public.cameras (
  id            uuid primary key default gen_random_uuid(),
  code          text not null unique,       -- 'PN-CAM-004'; used in evidence object paths
  name          text not null,
  zone_id       uuid references public.zones(id) on delete set null,
  location      geography(Point,4326) not null,
  heading_deg   smallint check (heading_deg between 0 and 359),  -- direction the lens faces
  fov_deg       smallint not null default 70 check (fov_deg between 10 and 180),
  mount_height_m numeric(4,2) check (mount_height_m between 1 and 20),
  stream_url    text not null,              -- rtsp://127.0.0.1:8554/cam004 in the demo
  stream_kind   text not null default 'rtsp' check (stream_kind in ('rtsp','file','hls','webcam')),
  resolution_w  int not null default 1920,
  resolution_h  int not null default 1080,
  target_fps    smallint not null default 12 check (target_fps between 1 and 30),
  speed_limit_kmph smallint check (speed_limit_kmph between 5 and 150),
  is_active     boolean not null default true,
  status        camera_status not null default 'offline',
  last_heartbeat_at timestamptz,
  worker_id     text,                       -- which edge process owns this feed
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
comment on column public.cameras.target_fps is
  'Frames/sec actually pushed to YOLOv8. 12 is the sustainable rate for 3-4 concurrent feeds on RX 6500M / ONNX Runtime DirectML at 640x640 INT8. Not a hardware property - a scheduling budget.';

-- Directed corridor between two cameras, with a MEASURED road distance.
-- Straight-line ST_Distance under-reports by 20-40% in a real street grid,
-- so section-speed enforcement must use a surveyed number, not the geodesic.
create table public.camera_links (
  id            uuid primary key default gen_random_uuid(),
  from_camera_id uuid not null references public.cameras(id) on delete cascade,
  to_camera_id   uuid not null references public.cameras(id) on delete cascade,
  road_distance_m  int not null check (road_distance_m > 0),
  speed_limit_kmph smallint not null default 50,
  min_plausible_s  int not null,   -- road_distance_m / (150 km/h) -> below this = impossible
  is_bidirectional boolean not null default false,
  constraint camera_links_distinct check (from_camera_id <> to_camera_id),
  constraint camera_links_uniq unique (from_camera_id, to_camera_id)
);

create table public.vehicles (
  plate_text   text primary key,
  plate_key    text generated always as (
                 translate(upper(regexp_replace(plate_text,'[^A-Za-z0-9]','','g')),'OIZSB','01258')
               ) stored,
  vehicle_class vehicle_class not null default 'unknown',
  color        text,
  make_model   text,
  owner_name   text,          -- DPDP: personal data. Populated only from an authorised VAHAN lookup.
  owner_phone  text,
  rto_state    text generated always as (substring(upper(plate_text) from 1 for 2)) stored,
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  sighting_count bigint not null default 0,
  is_flagged   boolean not null default false,
  notes        text,
  constraint vehicles_plate_format check (
    plate_text ~ '^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$'   -- MH12DE1433, DL8CAF5030, HR26DK8337, MH01A1234
 or plate_text ~ '^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$'           -- 22BH1234AA  (Bharat series)
 or plate_text ~ '^[0-9]{2}[A-Z]{2}[0-9]{6}[A-Z]$'          -- armed forces, e.g. 09AB123456C
  )
);
comment on column public.vehicles.plate_key is
  'Confusable-collapsed key: O->0, I->1, Z->2, S->5, B->8. Used to detect that MH12DE1433 and MHI2DE1433 are the same car. NEVER shown to the operator, only joined on.';
comment on table public.vehicles is
  'Identity registry. A row exists only when a plate has been read at >= 0.90 voted confidence at least once. Garbage OCR never lands here - it goes to review_queue.';

create table public.watchlist (
  id           uuid primary key default gen_random_uuid(),
  plate_text   text not null,
  plate_key    text generated always as (
                 translate(upper(regexp_replace(plate_text,'[^A-Za-z0-9]','','g')),'OIZSB','01258')
               ) stored,
  reason       watch_reason not null,
  severity     severity_level not null default 'high',
  fir_number   text,
  description  text,
  valid_from   timestamptz not null default now(),
  valid_until  timestamptz,
  is_active    boolean not null default true,
  created_by   uuid references public.profiles(id),
  created_at   timestamptz not null default now()
);
comment on table public.watchlist is
  'Not FK-constrained to vehicles: a stolen car is put on the watchlist BEFORE the system has ever seen it.';
```

---

### 5. Event tables

```sql
-- 0003_event_tables.sql
create table public.track_sessions (
  id             uuid primary key default gen_random_uuid(),
  camera_id      uuid not null references public.cameras(id) on delete cascade,
  tracker_id     int  not null,             -- ByteTrack local id; unique only within (camera, run)
  run_id         uuid not null,             -- worker process instance; makes tracker_id globally unique
  started_at     timestamptz not null,
  ended_at       timestamptz,
  frame_count    int not null default 0,
  vehicle_class  vehicle_class not null default 'unknown',
  class_confidence numeric(4,3),
  -- temporally voted result:
  plate_text     text references public.vehicles(plate_text) on update cascade,
  plate_raw      text,                      -- best single-frame string, kept even when voting fails
  plate_confidence numeric(4,3) check (plate_confidence between 0 and 1),
  vote_margin    numeric(4,3),              -- top char-vote share minus runner-up; < 0.15 => review
  read_count     int not null default 0,    -- how many plate_reads fed the vote
  engine         plate_engine not null default 'paddleocr',
  is_closed      boolean not null default false,
  created_at     timestamptz not null default now(),
  constraint track_sessions_uniq unique (run_id, camera_id, tracker_id),
  constraint track_sessions_time check (ended_at is null or ended_at >= started_at)
);

create table public.plate_reads (
  id             bigint generated always as identity primary key,
  track_session_id uuid not null references public.track_sessions(id) on delete cascade,
  camera_id      uuid not null references public.cameras(id) on delete cascade,
  frame_index    int  not null,
  read_at        timestamptz not null,
  raw_text       text not null,                    -- exactly what the OCR emitted, un-normalised
  norm_text      text generated always as (upper(regexp_replace(raw_text,'[^A-Za-z0-9]','','g'))) stored,
  ocr_confidence numeric(4,3) not null check (ocr_confidence between 0 and 1),
  char_confidences real[],                         -- per-character; length = length(norm_text)
  plate_bbox     int4[] not null,                  -- [x1,y1,x2,y2] in source frame pixels
  crop_w         smallint, crop_h smallint,        -- crop size: < 20px height => unreliable, weight down
  blur_score     real,                             -- variance of Laplacian; low = motion blur
  engine         plate_engine not null default 'paddleocr',
  is_format_valid boolean generated always as (
      upper(regexp_replace(raw_text,'[^A-Za-z0-9]','','g')) ~ '^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$'
   or upper(regexp_replace(raw_text,'[^A-Za-z0-9]','','g')) ~ '^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$'
  ) stored
);
comment on table public.plate_reads is
  'Per-frame raw OCR. NO format CHECK - garbage must be storable, that is the point. Purged at 48h by cron.';

create table public.sightings (
  id             bigint generated always as identity primary key,
  track_session_id uuid not null references public.track_sessions(id) on delete cascade,
  camera_id      uuid not null references public.cameras(id) on delete cascade,
  zone_id        uuid references public.zones(id) on delete set null,
  plate_text     text references public.vehicles(plate_text) on update cascade,  -- NULL = unresolved
  plate_key      text,
  plate_confidence numeric(4,3),
  vehicle_class  vehicle_class not null default 'unknown',
  vehicle_color  text,
  seen_at        timestamptz not null,      -- EVENT time (stream PTS mapped to demo clock)
  ingested_at    timestamptz not null default now(),  -- WALL-CLOCK write time
  dwell_ms       int,                       -- ended_at - started_at of the track
  geog           geography(Point,4326) not null,      -- denormalised camera location
  heading_deg    smallint,
  speed_kmph     numeric(5,1),              -- single-camera estimate; low trust, informational only
  bbox           int4[],
  evidence_id    uuid,                      -- FK added after evidence table exists
  is_verified    boolean not null default false      -- a human confirmed the plate
);
comment on column public.sightings.seen_at is
  'CANONICAL event time. In the demo, replayed files are offset to a synthetic clock so cross-camera logic is deterministic on stage. ingested_at is the real clock. Never sort a trajectory by ingested_at.';
comment on column public.sightings.geog is
  'Copied from cameras.location at insert by trg_sightings_enrich. Denormalised on purpose: the map query becomes a single-table GiST scan.';

-- Derived edge between two consecutive sightings of the same plate.
-- This is the workhorse for cloned-plate and section-speed detection.
create table public.vehicle_transits (
  id             bigint generated always as identity primary key,
  plate_text     text not null references public.vehicles(plate_text) on update cascade,
  from_sighting_id bigint not null references public.sightings(id) on delete cascade,
  to_sighting_id   bigint not null references public.sightings(id) on delete cascade,
  from_camera_id uuid not null references public.cameras(id),
  to_camera_id   uuid not null references public.cameras(id),
  departed_at    timestamptz not null,
  arrived_at     timestamptz not null,
  dt_seconds     numeric(10,2) not null check (dt_seconds > 0),
  geodesic_m     numeric(10,2) not null,    -- ST_Distance, straight line
  road_m         int,                       -- from camera_links when the pair is surveyed
  implied_kmph   numeric(7,2) not null,     -- uses road_m when present, else geodesic_m
  is_impossible  boolean not null default false,   -- implied_kmph > 150 => physical clone signal
  created_at     timestamptz not null default now(),
  constraint vehicle_transits_uniq unique (from_sighting_id, to_sighting_id)
);
comment on column public.vehicle_transits.is_impossible is
  '150 km/h threshold. Reasoning: Pune arterial limit is 50-60, expressway 100-120. geodesic_m UNDER-estimates the real road path, so implied_kmph is a LOWER BOUND on true speed. If even the lower bound exceeds 150 in a city grid, one plate is physically in two places -> clone. Guard rails: only computed when dt_seconds >= 5 AND geodesic_m >= 200, so two cameras on the same junction cannot fire it.';
```

---

### 6. Analytics, alerting and governance tables

```sql
-- 0004_analytics_tables.sql
create table public.anomaly_events (
  id            uuid primary key default gen_random_uuid(),
  camera_id     uuid not null references public.cameras(id) on delete cascade,
  zone_id       uuid references public.zones(id) on delete set null,
  kind          anomaly_kind not null,
  score         numeric(5,4) not null check (score between 0 and 1),  -- VAD anomaly score
  threshold     numeric(5,4) not null,      -- what the model was compared against, for auditability
  started_at    timestamptz not null,
  ended_at      timestamptz,
  clip_start_at timestamptz not null,
  clip_end_at   timestamptz not null,
  geog          geography(Point,4326) not null,
  model_name    text not null default 'videomae-rtfm-onnx',
  model_version text not null default 'v0.1',
  bbox          int4[],
  evidence_id   uuid,
  alert_id      uuid,
  created_at    timestamptz not null default now()
);
comment on column public.anomaly_events.threshold is
  'Stored per-row because the operating point is tuned live during the demo. Without it you cannot explain later why a 0.71 fired and a 0.69 did not.';

create table public.alerts (
  id            uuid primary key default gen_random_uuid(),
  kind          alert_kind not null,
  severity      severity_level not null,
  status        alert_status not null default 'new',
  title         text not null,              -- 'Cloned plate: MH12DE1433'
  detail        text,
  plate_text    text references public.vehicles(plate_text) on update cascade,
  camera_id     uuid references public.cameras(id) on delete set null,
  zone_id       uuid references public.zones(id) on delete set null,
  sighting_id   bigint references public.sightings(id) on delete set null,
  transit_id    bigint references public.vehicle_transits(id) on delete set null,
  anomaly_id    uuid references public.anomaly_events(id) on delete set null,
  watchlist_id  uuid references public.watchlist(id) on delete set null,
  geog          geography(Point,4326),
  raised_at     timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  occurrence_count int not null default 1,
  dedupe_key    text not null,              -- e.g. 'overspeed:MH12DE1433:PN-CAM-004:2026-09-05T14'
  acknowledged_by uuid references public.profiles(id),
  acknowledged_at timestamptz,
  resolved_by   uuid references public.profiles(id),
  resolved_at   timestamptz,
  resolution_note text,
  payload       jsonb not null default '{}'::jsonb,   -- rule-specific numbers
  constraint alerts_ack_consistent check (
    (status = 'new' and acknowledged_at is null) or status <> 'new'),
  constraint alerts_resolution check (
    (status in ('resolved','false_positive')) = (resolved_at is not null))
);
comment on column public.alerts.dedupe_key is
  'Alert-storm control. A cloned plate seen 40 times must be ONE row with occurrence_count=40, not 40 rows. The worker always does INSERT ... ON CONFLICT (dedupe_key) WHERE status not in (resolved,false_positive,expired) DO UPDATE.';

create table public.evidence (
  id            uuid primary key default gen_random_uuid(),
  bucket        text not null default 'evidence',
  object_path   text not null unique,
  kind          text not null check (kind in ('plate_crop','vehicle_crop','context_frame','clip','montage')),
  mime_type     text not null default 'image/jpeg',
  size_bytes    int,
  width         int, height int,
  sha256        text,                       -- tamper-evidence for chain of custody
  camera_id     uuid references public.cameras(id) on delete set null,
  track_session_id uuid references public.track_sessions(id) on delete set null,
  captured_at   timestamptz not null,
  retain_until  timestamptz not null default (now() + interval '7 days'),
  created_at    timestamptz not null default now()
);
comment on column public.evidence.object_path is
  'Supabase Storage key. Convention: {camera_code}/{YYYY}/{MM}/{DD}/{track_session_id}/{kind}.jpg';
comment on column public.evidence.retain_until is
  'Default 7 days. Bumped to +1 year by trg_alert_evidence_hold when the evidence is attached to an alert (DPDP storage limitation: keep only as long as the purpose lasts).';

alter table public.sightings       add constraint sightings_evidence_fk
  foreign key (evidence_id) references public.evidence(id) on delete set null;
alter table public.anomaly_events  add constraint anomaly_evidence_fk
  foreign key (evidence_id) references public.evidence(id) on delete set null;
alter table public.anomaly_events  add constraint anomaly_alert_fk
  foreign key (alert_id) references public.alerts(id) on delete set null;

create table public.review_queue (
  id            uuid primary key default gen_random_uuid(),
  track_session_id uuid not null unique references public.track_sessions(id) on delete cascade,
  camera_id     uuid not null references public.cameras(id) on delete cascade,
  suggested_plate text,
  candidates    jsonb not null default '[]'::jsonb,  -- [{"text":"MH12DE1433","votes":0.61},...]
  confidence    numeric(4,3) not null,
  vote_margin   numeric(4,3),
  evidence_id   uuid references public.evidence(id) on delete set null,
  status        review_status not null default 'pending',
  corrected_plate text,
  reviewed_by   uuid references public.profiles(id),
  reviewed_at   timestamptz,
  seen_at       timestamptz not null,
  created_at    timestamptz not null default now()
);
comment on table public.review_queue is
  'Human-in-the-loop. Populated when voted confidence < 0.90 OR vote_margin < 0.15 OR the string fails the RTO format regex. Confirmed corrections are written back to sightings and become fine-tuning labels.';

create table public.audit_log (
  id            bigint generated always as identity primary key,
  actor_id      uuid references public.profiles(id),
  actor_role    app_role,
  action        text not null,   -- 'alert.acknowledge','vehicle.lookup','plate.search','evidence.download'
  entity_table  text not null,
  entity_id     text,
  before_data   jsonb,
  after_data    jsonb,
  ip_address    inet,
  user_agent    text,
  at            timestamptz not null default now()
);
comment on table public.audit_log is
  'DPDP Act 2023 accountability. EVERY read of owner_name/owner_phone and EVERY evidence download is logged from the FastAPI layer, not just writes. Append-only: no UPDATE/DELETE policy exists for any role.';
```

---

### 7. Indexes — each with the query it serves

```sql
-- 0005_indexes.sql
-- Map: "everything in the last 60 minutes", "what is near this point"
create index sightings_seen_at_brin  on public.sightings using brin (seen_at) with (pages_per_range = 32);
create index sightings_geog_gix      on public.sightings using gist (geog);
create index sightings_camera_time   on public.sightings (camera_id, seen_at desc);
-- THE trajectory index: vehicle_trajectory() lives or dies on this one
create index sightings_plate_time    on public.sightings (plate_text, seen_at desc) where plate_text is not null;
create index sightings_plate_key     on public.sightings (plate_key, seen_at desc) where plate_key is not null;
create index sightings_unresolved    on public.sightings (seen_at desc) where plate_text is null;
create index sightings_zone_time     on public.sightings (zone_id, seen_at desc);

-- Plate search box: exact, prefix, and fuzzy
create index vehicles_plate_trgm     on public.vehicles using gin (plate_text extensions.gin_trgm_ops);
create index vehicles_plate_prefix   on public.vehicles (plate_text text_pattern_ops);
create index vehicles_plate_key      on public.vehicles (plate_key);
create index vehicles_last_seen      on public.vehicles (last_seen_at desc);

-- Watchlist join on every single sighting -> must be a partial index on the active set
create index watchlist_active_key    on public.watchlist (plate_key)
  where is_active and (valid_until is null or valid_until > now());
```
> **Verify:** the `now()` predicate makes this index non-IMMUTABLE and PostgreSQL will reject it. Use `create index watchlist_active_key on public.watchlist (plate_key) where is_active;` and filter `valid_until` in the query. Ship the simple form.

```sql
-- Alert queue: the dashboard's hottest query, and it only ever wants open alerts
create index alerts_open_queue on public.alerts (severity desc, raised_at desc)
  where status in ('new','acknowledged','investigating');
create unique index alerts_dedupe_open on public.alerts (dedupe_key)
  where status in ('new','acknowledged','investigating');
create index alerts_plate_time on public.alerts (plate_text, raised_at desc);
create index alerts_geog_gix   on public.alerts using gist (geog);
create index alerts_raised_brin on public.alerts using brin (raised_at);

-- Voting + forensic drill-down
create index plate_reads_track      on public.plate_reads (track_session_id, frame_index);
create index plate_reads_read_brin  on public.plate_reads using brin (read_at) with (pages_per_range = 16);
create index track_sessions_cam_time on public.track_sessions (camera_id, started_at desc);
create index track_sessions_open    on public.track_sessions (camera_id) where not is_closed;
create index track_sessions_plate   on public.track_sessions (plate_text, started_at desc);

-- Speed / clone engine
create index transits_plate_time    on public.vehicle_transits (plate_text, arrived_at desc);
create index transits_impossible    on public.vehicle_transits (arrived_at desc) where is_impossible;

-- Geofencing
create index zones_area_gix on public.zones using gist (area);

create index review_pending on public.review_queue (seen_at desc) where status = 'pending';
create index anomaly_cam_time on public.anomaly_events (camera_id, started_at desc);
create index audit_actor_time on public.audit_log (actor_id, at desc);
create index audit_at_brin    on public.audit_log using brin (at);
```

**Why BRIN and not B-tree on `seen_at`.** `sightings` is append-only in near-time order, so physical page order correlates with `seen_at` at ~0.99. A BRIN index over 10 M rows is roughly **48 KB**; the equivalent B-tree is ~220 MB. For "last 60 minutes" and "between 14:00 and 15:00" BRIN is within a few ms of B-tree. Keep the *composite* `(plate_text, seen_at desc)` as a real B-tree, because that one is used for equality-then-ordering and BRIN cannot serve it. This pairing — BRIN for time-range scans, B-tree composite for per-plate history — is the whole indexing story.

**Trigram vs exact.** `pg_trgm` GIN on `vehicles.plate_text` powers the operator typing `MH12DE` or a half-remembered `MH12D3` (`similarity(plate_text,$1) > 0.4 order by similarity desc`). `text_pattern_ops` is required separately because Supabase databases use a non-`C` collation, under which a plain B-tree cannot serve `LIKE 'MH12%'`. Both cost ~20 MB at demo scale; keep both.

---

### 8. Time partitioning: the honest answer

**Do not partition in the 36 hours.** Declarative range partitioning of `sightings` by `seen_at` is correct at 50 M+ rows, but it costs you: the primary key must become `(id, seen_at)`, every FK pointing at `sightings` (from `alerts`, `vehicle_transits`) must carry `seen_at` too or be dropped, `pg_cron` must pre-create partitions or inserts hard-fail at midnight, and Supabase Realtime on a partitioned parent requires `publish_via_partition_root = true` on the publication (> **Verify:** Supabase's managed `supabase_realtime` publication may not have this set, in which case the browser silently receives nothing — a demo-day catastrophe). At the demo's ~150 k total sightings, a BRIN scan is single-digit milliseconds. Partitioning buys nothing and risks everything.

Ship the DDL as a commented stretch block so a judge asking "does this scale?" gets a real answer:

```sql
-- STRETCH ONLY - do not run before the demo.
create table public.sightings_p (
  like public.sightings including defaults including constraints,
  primary key (id, seen_at)
) partition by range (seen_at);
create table sightings_p_2026_09 partition of public.sightings_p
  for values from ('2026-09-01') to ('2026-10-01');
-- + a pg_cron job on the 25th of each month to create the next partition.
```
The 36-hour-compatible substitute for partition-drop is a `pg_cron` `DELETE` with a BRIN-driven range predicate (section 12) — slower, but it cannot break the schema at 2 a.m.

---

### 9. Triggers: enrichment, voting, clone detection

```sql
-- 0007_triggers.sql
create or replace function public.fn_touch_updated_at() returns trigger
language plpgsql as $$
begin new.updated_at := now(); return new; end $$;
create trigger trg_cameras_touch before update on public.cameras
  for each row execute function public.fn_touch_updated_at();

-- Fill geog + zone_id from the camera; keep vehicles.last_seen_at fresh.
create or replace function public.fn_sightings_enrich() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
declare cam public.cameras%rowtype;
begin
  select * into cam from public.cameras where id = new.camera_id;
  new.geog        := coalesce(new.geog, cam.location);
  new.heading_deg := coalesce(new.heading_deg, cam.heading_deg);
  new.zone_id     := coalesce(new.zone_id, cam.zone_id,
                       (select z.id from public.zones z
                         where ST_Covers(z.area, new.geog) limit 1));
  if new.plate_text is not null then
    new.plate_key := translate(upper(new.plate_text),'OIZSB','01258');
    update public.vehicles
       set last_seen_at   = greatest(last_seen_at, new.seen_at),
           sighting_count = sighting_count + 1
     where plate_text = new.plate_text;
  end if;
  return new;
end $$;
create trigger trg_sightings_enrich before insert on public.sightings
  for each row execute function public.fn_sightings_enrich();

-- Build the transit edge to the previous sighting of the same plate.
create or replace function public.fn_build_transit() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
declare prev public.sightings%rowtype;
        d_m numeric; dt numeric; link public.camera_links%rowtype; kmph numeric; use_m numeric;
begin
  if new.plate_text is null then return null; end if;
  select * into prev from public.sightings
   where plate_text = new.plate_text and seen_at < new.seen_at
   order by seen_at desc limit 1;
  if not found then return null; end if;

  dt  := extract(epoch from (new.seen_at - prev.seen_at));
  d_m := ST_Distance(prev.geog, new.geog);
  if dt < 5 or d_m < 200 then return null; end if;   -- same junction / noise guard

  select * into link from public.camera_links
   where from_camera_id = prev.camera_id and to_camera_id = new.camera_id;
  use_m := coalesce(link.road_distance_m, d_m);
  kmph  := (use_m / dt) * 3.6;

  insert into public.vehicle_transits(
    plate_text, from_sighting_id, to_sighting_id, from_camera_id, to_camera_id,
    departed_at, arrived_at, dt_seconds, geodesic_m, road_m, implied_kmph, is_impossible)
  values (new.plate_text, prev.id, new.id, prev.camera_id, new.camera_id,
          prev.seen_at, new.seen_at, dt, d_m, link.road_distance_m, kmph, kmph > 150)
  on conflict do nothing;
  return null;
end $$;
create trigger trg_build_transit after insert on public.sightings
  for each row execute function public.fn_build_transit();
```

Position-wise temporal voting, as an authoritative SQL implementation (the Python worker does the same thing in-process for latency; this function is the fallback and the thing you show a judge):

```sql
create or replace function public.fn_vote_plate(p_track uuid)
returns table (voted_text text, confidence numeric, margin numeric, reads int)
language sql stable set search_path = public as $$
with r as (
  select norm_text, ocr_confidence,
         coalesce(char_confidences, array_fill(ocr_confidence::real, array[length(norm_text)])) cc
  from public.plate_reads
  where track_session_id = p_track
    and length(norm_text) between 8 and 11
),
modal as (select length(norm_text) len from r group by 1 order by count(*) desc, 1 limit 1),
chars as (
  select g.i, substring(r.norm_text from g.i for 1) ch,
         sum(r.cc[g.i] * r.ocr_confidence) w
  from r, modal m, generate_series(1, m.len) g(i)
  where length(r.norm_text) = m.len
  group by g.i, 2
),
ranked as (select i, ch, w, row_number() over (partition by i order by w desc) rn,
                  w / nullif(sum(w) over (partition by i),0) share from chars)
select string_agg(ch, '' order by i) filter (where rn = 1),
       round(avg(share) filter (where rn = 1)::numeric, 3),
       round((min(share) filter (where rn = 1)
              - coalesce(max(share) filter (where rn = 2), 0))::numeric, 3),
       (select count(*)::int from r)
from ranked;
$$;
```

---

### 10. The four functions/views the dashboard actually calls

```sql
-- 0006_functions_views.sql

-- (1) Trajectory for the map replay. Returns ordered points + leg speed.
create or replace function public.vehicle_trajectory(
  p_plate text, p_from timestamptz, p_to timestamptz)
returns table (
  seq int, sighting_id bigint, camera_id uuid, camera_code text, camera_name text,
  zone_name text, seen_at timestamptz, lon double precision, lat double precision,
  plate_confidence numeric, leg_seconds numeric, leg_metres numeric, leg_kmph numeric,
  evidence_path text)
language sql stable security definer set search_path = public, extensions as $$
  with s as (
    select si.id, si.camera_id, c.code, c.name cam_name, z.name zone_name, si.seen_at,
           ST_X(si.geog::geometry) lon, ST_Y(si.geog::geometry) lat,
           si.plate_confidence, si.geog, e.object_path,
           lag(si.seen_at) over w prev_t, lag(si.geog) over w prev_g
    from public.sightings si
    join public.cameras c on c.id = si.camera_id
    left join public.zones z on z.id = si.zone_id
    left join public.evidence e on e.id = si.evidence_id
    where si.plate_text = upper(p_plate)
      and si.seen_at >= p_from and si.seen_at < p_to
    window w as (order by si.seen_at)
  )
  select row_number() over (order by seen_at)::int, id, camera_id, code, cam_name, zone_name,
         seen_at, lon, lat, plate_confidence,
         round(extract(epoch from (seen_at - prev_t))::numeric, 1),
         round(ST_Distance(prev_g, geog)::numeric, 1),
         round((ST_Distance(prev_g, geog) / nullif(extract(epoch from (seen_at - prev_t)),0) * 3.6)::numeric, 1),
         object_path
  from s order by seen_at;
$$;
-- Called as: POST /rest/v1/rpc/vehicle_trajectory {"p_plate":"MH12DE1433","p_from":"...","p_to":"..."}
-- Uses sightings_plate_time. ~3 ms for 40 points on the demo dataset.

-- (2) Live feed: the left-hand rail of the command centre.
create or replace view public.v_live_sightings as
select si.id, si.seen_at, si.plate_text, si.plate_confidence, si.vehicle_class, si.vehicle_color,
       c.code camera_code, c.name camera_name, z.name zone_name,
       ST_X(si.geog::geometry) lon, ST_Y(si.geog::geometry) lat,
       si.is_verified, e.object_path plate_crop_path,
       exists (select 1 from public.watchlist w
                where w.plate_key = si.plate_key and w.is_active
                  and (w.valid_until is null or w.valid_until > now())) as is_watchlisted
from public.sightings si
join public.cameras c on c.id = si.camera_id
left join public.zones z on z.id = si.zone_id
left join public.evidence e on e.id = si.evidence_id
where si.seen_at > now() - interval '60 minutes'
order by si.seen_at desc;

-- (3) Alert queue, already joined and ranked. The dashboard never joins alerts itself.
create or replace view public.v_alert_queue as
select a.id, a.kind, a.severity, a.status, a.title, a.detail, a.plate_text,
       a.raised_at, a.last_seen_at, a.occurrence_count, a.payload,
       c.code camera_code, c.name camera_name, z.name zone_name,
       ST_X(a.geog::geometry) lon, ST_Y(a.geog::geometry) lat,
       p.full_name acknowledged_by_name,
       extract(epoch from (now() - a.raised_at))::int age_seconds,
       (select count(*) from public.evidence ev
         where ev.track_session_id = (select track_session_id from public.sightings
                                       where id = a.sighting_id)) evidence_count
from public.alerts a
left join public.cameras c on c.id = a.camera_id
left join public.zones z   on z.id = a.zone_id
left join public.profiles p on p.id = a.acknowledged_by
where a.status in ('new','acknowledged','investigating')
order by a.severity desc, a.raised_at desc;

-- (4) Camera health strip + per-camera throughput for the ops bar.
create or replace view public.v_camera_health as
select c.id, c.code, c.name, c.status, c.is_active, c.last_heartbeat_at,
       ST_X(c.location::geometry) lon, ST_Y(c.location::geometry) lat,
       case when c.last_heartbeat_at is null then 'offline'
            when c.last_heartbeat_at < now() - interval '90 seconds' then 'offline'
            when c.last_heartbeat_at < now() - interval '30 seconds' then 'degraded'
            else 'online' end effective_status,
       (select count(*) from public.sightings s
         where s.camera_id = c.id and s.seen_at > now() - interval '15 minutes') sightings_15m,
       (select round(avg(plate_confidence),3) from public.sightings s
         where s.camera_id = c.id and s.seen_at > now() - interval '60 minutes'
           and s.plate_text is not null) avg_conf_60m
from public.cameras c order by c.code;

-- Bonus: radius search for "show me every camera within 1 km of the incident"
create or replace function public.cameras_near(p_lat double precision, p_lon double precision, p_radius_m int default 1000)
returns table (id uuid, code text, name text, distance_m double precision, lon double precision, lat double precision)
language sql stable set search_path = public, extensions as $$
  select c.id, c.code, c.name,
         round(ST_Distance(c.location, ST_MakePoint(p_lon, p_lat)::geography)::numeric, 1)::float8,
         ST_X(c.location::geometry), ST_Y(c.location::geometry)
  from public.cameras c
  where ST_DWithin(c.location, ST_MakePoint(p_lon, p_lat)::geography, p_radius_m)
  order by 4;
$$;
```
> **Verify:** views owned by `postgres` in Supabase may run with the definer's rights. Newer PG/Supabase supports `alter view ... set (security_invoker = on)`; set it on all four views so RLS on the base tables still applies to the caller. If your PG version rejects it, front the views with FastAPI using the service key instead of exposing them via PostgREST.

---

### 11. Supabase: RLS, Realtime, and the service-key trap

```sql
-- 0008_rls.sql
create or replace function public.current_app_role() returns app_role
language sql stable security definer set search_path = public as $$
  select coalesce((select role from public.profiles
                    where id = (select auth.uid()) and is_active), 'viewer'::app_role);
$$;
-- (select auth.uid()) not bare auth.uid(): the subselect is evaluated ONCE per query
-- as an InitPlan instead of once per row. On a 100k-row scan that is the difference
-- between 40 ms and 4 s. This is the single most important Supabase RLS performance rule.

alter table public.cameras, public.zones, public.vehicles, public.sightings,
             public.track_sessions, public.plate_reads, public.vehicle_transits,
             public.anomaly_events, public.alerts, public.evidence,
             public.watchlist, public.review_queue, public.profiles,
             public.audit_log enable row level security;
-- (run one ALTER TABLE per table; the comma form above is shorthand for the doc)

-- anon: NO policies at all => zero rows. The public internet sees nothing.
-- authenticated read tiers:
create policy sightings_read on public.sightings for select to authenticated
  using (public.current_app_role() in ('viewer','operator','investigator','admin'));
create policy vehicles_read on public.vehicles for select to authenticated
  using (public.current_app_role() in ('operator','investigator','admin'));
create policy watchlist_read on public.watchlist for select to authenticated
  using (public.current_app_role() in ('investigator','admin'));
create policy watchlist_write on public.watchlist for all to authenticated
  using (public.current_app_role() = 'admin')
  with check (public.current_app_role() = 'admin');
create policy alerts_read on public.alerts for select to authenticated using (true);
-- operators may change lifecycle state, nothing else. Column-level lock is enforced in FastAPI;
-- the DB guarantee is "cannot invent or delete an alert".
create policy alerts_update on public.alerts for update to authenticated
  using (public.current_app_role() in ('operator','investigator','admin'))
  with check (public.current_app_role() in ('operator','investigator','admin'));
create policy review_read on public.review_queue for select to authenticated
  using (public.current_app_role() in ('operator','investigator','admin'));
create policy review_update on public.review_queue for update to authenticated
  using (public.current_app_role() in ('operator','investigator','admin'))
  with check (true);
create policy profiles_self on public.profiles for select to authenticated
  using (id = (select auth.uid()) or public.current_app_role() = 'admin');
create policy audit_read on public.audit_log for select to authenticated
  using (public.current_app_role() = 'admin');
-- audit_log deliberately has NO insert/update/delete policy for authenticated: append-only via service_role.
-- plate_reads has NO authenticated policy: raw OCR noise is never browser-visible.
```

**`service_role` bypasses RLS entirely.** That key belongs in exactly two places: the Python edge worker's `.env` (a process on the operator's machine) and the FastAPI server's environment. It must **never** appear in a `NEXT_PUBLIC_*` variable, never in a Next.js Client Component, never in `next.config.js`. A leaked service key is a full database read/write to anyone who opens DevTools — including `owner_name`/`owner_phone` on `vehicles`, which under the **DPDP Act 2023** is a reportable personal-data breach. Concretely for the team:

- `.env.local`: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` only.
- `SUPABASE_SERVICE_ROLE_KEY` is read only in Route Handlers / Server Actions / FastAPI.
- Add to `.gitignore` before the first commit, and grep the built bundle once: `grep -r "service_role" .next/static/` must return nothing.

**Realtime.**

```sql
-- 0009_realtime_cron.sql
alter publication supabase_realtime add table public.alerts;
alter publication supabase_realtime add table public.anomaly_events;
alter publication supabase_realtime add table public.cameras;
alter publication supabase_realtime add table public.sightings;
alter publication supabase_realtime add table public.review_queue;
alter table public.alerts  replica identity full;   -- so UPDATE payloads carry `old_record`
alter table public.cameras replica identity full;
```
- **`alerts`** — the whole point of a command centre: a new critical alert must appear without a refresh. `replica identity full` so the UI can diff `new`→`acknowledged`.
- **`cameras`** — status pill flips online/offline live. Tiny table, tiny WAL cost.
- **`anomaly_events`, `review_queue`** — low volume, high operator value.
- **`sightings`** — enabled *for the demo only*. At 12 replayed feeds the insert rate is ~5–10 rows/s, which Realtime handles comfortably. Realtime's row filters are applied server-side but every WAL row is still decoded per connected client, so above roughly **50 inserts/s** switch the live rail to 2-second polling of `v_live_sightings`, or have FastAPI aggregate and push on a Realtime *Broadcast* channel. Write that switch as a feature flag now.
- **`plate_reads` is never on Realtime.** 300 k rows/camera/day of OCR noise would saturate the replication slot and take the alerts channel down with it. This is the most likely way for the team to break their own demo.

**Storage.** One private bucket `evidence`. No public policy. The browser never gets a raw object URL; FastAPI issues a 60-second signed URL (`create_signed_url`) and writes an `audit_log` row with `action='evidence.download'` at the same time.

---

### 12. Retention, purge and storage math

Real-world numbers for one busy Pune junction (~25,000 vehicle passes across an 18-hour active day, `target_fps = 12`, mean track 1.6 s ⇒ ~18 plate_reads per pass):

| Table | Rows/camera/day | Row + index bytes | MB/camera/day |
|---|---:|---:|---:|
| `plate_reads` | 450,000 | ~240 | **108** |
| `sightings` | 25,000 | ~500 | 12 |
| `track_sessions` | 25,000 | ~320 | 8 |
| `vehicle_transits` | ~14,000 | ~200 | 3 |
| `alerts` | ~60 | ~700 | <1 |
| **Total (hot)** | | | **~131 MB** |
| **Total (after 48 h `plate_reads` purge)** | | | **~23 MB** |

Twelve cameras: **1.6 GB/day** at peak, settling to **~280 MB/day / 8.4 GB/month** once raw reads roll off. The Supabase free tier gives 500 MB of database, so a free project holds roughly **2 days** of 12-camera real traffic — fine, because the demo dataset (12 looped clips, ~1,500 sightings/hour total) is about 100× smaller and fits in ~60 MB.

Evidence is the real cost. Plate crop ≈ 8 KB, context frame ≈ 60 KB, 6-second 720p clip ≈ 1.5 MB. Storing crop+frame for *every* sighting is 25,000 × 68 KB = **1.7 GB/camera/day** — impossible on a 1 GB free bucket. Policy:

1. Full evidence (crop + frame) for every **alert** and every **review_queue** item.
2. Plate crop only, for a **1-in-50 sample** of ordinary sightings (keeps the UI feeling complete, gives fine-tuning data).
3. Clips only for `critical`/`high` alerts and all `anomaly_events`.

That lands at ~120 MB/camera/day, and evidence attached to an alert gets `retain_until = now() + 1 year`.

```sql
-- Retention, all via pg_cron. DPDP Act 2023 s.8(7): erase personal data once the purpose is served.
select cron.schedule('purge_plate_reads','17 * * * *', $$
  delete from public.plate_reads where read_at < now() - interval '48 hours';
$$);
select cron.schedule('purge_sightings','25 3 * * *', $$
  delete from public.sightings
   where seen_at < now() - interval '90 days'
     and id not in (select sighting_id from public.alerts where sighting_id is not null);
$$);
select cron.schedule('expire_alerts','*/10 * * * *', $$
  update public.alerts set status='expired'
   where status='new' and raised_at < now() - interval '24 hours';
$$);
select cron.schedule('expire_reviews','40 2 * * *', $$
  update public.review_queue set status='expired'
   where status='pending' and created_at < now() - interval '14 days';
$$);
select cron.schedule('camera_offline_sweep','* * * * *', $$
  update public.cameras set status='offline'
   where is_active and (last_heartbeat_at is null or last_heartbeat_at < now() - interval '90 seconds')
     and status <> 'offline';
$$);
select cron.schedule('purge_vehicle_pii','5 4 * * *', $$
  update public.vehicles set owner_name=null, owner_phone=null
   where last_seen_at < now() - interval '90 days' and not is_flagged;
$$);
```
Expired storage objects are swept by a nightly FastAPI job that reads `evidence where retain_until < now()`, deletes the object, then deletes the row — Postgres cannot delete from a Storage bucket by itself.

---

### 13. Seed data — a 12-camera demo city (Pune)

Real coordinates, real junction names, one restricted zone, one surveyed corridor for section-speed, a watchlist and two planted vehicles.

```sql
-- 0010_seed_pune.sql
insert into public.zones (code, name, kind, area, speed_limit_kmph, is_restricted) values
('Z-CENTRAL','Pune Central','ward',
  ST_GeogFromText('POLYGON((73.830 18.495,73.895 18.495,73.895 18.545,73.830 18.545,73.830 18.495))'),50,false),
('Z-EAST','Pune East (Kharadi-Viman Nagar)','ward',
  ST_GeogFromText('POLYGON((73.895 18.535,73.960 18.535,73.960 18.585,73.895 18.585,73.895 18.535))'),60,false),
('Z-WEST','Pune West (Baner-Hinjewadi)','ward',
  ST_GeogFromText('POLYGON((73.730 18.540,73.815 18.540,73.815 18.600,73.730 18.600,73.730 18.540))'),60,false),
('Z-RESTRICT-COUNCILHALL','Council Hall Secure Perimeter','restricted',
  ST_GeogFromText('POLYGON((73.870 18.527,73.880 18.527,73.880 18.535,73.870 18.535,73.870 18.527))'),30,true);

insert into public.cameras (code,name,zone_id,location,heading_deg,stream_url,stream_kind,target_fps,speed_limit_kmph,is_active) values
('PN-CAM-001','Shivajinagar Junction',      (select id from zones where code='Z-CENTRAL'), ST_MakePoint(73.8470,18.5308)::geography, 90,'rtsp://127.0.0.1:8554/cam001','rtsp',12,50,true),
('PN-CAM-002','Deccan Gymkhana Chowk',      (select id from zones where code='Z-CENTRAL'), ST_MakePoint(73.8410,18.5165)::geography,135,'rtsp://127.0.0.1:8554/cam002','rtsp',12,50,true),
('PN-CAM-003','Swargate Bus Stand',         (select id from zones where code='Z-CENTRAL'), ST_MakePoint(73.8586,18.5013)::geography,  0,'rtsp://127.0.0.1:8554/cam003','rtsp',12,40,true),
('PN-CAM-004','Pune Railway Station Gate',  (select id from zones where code='Z-CENTRAL'), ST_MakePoint(73.8743,18.5286)::geography,270,'rtsp://127.0.0.1:8554/cam004','rtsp',12,40,true),
('PN-CAM-005','Council Hall Perimeter',     (select id from zones where code='Z-RESTRICT-COUNCILHALL'), ST_MakePoint(73.8752,18.5305)::geography,180,'rtsp://127.0.0.1:8554/cam005','rtsp',12,30,true),
('PN-CAM-006','Yerawada Bridge North',      (select id from zones where code='Z-CENTRAL'), ST_MakePoint(73.8800,18.5510)::geography, 45,'rtsp://127.0.0.1:8554/cam006','rtsp',12,60,true),
('PN-CAM-007','Viman Nagar Phoenix Chowk',  (select id from zones where code='Z-EAST'),    ST_MakePoint(73.9143,18.5679)::geography, 90,'rtsp://127.0.0.1:8554/cam007','rtsp',12,60,true),
('PN-CAM-008','Kharadi Bypass',             (select id from zones where code='Z-EAST'),    ST_MakePoint(73.9430,18.5515)::geography,225,'rtsp://127.0.0.1:8554/cam008','rtsp',12,80,true),
('PN-CAM-009','Hadapsar Gadital',           (select id from zones where code='Z-EAST'),    ST_MakePoint(73.9260,18.5089)::geography,315,'rtsp://127.0.0.1:8554/cam009','rtsp',12,50,true),
('PN-CAM-010','Kothrud Depot',              (select id from zones where code='Z-CENTRAL'), ST_MakePoint(73.8077,18.5074)::geography, 90,'rtsp://127.0.0.1:8554/cam010','rtsp',12,50,true),
('PN-CAM-011','Baner Road Junction',        (select id from zones where code='Z-WEST'),    ST_MakePoint(73.7868,18.5590)::geography,180,'rtsp://127.0.0.1:8554/cam011','rtsp',12,60,true),
('PN-CAM-012','Hinjewadi Phase 1 Gate',     (select id from zones where code='Z-WEST'),    ST_MakePoint(73.7389,18.5912)::geography,270,'rtsp://127.0.0.1:8554/cam012','rtsp',12,60,true);

-- Surveyed corridors. road_distance_m is a real road path, ~25% longer than the geodesic.
insert into public.camera_links (from_camera_id,to_camera_id,road_distance_m,speed_limit_kmph,min_plausible_s,is_bidirectional)
select a.id,b.id,d,lim,ceil(d/41.7),true from (values
  ('PN-CAM-001','PN-CAM-002',1900,50),
  ('PN-CAM-002','PN-CAM-003',2400,50),
  ('PN-CAM-004','PN-CAM-006',3100,60),
  ('PN-CAM-007','PN-CAM-008',3600,80),
  ('PN-CAM-011','PN-CAM-012',6200,60)
) v(fa,fb,d,lim)
join public.cameras a on a.code=v.fa join public.cameras b on b.code=v.fb;
-- 41.7 m/s = 150 km/h, the impossibility ceiling.

insert into public.vehicles (plate_text,vehicle_class,color,make_model,is_flagged) values
('MH12DE1433','car','white','Maruti Swift',true),        -- planted: cloned-plate demo
('MH14GH2255','car','silver','Hyundai Creta',true),      -- planted: overspeed demo
('MH12AB1234','motorcycle','black','Honda Activa',false),
('DL8CAF5030','car','red','Tata Nexon',false),
('HR26DK8337','truck','blue','Tata 407',false),
('22BH1234AA','car','grey','Kia Seltos',false),
('MH31FF9911','auto_rickshaw','yellow','Bajaj RE',false);

insert into public.watchlist (plate_text,reason,severity,fir_number,description) values
('MH12DE1433','stolen','critical','FIR/2026/PUN/00871','Reported stolen from Shivajinagar on 2026-08-29'),
('HR26DK8337','wanted','high','FIR/2026/PUN/00912','Suspected in inter-state contraband movement'),
('MH14GH2255','test_planted','medium',null,'Demo vehicle for section-speed enforcement'),
('DL8CAF5030','bolo','medium',null,'Owner absconding - be on lookout');
```

The **cloned-plate stage moment**: the replay controller starts `PN-CAM-012` (Hinjewadi, 73.7389 E) and `PN-CAM-008` (Kharadi, 73.9430 E) clips both containing `MH12DE1433` with a 90-second `seen_at` offset. Geodesic distance is ≈ 21.6 km; 21,600 m / 90 s × 3.6 = **864 km/h**. `fn_build_transit` sets `is_impossible = true`, the rules engine raises `alert_kind='cloned_plate'`, `severity='critical'`, and Realtime paints it on the map before the presenter finishes the sentence. Deterministic, reproducible, no luck involved.

---

### 14. What the schema costs you if you skip a piece

| If you skip | What breaks |
|---|---|
| `plate_reads` | Accuracy caps at single-frame OCR (~85%). No voting, no evidence trail, no fine-tuning data. |
| `sightings` (query from `track_sessions`) | Trajectory queries fight with the worker's UPDATE traffic; no clean append-only table to BRIN or partition. |
| `vehicle_transits` | Clone and section-speed detection become a nested self-join over `sightings` on every insert — the demo stalls. |
| `plate_key` | `MHI2DE1433` and `MH12DE1433` are two different cars; the watchlist misses. |
| `dedupe_key` | One cloned plate produces 40 identical alert rows and the operator queue is unusable. |
| `seen_at` separate from `ingested_at` | Replayed video makes every vehicle appear to be at 12 cameras simultaneously. The demo cannot work. |

---

## Appendix — Interface Contracts Declared by This Section

- `extension: postgis (schema extensions), pg_trgm, btree_gist, pg_cron`
- `enum: camera_status ('online','degraded','offline','maintenance')`
- `enum: vehicle_class ('car','motorcycle','auto_rickshaw','bus','truck','tractor','other','unknown')`
- `enum: plate_engine ('paddleocr','lprnet','manual','imported')`
- `enum: severity_level ('info','low','medium','high','critical')`
- `enum: alert_status ('new','acknowledged','investigating','resolved','false_positive','expired')`
- `enum: alert_kind ('watchlist_hit','cloned_plate','overspeed','wrong_way','red_light_jump','hit_and_run','loitering','abandoned_vehicle','route_deviation','convoy','fight','accident','weapon','crowd_surge','camera_offline','low_confidence_read')`
- `enum: anomaly_kind ('fight','accident','weapon','fall','crowd_surge','loitering','object_abandoned','generic')`
- `enum: review_status ('pending','confirmed','corrected','rejected','expired')`
- `enum: app_role ('viewer','operator','investigator','admin')`
- `enum: watch_reason ('stolen','wanted','fir_linked','expired_permit','bolo','vip_escort','test_planted')`
- `table: public.profiles(id uuid PK -> auth.users, full_name text, badge_no text, role app_role, unit text, is_active bool, created_at timestamptz)`
- `table: public.zones(id uuid PK, code text UNIQUE, name text, kind text, area geography(Polygon,4326), speed_limit_kmph smallint, is_restricted bool)`
- `table: public.cameras(id uuid PK, code text UNIQUE, name text, zone_id uuid, location geography(Point,4326), heading_deg smallint, fov_deg smallint, mount_height_m numeric, stream_url text, stream_kind text, resolution_w int, resolution_h int, target_fps smallint default 12, speed_limit_kmph smallint, is_active bool, status camera_status, last_heartbeat_at timestamptz, worker_id text, created_at, updated_at)`
- `table: public.camera_links(id uuid PK, from_camera_id uuid, to_camera_id uuid, road_distance_m int, speed_limit_kmph smallint, min_plausible_s int, is_bidirectional bool)`
- `table: public.vehicles(plate_text text PK, plate_key text GENERATED, vehicle_class vehicle_class, color text, make_model text, owner_name text, owner_phone text, rto_state text GENERATED, first_seen_at, last_seen_at, sighting_count bigint, is_flagged bool, notes text)`
- `table: public.watchlist(id uuid PK, plate_text text, plate_key text GENERATED, reason watch_reason, severity severity_level, fir_number text, description text, valid_from, valid_until, is_active bool, created_by uuid, created_at)`
- `table: public.track_sessions(id uuid PK, camera_id uuid, tracker_id int, run_id uuid, started_at, ended_at, frame_count int, vehicle_class, class_confidence numeric, plate_text text FK vehicles, plate_raw text, plate_confidence numeric(4,3), vote_margin numeric(4,3), read_count int, engine plate_engine, is_closed bool) UNIQUE(run_id,camera_id,tracker_id)`
- `table: public.plate_reads(id bigint identity PK, track_session_id uuid, camera_id uuid, frame_index int, read_at timestamptz, raw_text text, norm_text text GENERATED, ocr_confidence numeric(4,3), char_confidences real[], plate_bbox int4[], crop_w smallint, crop_h smallint, blur_score real, engine plate_engine, is_format_valid bool GENERATED)`
- `table: public.sightings(id bigint identity PK, track_session_id uuid, camera_id uuid, zone_id uuid, plate_text text NULLABLE FK vehicles, plate_key text, plate_confidence numeric(4,3), vehicle_class, vehicle_color text, seen_at timestamptz, ingested_at timestamptz, dwell_ms int, geog geography(Point,4326), heading_deg smallint, speed_kmph numeric(5,1), bbox int4[], evidence_id uuid, is_verified bool)`
- `table: public.vehicle_transits(id bigint identity PK, plate_text text, from_sighting_id bigint, to_sighting_id bigint, from_camera_id uuid, to_camera_id uuid, departed_at, arrived_at, dt_seconds numeric, geodesic_m numeric, road_m int, implied_kmph numeric, is_impossible bool) UNIQUE(from_sighting_id,to_sighting_id)`
- `table: public.anomaly_events(id uuid PK, camera_id uuid, zone_id uuid, kind anomaly_kind, score numeric(5,4), threshold numeric(5,4), started_at, ended_at, clip_start_at, clip_end_at, geog geography(Point,4326), model_name text, model_version text, bbox int4[], evidence_id uuid, alert_id uuid)`
- `table: public.alerts(id uuid PK, kind alert_kind, severity severity_level, status alert_status, title text, detail text, plate_text text, camera_id uuid, zone_id uuid, sighting_id bigint, transit_id bigint, anomaly_id uuid, watchlist_id uuid, geog geography(Point,4326), raised_at, last_seen_at, occurrence_count int, dedupe_key text, acknowledged_by uuid, acknowledged_at, resolved_by uuid, resolved_at, resolution_note text, payload jsonb)`
- `table: public.evidence(id uuid PK, bucket text default 'evidence', object_path text UNIQUE, kind text in ('plate_crop','vehicle_crop','context_frame','clip','montage'), mime_type text, size_bytes int, width int, height int, sha256 text, camera_id uuid, track_session_id uuid, captured_at timestamptz, retain_until timestamptz)`
- `table: public.review_queue(id uuid PK, track_session_id uuid UNIQUE, camera_id uuid, suggested_plate text, candidates jsonb, confidence numeric(4,3), vote_margin numeric(4,3), evidence_id uuid, status review_status, corrected_plate text, reviewed_by uuid, reviewed_at, seen_at, created_at)`
- `table: public.audit_log(id bigint identity PK, actor_id uuid, actor_role app_role, action text, entity_table text, entity_id text, before_data jsonb, after_data jsonb, ip_address inet, user_agent text, at timestamptz) APPEND-ONLY`
- `function: public.vehicle_trajectory(p_plate text, p_from timestamptz, p_to timestamptz) RETURNS TABLE(seq int, sighting_id bigint, camera_id uuid, camera_code text, camera_name text, zone_name text, seen_at timestamptz, lon float8, lat float8, plate_confidence numeric, leg_seconds numeric, leg_metres numeric, leg_kmph numeric, evidence_path text)`
- `function: public.cameras_near(p_lat float8, p_lon float8, p_radius_m int default 1000) RETURNS TABLE(id uuid, code text, name text, distance_m float8, lon float8, lat float8)`
- `function: public.fn_vote_plate(p_track uuid) RETURNS TABLE(voted_text text, confidence numeric, margin numeric, reads int)`
- `function: public.current_app_role() RETURNS app_role`
- `trigger fn: public.fn_sightings_enrich() BEFORE INSERT ON sightings (fills geog, zone_id, plate_key; bumps vehicles.last_seen_at/sighting_count)`
- `trigger fn: public.fn_build_transit() AFTER INSERT ON sightings (writes vehicle_transits)`
- `trigger fn: public.fn_touch_updated_at()`
- `view: public.v_live_sightings (last 60 minutes, includes is_watchlisted bool, lon, lat, plate_crop_path)`
- `view: public.v_alert_queue (open alerts only, includes age_seconds, evidence_count, lon, lat)`
- `view: public.v_camera_health (includes effective_status, sightings_15m, avg_conf_60m)`
- `rpc endpoint: POST /rest/v1/rpc/vehicle_trajectory {p_plate, p_from, p_to}`
- `rpc endpoint: POST /rest/v1/rpc/cameras_near {p_lat, p_lon, p_radius_m}`
- `storage bucket: 'evidence' (private); object path convention {camera_code}/{YYYY}/{MM}/{DD}/{track_session_id}/{kind}.jpg`
- `realtime publication: supabase_realtime includes alerts, anomaly_events, cameras, sightings, review_queue; NOT plate_reads`
- `env var: NEXT_PUBLIC_SUPABASE_URL (browser-safe)`
- `env var: NEXT_PUBLIC_SUPABASE_ANON_KEY (browser-safe)`
- `env var: SUPABASE_SERVICE_ROLE_KEY (server/worker ONLY, never NEXT_PUBLIC_)`
- `threshold: plate promotion to vehicles registry requires voted plate_confidence >= 0.90 AND vote_margin >= 0.15`
- `threshold: review_queue trigger = plate_confidence < 0.90 OR vote_margin < 0.15 OR RTO format regex fail`
- `threshold: impossible-speed / cloned plate = implied_kmph > 150`
- `threshold: transit guard rails = dt_seconds >= 5 AND geodesic_m >= 200`
- `threshold: camera offline = last_heartbeat_at older than 90s; degraded = older than 30s`
- `threshold: realtime sightings cutover to polling at ~50 inserts/sec`
- `constant: 41.7 m/s = 150 km/h used for camera_links.min_plausible_s`
- `regex: plate format ^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$ | ^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$ | ^[0-9]{2}[A-Z]{2}[0-9]{6}[A-Z]$`
- `normalization: plate_key = translate(upper(strip_non_alnum(plate_text)),'OIZSB','01258')`
- `SRID: 4326 everywhere; geography type (metres); EPSG:32643 only for optional planar casts`
- `camera codes seeded: PN-CAM-001..PN-CAM-012 (Pune); zone codes Z-CENTRAL, Z-EAST, Z-WEST, Z-RESTRICT-COUNCILHALL`
- `demo RTSP convention: rtsp://127.0.0.1:8554/cam001 .. cam012`
- `planted demo plates: MH12DE1433 (clone/stolen), MH14GH2255 (overspeed), HR26DK8337 (wanted), DL8CAF5030 (bolo)`
- `migration file order: 0000_extensions .. 0010_seed_pune under infra/supabase/migrations/`
- `retention: plate_reads 48h, sightings 90d, evidence 7d default / 1yr when alert-linked, vehicles PII nulled at 90d idle`

## Appendix — MVP vs Stretch

- MVP: all 0000-0010 migrations applied to a single Supabase project; every table, enum, index and seed row in section 3-7 and 13 exists before any inference code is written
- MVP: three-tier write path implemented by the worker - plate_reads (per frame) -> track_sessions (voted on close) -> exactly one sightings row per closed track
- MVP: geography(Point,4326) on cameras.location, sightings.geog, zones.area with GiST indexes
- MVP: BRIN on sightings.seen_at plus B-tree composite (plate_text, seen_at DESC)
- MVP: fn_sightings_enrich and fn_build_transit triggers, so cloned-plate and section-speed detection are automatic on insert
- MVP: vehicle_trajectory(), v_live_sightings, v_alert_queue, v_camera_health
- MVP: RLS enabled on all 14 tables with zero anon policies; service_role key confined to FastAPI + worker env
- MVP: Realtime on alerts, cameras, anomaly_events, review_queue, sightings
- MVP: alerts.dedupe_key partial unique index + ON CONFLICT upsert to prevent alert storms
- MVP: seed_pune.sql - 12 cameras, 4 zones, 5 camera_links, 7 vehicles, 4 watchlist entries
- MVP: separate seen_at (event/stream clock) and ingested_at (wall clock) on sightings - without this the replay demo is incoherent
- MVP: pg_cron purge of plate_reads at 48h and camera_offline_sweep every minute
- STRETCH: declarative range partitioning of sightings by seen_at with monthly partitions and auto-create cron
- STRETCH: fn_vote_plate() in SQL (the Python worker already votes in-process; SQL version is for audit/demo)
- STRETCH: materialized view mv_hourly_camera_counts refreshed every 5 minutes for dashboard charts
- STRETCH: generic fn_audit() trigger writing to audit_log on all mutable tables (MVP logs from FastAPI only)
- STRETCH: btree_gist exclusion constraint preventing overlapping track_sessions per (camera_id, tracker_id, time range)
- STRETCH: app role carried in JWT app_metadata instead of a profiles lookup inside current_app_role()
- STRETCH: nightly FastAPI job sweeping storage objects whose evidence.retain_until has passed
- STRETCH: vehicles.owner_name/owner_phone populated from a VAHAN-style lookup with per-read audit entries

## Appendix — Risks

- service_role key leaking into the Next.js client bundle exposes owner_name/owner_phone and full write access - a DPDP Act 2023 reportable breach. Mitigation: key only in FastAPI/worker env, gitignored before first commit, and a `grep -r service_role .next/static/` check in the build script.
- Realtime enabled on plate_reads (300k rows/camera/day) would saturate the replication slot and silently kill the alerts channel mid-demo. Mitigation: plate_reads is explicitly excluded from supabase_realtime; add a review step before anyone runs ALTER PUBLICATION.
- Bare auth.uid() inside RLS USING clauses is re-evaluated per row, turning a 100k-row sightings scan into multi-second latency. Mitigation: always (select auth.uid()) and a SECURITY DEFINER STABLE current_app_role().
- Partitioning sightings under time pressure breaks the PK, every inbound FK, and possibly Realtime (publish_via_partition_root). Mitigation: do not partition for the demo; BRIN + cron DELETE covers demo volume.
- Supabase free tier is 500 MB DB / 1 GB storage; 12 real cameras fill it in ~2 days and evidence-for-every-sighting fills the bucket in hours. Mitigation: 48h plate_reads purge, evidence only for alerts + review items + 1-in-50 sample.
- Confusable OCR characters (O/0, I/1, S/5, B/8, Z/2) silently split one vehicle into several identities and cause watchlist misses. Mitigation: plate_key generated column joined on for all matching; plate_text shown to operators.
- Sorting a trajectory by ingested_at instead of seen_at makes every replayed vehicle appear at all 12 cameras at once. Mitigation: both columns exist, seen_at is documented as canonical, and vehicle_trajectory() orders by seen_at only.
- FK from sightings.plate_text to vehicles means garbage OCR would create junk registry rows. Mitigation: sightings.plate_text is NULLable and only populated above the 0.90/0.15 promotion gate; everything else goes to review_queue.
- Two cameras covering the same junction produce sub-200 m transits with tiny dt and fabricate impossible-speed alerts. Mitigation: fn_build_transit hard guard of dt_seconds >= 5 AND geodesic_m >= 200.
- Straight-line ST_Distance under-reports road distance by 20-40%, so geodesic-based overspeed alerts under-fire and clone alerts over-fire. Mitigation: camera_links.road_distance_m surveyed per corridor, used in preference to the geodesic.
- Views owned by postgres may execute with definer rights and bypass RLS via PostgREST. Mitigation: set security_invoker = on on all four views, or serve them exclusively through FastAPI.
- Enum values cannot be removed and ALTER TYPE ADD VALUE has transaction restrictions on some PG versions. Mitigation: freeze all enum vocabularies in 0001 before any other migration runs; use CHECK constraints for anything expected to change.

## Appendix — Open Questions

- Which Supabase plan is the team on? The free tier's 500 MB DB and 1 GB storage force the 48h plate_reads purge and the 1-in-50 evidence sampling. On Pro those numbers relax and the sampling rule can be dropped.
- Is pg_cron enabled by default on the target Supabase project, or must it be toggled in Database > Extensions first? If unavailable, the retention jobs move into a FastAPI APScheduler task.
- Does the deployed PostgreSQL support `alter view ... set (security_invoker = on)`? If not, the four dashboard views must be served only through FastAPI with the service key rather than exposed via PostgREST.
- Should owner_name/owner_phone exist at all for the SIH submission? Populating them requires a VAHAN-style source we will not have; leaving the columns empty is the safest DPDP posture, but judges may ask about the RTO integration path.
- Who owns the replay clock? The exact mapping from stream PTS to sightings.seen_at must be agreed with the ingestion-section author, including the per-camera time offsets used for the cloned-plate demo.
- Are the demo video clips long enough that a looped replay does not re-emit the same plate every N minutes and fire spurious clone alerts? If loops are short, the worker needs a loop-generation counter folded into the dedupe_key.
- Confirm the exact alert_kind values the rules-engine section emits so the enum in 0001 does not need altering after other sections are written.
