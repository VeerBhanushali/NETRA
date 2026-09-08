<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## API Contract — Python Vision Pipeline to Next.js

Three processes, two trust zones, one contract.

```
apps/edge      (Python 3.12, ONNX Runtime + DirectML)  ─┐ HMAC-signed, machine identity
                                                        ├──► apps/api (FastAPI, service_role key)
apps/web       (Next.js App Router, browser)           ─┘ Supabase JWT, human identity
                     │                                            │
                     └──────── Supabase Realtime (WSS) ───────────┴──► Postgres + Storage
```

Rule that decides every ambiguity below: **the browser never writes to Postgres, and the edge worker never talks to Postgres.** All writes funnel through FastAPI, which is the only holder of `SUPABASE_SERVICE_ROLE_KEY`. The browser is allowed exactly two direct Supabase connections: Realtime subscriptions (read-only, RLS-enforced) and `GET` of a signed Storage URL that FastAPI minted for it.

---

### 1. Base URLs, versioning, common headers

| Env | API base | Web origin |
|---|---|---|
| Local dev | `http://127.0.0.1:8000` | `http://localhost:3000` |
| Demo (LAN / tunnel) | `http://<laptop-ip>:8000` | `http://<laptop-ip>:3000` |

All routes are prefixed `/v1`. Breaking changes bump to `/v2`; additive fields never bump.

Every response carries:

| Header | Meaning |
|---|---|
| `X-Request-ID` | Echoed from the request or generated (uuid4). Log it on both sides; it is the only way to correlate an edge failure with an API traceback. |
| `X-RateLimit-Remaining` | Requests left in the current window for this identity. |
| `Date` | Used by the edge worker to measure clock skew (§3.5). |

```python
# apps/api/app/middleware/request_id.py
import uuid
from starlette.middleware.base import BaseHTTPMiddleware

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
```

---

### 2. Auth model

#### 2.1 Human identities (browser → FastAPI)

Three roles, stored in Supabase Auth `app_metadata.sih_role` (Supabase copies `app_metadata` into the access-token payload; `user_metadata` is user-writable and must **never** be used for authorisation).

| Role | Who | Can |
|---|---|---|
| `operator` | Control-room staff on shift | Read alerts, acknowledge, request evidence URLs, see cameras/map, see sightings from the last 24 h only |
| `investigator` | Case officer | Everything operator can, plus unbounded plate search, full trajectory history, review queue, resolve alerts |
| `admin` | System owner | Everything, plus camera CRUD, watchlist CRUD, edge-key management, user role assignment |

Setting a role (one-time, from a trusted shell — never an endpoint the UI can reach):

```bash
curl -X PUT "$SUPABASE_URL/auth/v1/admin/users/$USER_ID" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"app_metadata":{"sih_role":"investigator"}}'
```

FastAPI verification:

```python
# apps/api/app/deps/auth.py
import os, jwt                      # PyJWT
from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

bearer = HTTPBearer(auto_error=True)
JWT_SECRET = os.environ["SUPABASE_JWT_SECRET"]

class Principal(BaseModel):
    user_id: str
    email: str | None = None
    role: str            # operator | investigator | admin

RANK = {"operator": 1, "investigator": 2, "admin": 3}

def current_user(cred: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> Principal:
    try:
        claims = jwt.decode(
            cred.credentials, JWT_SECRET, algorithms=["HS256"],
            audience="authenticated", options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid_token: {e}")
    role = (claims.get("app_metadata") or {}).get("sih_role")
    if role not in RANK:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "role_not_assigned")
    return Principal(user_id=claims["sub"], email=claims.get("email"), role=role)

def require(min_role: str):
    def _guard(p: Annotated[Principal, Depends(current_user)]) -> Principal:
        if RANK[p.role] < RANK[min_role]:
            raise HTTPException(403, "insufficient_role")
        return p
    return _guard

# usage: async def list_alerts(p: Annotated[Principal, Depends(require("operator"))]): ...
```

> **Verify:** Supabase projects created recently default to **asymmetric JWT signing keys** (ECC P-256 / ES256). If your dashboard shows "JWT Signing Keys" rather than a legacy "JWT Secret", drop `JWT_SECRET` and verify against the JWKS at `https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json` using `PyJWKClient`, `algorithms=["ES256"]`. The rest of the function is unchanged.

#### 2.2 RLS vs API-level checks — who owns what

They are not redundant; they guard different doors.

- **RLS owns the direct-read door**: Realtime `postgres_changes` and any future direct `supabase-js` select. RLS is coarse and row-shaped — "may this role see this row at all". Policies read `(auth.jwt() -> 'app_metadata' ->> 'sih_role')`.
- **FastAPI owns the write door and the business door**: "may this user resolve an alert someone else acknowledged", rate limits, DPDP access logging, cross-table invariants, evidence URL issuance. Service role bypasses RLS by design, so *every* handler must call `require(...)` — a missing dependency is a total auth bypass, not a partial one.

Minimum policy set the API section depends on (DB section owns the DDL):

```sql
alter table public.alerts enable row level security;
create policy alerts_read on public.alerts for select to authenticated
  using ((auth.jwt() -> 'app_metadata' ->> 'sih_role') in ('operator','investigator','admin'));

alter table public.sightings enable row level security;
create policy sightings_read on public.sightings for select to authenticated
  using (
    (auth.jwt() -> 'app_metadata' ->> 'sih_role') in ('investigator','admin')
    or ((auth.jwt() -> 'app_metadata' ->> 'sih_role') = 'operator'
        and captured_at > now() - interval '24 hours')
  );
```

The 24-hour operator window is purpose limitation under **DPDP Act 2023 §8** — a shift operator needs live situational awareness, not the city's six-month movement history. Say this sentence to the judges.

#### 2.3 Machine identity (edge worker → FastAPI)

Not a JWT. The edge worker is unattended, restarts often, and runs on the same laptop; a long-lived bearer token in a `.env` is a static secret that leaks in a screen-share. Use **HMAC-SHA256 over the request**, so a captured request cannot be replayed or modified.

Headers:

| Header | Example |
|---|---|
| `X-Edge-Key-Id` | `edge-worker-01` |
| `X-Edge-Timestamp` | `1757088012` (unix seconds, UTC) |
| `X-Edge-Nonce` | `9f4c1d2e-...` (uuid4, unique per request) |
| `X-Edge-Signature` | `v1=3af9...` (hex) |

Canonical string — **exactly** this, `\n`-joined, no trailing newline:

```
{key_id}\n{timestamp}\n{nonce}\n{METHOD}\n{path}\n{sha256_hex(raw_body_bytes)}
```

```python
# apps/edge/transport/signing.py
import hashlib, hmac, time, uuid

def sign(key_id: str, secret: str, method: str, path: str, body: bytes) -> dict[str, str]:
    ts, nonce = str(int(time.time())), str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    canon = "\n".join([key_id, ts, nonce, method.upper(), path, body_hash])
    sig = hmac.new(secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Edge-Key-Id": key_id, "X-Edge-Timestamp": ts,
        "X-Edge-Nonce": nonce, "X-Edge-Signature": f"v1={sig}",
        "Content-Type": "application/json",
    }
```

```python
# apps/api/app/deps/edge_auth.py
import hashlib, hmac, os, time
from cachetools import TTLCache
from fastapi import HTTPException, Request

EDGE_KEYS = {"edge-worker-01": os.environ["EDGE_SECRET_WORKER_01"]}
MAX_SKEW_S = 300
_seen_nonces: TTLCache = TTLCache(maxsize=200_000, ttl=MAX_SKEW_S * 2)

async def verify_edge(request: Request) -> str:
    h = request.headers
    key_id, ts, nonce = h.get("x-edge-key-id"), h.get("x-edge-timestamp"), h.get("x-edge-nonce")
    sig = (h.get("x-edge-signature") or "").removeprefix("v1=")
    secret = EDGE_KEYS.get(key_id or "")
    if not (secret and ts and nonce and sig):
        raise HTTPException(401, "edge_auth_missing")
    if abs(time.time() - int(ts)) > MAX_SKEW_S:
        raise HTTPException(401, "edge_clock_skew")
    if nonce in _seen_nonces:
        raise HTTPException(401, "edge_nonce_replay")
    body = await request.body()                       # cached by Starlette; safe to re-read
    canon = "\n".join([key_id, ts, nonce, request.method,
                       request.url.path, hashlib.sha256(body).hexdigest()])
    expected = hmac.new(secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, "edge_bad_signature")
    _seen_nonces[nonce] = True
    return key_id
```

The nonce cache is process-local. With one uvicorn worker (what you will run for the demo) that is correct. With `--workers N` replay protection degrades to per-worker; move nonces to a `edge_nonces(nonce text primary key, seen_at timestamptz)` table with a 10-minute sweep if you scale out. Signature verification itself stays correct under any worker count.

---

### 3. The ingest path

#### 3.1 Idempotency — the sighting UID

A sighting is one **track**, not one frame. ByteTrack gives a `track_id` that is stable for the life of a vehicle in one camera's view; the edge emits a sighting when the track ends or after 3 s, whichever comes first, using the highest-confidence plate read in the track.

The idempotency key is deterministic and computed on the edge, so a retry after a network timeout produces byte-identical rows:

```python
# apps/edge/models.py
import uuid
NS_SIGHTING = uuid.UUID("6f2a1c88-1c3e-5a9b-9e3d-0c7a11b6d401")  # fixed, never change

def sighting_uid(camera_id: str, worker_run_id: str, track_id: str, first_frame_ms: int) -> uuid.UUID:
    return uuid.uuid5(NS_SIGHTING, f"{camera_id}/{worker_run_id}/{track_id}/{first_frame_ms}")
```

`worker_run_id` (uuid4 minted at process start) is in the key because ByteTrack restarts its counter at 1 on every process restart — without it, restarting the worker collides `track_id=1` with yesterday's `track_id=1` and the DB silently swallows a real sighting.

Server side, the column is `sightings.sighting_uid uuid unique`, and inserts are:

```sql
insert into public.sightings (sighting_uid, camera_id, ...) values ...
on conflict (sighting_uid) do nothing
returning id, sighting_uid;
```

`do nothing` (not `do update`): a duplicate is a retry of the same observation, and the first write wins. The response reports it as a duplicate, not an error, so the edge can drop it from the spool.

Batch-level `Idempotency-Key` header: **stretch**. Per-row keys already make the whole batch safely replayable, which is the property that matters.

#### 3.2 `POST /v1/ingest/sightings`

```python
# apps/api/app/schemas/ingest.py
from datetime import datetime
from enum import StrEnum
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

class VehicleClass(StrEnum):
    car = "car"; motorcycle = "motorcycle"; auto_rickshaw = "auto_rickshaw"
    bus = "bus"; truck = "truck"; tempo = "tempo"; tractor = "tractor"; other = "other"

class PipelineVersions(BaseModel):
    detector: str            # "yolov8s-plate-v3.onnx"
    ocr: str                 # "paddleocr-en-ppocrv4-int8"
    tracker: str             # "bytetrack-0.3"
    provider: str            # "DmlExecutionProvider"

class SightingIn(BaseModel):
    # protected_namespaces=() is REQUIRED if you ever name a field model_*.
    # We avoided it (pipeline_versions, not model_versions) — do not rename it back.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sighting_uid: UUID
    camera_id: str = Field(min_length=3, max_length=64)
    track_id: str = Field(max_length=64)
    captured_at: AwareDatetime           # UTC, first frame of the track
    plate_text: str | None = Field(default=None, max_length=16,
                                   pattern=r"^[A-Z0-9]{4,16}$")   # normalised
    plate_text_raw: str = Field(max_length=32)                    # exactly what OCR said
    plate_valid_format: bool             # matches Bharat/RTO or BH-series regex
    ocr_confidence: float = Field(ge=0.0, le=1.0)
    char_confidences: list[float] = Field(default_factory=list, max_length=16)
    det_confidence: float = Field(ge=0.0, le=1.0)
    vehicle_class: VehicleClass
    vehicle_color: str | None = Field(default=None, max_length=24)
    bbox: tuple[int, int, int, int]          # vehicle, xyxy, source-frame pixels
    plate_bbox: tuple[int, int, int, int] | None = None
    frames_seen: int = Field(ge=1)
    plate_crop_key: str | None = Field(default=None, max_length=256)
    frame_key: str | None = Field(default=None, max_length=256)
    pipeline_versions: PipelineVersions

class SightingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str
    worker_run_id: UUID
    sent_at: AwareDatetime
    sightings: list[SightingIn] = Field(min_length=1, max_length=200)

class RejectedItem(BaseModel):
    index: int
    sighting_uid: UUID | None
    code: str            # unknown_camera | bad_timestamp | constraint_violation
    message: str

class AlertStub(BaseModel):
    alert_id: UUID
    alert_type: str
    severity: int
    plate_text: str | None

class IngestResult(BaseModel):
    received: int
    inserted: int
    duplicates: int
    rejected: list[RejectedItem]
    alerts_created: list[AlertStub]
    server_time: AwareDatetime
    clock_skew_ms: int       # server_time - sent_at; edge warns if abs() > 2000
```

`plate_text` is nullable and `plate_text_raw` is not. An unreadable plate is still a vehicle sighting with a colour, a class and a location — throwing it away destroys the trajectory. Normalisation on the edge: uppercase, strip non-`[A-Z0-9]`, then match

```python
PLATE_RE = r"^([A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}|\d{2}BH\d{4}[A-Z]{1,2})$"
```

covering `MH12DE1433`, `DL8CAF5030`, `HR26DK8337` and BH-series `22BH1234AA`. Failure sets `plate_valid_format=false`, which routes the row to the review queue.

**Request** (`POST /v1/ingest/sightings`, HMAC headers, `Content-Type: application/json`):

```json
{
  "worker_id": "edge-worker-01",
  "worker_run_id": "0b6f7a2c-8b1e-4f77-9a10-2f5c9d3e6a11",
  "sent_at": "2026-09-05T11:42:19.480Z",
  "sightings": [
    {
      "sighting_uid": "3d9c0f5a-7b21-53a8-9f44-8c0b1a7e2d90",
      "camera_id": "CAM-NGP-RING-07",
      "track_id": "1183",
      "captured_at": "2026-09-05T11:42:17.226Z",
      "plate_text": "MH12DE1433",
      "plate_text_raw": "MH 12 DE 1433",
      "plate_valid_format": true,
      "ocr_confidence": 0.947,
      "char_confidences": [0.99,0.98,0.97,0.95,0.91,0.93,0.99,0.98,0.96,0.94],
      "det_confidence": 0.912,
      "vehicle_class": "car",
      "vehicle_color": "white",
      "bbox": [812, 430, 1104, 688],
      "plate_bbox": [928, 604, 1022, 642],
      "frames_seen": 17,
      "plate_crop_key": "evidence/CAM-NGP-RING-07/2026/09/05/11/3d9c0f5a-7b21-53a8-9f44-8c0b1a7e2d90_plate.jpg",
      "frame_key": "evidence/CAM-NGP-RING-07/2026/09/05/11/3d9c0f5a-7b21-53a8-9f44-8c0b1a7e2d90_frame.jpg",
      "pipeline_versions": {
        "detector": "yolov8s-plate-v3.onnx",
        "ocr": "paddleocr-en-ppocrv4-int8",
        "tracker": "bytetrack-0.3",
        "provider": "DmlExecutionProvider"
      }
    },
    {
      "sighting_uid": "b1e77d40-5cc9-59e2-b3d1-441ab2f0c7e5",
      "camera_id": "CAM-NGP-RING-07",
      "track_id": "1184",
      "captured_at": "2026-09-05T11:42:18.061Z",
      "plate_text": null,
      "plate_text_raw": "MH1?DE14??",
      "plate_valid_format": false,
      "ocr_confidence": 0.412,
      "char_confidences": [0.88,0.31,0.29,0.77,0.81,0.44,0.22,0.19],
      "det_confidence": 0.674,
      "vehicle_class": "motorcycle",
      "vehicle_color": "black",
      "bbox": [220, 502, 340, 664],
      "plate_bbox": [262, 620, 310, 646],
      "frames_seen": 6,
      "plate_crop_key": "evidence/CAM-NGP-RING-07/2026/09/05/11/b1e77d40-5cc9-59e2-b3d1-441ab2f0c7e5_plate.jpg",
      "frame_key": null,
      "pipeline_versions": {
        "detector": "yolov8s-plate-v3.onnx",
        "ocr": "paddleocr-en-ppocrv4-int8",
        "tracker": "bytetrack-0.3",
        "provider": "DmlExecutionProvider"
      }
    }
  ]
}
```

**Response `200 OK`**:

```json
{
  "received": 2,
  "inserted": 2,
  "duplicates": 0,
  "rejected": [],
  "alerts_created": [
    { "alert_id": "7c2b93f0-1a44-4f0e-9a8e-6b2e1c5d9f30",
      "alert_type": "watchlist_hit", "severity": 4, "plate_text": "MH12DE1433" }
  ],
  "server_time": "2026-09-05T11:42:19.612Z",
  "clock_skew_ms": 132
}
```

The whole batch is one transaction. If `camera_id` is unknown the batch is **not** failed — that row goes to `rejected` with `code:"unknown_camera"` and the rest commit. Only a malformed envelope (Pydantic validation of `SightingBatch` itself) returns `422` with nothing written; the edge must treat `422` as poison and move the batch to a dead-letter file rather than retrying forever.

#### 3.3 `POST /v1/ingest/anomalies`

```python
class AnomalyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anomaly_uid: UUID                 # uuid5(camera_id/worker_run_id/window_start_ms/event_type)
    camera_id: str
    event_type: str                   # fight | accident | loitering | weapon | crowd_surge | generic
    score: float = Field(ge=0.0, le=1.0)
    threshold: float                  # the value that was exceeded, for auditability
    window_start: AwareDatetime
    window_end: AwareDatetime
    clip_key: str | None = None       # clips/<camera>/<date>/<anomaly_uid>.mp4
    keyframe_key: str | None = None
    linked_track_ids: list[str] = Field(default_factory=list, max_length=32)
    meta: dict = Field(default_factory=dict)

class AnomalyBatch(BaseModel):
    worker_id: str
    worker_run_id: UUID
    sent_at: AwareDatetime
    anomalies: list[AnomalyIn] = Field(min_length=1, max_length=50)
```

Response mirrors `IngestResult`. `threshold` travels with the event so a reviewer six weeks later can see the model was at 0.71 against a 0.65 bar — the number that made the machine act is part of the evidence.

#### 3.4 `POST /v1/ingest/heartbeat`

```json
{ "worker_id": "edge-worker-01",
  "worker_run_id": "0b6f7a2c-8b1e-4f77-9a10-2f5c9d3e6a11",
  "cameras": [
    { "camera_id": "CAM-NGP-RING-07", "state": "streaming", "fps_in": 25.0,
      "fps_processed": 11.4, "queue_depth": 3, "last_frame_at": "2026-09-05T11:42:19.1Z",
      "dropped_frames_1m": 812 }
  ],
  "spool_depth": 0,
  "provider": "DmlExecutionProvider",
  "vram_mb_used": 2840 }
```

Sent every **15 s**. The UI marks a camera `STALE` after 45 s (3 missed beats) and `OFFLINE` after 120 s. `fps_processed: 11.4` against `fps_in: 25.0` is the honest number for YOLOv8s + PaddleOCR on ONNX Runtime DirectML on an RX 6500M (4 GB) with 2 concurrent streams — the edge deliberately drops frames and says so, rather than lying about real-time. Surface it in the header strip; a judge who sees `11.4 / 25.0 fps · frames dropped: 812/min` trusts you more than one who sees a fake 25.

#### 3.5 Clock skew

Speed-between-cameras is derived from `captured_at`, so edge clock error becomes speed error directly: over a 500 m baseline at 60 km/h (30 s travel), a 1 s skew yields ~3.2 % speed error. The edge computes `clock_skew_ms` from every ingest response, logs a warning above 2000 ms, and refuses to emit `speed_violation` alerts if skew exceeds 5000 ms. On the demo laptop all replays share one clock, so skew is 0 — but say the mechanism exists.

#### 3.6 Offline buffer and retry

The edge never blocks the inference loop on HTTP. Sightings go to a local SQLite spool; a separate thread drains it.

```sql
-- apps/edge/spool.db
create table if not exists outbox (
  id            integer primary key autoincrement,
  endpoint      text    not null,      -- '/v1/ingest/sightings'
  body          blob    not null,      -- exact bytes to be signed & sent
  created_at    real    not null,
  attempts      integer not null default 0,
  next_attempt  real    not null default 0,
  last_error    text
);
create index if not exists outbox_ready on outbox(next_attempt, id);
```

```python
# apps/edge/transport/sender.py  (drain loop, abridged but complete in behaviour)
import json, random, sqlite3, time
import httpx
from .signing import sign

BACKOFF = [1, 2, 4, 8, 15, 30, 60]     # seconds, then held at 60
MAX_SPOOL_ROWS = 50_000

def drain(conn: sqlite3.Connection, client: httpx.Client, base: str, key_id: str, secret: str):
    while True:
        row = conn.execute(
            "select id, endpoint, body, attempts from outbox "
            "where next_attempt <= ? order by id limit 1", (time.time(),)
        ).fetchone()
        if row is None:
            time.sleep(0.25); continue
        rid, endpoint, body, attempts = row
        try:
            r = client.post(base + endpoint, content=body,
                            headers=sign(key_id, secret, "POST", endpoint, body), timeout=10.0)
        except httpx.HTTPError as e:
            _defer(conn, rid, attempts, str(e)); continue

        if r.status_code == 200:
            conn.execute("delete from outbox where id=?", (rid,)); conn.commit()
        elif r.status_code in (400, 401, 403, 422):
            _dead_letter(body, r.text)                       # poison: never retry
            conn.execute("delete from outbox where id=?", (rid,)); conn.commit()
        elif r.status_code == 429:
            wait = float(r.headers.get("retry-after", 5))
            _defer(conn, rid, attempts, "429", override=wait)
        else:                                                # 5xx, 502 from a tunnel, etc.
            _defer(conn, rid, attempts, f"http_{r.status_code}")

def _defer(conn, rid, attempts, err, override: float | None = None):
    delay = override if override is not None else BACKOFF[min(attempts, len(BACKOFF) - 1)]
    delay *= (0.8 + 0.4 * random.random())                   # jitter: avoid retry convoys
    conn.execute("update outbox set attempts=attempts+1, next_attempt=?, last_error=? where id=?",
                 (time.time() + delay, err, rid))
    conn.commit()
```

Ordering is FIFO by `id`, single-flight. Overflow: when `count(*) > MAX_SPOOL_ROWS`, delete the oldest 5 000 rows and increment a `spool_dropped_total` counter reported in the heartbeat — dropping the oldest is right because the newest sightings are the ones an operator is watching for. 50 000 sightings ≈ 20 MB of JSON, roughly 4 hours of a busy camera.

**Evidence images are spooled separately.** Crops are written to `apps/edge/spool/evidence/<key>` on disk immediately; the sighting JSON carries `plate_crop_key` regardless of whether the upload succeeded. A second drain thread uploads pending files. The UI renders a neutral placeholder tile for a key that 404s, which is correct: the sighting is real even when its picture has not landed yet.

---

### 4. Where images actually travel

**Never base64 in JSON.** A 90 KB plate crop becomes 120 KB of base64, inflates every batch by 20×, blows the 1 MiB body cap, and forces the JSON parser to hold megabytes of string. Images go to Supabase Storage as bytes; the API only ever moves *keys*.

Bucket `evidence`, **private**. Key convention (time-partitioned so a retention job is a prefix delete):

```
evidence/{camera_id}/{YYYY}/{MM}/{DD}/{HH}/{sighting_uid}_plate.jpg
evidence/{camera_id}/{YYYY}/{MM}/{DD}/{HH}/{sighting_uid}_frame.jpg
clips/{camera_id}/{YYYY}/{MM}/{DD}/{anomaly_uid}.mp4
exports/{user_id}/{export_id}.zip
```

All timestamps in the path are **UTC**, matching `captured_at`.

**Write path (edge).** The edge does not hold the service-role key. It asks for signed upload URLs in one batched call per ingest batch — one extra round trip per ~200 sightings, not per image.

`POST /v1/evidence/upload-urls` (edge HMAC auth)
```json
{ "keys": ["evidence/CAM-NGP-RING-07/2026/09/05/11/3d9c0f5a-..._plate.jpg"],
  "content_type": "image/jpeg" }
```
```json
{ "urls": [ { "key": "evidence/CAM-NGP-RING-07/.../..._plate.jpg",
              "upload_url": "https://<ref>.supabase.co/storage/v1/object/upload/sign/evidence/...?token=eyJ...",
              "expires_at": "2026-09-05T12:42:19Z" } ] }
```
The API calls `supabase.storage.from_("evidence").create_signed_upload_url(key)` and the edge `PUT`s the JPEG bytes to `upload_url`. Max 100 keys per call.

> **Verify:** in `supabase-py`, the signed-upload response field naming (`signed_url` / `signedUrl`, plus a separate `token`) has moved between versions. Print the dict once on day one and pin the shape.

**Read path (browser).** `POST /v1/evidence/signed-urls` with a user JWT, max 50 keys, `ttl_seconds` clamped to 60–900 (default 300). Every issuance writes an `access_log` row (`user_id`, `action='evidence_view'`, `resource=key`) — DPDP Act 2023 accountability, and it is one SQL query to answer "who looked at this vehicle".

Size caps enforced at the bucket: plate crop ≤ 2 MiB, full frame ≤ 8 MiB, clip ≤ 25 MiB. JPEG quality 85, plate crop upscaled to 192 px height max — a 96×32 plate crop lands at 6–12 KB.

---

### 5. Complete endpoint table

`M` = MVP (must exist for the demo), `S` = stretch. Auth column names the *minimum* role.

| M/S | Method | Path | Auth | Purpose |
|---|---|---|---|---|
| M | GET | `/v1/health` | public | Liveness. Always 200 if the process is up. |
| M | GET | `/v1/health/deep` | public | DB round-trip, Storage reachability, per-camera heartbeat age. |
| M | GET | `/v1/cameras` | operator | List cameras with coords, state, last heartbeat. |
| M | GET | `/v1/cameras.geojson` | operator | Same data as a GeoJSON `FeatureCollection` — Mapbox consumes the URL directly. |
| M | GET | `/v1/cameras/{camera_id}` | operator | One camera + last 20 sightings. |
| M | POST | `/v1/cameras` | admin | Register a camera (`camera_id`, name, lat, lon, heading, `stream_url`). |
| M | PATCH | `/v1/cameras/{camera_id}` | admin | Update coords/name/`is_active`. |
| S | DELETE | `/v1/cameras/{camera_id}` | admin | Soft delete (`is_active=false`); never hard-delete, sightings reference it. |
| M | POST | `/v1/ingest/sightings` | edge HMAC | Batch sighting ingest (§3.2). |
| M | POST | `/v1/ingest/anomalies` | edge HMAC | Batch anomaly ingest (§3.3). |
| M | POST | `/v1/ingest/heartbeat` | edge HMAC | Worker/camera liveness + FPS (§3.4). |
| M | POST | `/v1/evidence/upload-urls` | edge HMAC | Batch signed **upload** URLs. |
| M | POST | `/v1/evidence/signed-urls` | operator | Batch signed **download** URLs + access log. |
| M | GET | `/v1/search/plates` | operator | Plate search: exact, prefix, fuzzy/OCR-confusion. |
| M | GET | `/v1/vehicles/{plate_text}/trajectory` | operator | Ordered sightings + legs + GeoJSON LineString for a time window. |
| S | GET | `/v1/vehicles/{plate_text}` | operator | Profile: first/last seen, camera histogram, class/colour consensus. |
| M | GET | `/v1/alerts` | operator | Filtered, cursor-paginated alert list. |
| M | GET | `/v1/alerts/{alert_id}` | operator | Alert detail + evidence keys + linked sightings. |
| M | POST | `/v1/alerts/{alert_id}/acknowledge` | operator | Claim the alert (`new` → `acknowledged`). |
| M | POST | `/v1/alerts/{alert_id}/resolve` | investigator | Close it with an outcome + note. |
| M | GET | `/v1/watchlist` | operator | Active watchlist entries. |
| M | POST | `/v1/watchlist` | investigator | Add a plate (reason, severity, expiry). |
| S | PATCH | `/v1/watchlist/{id}` | investigator | Edit reason/severity/expiry. |
| M | DELETE | `/v1/watchlist/{id}` | investigator | Deactivate (`active=false`). |
| M | GET | `/v1/review` | operator | Low-confidence sighting queue, cursor-paginated. |
| M | POST | `/v1/review/{sighting_id}/correction` | operator | Confirm / correct / discard a plate read. |
| M | GET | `/v1/stats/summary` | operator | Dashboard tiles + 24 h sparkline buckets. |
| S | GET | `/v1/stats/heatmap` | investigator | H3/grid-binned sighting density for the map layer. |
| S | GET | `/v1/cameras/{camera_id}/preview.mjpg` | operator | Annotated MJPEG preview proxied from the edge. |
| S | POST | `/v1/exports/case` | investigator | Build a signed ZIP of a case's evidence. |

---

### 6. Full schemas for the six endpoints that matter

#### 6.1 `GET /v1/alerts` — the screen the demo lives on

Query params:

| Param | Type | Default | Notes |
|---|---|---|---|
| `status` | `new,acknowledged,resolved,dismissed` (CSV) | `new,acknowledged` | |
| `severity_min` | int 1–5 | `1` | |
| `alert_type` | CSV | all | `cloned_plate,speeding,hit_and_run,loitering,fight,accident,weapon,watchlist_hit,unregistered` |
| `camera_id` | CSV | all | |
| `plate_text` | string | — | exact match |
| `from`, `to` | RFC 3339 | last 24 h | on `first_seen_at` |
| `cursor` | opaque | — | §8 |
| `limit` | int 1–100 | `50` | |

```python
class AlertOut(BaseModel):
    alert_id: UUID
    alert_type: str
    severity: int = Field(ge=1, le=5)          # 5 = critical
    status: str                                 # new|acknowledged|resolved|dismissed
    plate_text: str | None
    vehicle_class: VehicleClass | None
    camera_id: str | None
    camera_name: str | None
    lat: float | None
    lon: float | None
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    title: str                                  # "Cloned plate — MH12DE1433"
    detail: str                                 # human sentence, pre-rendered server-side
    evidence_keys: list[str]
    linked_sighting_ids: list[int]
    confidence: float
    acknowledged_by: UUID | None
    acknowledged_by_email: str | None
    acknowledged_at: AwareDatetime | None
    resolved_at: AwareDatetime | None
    resolution: str | None                      # actionable|false_positive|duplicate|no_action
    resolution_note: str | None

class Page(BaseModel):
    items: list[AlertOut]
    next_cursor: str | None
    has_more: bool
    total_estimate: int | None    # from pg_class.reltuples when filters are trivial; else null
```

`detail` is rendered on the server, not in React. One sentence generator, one place to fix, and the CSV export and the UI never disagree.

```json
{
  "items": [
    { "alert_id": "7c2b93f0-1a44-4f0e-9a8e-6b2e1c5d9f30",
      "alert_type": "cloned_plate", "severity": 5, "status": "new",
      "plate_text": "MH12DE1433", "vehicle_class": "car",
      "camera_id": "CAM-NGP-RING-07", "camera_name": "Ring Road / Wardha Jn",
      "lat": 21.1257, "lon": 79.0512,
      "first_seen_at": "2026-09-05T11:42:17.226Z",
      "last_seen_at": "2026-09-05T11:44:02.910Z",
      "title": "Cloned plate — MH12DE1433",
      "detail": "MH12DE1433 seen at CAM-NGP-RING-07 and CAM-NGP-SITA-02 within 105 s; the 8.4 km separation implies 288 km/h. One of the two is a clone.",
      "evidence_keys": [
        "evidence/CAM-NGP-RING-07/2026/09/05/11/3d9c0f5a-..._plate.jpg",
        "evidence/CAM-NGP-SITA-02/2026/09/05/11/91ab33c1-..._plate.jpg"
      ],
      "linked_sighting_ids": [884213, 884977],
      "confidence": 0.93,
      "acknowledged_by": null, "acknowledged_by_email": null,
      "acknowledged_at": null, "resolved_at": null,
      "resolution": null, "resolution_note": null }
  ],
  "next_cursor": "eyJ0cyI6IjIwMjYtMDktMDVUMTE6NDI6MTcuMjI2WiIsImlkIjoiN2MyYjkzZjAtMWE0NC00ZjBlLTlhOGUtNmIyZTFjNWQ5ZjMwIn0",
  "has_more": true,
  "total_estimate": null
}
```

#### 6.2 `POST /v1/alerts/{alert_id}/acknowledge` and `/resolve`

```python
class AcknowledgeIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)

class ResolveIn(BaseModel):
    resolution: Literal["actionable", "false_positive", "duplicate", "no_action"]
    note: str = Field(min_length=3, max_length=2000)
    fir_reference: str | None = Field(default=None, max_length=64)
```

Both return the full `AlertOut`. Transitions are enforced server-side with a conditional update, which makes them safe against two operators clicking at once:

```sql
update public.alerts
   set status='acknowledged', acknowledged_by=$2, acknowledged_at=now()
 where alert_id=$1 and status='new'
returning *;
```

Zero rows → `409 alert_already_claimed`, and the response body includes `details.current_status` and `details.acknowledged_by_email` so the UI can say "already claimed by r.kumar@…" rather than a generic failure. `resolve` requires `investigator`; an `operator` who resolves gets `403 insufficient_role`. `resolution` is a closed enum because a free-text outcome field is worthless for the "false-positive rate" stat on the dashboard.

#### 6.3 `GET /v1/vehicles/{plate_text}/trajectory`

Params: `from` (required, RFC 3339), `to` (required), `max_points` (default 500, max 2000), `min_confidence` (default `0.60`).

```python
class TrajectoryPoint(BaseModel):
    sighting_id: int
    camera_id: str
    camera_name: str
    lat: float
    lon: float
    captured_at: AwareDatetime
    ocr_confidence: float
    vehicle_class: VehicleClass
    plate_crop_key: str | None

class TrajectoryLeg(BaseModel):
    from_camera: str
    to_camera: str
    depart_at: AwareDatetime
    arrive_at: AwareDatetime
    dt_s: float
    straight_line_m: float          # ST_Distance on geography
    road_distance_m: float | None   # null in MVP; Mapbox Matrix API in stretch
    implied_speed_kmph: float       # straight_line_m / dt_s, so it under-reads real speed
    plausible: bool                 # false if implied_speed_kmph > 150

class TrajectoryOut(BaseModel):
    plate_text: str
    window_from: AwareDatetime
    window_to: AwareDatetime
    point_count: int
    truncated: bool
    points: list[TrajectoryPoint]
    legs: list[TrajectoryLeg]
    path_geojson: dict              # LineString, feed straight into a Mapbox GeoJSON source
    distinct_cameras: int
    total_straight_line_m: float
```

`implied_speed_kmph` uses straight-line distance and is therefore a **lower bound** on real speed. That asymmetry is the point: if the straight-line speed already exceeds 150 km/h, the road speed is higher still, so `plausible=false` is a sound clone signal and never a false positive from road curvature. 150 km/h is chosen above any legal Indian road speed (max 120 km/h on expressways) with headroom for a 20 s clock/GPS error on a short baseline.

```json
{ "plate_text": "MH12DE1433",
  "window_from": "2026-09-05T06:00:00Z", "window_to": "2026-09-05T12:00:00Z",
  "point_count": 4, "truncated": false,
  "points": [
    {"sighting_id": 884213, "camera_id": "CAM-NGP-RING-07", "camera_name": "Ring Road / Wardha Jn",
     "lat": 21.1257, "lon": 79.0512, "captured_at": "2026-09-05T11:42:17.226Z",
     "ocr_confidence": 0.947, "vehicle_class": "car",
     "plate_crop_key": "evidence/CAM-NGP-RING-07/2026/09/05/11/3d9c0f5a-..._plate.jpg"}
  ],
  "legs": [
    {"from_camera": "CAM-NGP-RING-07", "to_camera": "CAM-NGP-SITA-02",
     "depart_at": "2026-09-05T11:42:17.226Z", "arrive_at": "2026-09-05T11:44:02.910Z",
     "dt_s": 105.68, "straight_line_m": 8412.0, "road_distance_m": null,
     "implied_speed_kmph": 286.6, "plausible": false}
  ],
  "path_geojson": {"type": "LineString",
                   "coordinates": [[79.0512, 21.1257], [79.0930, 21.1701]]},
  "distinct_cameras": 3, "total_straight_line_m": 11840.0 }
```

#### 6.4 `GET /v1/search/plates`

Params: `q` (min 3 chars), `mode` = `auto|exact|prefix|fuzzy` (default `auto`), `from`, `to`, `camera_id`, `vehicle_class`, `cursor`, `limit` (≤100).

`auto` resolves to `exact` when `q` fully matches `PLATE_RE`, `prefix` when it is a valid left-anchored fragment (`MH12DE`), otherwise `fuzzy`.

`fuzzy` expands each character into its OCR confusion set and builds a `similar to` / trigram query. This is the difference between finding the vehicle and not:

```python
CONFUSIONS = {
    "0": "0OQD", "O": "O0QD", "Q": "Q0O", "D": "D0O",
    "1": "1IL7", "I": "I1L", "L": "L1I",
    "8": "8B6",  "B": "B8",
    "5": "5S6",  "S": "S5",
    "2": "2Z",   "Z": "Z2",
    "6": "6G58", "G": "G6C",
    "4": "4A",   "A": "A4",
    "7": "71T",  "T": "T7",
    "9": "9gq",  "M": "MN", "N": "NM", "U": "UV", "V": "VU",
}
def confusion_regex(q: str) -> str:
    return "^" + "".join(f"[{CONFUSIONS.get(c, c)}]" for c in q.upper()) + ".*$"
```

Response groups by plate, not by sighting — an operator wants "which vehicles", then drills in:

```python
class PlateMatch(BaseModel):
    plate_text: str
    match_kind: Literal["exact", "prefix", "fuzzy"]
    similarity: float                  # 1.0 for exact; pg_trgm similarity() for fuzzy
    sighting_count: int
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    last_camera_id: str
    last_camera_name: str
    vehicle_class: VehicleClass | None  # modal value across sightings
    vehicle_color: str | None
    on_watchlist: bool
    open_alert_count: int
    best_plate_crop_key: str | None     # crop from the highest-ocr_confidence sighting
```

#### 6.5 `POST /v1/review/{sighting_id}/correction`

The review queue is what turns "94 % OCR" into "near-perfect" honestly, and it doubles as your fine-tuning dataset. `GET /v1/review` returns sightings where `ocr_confidence < 0.85` **or** `plate_valid_format = false`, oldest first. 0.85 is set from PaddleOCR behaviour on Indian plates: above it, character-level errors are rare; between 0.60 and 0.85 the failures are almost entirely the confusion pairs above, which a human resolves in under two seconds from the crop.

```python
class CorrectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm", "correct", "discard"]
    corrected_plate: str | None = Field(default=None, pattern=r"^[A-Z0-9]{4,16}$")
    reason: Literal["glare","motion_blur","occlusion","mud","angle","fancy_font",
                    "not_a_plate","other"] | None = None
    note: str | None = Field(default=None, max_length=500)

class CorrectionOut(BaseModel):
    sighting_id: int
    plate_text: str | None
    needs_review: bool
    corrected_by: UUID
    corrected_at: AwareDatetime
    alerts_reevaluated: list[AlertStub]   # correcting a plate can create/void alerts
```

`action="correct"` requires `corrected_plate` (422 otherwise), writes a `plate_corrections` row (`sighting_id`, `original_text`, `corrected_text`, `reason`, `corrected_by`, `plate_crop_key`) and **re-runs the alert rules** for that sighting — a corrected plate can turn out to be on the watchlist. Export `plate_corrections` joined to the crops as the Colab fine-tune set; that loop is a slide.

#### 6.6 `GET /v1/stats/summary`

Param `window` = `1h|24h|7d` (default `24h`).

```python
class StatsSummary(BaseModel):
    window: str
    generated_at: AwareDatetime
    sightings_total: int
    sightings_with_plate: int
    unique_plates: int
    read_rate: float                      # sightings_with_plate / sightings_total
    mean_ocr_confidence: float
    cameras_total: int
    cameras_streaming: int
    cameras_stale: int
    cameras_offline: int
    alerts_open: int
    alerts_by_severity: dict[str, int]    # {"5": 2, "4": 7, "3": 19, "2": 40, "1": 88}
    alerts_by_type: dict[str, int]
    median_ack_seconds: float | None
    false_positive_rate: float | None     # resolved as false_positive / resolved
    review_queue_depth: int
    watchlist_active: int
    timeline: list[TimelineBucket]        # 24 hourly buckets: {bucket_start, sightings, alerts}
```

Cache for 15 s in-process (`cachetools.TTLCache`). The dashboard polls it every 10 s; without the cache, six open browser tabs run six full aggregations per second against a growing sightings table.

---

### 7. Error envelope and status codes

Every non-2xx has exactly this body — the frontend has one error renderer, not fourteen.

```json
{ "error": {
    "code": "alert_already_claimed",
    "message": "Alert was acknowledged by another operator.",
    "details": { "current_status": "acknowledged",
                 "acknowledged_by_email": "r.kumar@nagpurpolice.gov.in" },
    "request_id": "5f0d9b2a-7c31-4a6e-9b02-8d61f4c3e7a1" } }
```

```python
# apps/api/app/errors.py
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}

def install(app):
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError):
        return JSONResponse(exc.status, {"error": {
            "code": exc.code, "message": exc.message, "details": exc.details,
            "request_id": getattr(request.state, "request_id", None)}})

    @app.exception_handler(RequestValidationError)
    async def _val(request: Request, exc: RequestValidationError):
        return JSONResponse(422, {"error": {
            "code": "validation_error", "message": "Request body failed validation.",
            "details": {"errors": exc.errors()},
            "request_id": getattr(request.state, "request_id", None)}})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return JSONResponse(exc.status_code, {"error": {
            "code": str(exc.detail), "message": str(exc.detail), "details": {},
            "request_id": getattr(request.state, "request_id", None)}})
```

| Status | When | Edge behaviour |
|---|---|---|
| `200` | Success, including ingest batches with per-row rejects | delete from spool |
| `201` | Resource created (`POST /v1/cameras`, `/v1/watchlist`) | — |
| `204` | Soft delete succeeded | — |
| `400` | Malformed query params / bad cursor | dead-letter |
| `401` | Bad or missing JWT / HMAC | dead-letter, alarm loudly |
| `403` | Authenticated but wrong role | dead-letter |
| `404` | Unknown alert / camera / plate | — |
| `409` | State conflict (already acknowledged, duplicate `camera_id`) | — |
| `413` | Body over the cap | dead-letter (split the batch) |
| `422` | Pydantic validation failure | dead-letter |
| `429` | Rate limited; `Retry-After` present | back off by `Retry-After` |
| `500` | Unhandled — body still uses the envelope, message is generic | retry with backoff |
| `503` | DB unreachable (from `/health/deep` or a pool timeout) | retry with backoff |

Never leak a traceback into `message`; log it against `request_id`.

---

### 8. Pagination — cursor, not offset

`sightings` grows at roughly 30–80 rows per camera per minute; a two-hour demo with six replay cameras is ~40 k rows, and a week of a real deployment is millions. `OFFSET 20000` makes Postgres walk and discard 20 000 rows — latency grows linearly with page depth. Worse, offset is *incorrect* under concurrent inserts: a new sighting arriving between page 1 and page 2 shifts everything down one, so the operator sees a duplicate row and misses another. Both problems vanish with keyset pagination.

Every paginated list is ordered by `(<time_col> DESC, <id> DESC)` with a matching composite index, and the cursor is base64url of the last row's sort key:

```python
# apps/api/app/pagination.py
import base64, json
from datetime import datetime

def encode_cursor(ts: datetime, row_id: str | int) -> str:
    raw = json.dumps({"ts": ts.isoformat(), "id": str(row_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

def decode_cursor(cur: str) -> tuple[datetime, str]:
    pad = "=" * (-len(cur) % 4)
    try:
        d = json.loads(base64.urlsafe_b64decode(cur + pad))
        return datetime.fromisoformat(d["ts"]), d["id"]
    except Exception:
        raise ApiError(400, "bad_cursor", "Cursor is malformed or from an older API version.")
```

```sql
-- page N>1 of /v1/alerts
select * from public.alerts
 where status = any($1)
   and (first_seen_at, alert_id) < ($cursor_ts, $cursor_id)   -- row-value comparison
 order by first_seen_at desc, alert_id desc
 limit $limit + 1;                                            -- +1 reveals has_more
create index alerts_keyset on public.alerts (first_seen_at desc, alert_id desc);
```

Fetch `limit + 1`, return `limit`, set `has_more` from the extra row. `total_estimate` is `null` for filtered queries — an exact `count(*)` on a large filtered scan costs more than the page itself, and nobody clicks page 400.

---

### 9. Real-time — use Supabase Realtime, not a FastAPI WebSocket

**Decision: the browser subscribes to Supabase Realtime directly.**

| | Supabase Realtime | FastAPI WebSocket |
|---|---|---|
| Auth | Existing JWT, RLS filters rows per role | Hand-rolled: token in query string or first frame |
| Fan-out | Managed; N tabs is not your problem | You write the connection registry and broadcast loop |
| Reconnect | `supabase-js` backs off and re-subscribes | You implement it, twice (client and server) |
| Missed events while offline | Refetch the REST list on `SUBSCRIBED` | Same, plus you build the replay |
| Failure surface | One less process to keep alive during the demo | A stalled event loop kills alerts silently |
| Code to write | ~15 lines of TS | ~150 lines of Python + ~80 of TS |

The one thing a FastAPI WebSocket would buy — pushing events that are not database rows — you do not need, because every event worth showing (alert, camera health, review item) is already a row you must persist anyway. Reserve `/v1/ws/*` for the stretch MJPEG/preview path only.

Enable the publication (DB section owns this, API section depends on it):

```sql
alter publication supabase_realtime add table public.alerts;
alter publication supabase_realtime add table public.camera_health;
alter table public.alerts replica identity full;   -- so UPDATE payloads carry `old` values
```

Channel naming — one channel per concern, filters do the narrowing:

| Channel name | Source | Events |
|---|---|---|
| `rt:alerts` | `postgres_changes` on `public.alerts` | `INSERT` (new alert toast + row prepend), `UPDATE` (status change) |
| `rt:alerts:sev-critical` | same table, `filter: severity=gte.4` | drives the audible chime; keeps low-severity noise off it |
| `rt:camera-health` | `postgres_changes` on `public.camera_health` | camera state chips in the header |
| `rt:review` | `postgres_changes` on `public.review_queue` | queue-depth badge |
| `ops:presence` | Realtime Presence | which operators are online (stretch) |

```ts
// apps/web/src/lib/realtime.ts
import { createClient, type RealtimePostgresInsertPayload } from "@supabase/supabase-js";
import type { components } from "./api-types";

type Alert = components["schemas"]["AlertOut"];

export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  { realtime: { params: { eventsPerSecond: 20 } } },  // throttle: a burst must not stall React
);

export function subscribeAlerts(
  onInsert: (a: Alert) => void,
  onUpdate: (a: Alert) => void,
  onResync: () => void,               // called on (re)connect — refetch the REST list
) {
  const ch = supabase
    .channel("rt:alerts")
    .on("postgres_changes",
        { event: "INSERT", schema: "public", table: "alerts" },
        (p: RealtimePostgresInsertPayload<Alert>) => onInsert(p.new))
    .on("postgres_changes",
        { event: "UPDATE", schema: "public", table: "alerts" },
        (p) => onUpdate(p.new as Alert))
    .subscribe((status) => {
      if (status === "SUBSCRIBED") onResync();
      if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
        console.warn("[rt] alerts channel down:", status);   // UI shows a LIVE/RECONNECTING chip
      }
    });
  return () => { void supabase.removeChannel(ch); };
}
```

Two rules that prevent the classic demo failure:

1. **Realtime is an optimisation, never the source of truth.** Every screen fetches its data over REST on mount and on every `SUBSCRIBED` transition; Realtime only patches the already-loaded list. If the socket dies mid-demo, a 10 s poll fallback keeps the wall updating and nobody notices.
2. `replica identity full` on `alerts` is required, otherwise `UPDATE` payloads omit unchanged columns and your acknowledged row loses its `plate_text` in the UI.

The header carries a hairline status chip: `LIVE` (accent), `RECONNECTING` (amber), `OFFLINE — POLLING` (grey). Uppercase micro-label, tabular numerals for the latency, no animation beyond a 1 px underline.

---

### 10. Keeping TypeScript in sync with Pydantic

Pydantic v2 models are the single source of truth; TypeScript is **generated**, never hand-written. Nobody edits `api-types.ts`.

```bash
# apps/api — dump the schema without booting a server (works offline, works in CI)
python -c "import json; from app.main import app; print(json.dumps(app.openapi()))" > ../../packages/contracts/openapi.json

# repo root — generate TS
npx openapi-typescript packages/contracts/openapi.json -o apps/web/src/lib/api-types.ts
```

`package.json` (root):

```json
{
  "scripts": {
    "contracts:gen": "python -c \"import json; from app.main import app; print(json.dumps(app.openapi()))\" > packages/contracts/openapi.json && openapi-typescript packages/contracts/openapi.json -o apps/web/src/lib/api-types.ts",
    "contracts:check": "npm run contracts:gen && git diff --exit-code packages/contracts/openapi.json apps/web/src/lib/api-types.ts"
  },
  "devDependencies": { "openapi-typescript": "^7.4.0", "openapi-fetch": "^0.13.0" }
}
```

Run `contracts:gen` in the shell script that starts the dev servers, so drift is impossible by construction. `contracts:check` is the CI gate: if a Pydantic model changed and the TS was not regenerated, the diff is non-empty and the build fails.

> **Verify:** `openapi-typescript` v7 emits `components["schemas"]["X"]`; v6 emitted `components["schemas"]["X"]` too but with different `paths` operation shapes. Pin the major version in `package.json` and check the first generated file by eye.

Typed client, zero `any`:

```ts
// apps/web/src/lib/api.ts
import createClient from "openapi-fetch";
import type { paths } from "./api-types";
import { supabase } from "./realtime";

export const api = createClient<paths>({ baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL! });

api.use({
  async onRequest({ request }) {
    const { data } = await supabase.auth.getSession();
    if (data.session) request.headers.set("Authorization", `Bearer ${data.session.access_token}`);
    request.headers.set("X-Request-ID", crypto.randomUUID());
    return request;
  },
});

// call site — params and response are fully typed from the Pydantic models
const { data, error } = await api.GET("/v1/alerts", {
  params: { query: { status: "new,acknowledged", severity_min: 3, limit: 50 } },
});
```

Two hygiene rules that make the generated types usable: give every route an explicit `operation_id` (`@router.get("/alerts", operation_id="listAlerts", response_model=Page[AlertOut])`), and always set `response_model` — without it FastAPI emits an untyped `{}` schema and the TS is worthless.

---

### 11. Rate limiting, body size, CORS

```python
# apps/api/app/main.py (assembly, abridged)
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

def identity(request: Request) -> str:
    kid = request.headers.get("x-edge-key-id")
    if kid:
        return f"edge:{kid}"
    auth = request.headers.get("authorization", "")
    return f"user:{auth[-24:]}" if auth else f"ip:{request.client.host}"

limiter = Limiter(key_func=identity, default_limits=["600/minute"])
app = FastAPI(title="ANPR City Platform API", version="0.1.0")
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

ALLOWED_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,          # never ["*"] — explicit list, even in dev
    allow_credentials=False,                # we use Bearer headers, not cookies
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["authorization", "content-type", "x-request-id", "x-idempotency-key"],
    expose_headers=["x-request-id", "x-ratelimit-remaining", "retry-after"],
    max_age=600,
)

MAX_BODY_BYTES = 1_048_576   # 1 MiB

@app.middleware("http")
async def limit_body(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_BODY_BYTES:
        return JSONResponse(413, {"error": {"code": "payload_too_large",
            "message": f"Body exceeds {MAX_BODY_BYTES} bytes. Split the batch.",
            "details": {"max_bytes": MAX_BODY_BYTES},
            "request_id": getattr(request.state, "request_id", None)}})
    return await call_next(request)

@app.exception_handler(RateLimitExceeded)
async def _ratelimited(request: Request, exc: RateLimitExceeded):
    return JSONResponse(429, {"error": {"code": "rate_limited",
        "message": "Too many requests.", "details": {"limit": str(exc.detail)},
        "request_id": getattr(request.state, "request_id", None)}},
        headers={"Retry-After": "5"})
```

Per-route limits:

| Route | Limit | Reasoning |
|---|---|---|
| `POST /v1/ingest/sightings` | `240/minute` per edge key | 240 × 200 = 48 000 sightings/min ceiling, ~40× the 6-camera demo load; a runaway loop is capped |
| `POST /v1/ingest/heartbeat` | `20/minute` per edge key | 15 s cadence = 4/min; 5× headroom |
| `POST /v1/evidence/upload-urls` | `120/minute` per edge key | 1 call per ingest batch |
| `POST /v1/evidence/signed-urls` | `300/minute` per user | a gallery of 50 tiles is one call; 300 covers heavy scrolling |
| `GET /v1/search/plates` | `60/minute` per user | search-as-you-type is debounced 350 ms client-side |
| `GET /v1/vehicles/*/trajectory` | `60/minute` per user | expensive PostGIS query |
| everything else | `600/minute` per identity | |

**1 MiB is generous precisely because images are not in the body**: 200 sightings × ~450 bytes ≈ 90 KB. If you ever hit 413 on ingest, something is wrong with your batching, not with the cap.

The in-memory limiter is per-worker. Run `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1` for the demo; multiple workers make both the rate limiter and the HMAC nonce cache per-worker.

---

### 12. curl book (paste at 3 a.m.)

```bash
# --- environment ------------------------------------------------------------
export API=http://127.0.0.1:8000
export EDGE_KEY_ID=edge-worker-01
export EDGE_SECRET='<contents of EDGE_SECRET_WORKER_01>'
export JWT='<access_token from supabase.auth.getSession() in the browser console>'
```

```bash
# --- 1. health --------------------------------------------------------------
curl -s "$API/v1/health" | python -m json.tool
curl -s "$API/v1/health/deep" | python -m json.tool
# expect: {"status":"ok","db_ms":4.2,"storage_ok":true,"cameras":{"streaming":6,"stale":0,"offline":0}}
```

```bash
# --- 2. signed ingest (reusable helper; Git Bash on Windows, needs openssl) ---
edge_post () {   # usage: edge_post /v1/ingest/sightings body.json
  local path="$1" file="$2"
  local body; body=$(cat "$file")
  local hash; hash=$(printf '%s' "$body" | openssl dgst -sha256 -hex | awk '{print $NF}')
  local ts;   ts=$(date +%s)
  local nonce; nonce=$(python -c "import uuid;print(uuid.uuid4())")   # uuidgen is absent in Git Bash
  local canon; canon=$(printf '%s\n%s\n%s\n%s\n%s\n%s' "$EDGE_KEY_ID" "$ts" "$nonce" "POST" "$path" "$hash")
  local sig;  sig=$(printf '%s' "$canon" | openssl dgst -sha256 -hmac "$EDGE_SECRET" -hex | awk '{print $NF}')
  curl -s -X POST "$API$path" \
    -H "Content-Type: application/json" \
    -H "X-Edge-Key-Id: $EDGE_KEY_ID" -H "X-Edge-Timestamp: $ts" \
    -H "X-Edge-Nonce: $nonce" -H "X-Edge-Signature: v1=$sig" \
    --data-binary "$body" | python -m json.tool
}
edge_post /v1/ingest/sightings ./fixtures/batch.json
# Gotcha: --data-binary, never -d. -d strips newlines and the body hash stops matching.
```

```bash
# --- 3. alerts list (filtered, first page) ----------------------------------
curl -s -G "$API/v1/alerts" -H "Authorization: Bearer $JWT" \
  --data-urlencode "status=new,acknowledged" \
  --data-urlencode "severity_min=3" \
  --data-urlencode "alert_type=cloned_plate,speeding" \
  --data-urlencode "limit=25" | python -m json.tool

# next page — paste next_cursor verbatim
curl -s -G "$API/v1/alerts" -H "Authorization: Bearer $JWT" \
  --data-urlencode "cursor=eyJ0cyI6IjIwMjYtMDktMDVUMTE6NDI6MTcuMjI2WiIsImlkIjoiN2MyYjkzZjAtLi4uIn0" \
  --data-urlencode "limit=25" | python -m json.tool
```

```bash
# --- 4. acknowledge / resolve ------------------------------------------------
curl -s -X POST "$API/v1/alerts/7c2b93f0-1a44-4f0e-9a8e-6b2e1c5d9f30/acknowledge" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"note":"Dispatching unit 12."}' | python -m json.tool

curl -s -X POST "$API/v1/alerts/7c2b93f0-1a44-4f0e-9a8e-6b2e1c5d9f30/resolve" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"resolution":"actionable","note":"Clone confirmed at Sitabuldi; FIR filed.","fir_reference":"FIR/2026/NGP/1187"}' \
  | python -m json.tool
```

```bash
# --- 5. plate search + trajectory -------------------------------------------
curl -s -G "$API/v1/search/plates" -H "Authorization: Bearer $JWT" \
  --data-urlencode "q=MH12DE" --data-urlencode "mode=auto" --data-urlencode "limit=20" \
  | python -m json.tool

curl -s -G "$API/v1/vehicles/MH12DE1433/trajectory" -H "Authorization: Bearer $JWT" \
  --data-urlencode "from=2026-09-05T06:00:00Z" \
  --data-urlencode "to=2026-09-05T12:00:00Z" \
  --data-urlencode "max_points=500" | python -m json.tool
```

```bash
# --- 6. evidence URLs (read) -------------------------------------------------
curl -s -X POST "$API/v1/evidence/signed-urls" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"keys":["evidence/CAM-NGP-RING-07/2026/09/05/11/3d9c0f5a-7b21-53a8-9f44-8c0b1a7e2d90_plate.jpg"],"ttl_seconds":300}' \
  | python -m json.tool
```

```bash
# --- 7. review correction ----------------------------------------------------
curl -s -G "$API/v1/review" -H "Authorization: Bearer $JWT" --data-urlencode "limit=20" | python -m json.tool

curl -s -X POST "$API/v1/review/884977/correction" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"action":"correct","corrected_plate":"MH12DE1433","reason":"motion_blur","note":"Two-wheeler, 1 vs I."}' \
  | python -m json.tool
```

```bash
# --- 8. camera registration + stats -----------------------------------------
curl -s -X POST "$API/v1/cameras" -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"camera_id":"CAM-NGP-RING-07","name":"Ring Road / Wardha Jn","lat":21.1257,"lon":79.0512,
       "heading_deg":135,"road_name":"Ring Road","stream_url":"rtsp://127.0.0.1:8554/ring07","is_active":true}' \
  | python -m json.tool

curl -s -G "$API/v1/stats/summary" -H "Authorization: Bearer $JWT" \
  --data-urlencode "window=24h" | python -m json.tool
```

```bash
# --- 9. auth sanity: is my token any good, and what role am I? ---------------
python - <<'PY'
import base64, json, os
tok = os.environ["JWT"].split(".")[1]
print(json.dumps(json.loads(base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))), indent=2))
PY
# look for: "aud":"authenticated", "app_metadata":{"sih_role":"investigator"}, "exp" in the future
```

---

### 13. Build order for 36 hours

Hour 0–2, one person, before anyone else can start: `apps/api` skeleton with `/v1/health`, the error envelope, `Principal`/`require`, `verify_edge`, and **stub handlers for every MVP route returning hard-coded fixtures matching the schemas above**. Run `contracts:gen`. The frontend and the edge then build against a real, typed, always-200 API from hour 2 while the database work proceeds in parallel. Nothing else in this plan unblocks as many people per hour of effort.

---

## Appendix — Interface Contracts Declared by This Section

- `base path prefix: /v1 on all API routes; API base URL env NEXT_PUBLIC_API_BASE_URL (web), API_BASE_URL (edge)`
- `endpoint: GET /v1/health -> {status, version, commit, uptime_s}`
- `endpoint: GET /v1/health/deep -> {status, db_ms, storage_ok, cameras:{streaming,stale,offline}}`
- `endpoint: GET /v1/cameras (auth operator) -> Page[CameraOut]`
- `endpoint: GET /v1/cameras.geojson (auth operator) -> GeoJSON FeatureCollection for Mapbox source`
- `endpoint: GET /v1/cameras/{camera_id} (auth operator)`
- `endpoint: POST /v1/cameras (auth admin) -> 201 CameraOut`
- `endpoint: PATCH /v1/cameras/{camera_id} (auth admin)`
- `endpoint: DELETE /v1/cameras/{camera_id} (auth admin, soft delete sets is_active=false) -> 204`
- `endpoint: POST /v1/ingest/sightings (edge HMAC) body SightingBatch -> 200 IngestResult`
- `endpoint: POST /v1/ingest/anomalies (edge HMAC) body AnomalyBatch -> 200 IngestResult`
- `endpoint: POST /v1/ingest/heartbeat (edge HMAC) body HeartbeatIn -> 200`
- `endpoint: POST /v1/evidence/upload-urls (edge HMAC) body {keys[<=100], content_type} -> {urls:[{key,upload_url,expires_at}]}`
- `endpoint: POST /v1/evidence/signed-urls (auth operator) body {keys[<=50], ttl_seconds 60..900 default 300} -> {urls:[{key,url,expires_at}]}`
- `endpoint: GET /v1/search/plates (auth operator) params q,mode=auto|exact|prefix|fuzzy,from,to,camera_id,vehicle_class,cursor,limit -> Page[PlateMatch]`
- `endpoint: GET /v1/vehicles/{plate_text}/trajectory (auth operator) params from,to,max_points,min_confidence -> TrajectoryOut`
- `endpoint: GET /v1/vehicles/{plate_text} (auth operator, stretch)`
- `endpoint: GET /v1/alerts (auth operator) params status,severity_min,alert_type,camera_id,plate_text,from,to,cursor,limit -> Page[AlertOut]`
- `endpoint: GET /v1/alerts/{alert_id} (auth operator) -> AlertOut`
- `endpoint: POST /v1/alerts/{alert_id}/acknowledge (auth operator) body AcknowledgeIn -> AlertOut; 409 alert_already_claimed`
- `endpoint: POST /v1/alerts/{alert_id}/resolve (auth investigator) body ResolveIn -> AlertOut`
- `endpoint: GET /v1/watchlist (auth operator) -> Page[WatchlistOut]`
- `endpoint: POST /v1/watchlist (auth investigator) -> 201`
- `endpoint: PATCH /v1/watchlist/{id} (auth investigator)`
- `endpoint: DELETE /v1/watchlist/{id} (auth investigator, sets active=false) -> 204`
- `endpoint: GET /v1/review (auth operator) params cursor,limit -> Page[ReviewItem]`
- `endpoint: POST /v1/review/{sighting_id}/correction (auth operator) body CorrectionIn -> CorrectionOut`
- `endpoint: GET /v1/stats/summary (auth operator) param window=1h|24h|7d -> StatsSummary`
- `endpoint: GET /v1/stats/heatmap (auth investigator, stretch)`
- `endpoint: GET /v1/cameras/{camera_id}/preview.mjpg (auth operator, stretch)`
- `endpoint: POST /v1/exports/case (auth investigator, stretch)`
- `header: X-Request-ID (echoed or generated uuid4, present on every response)`
- `header: X-RateLimit-Remaining`
- `header: X-Edge-Key-Id (edge HMAC)`
- `header: X-Edge-Timestamp (unix seconds, edge HMAC)`
- `header: X-Edge-Nonce (uuid4, edge HMAC)`
- `header: X-Edge-Signature (format 'v1=<hex hmac-sha256>')`
- `header: X-Idempotency-Key (batch-level, stretch only)`
- `HMAC canonical string: key_id\ntimestamp\nMETHOD\npath\nsha256_hex(body) -- exact order is key_id, timestamp, nonce, METHOD, path, body_sha256, joined by \n with no trailing newline`
- `HMAC max clock skew: 300 s; nonce replay window 600 s`
- `role names in JWT: app_metadata.sih_role in {operator, investigator, admin}; rank operator=1 < investigator=2 < admin=3`
- `JWT audience claim must equal 'authenticated'`
- `env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_JWT_SECRET (API only)`
- `env: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, NEXT_PUBLIC_API_BASE_URL, NEXT_PUBLIC_MAPBOX_TOKEN (web)`
- `env: EDGE_SECRET_WORKER_01 (shared secret for key_id edge-worker-01), API_BASE_URL, EDGE_KEY_ID (edge)`
- `env: CORS_ORIGINS (comma-separated allowlist, default http://localhost:3000)`
- `table: cameras(camera_id text pk, name text, lat double precision, lon double precision, geom geography(Point,4326), heading_deg int, road_name text, stream_url text, is_active bool)`
- `table: sightings(id bigint pk, sighting_uid uuid unique not null, camera_id text fk, track_id text, worker_run_id uuid, captured_at timestamptz, ingested_at timestamptz default now(), plate_text text null, plate_text_raw text, plate_valid_format bool, ocr_confidence real, char_confidences real[], det_confidence real, vehicle_class text, vehicle_color text, bbox int[4], plate_bbox int[4], frames_seen int, plate_crop_key text, frame_key text, pipeline_versions jsonb, needs_review bool)`
- `table: anomaly_events(anomaly_uid uuid unique, camera_id text, event_type text, score real, threshold real, window_start timestamptz, window_end timestamptz, clip_key text, keyframe_key text, linked_track_ids text[], meta jsonb)`
- `table: alerts(alert_id uuid pk, alert_type text, severity smallint 1..5, status text in (new,acknowledged,resolved,dismissed), plate_text text, vehicle_class text, camera_id text, first_seen_at timestamptz, last_seen_at timestamptz, title text, detail text, evidence_keys text[], linked_sighting_ids bigint[], confidence real, acknowledged_by uuid, acknowledged_at timestamptz, resolved_at timestamptz, resolution text, resolution_note text, fir_reference text)`
- `table: camera_health(camera_id text pk, worker_id text, state text in (streaming,stale,offline), fps_in real, fps_processed real, queue_depth int, dropped_frames_1m int, last_frame_at timestamptz, updated_at timestamptz)`
- `table: watchlist(id uuid pk, plate_text text, reason text, severity smallint, added_by uuid, active bool, expires_at timestamptz)`
- `table: review_queue(sighting_id bigint pk/fk, enqueued_at timestamptz, resolved bool)`
- `table: plate_corrections(id bigserial, sighting_id bigint, original_text text, corrected_text text, reason text, corrected_by uuid, corrected_at timestamptz, plate_crop_key text)`
- `table: access_log(id bigserial, user_id uuid, action text, resource text, request_id uuid, at timestamptz) -- DPDP Act 2023 accountability`
- `table: edge_nonces(nonce text pk, seen_at timestamptz) -- only needed if API runs >1 worker`
- `index: alerts_keyset on alerts(first_seen_at desc, alert_id desc)`
- `index required for sightings keyset: (captured_at desc, id desc) and (plate_text, captured_at desc)`
- `storage bucket: 'evidence' (private)`
- `object key: evidence/{camera_id}/{YYYY}/{MM}/{DD}/{HH}/{sighting_uid}_plate.jpg`
- `object key: evidence/{camera_id}/{YYYY}/{MM}/{DD}/{HH}/{sighting_uid}_frame.jpg`
- `object key: clips/{camera_id}/{YYYY}/{MM}/{DD}/{anomaly_uid}.mp4`
- `object key: exports/{user_id}/{export_id}.zip`
- `uuid5 namespace for sighting_uid: 6f2a1c88-1c3e-5a9b-9e3d-0c7a11b6d401 over string '{camera_id}/{worker_run_id}/{track_id}/{first_frame_ms}'`
- `anomaly_uid = uuid5(same namespace, '{camera_id}/{worker_run_id}/{window_start_ms}/{event_type}')`
- `Realtime channel: rt:alerts (postgres_changes on public.alerts, INSERT+UPDATE)`
- `Realtime channel: rt:alerts:sev-critical (postgres_changes on public.alerts, filter severity=gte.4)`
- `Realtime channel: rt:camera-health (postgres_changes on public.camera_health)`
- `Realtime channel: rt:review (postgres_changes on public.review_queue)`
- `Realtime channel: ops:presence (Presence, stretch)`
- `requires: alter publication supabase_realtime add table public.alerts, public.camera_health, public.review_queue`
- `requires: alter table public.alerts replica identity full`
- `error envelope: {"error":{"code":string,"message":string,"details":object,"request_id":string}} on every non-2xx`
- `error codes: validation_error, bad_cursor, edge_auth_missing, edge_clock_skew, edge_nonce_replay, edge_bad_signature, invalid_token, role_not_assigned, insufficient_role, alert_already_claimed, unknown_camera, payload_too_large, rate_limited`
- `pagination: cursor = base64url(json {"ts": iso8601, "id": string}); response envelope {items, next_cursor, has_more, total_estimate}`
- `pagination default limit 50, max 100; fetch limit+1 to compute has_more`
- `MAX_BODY_BYTES = 1048576 (1 MiB) on all JSON bodies -> 413`
- `batch limits: sightings max 200 per POST, anomalies max 50, upload-urls max 100 keys, signed-urls max 50 keys`
- `rate limits: ingest/sightings 240/min per edge key, heartbeat 20/min, upload-urls 120/min, signed-urls 300/min per user, search/plates 60/min, trajectory 60/min, default 600/min`
- `storage size caps: plate crop <= 2 MiB, full frame <= 8 MiB, clip <= 25 MiB`
- `threshold: needs_review when ocr_confidence < 0.85 or plate_valid_format = false`
- `threshold: trajectory leg plausible=false when implied_speed_kmph > 150`
- `threshold: heartbeat every 15 s; camera STALE at >45 s, OFFLINE at >120 s`
- `threshold: edge warns when clock_skew_ms > 2000, suppresses speed alerts when > 5000`
- `threshold: search min q length 3 chars; client debounce 350 ms`
- `plate regex: ^([A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}|\d{2}BH\d{4}[A-Z]{1,2})$`
- `enum VehicleClass: car|motorcycle|auto_rickshaw|bus|truck|tempo|tractor|other`
- `enum alert_type: cloned_plate|speeding|hit_and_run|loitering|fight|accident|weapon|watchlist_hit|unregistered`
- `enum alert status: new|acknowledged|resolved|dismissed`
- `enum resolution: actionable|false_positive|duplicate|no_action`
- `enum correction action: confirm|correct|discard`
- `enum correction reason: glare|motion_blur|occlusion|mud|angle|fancy_font|not_a_plate|other`
- `enum anomaly event_type: fight|accident|loitering|weapon|crowd_surge|generic`
- `generated file: apps/web/src/lib/api-types.ts (from packages/contracts/openapi.json, never hand-edited)`
- `generated file: packages/contracts/openapi.json`
- `npm script: contracts:gen and contracts:check (CI gate via git diff --exit-code)`
- `file: apps/edge/spool.db, table outbox(id, endpoint, body, created_at, attempts, next_attempt, last_error)`
- `edge retry backoff: [1,2,4,8,15,30,60] s with 0.8-1.2x jitter, held at 60 s; MAX_SPOOL_ROWS = 50000`
- `edge treats 400/401/403/413/422 as poison (dead-letter, no retry); 429 obeys Retry-After; 5xx retries with backoff`
- `run command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 (single worker required for in-memory nonce cache and rate limiter)`

## Appendix — MVP vs Stretch

- MVP: FastAPI skeleton with /v1/health, error envelope, X-Request-ID middleware, CORS, 1 MiB body cap
- MVP: HMAC edge auth (verify_edge) with 300 s skew window and in-process nonce cache
- MVP: Supabase JWT verification (current_user / require(min_role)) reading app_metadata.sih_role
- MVP: POST /v1/ingest/sightings with deterministic uuid5 sighting_uid and ON CONFLICT DO NOTHING idempotency
- MVP: POST /v1/ingest/anomalies and POST /v1/ingest/heartbeat
- MVP: SQLite outbox spool on the edge with FIFO drain, exponential backoff + jitter, dead-letter on 4xx
- MVP: POST /v1/evidence/upload-urls (edge) and POST /v1/evidence/signed-urls (browser) - no base64 images anywhere
- MVP: GET /v1/alerts with cursor pagination and status/severity/type/camera/time filters
- MVP: GET /v1/alerts/{id}, POST /v1/alerts/{id}/acknowledge, POST /v1/alerts/{id}/resolve with conditional-update 409 handling
- MVP: GET /v1/search/plates with exact/prefix/fuzzy OCR-confusion expansion
- MVP: GET /v1/vehicles/{plate}/trajectory returning points, legs with implied_speed_kmph, and path_geojson
- MVP: GET /v1/review + POST /v1/review/{sighting_id}/correction writing plate_corrections and re-running alert rules
- MVP: GET /v1/stats/summary with 15 s in-process TTL cache
- MVP: GET /v1/cameras, GET /v1/cameras.geojson, POST/PATCH /v1/cameras
- MVP: GET /v1/watchlist, POST /v1/watchlist, DELETE /v1/watchlist/{id}
- MVP: Supabase Realtime subscription from the browser on rt:alerts and rt:camera-health, with REST refetch on every SUBSCRIBED transition and a 10 s poll fallback
- MVP: contracts:gen script generating apps/web/src/lib/api-types.ts from the FastAPI OpenAPI schema, plus openapi-fetch typed client
- MVP: slowapi per-route rate limits with 429 + Retry-After
- MVP: RLS policies on alerts and sightings so the direct Realtime read path is role-filtered
- MVP: access_log row on every evidence signed-URL issuance and every plate search (DPDP Act 2023)
- MVP: stub handlers for every MVP route returning schema-shaped fixtures within the first 2 hours, so web and edge can build in parallel
- STRETCH: batch-level Idempotency-Key header with a 24 h stored-response replay cache
- STRETCH: edge_nonces table to make replay protection correct under multiple uvicorn workers
- STRETCH: GET /v1/vehicles/{plate} profile endpoint
- STRETCH: GET /v1/stats/heatmap grid-binned density layer
- STRETCH: GET /v1/cameras/{camera_id}/preview.mjpg annotated preview proxy (the only justified WebSocket/stream endpoint)
- STRETCH: POST /v1/exports/case signed evidence ZIP
- STRETCH: DELETE /v1/cameras/{camera_id} soft delete and PATCH /v1/watchlist/{id}
- STRETCH: road_distance_m on trajectory legs via Mapbox Matrix API (MVP leaves it null and uses straight-line distance)
- STRETCH: ops:presence Realtime channel showing which operators are online
- STRETCH: Redis-backed rate limiter and nonce store to allow --workers > 1

## Appendix — Risks

- Supabase JWT signing algorithm mismatch: newer projects use asymmetric ES256 keys, not the legacy HS256 shared secret, so jwt.decode(..., algorithms=['HS256']) fails with an opaque 401 on every request. Mitigation: on hour one, decode a real token's header and check 'alg'; if ES256, swap to PyJWKClient against /auth/v1/.well-known/jwks.json. Budget 20 minutes for this, not zero.
- HMAC canonical-string drift between the Python signer and the Python verifier (trailing newline, path with vs without query string, method case). Mitigation: write one unit test that signs a fixture body and verifies it in-process before either side is wired to HTTP; use --data-binary in curl, never -d.
- ByteTrack track_id resets to 1 on every worker restart, so a naive uuid5 over (camera_id, track_id) silently collides with yesterday's sightings and ON CONFLICT DO NOTHING drops real data with no error. Mitigation: worker_run_id (uuid4 per process start) is part of the uuid5 input - already in the contract; never remove it.
- Realtime silently stops delivering (token expiry after 1 h, laptop sleep, Wi-Fi flap) and the demo wall freezes while looking healthy. Mitigation: every screen refetches over REST on mount and on each SUBSCRIBED transition, a 10 s poll runs as a fallback, and a LIVE / RECONNECTING / OFFLINE-POLLING chip in the header makes the state visible before a judge notices.
- alerts table without 'replica identity full' means Realtime UPDATE payloads omit unchanged columns, so an acknowledged alert loses plate_text and camera_name in the UI and renders as a blank row. Mitigation: the ALTER TABLE is in the contract; add a smoke test that acknowledges an alert and asserts the pushed payload has plate_text.
- Running uvicorn with --workers > 1 makes the in-memory nonce cache and the slowapi limiter per-worker, quietly weakening replay protection and multiplying effective rate limits. Mitigation: --workers 1 is pinned in the run script and stated in the contract; moving beyond one worker requires the edge_nonces table and a Redis limiter first.
- Someone adds a route and forgets the require(...) dependency; because FastAPI holds the service_role key, that route bypasses RLS entirely and is a total auth bypass, not a partial one. Mitigation: a test that walks app.routes and asserts every /v1 path except /v1/health* has either a require(...) or verify_edge dependency in its dependant tree.
- Generated api-types.ts drifts from the Pydantic models after a rushed field rename, producing TypeScript that compiles against a shape the API no longer returns. Mitigation: contracts:gen runs inside the dev start script and contracts:check (git diff --exit-code) gates CI; routes without an explicit response_model are rejected in review because they emit an untyped {} schema.
- Pydantic v2 warns and can shadow behaviour on any field named model_* (protected_namespaces). Naming the field model_versions instead of pipeline_versions produces a confusing warning storm at import time. Mitigation: the contract names it pipeline_versions; if it must be model_*, set ConfigDict(protected_namespaces=()).
- Edge clock skew turns directly into speed error (1 s over a 500 m baseline at 60 km/h is ~3.2%), which can manufacture or hide a cloned-plate alert. Mitigation: clock_skew_ms is returned on every ingest response; the edge warns above 2000 ms and suppresses speed_violation alerts above 5000 ms.
- Evidence upload fails while the sighting POST succeeds, so the UI renders a broken image on the alert that decides the demo. Mitigation: crops are written to the local evidence spool first and uploaded by an independent drain thread; the UI renders a neutral placeholder tile for a 404 key rather than an error state.
- An unfiltered count(*) for total_estimate on a multi-million-row sightings table stalls the dashboard. Mitigation: total_estimate is null for any filtered query, cursor pagination never needs it, and stats/summary is TTL-cached for 15 s.

## Appendix — Open Questions

- Which Supabase JWT signing scheme does the actual project use - legacy HS256 shared secret or the newer asymmetric ES256 signing keys? This changes ~10 lines in apps/api/app/deps/auth.py and must be settled in the first hour.
- Does the DB section expose the alert rules as Postgres triggers/functions (so alerts appear the moment a sighting row lands) or does FastAPI evaluate them inside the ingest handler? The API contract assumes FastAPI evaluates them, because IngestResult.alerts_created returns the alert stubs synchronously; if the DB owns the rules, alerts_created becomes an empty list and the field should be dropped rather than left lying.
- Should sightings ever be readable directly by the browser via supabase-js (bypassing FastAPI) for the map's live dot layer? The current contract says no - every read goes through /v1 - but a 500-dot live layer polling REST at 2 Hz may justify a narrow RLS-protected direct select. Decide before the map layer is built, not after.
- Is one shared edge secret per worker sufficient, or does each camera process get its own key_id? The contract supports N keys via the EDGE_KEYS map; the demo probably runs one multi-camera worker, but confirm with whoever writes the edge supervisor.
- What is the evidence retention period, and who runs the prefix-delete job? The key layout is time-partitioned to make this a one-line delete, but the DPDP Act 2023 storage-limitation answer needs an actual number (suggest 90 days for sightings evidence, indefinite for evidence attached to a resolved-actionable alert) that the compliance section should own.
- Does the review queue need per-item locking so two operators do not correct the same sighting simultaneously? MVP assumes last-write-wins on plate_corrections; if two people review in parallel on stage this will look sloppy. A claim_expires_at column on review_queue is the cheap fix if it matters.
