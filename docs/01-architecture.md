<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## System Architecture & Repository Layout

This section fixes the physical shape of the system: which processes exist, what they say to each other, where the code lives, and what a team member types on day zero. Every other section (schema, models, rules, UI) plugs into the contracts named here.

### 1. Design constraints that drive every decision below

| Constraint | Architectural consequence |
|---|---|
| One laptop, 16 GB RAM, RX 6500M / 4 GB VRAM, **no CUDA** | Exactly **one** GPU-owning process. Extra worker processes do not multiply throughput — they duplicate weights in VRAM and serialise on the same queue. Concurrency comes from *threads inside* that process, not from more processes. |
| ONNX Runtime **DirectML** EP is a Windows-only wheel; ROCm does not support gfx1034 (RX 6500M) | The edge worker **must not be dockerised**. It runs natively in a Windows venv. |
| 36 hours, 4–6 people | Transport and infra must be one `docker compose up` plus three terminals. No Kubernetes, no Kafka, no service mesh. |
| Demo is replayed video, not live cameras | Camera identity, geo-position and time offset are **configuration** (`infra/cameras.yaml`), never hardcoded. Same code path works if a real RTSP camera is plugged in. |
| Judges watch a live dashboard | Alert path must be push, not poll, and must have a visible fallback if venue Wi-Fi drops. |

### 2. End-to-end component diagram

```mermaid
flowchart LR
  subgraph SRC["Video source layer (simulated city)"]
    MP4["datasets/demo_clips/*.mp4"]
    FF["ffmpeg -stream_loop -1 -re<br/>(one process per camera)"]
    MTX["MediaMTX<br/>rtsp://127.0.0.1:8554/cam-XX<br/>(docker)"]
    MP4 --> FF --> MTX
  end

  subgraph EDGE["apps/edge — ONE native Windows process, owns the GPU"]
    CAP["CaptureThread x N<br/>PyAV / cv2 FFMPEG, TCP<br/>ring buffer depth = 1"]
    SCH["Scheduler<br/>fair-share, adaptive fps,<br/>batch up to 4"]
    DET["ORT DirectML session<br/>YOLOv8 vehicle + plate"]
    TRK["ByteTrack<br/>per-camera track ids"]
    OCR["ThreadPool (CPU)<br/>PaddleOCR rec + plate_norm"]
    EVD["EvidenceUploader<br/>bounded queue, drop-on-full"]
    CAP --> SCH --> DET --> TRK --> OCR --> PUB["Publisher"]
    TRK --> EVD
  end

  VAD["apps/edge --mode vad<br/>separate killable process<br/>VideoMAE clip scoring"]

  subgraph BUS["Transport"]
    R1["redis stream: anpr.reads<br/>MAXLEN ~ 20000"]
    R2["redis stream: anpr.tracks"]
    R3["redis stream: anpr.anomaly"]
    HB["redis keys: hb:*  TTL 15s"]
  end

  subgraph API["apps/api — FastAPI (uvicorn, native)"]
    ING["IngestConsumer<br/>XREADGROUP g:ingest"]
    RUL["Rules engine<br/>asyncio task in-process"]
    REST["REST /v1/*"]
    SSE["GET /v1/stream/alerts (SSE fallback)"]
  end

  SB[("Supabase (hosted)<br/>Postgres 15 + PostGIS<br/>Storage: evidence<br/>Realtime: alerts")]
  WEB["apps/web — Next.js App Router<br/>Mapbox GL + data tables"]

  PUB --> R1 & R2
  VAD --> R3
  EDGE -.heartbeat.-> HB
  R1 & R2 & R3 --> ING --> SB
  ING --> RUL --> SB
  EVD -- "signed PUT" --> SB
  SB -- "Realtime WS: alerts INSERT" --> WEB
  SSE -. "fallback if WS blocked" .-> WEB
  WEB -- "REST reads" --> REST
  REST --> SB
  HB --> REST
```

ASCII fallback (paste into the PPT if Mermaid does not render):

```text
 demo_clips/*.mp4
        |  ffmpeg -stream_loop -1 -re  (1 proc per camera)
        v
 +----------------+   rtsp/tcp:8554   +--------------------------------------------+
 |   MediaMTX     |------------------>|  apps/edge  (NATIVE WINDOWS, OWNS THE GPU) |
 |   (docker)     |                   |  N capture threads -> ring buf depth 1     |
 +----------------+                   |  scheduler(batch<=4, adaptive fps)         |
                                      |  YOLOv8 ONNX / DirectML -> ByteTrack       |
                                      |  -> CPU threadpool: PaddleOCR + normalise  |
                                      |  -> evidence uploader (bounded, droppable) |
                                      +----------------+---------------------------+
                                                       | XADD
                     [ apps/edge --mode vad ] --------->|
                          (separate, killable)          v
                                            +-------------------------------+
                                            |  REDIS (docker)               |
                                            |  anpr.reads / anpr.tracks /   |
                                            |  anpr.anomaly  (MAXLEN ~)     |
                                            |  hb:<proc> TTL 15s            |
                                            +---------------+---------------+
                                                            | XREADGROUP g:ingest
                                                            v
                                            +-------------------------------+
                                            | apps/api  FastAPI (native)    |
                                            | ingest consumer + rules loop  |
                                            | REST /v1/*  +  SSE fallback   |
                                            +---------------+---------------+
                                                            | postgrest / storage
                                                            v
                                            +-------------------------------+
                                            | SUPABASE (hosted free tier)   |
                                            | PG15 + PostGIS | Storage      |
                                            +---------------+---------------+
                                                            | Realtime WS (alerts)
                                                            v
                                            +-------------------------------+
                                            | apps/web  Next.js dashboard   |
                                            +-------------------------------+
```

### 3. Transport decision: Redis Streams

| Option | Fit for this build | Verdict |
|---|---|---|
| **Direct HTTPS batch POST** worker → `/v1/ingest` | Zero extra infra, but the frame loop now owns retry/queue/timeout logic. `uvicorn --reload` (which fires ~40× on a hackathon day) drops in-flight batches. No shared state for cross-camera dedup, so we would build a second cache anyway. | Rejected as primary; kept as a **fallback flag**. |
| **NATS / JetStream** | Excellent semantics, single 20 MB binary. But nobody on a student team has debugged a JetStream consumer at 3 a.m., and it gives nothing Redis does not already give here. | Rejected — unfamiliarity is the cost driver at 36 h. |
| **Redis Streams** ✅ | One `redis:7-alpine` container, ~30 MB RSS. `XADD MAXLEN ~ N` gives drop-oldest backpressure *for free*. `XREADGROUP` + `XACK` survives API restarts — the worker never notices a reload. Redis is **already needed** for plate-vote caches, cross-camera last-seen keys, rate limits and heartbeats, so it is not extra infrastructure. `redis-py` is 5 lines to learn. | **Chosen.** |

Escape hatch, so this is not a single point of stage failure: the publisher is an interface with two implementations selected by `EDGE_TRANSPORT`.

```python
# apps/edge/edge/publisher.py
from typing import Protocol
import orjson, redis, httpx

class Publisher(Protocol):
    def emit(self, stream: str, payload: dict) -> None: ...

class RedisPublisher:
    def __init__(self, url: str, maxlen: int = 20_000):
        self.r = redis.Redis.from_url(url, socket_timeout=0.25)
        self.maxlen = maxlen
    def emit(self, stream: str, payload: dict) -> None:
        # approximate trimming (~) is O(1); exact trimming is not. Drop-oldest = correct
        # policy for live surveillance: a 40s-stale plate read has no operational value.
        self.r.xadd(stream, {"v": orjson.dumps(payload)}, maxlen=self.maxlen, approximate=True)

class HttpPublisher:                      # EDGE_TRANSPORT=http
    def __init__(self, base: str, token: str, batch: int = 25):
        self.c = httpx.Client(base_url=base, timeout=2.0,
                              headers={"Authorization": f"Bearer {token}"})
        self.buf: list[dict] = []; self.batch = batch
    def emit(self, stream: str, payload: dict) -> None:
        self.buf.append({"stream": stream, **payload})
        if len(self.buf) >= self.batch:
            try: self.c.post("/v1/ingest/batch", json={"items": self.buf})
            except httpx.HTTPError: pass          # never block the frame loop
            finally: self.buf.clear()
```

Stream names and payload envelope are **frozen contracts**: `anpr.reads`, `anpr.tracks`, `anpr.anomaly`, `anpr.events.dlq`. Every message is a single Redis field `v` holding UTF-8 JSON with mandatory keys `schema_version`, `camera_id`, `ts_utc` (RFC3339, µs), `worker_id`.

### 4. Process topology on this machine

| # | Process | Command | GPU | RAM (est.) | Supervision |
|---|---|---|---|---|---|
| 1 | MediaMTX | `docker compose up mediamtx` | no | 60 MB | compose `restart: unless-stopped` |
| 2 | Redis | `docker compose up redis` | no | 30 MB | compose `restart: unless-stopped` |
| 3–8 | `ffmpeg` replay ×N cameras | `scripts/replay.ps1` | no | ~45 MB each | PowerShell restart loop |
| 9 | **Edge ANPR worker** | `python -m edge --config infra/cameras.yaml` | **primary** | 1.2–1.6 GB | `scripts/supervise.ps1` |
| 10 | Edge VAD worker *(stretch)* | `python -m edge --mode vad --cameras cam-02,cam-05` | secondary | 1.0–1.3 GB | `scripts/supervise.ps1`, killable |
| 11 | API + rules | `uvicorn api.main:app --reload --port 8000` | no | 300 MB | terminal |
| 12 | Web | `pnpm --filter web dev` | no | 800–1000 MB | terminal |

Total ≈ 4.5 GB, leaving ~7 GB for Windows + Chrome + VS Code on a 16 GB box. The rules engine is an **asyncio task inside the API process**, not a 13th process — it needs the same Supabase client and the same in-memory hot state, and a 36-hour team should not debug two deployment units.

**How many cameras fit in process #9.** Measured planning budget at ~60 % GPU duty cycle (leave headroom or frame latency oscillates):

| Stage | Model / input | EP | est. ms/call |
|---|---|---|---|
| Hot-tier detect | YOLOv8s, 768×768, FP16 | DirectML | 25–35 |
| Cold-tier detect | YOLOv8n, 640×640, FP16 | DirectML | 12–18 |
| Batched detect (b=4, 640) | YOLOv8n | DirectML | 32–45 total (≈2.6× of b=1, so ~40 % saving/frame) |
| Plate rectify + rec | PP-OCRv4 rec, 48×192 | **CPU/OpenVINO** | 4–8 per crop |
| Anomaly clip | VideoMAE-S, 16×224² | DirectML | 250–500 |

> **Verify:** these are planning estimates for gfx1034, not measurements. Run `python -m edge.bench --all` on day zero and replace this table with the real numbers before anyone tunes thresholds.

With ~1000 ms of GPU per second and a 600 ms budget: **4 "hot" cameras at 5 analysed fps** (20 calls/s × 28 ms ≈ 560 ms) is the safe configuration. The demo runs **6 cameras: 4 hot @ 5 fps + 2 cold @ 2 fps** using the n-model tier. OCR is deliberately pushed to a **CPU thread pool** — PP-OCRv4-rec is tiny, the CPU is idle, and this keeps ~15 % of the GPU free. VAD lives in its own process precisely so a VRAM exhaustion kills only anomaly detection and the ANPR demo survives.

Supervision on Windows (no systemd):

```powershell
# scripts/supervise.ps1  —  .\scripts\supervise.ps1 -Name edge -Cmd "python" -CmdArgs "-m","edge"
param([string]$Name, [string]$Cmd, [string[]]$CmdArgs, [int]$MaxRestarts = 50)
$log = Join-Path $PSScriptRoot "..\logs\$Name.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
for ($i = 0; $i -lt $MaxRestarts; $i++) {
  "[$(Get-Date -f o)] START $Name attempt=$i" | Tee-Object -FilePath $log -Append
  & $Cmd @CmdArgs 2>&1 | Tee-Object -FilePath $log -Append
  $code = $LASTEXITCODE
  "[$(Get-Date -f o)] EXIT $Name code=$code" | Tee-Object -FilePath $log -Append
  if ($code -eq 0) { break }          # clean shutdown = do not resurrect
  Start-Sleep -Seconds ([Math]::Min(30, [Math]::Pow(2, [Math]::Min($i, 5))))  # capped backoff
}
```

Every long-lived process writes `SET hb:<name> <json> EX 15` every 5 s. `GET /v1/health/processes` reads all `hb:*` and the dashboard renders a hairline status strip — green / amber (stale >5 s) / red (missing). This is also the honest answer when a judge asks "what happens if a camera dies?".

### 5. Backpressure and frame-drop policy

Inference **will** be slower than 25 fps × 6 streams. The policy is explicit and instrumented, not accidental.

1. **Latest-frame-wins ring buffer.** Each `CaptureThread` owns a depth-1 slot. On a full slot the *old* frame is discarded, not the new one. For live surveillance, freshness beats completeness; a queue that grows is a queue that turns a 200 ms alert into a 40 s alert.
2. **Decode ≠ analyse.** Decoders run at the stream's native fps; the scheduler samples at `analyse_fps` per camera. Decoding 720p H.264 costs ~5–8 % CPU per stream, which is cheap and keeps timestamps honest.
3. **Fair-share + adaptive fps controller.** The scheduler round-robins cameras so one busy junction cannot starve the others, and adjusts globally:

```python
# apps/edge/edge/scheduler.py (control law)
BUDGET_MS = 1000 / TARGET_LOOP_HZ
if p95_loop_ms > 1.30 * BUDGET_MS and now - last_change > 5:
    self.scale = max(0.4, self.scale - 0.15); last_change = now      # shed load fast
elif p95_loop_ms < 0.70 * BUDGET_MS and now - last_change > 15:
    self.scale = min(1.0, self.scale + 0.10); last_change = now      # recover slowly
effective_fps = max(2.0, cam.analyse_fps * self.scale)               # never below 2 fps
```
   Fast down / slow up prevents oscillation. The 2 fps floor exists because below it ByteTrack loses identity across a 50 km/h vehicle.
4. **Transport backpressure.** `XADD ... MAXLEN ~ 20000` drops oldest. If `XLEN(anpr.reads) > REDIS_STREAM_ALARM` (15 000) the worker raises a `backpressure` health flag and the API surfaces it — visible degradation beats silent loss.
5. **Evidence never blocks.** Crop/clip uploads go to a `queue.Queue(maxsize=200)`; on full, drop the *image* but still emit the read with `evidence_status="dropped"`. Losing a JPEG must never cost us a plate.
6. **Counters are first-class.** `frames_decoded`, `frames_analysed`, `frames_dropped_stale`, `ocr_queue_depth`, `evidence_dropped`, `p95_loop_ms` are on `GET /v1/health/processes` and rendered as tabular numerals in the dashboard footer.

### 6. Monorepo layout

```text
SIH-HACKATHON/
├─ apps/
│  ├─ edge/                        # Python GPU worker. NEVER dockerised (DirectML).
│  │  ├─ edge/
│  │  │  ├─ __main__.py            # CLI: --mode anpr|vad, --config, --cameras
│  │  │  ├─ config.py              # pydantic-settings: env + cameras.yaml loader
│  │  │  ├─ capture.py             # RTSP CaptureThread, depth-1 ring buffer, reconnect
│  │  │  ├─ scheduler.py           # fair-share sampling, batching, adaptive-fps controller
│  │  │  ├─ session.py             # ORT session factory: DmlExecutionProvider -> CPU fallback
│  │  │  ├─ detect.py              # YOLOv8 ONNX pre/post-process, letterbox, NMS
│  │  │  ├─ track.py               # ByteTrack wrapper, per-camera track id namespacing
│  │  │  ├─ ocr.py                 # plate crop rectify + PaddleOCR rec on CPU threadpool
│  │  │  ├─ plate_norm.py          # Bharat/RTO regex normaliser, O/0 I/1 confusion fixes
│  │  │  ├─ vote.py                # multi-frame plate voting per track id
│  │  │  ├─ geometry.py            # line crossing, ROI test, px->metre speed estimate
│  │  │  ├─ vad.py                 # VideoMAE clip buffer + scoring (separate process)
│  │  │  ├─ evidence.py            # crop/clip encode + Supabase Storage upload, droppable
│  │  │  ├─ publisher.py           # RedisPublisher | HttpPublisher (EDGE_TRANSPORT)
│  │  │  ├─ health.py              # hb:<name> heartbeat writer + counters
│  │  │  └─ bench.py               # `python -m edge.bench --all` day-zero ms/call table
│  │  ├─ tests/                    # pytest: plate_norm, geometry, scheduler control law
│  │  └─ requirements.txt
│  ├─ api/                         # FastAPI: ingest consumer, rules engine, REST, SSE
│  │  ├─ api/
│  │  │  ├─ main.py                # app factory, lifespan starts consumer + rules tasks
│  │  │  ├─ settings.py            # pydantic-settings, reads .env
│  │  │  ├─ deps.py                # supabase client, redis pool, auth dependency
│  │  │  ├─ ingest/consumer.py     # XREADGROUP g:ingest -> validate -> upsert -> XACK
│  │  │  ├─ rules/                 # clone.py speed.py loiter.py hotlist.py runner.py
│  │  │  ├─ routers/               # cameras.py plates.py tracks.py alerts.py health.py stream.py
│  │  │  ├─ models/                # pydantic request/response models = OpenAPI source of truth
│  │  │  └─ services/              # supabase_repo.py, evidence_urls.py, notify.py
│  │  ├─ tests/
│  │  └─ requirements.txt
│  └─ web/                         # Next.js 15 App Router + TS + Tailwind
│     ├─ app/(dashboard)/          # live/ map/ alerts/ vehicles/[plate]/ cameras/ admin/
│     ├─ components/               # DataTable, AlertRow, MapCanvas, StatusStrip, Timeline
│     ├─ lib/                      # supabase-browser.ts, api.ts, realtime.ts, sse.ts
│     ├─ styles/tokens.css         # the ONE place colours/type scale live (Swiss grid tokens)
│     └─ package.json
├─ packages/
│  └─ shared/                      # cross-app TS contracts
│     ├─ src/api.d.ts              # GENERATED from FastAPI /openapi.json — do not hand-edit
│     ├─ src/domain.ts             # AlertSeverity, RuleCode, CameraTier enums (hand-written)
│     └─ package.json
├─ infra/
│  ├─ docker-compose.yml           # mediamtx + redis (+ optional local postgis fallback)
│  ├─ mediamtx.yml                 # RTSP server config: TCP only, no auth on localhost
│  ├─ cameras.yaml                 # THE camera registry — see §8
│  └─ supabase/
│     ├─ migrations/               # 0001_init.sql, 0002_postgis.sql, 0003_rls.sql ...
│     └─ seed.sql                  # demo cameras, hotlist plates, one seeded FIR
├─ models/
│  ├─ onnx/                        # yolov8s_vehicle_plate.onnx, yolov8n_*.onnx, ppocrv4_rec.onnx
│  ├─ labels/                      # coco_subset.txt, plate_charset.txt
│  └─ MODELS.md                    # sha256, source, export command, measured ms/call
├─ datasets/
│  ├─ demo_clips/                  # the 6 stage videos (git-lfs or gitignored + gdrive link)
│  ├─ plates/                      # annotated crops for OCR eval
│  └─ README.md                    # provenance + licence of every dataset used
├─ scripts/
│  ├─ bootstrap.ps1                # day-zero one-shot installer
│  ├─ dev_up.ps1                   # opens Windows Terminal panes for all 4 dev processes
│  ├─ supervise.ps1                # restart-with-backoff wrapper (see §4)
│  ├─ replay.ps1                   # ffmpeg loop publisher, one per cameras.yaml entry
│  ├─ export_onnx.py               # run on Colab: .pt -> .onnx (opset 17, fp16, dynamic batch)
│  ├─ gen_ts_types.ps1             # openapi-typescript -> packages/shared/src/api.d.ts
│  └─ smoke_e2e.py                 # publish 1 synthetic read -> assert row + alert appear
├─ docs/                           # architecture.md, schema.md, models.md, rules.md, dpdp.md, demo_script.md
├─ logs/                           # gitignored; supervise.ps1 writes here
├─ .env.example                    # §7
├─ pnpm-workspace.yaml
└─ README.md
```

**Python environments.** One root `.venv` shared by `apps/edge` and `apps/api` — two venvs cost more time than they save. One hard rule: install **`onnxruntime-directml` only**. `pip install onnxruntime` alongside it installs a conflicting `onnxruntime` package and DirectML silently disappears; if `ort.get_available_providers()` does not list `DmlExecutionProvider`, that is the cause.

**Type contract between API and web.** FastAPI's pydantic models are the single source of truth; `scripts/gen_ts_types.ps1` regenerates `packages/shared/src/api.d.ts` from `/openapi.json`. Hand-written duplicate interfaces in `apps/web` are banned.

### 7. Local dependencies: `infra/docker-compose.yml`

```yaml
# infra/docker-compose.yml — run from repo root: docker compose -f infra/docker-compose.yml up -d
name: sih-anpr

services:
  mediamtx:
    image: bluenviron/mediamtx:latest
    container_name: sih-mediamtx
    restart: unless-stopped
    ports:
      - "8554:8554"     # RTSP (TCP interleaved — we never use UDP, see note)
      - "1935:1935"     # RTMP ingest from ffmpeg
      - "8888:8888"     # HLS  (useful for a browser <video> preview tile)
      - "9997:9997"     # control API: GET /v3/paths/list to prove a camera is publishing
    volumes:
      - ./mediamtx.yml:/mediamtx.yml:ro
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:9997/v3/paths/list"]
      interval: 10s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: sih-redis
    restart: unless-stopped
    command: >
      redis-server
      --appendonly no
      --save ""
      --maxmemory 512mb
      --maxmemory-policy noeviction
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  # --- FALLBACK ONLY: `docker compose --profile localdb up -d` if venue Wi-Fi dies ---
  postgres:
    image: postgis/postgis:15-3.4
    container_name: sih-postgres
    profiles: ["localdb"]
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: sihlocal
      POSTGRES_DB: anpr
    ports:
      - "54322:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./supabase/migrations:/docker-entrypoint-initdb.d:ro

volumes:
  pgdata:
```

`infra/mediamtx.yml`:

```yaml
logLevel: info
rtspTransports: [tcp]        # UDP RTP through Docker Desktop NAT on Windows is unreliable
rtmp: yes
hls: yes
hlsVariant: lowLatency
api: yes
apiAddress: :9997
paths:
  all_others:                # any path name is accepted; ffmpeg creates cam-01..cam-06
```

**Supabase: hosted, not local.** `supabase start` spins up ~10 containers (postgres, gotrue, postgrest, realtime, storage, imgproxy, kong, studio, analytics, vector) costing 3.5–5 GB RAM. On a 16 GB machine that must also run a 1.5 GB GPU worker, Next.js dev and Chrome, that is the difference between a smooth demo and swapping. Use the **free hosted tier** (0 local RAM, real Realtime, real Storage, real RLS). Mitigate the network risk with three things: (a) keep `--profile localdb` above plus a nightly `supabase db dump > infra/supabase/snapshot.sql`; (b) the dashboard's SSE fallback (`GET /v1/stream/alerts`) so alerts still flow if the Realtime WebSocket is blocked by venue Wi-Fi; (c) a phone hotspot as the primary uplink on demo day.

**What must NOT be dockerised on this machine:**

| Component | Why native |
|---|---|
| `apps/edge` | Docker Desktop runs Linux containers under WSL2. `onnxruntime-directml` is a **Windows** wheel; the Linux equivalent would be ROCm, which does not support gfx1034 (RX 6500M). Containerising the worker silently demotes it to CPU and the demo dies at 3 fps. |
| `apps/api` | Needs `--reload` and `localhost:6379`. A container adds a rebuild cycle for zero benefit. |
| `apps/web` | Next.js file-watching across the Windows↔WSL2 filesystem boundary is 10–50× slower; HMR becomes unusable. |
| `ffmpeg` replay | Native `ffmpeg.exe` is already installed and reads `datasets/` without a bind mount. |

### 8. Camera registry: `infra/cameras.yaml`

Single source of truth, read by the edge worker at startup and pushed into the `cameras` table by `scripts/bootstrap.ps1`. Pixel coordinates are **normalised 0–1** so they survive a resolution change.

```yaml
version: 1
defaults:
  analyse_fps: 5
  tier: hot                    # hot -> yolov8s@768 ; cold -> yolov8n@640
  rtsp_transport: tcp
  reconnect_backoff_s: [1, 2, 5, 10, 20]

cameras:
  - id: cam-01                                  # STABLE KEY. Used as PK everywhere.
    name: "Sion Circle — North Approach"
    rtsp_url: "rtsp://127.0.0.1:8554/cam-01"
    source_file: "datasets/demo_clips/sion_north.mp4"
    replay_offset_s: 0                          # deterministic stage timing for cross-camera demos
    lat: 19.043210
    lon: 72.862140
    bearing_deg: 172                            # compass direction the lens faces; drives map cones
    tier: hot
    analyse_fps: 5
    zone_tags: ["ward-F-north", "arterial", "signalised", "high-priority"]
    roi: [[0.02, 0.35], [0.98, 0.35], [0.98, 0.99], [0.02, 0.99]]   # ignore sky/hoardings
    lines:
      - id: L1
        name: "stop-bar-southbound"
        p1: [0.10, 0.62]
        p2: [0.92, 0.66]
        positive_dir: "toward_camera"           # sign convention for crossing events
        purpose: ["count", "redlight"]
    speed_calibration:
      method: "two_line"                        # crossing L1 then L2, real-world separation
      second_line_id: L2
      separation_m: 18.5                        # measured off a satellite image; +/-1 m => +/-5% speed
      max_plausible_kmh: 140                    # anything above => discard, not an alert

  - id: cam-02
    name: "Dharavi T-Junction — East"
    rtsp_url: "rtsp://127.0.0.1:8554/cam-02"
    source_file: "datasets/demo_clips/dharavi_east.mp4"
    replay_offset_s: 12                         # cam-01 -> cam-02 in 12 s over 210 m = 63 km/h
    lat: 19.040880
    lon: 72.855010
    bearing_deg: 88
    tier: cold
    analyse_fps: 2
    zone_tags: ["ward-G-north", "residential"]
    roi: [[0.00, 0.30], [1.00, 0.30], [1.00, 1.00], [0.00, 1.00]]
    lines: []

links:                                          # inter-camera road distances for speed/clone rules
  - from: cam-01
    to: cam-02
    road_distance_m: 210
    min_travel_s: 6                             # 210 m at 126 km/h; anything faster => cloned plate
```

`min_travel_s` is the cloned-plate primitive: if the same normalised plate appears on both cameras separated by less than `min_travel_s`, one of them is a clone. It is derived (`road_distance_m / (max_plausible_kmh / 3.6)`) rather than guessed, and it is stated in config so the rules section can consume it without re-deriving.

### 9. `.env.example`

```dotenv
# ============================ [1] GLOBAL ============================
APP_ENV=dev                      # dev | demo | prod
TZ=Asia/Kolkata
LOG_LEVEL=INFO                   # DEBUG on the edge worker will halve your fps
SCHEMA_VERSION=1                 # bumped when the stream envelope changes

# ============================ [2] SUPABASE =========================
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOi...                  # browser-safe, RLS-restricted
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...          # SERVER ONLY. never in apps/web, never in git
SUPABASE_DB_URL=postgresql://postgres:PASS@db.xxxx.supabase.co:5432/postgres
SUPABASE_STORAGE_BUCKET_EVIDENCE=evidence        # plate crops + track thumbnails
SUPABASE_STORAGE_BUCKET_CLIPS=clips              # 10s anomaly clips
SUPABASE_SIGNED_URL_TTL_S=3600

# ============================ [3] TRANSPORT ========================
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_STREAM_READS=anpr.reads
REDIS_STREAM_TRACKS=anpr.tracks
REDIS_STREAM_ANOMALY=anpr.anomaly
REDIS_STREAM_DLQ=anpr.events.dlq
REDIS_CONSUMER_GROUP=g:ingest
REDIS_STREAM_MAXLEN=20000
REDIS_STREAM_ALARM=15000                         # backlog above this raises a health flag
REDIS_HEARTBEAT_TTL_S=15

# ============================ [4] API ==============================
API_HOST=127.0.0.1
API_PORT=8000
API_CORS_ORIGINS=http://localhost:3000
API_INGEST_TOKEN=change-me-32-chars-min          # bearer for EDGE_TRANSPORT=http fallback
API_JWT_SECRET=change-me-32-chars-min            # operator session signing
API_INGEST_BATCH_SIZE=200                        # XREADGROUP COUNT
API_INGEST_BLOCK_MS=2000
API_RULES_TICK_MS=1000                           # rules engine evaluation cadence

# ============================ [5] EDGE WORKER ======================
EDGE_WORKER_ID=edge-01
EDGE_TRANSPORT=redis                             # redis | http
EDGE_CAMERAS_FILE=infra/cameras.yaml
EDGE_ORT_PROVIDER=DmlExecutionProvider           # fallback chain: DML -> CPUExecutionProvider
EDGE_ORT_DEVICE_ID=0
EDGE_BATCH_SIZE=4                                # DirectML amortises batch>1; 4 fits in 4GB VRAM
EDGE_TARGET_LOOP_HZ=25
EDGE_MIN_ANALYSE_FPS=2                           # floor: below this ByteTrack loses identity
EDGE_JPEG_QUALITY=85
EDGE_EVIDENCE_QUEUE_MAX=200
EDGE_ENABLE_VAD=false                            # stretch goal; own process
MODEL_VEHICLE_PLATE_HOT=models/onnx/yolov8s_vehicle_plate_768_fp16.onnx
MODEL_VEHICLE_PLATE_COLD=models/onnx/yolov8n_vehicle_plate_640_fp16.onnx
MODEL_OCR_REC=models/onnx/ppocrv4_rec_en_number.onnx
MODEL_OCR_CHARSET=models/labels/plate_charset.txt
MODEL_VAD=models/onnx/videomae_small_rtfm.onnx
DET_CONF_VEHICLE=0.35
DET_CONF_PLATE=0.40
DET_IOU_NMS=0.45
OCR_MIN_CHAR_CONF=0.60
OCR_MIN_VOTES=3                                  # a plate must agree on >=3 frames before publish

# ============================ [6] MEDIAMTX / REPLAY ================
MEDIAMTX_RTSP_BASE=rtsp://127.0.0.1:8554
MEDIAMTX_API=http://127.0.0.1:9997
RTSP_TRANSPORT=tcp                               # UDP through Docker NAT on Windows drops frames
REPLAY_LOOP=true

# ============================ [7] WEB (NEXT_PUBLIC_* IS PUBLIC) ====
NEXT_PUBLIC_SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOi...
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_MAPBOX_TOKEN=pk.eyJ1Ijoi...          # public scope token, URL-restricted
NEXT_PUBLIC_MAP_CENTER_LAT=19.0760
NEXT_PUBLIC_MAP_CENTER_LON=72.8777
NEXT_PUBLIC_MAP_ZOOM=12.5
NEXT_PUBLIC_REALTIME_MODE=supabase               # supabase | sse  (venue-Wi-Fi fallback)

# ============================ [8] RULES THRESHOLDS =================
RULE_SPEED_LIMIT_KMH=60
RULE_SPEED_TOLERANCE_KMH=8                       # covers +/-5% two-line calibration error
RULE_CLONE_MIN_TRAVEL_MARGIN=0.8                 # fire only below 0.8 * link.min_travel_s
RULE_LOITER_DWELL_S=180
RULE_LOITER_MIN_REVISITS=3
RULE_HOTLIST_MIN_PLATE_CONF=0.75
RULE_ALERT_DEDUP_WINDOW_S=300                    # same plate+rule+camera collapses into one alert

# ============================ [9] PRIVACY / DPDP 2023 ==============
DPDP_RETENTION_DAYS_RAW_EVIDENCE=30              # purpose limitation: crops age out
DPDP_RETENTION_DAYS_PLATE_READS=180
DPDP_AUDIT_LOG_ENABLED=true                      # every operator plate search is logged
DPDP_FACE_BLUR_ENABLED=true                      # blur pedestrian faces in stored crops
```

### 10. Day-zero bootstrap, in order

```powershell
# 0. Prerequisites already verified on this machine: Python 3.12.10, Node 24.16,
#    Docker 29.5.2, ffmpeg 8.1.2. pnpm is NOT installed -> step 3.
git init "E:\D_Drive\Alll Websites\SIH HACKATHON"; cd "E:\D_Drive\Alll Websites\SIH HACKATHON"

# 1. Python env (ONE venv for edge + api)
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip wheel
pip install -r apps/edge/requirements.txt -r apps/api/requirements.txt

# 2. PROVE DirectML is live BEFORE writing any model code. If this prints CPU only, stop and fix.
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
#    expect: ['DmlExecutionProvider', 'CPUExecutionProvider']

# 3. Node workspace
corepack enable; corepack prepare pnpm@latest --activate
pnpm install

# 4. Config
Copy-Item .env.example .env      # then fill SUPABASE_*, NEXT_PUBLIC_MAPBOX_TOKEN, tokens

# 5. Local infra
docker compose -f infra/docker-compose.yml up -d
curl http://127.0.0.1:9997/v3/paths/list      # MediaMTX alive
docker exec sih-redis redis-cli ping          # PONG

# 6. Database: apply migrations + seed cameras from cameras.yaml
supabase link --project-ref <ref>
supabase db push
python scripts/seed_cameras.py --config infra/cameras.yaml

# 7. Models (exported on Colab by the CV lead, committed as ONNX under models/onnx)
python -m edge.bench --all        # writes docs/bench.md — REPLACE the §4 estimates with these

# 8. Start the replay city (one ffmpeg per camera, restarted on exit)
.\scripts\replay.ps1              # reads infra/cameras.yaml, honours replay_offset_s

# 9. Start the four dev processes (opens Windows Terminal panes)
.\scripts\dev_up.ps1
#    pane 1: .\scripts\supervise.ps1 -Name edge -Cmd python -CmdArgs "-m","edge"
#    pane 2: uvicorn api.main:app --reload --port 8000 --app-dir apps/api
#    pane 3: pnpm --filter web dev
#    pane 4: docker compose -f infra/docker-compose.yml logs -f

# 10. End-to-end smoke test — must pass before anyone writes a feature
python scripts/smoke_e2e.py
#     publishes one synthetic read to anpr.reads, asserts a plate_reads row appears in
#     Supabase within 3s and a hotlist alert lands on the dashboard Realtime channel.
```

Definition of done for day zero: step 2 prints `DmlExecutionProvider`, and step 10 exits 0. Nothing else is started until both are true.

### 11. MVP / stretch boundary for this section

| Item | MVP | Stretch |
|---|---|---|
| Cameras live | 4 (2 for the cloned-plate pair) | 6–8 with tiering |
| Transport | Redis Streams | HTTP fallback path actually tested |
| VAD process | disabled (`EDGE_ENABLE_VAD=false`) | enabled on 2 cameras |
| Supervision | `supervise.ps1` | NSSM Windows services |
| Realtime | Supabase Realtime | SSE fallback wired and demoed |
| DB | hosted Supabase | `--profile localdb` offline path rehearsed |

---

## Appendix — Interface Contracts Declared by This Section

- `monorepo root: E:/D_Drive/Alll Websites/SIH HACKATHON, workspaces apps/edge, apps/api, apps/web, packages/shared, infra, models, datasets, scripts, docs, logs`
- `redis stream: anpr.reads (field 'v' = UTF-8 JSON envelope)`
- `redis stream: anpr.tracks (field 'v' = UTF-8 JSON envelope)`
- `redis stream: anpr.anomaly (field 'v' = UTF-8 JSON envelope)`
- `redis stream: anpr.events.dlq (poison messages from the ingest consumer)`
- `redis consumer group: g:ingest (consumed by apps/api ingest consumer via XREADGROUP/XACK)`
- `redis stream trimming: XADD MAXLEN ~ 20000, approximate=True, drop-oldest`
- `redis key: hb:<process_name> -> JSON heartbeat, SET ... EX 15 (processes: edge-01, edge-vad-01, api, rules)`
- `stream envelope mandatory keys: schema_version:int, camera_id:str, ts_utc:str RFC3339 microseconds, worker_id:str`
- `endpoint: POST /v1/ingest/batch  body {items: [{stream, ...envelope}]}  Bearer API_INGEST_TOKEN (HTTP transport fallback only)`
- `endpoint: GET /v1/health/processes -> heartbeats + counters {frames_decoded, frames_analysed, frames_dropped_stale, ocr_queue_depth, evidence_dropped, p95_loop_ms, backpressure:bool}`
- `endpoint: GET /v1/stream/alerts (Server-Sent Events fallback when Supabase Realtime is unavailable)`
- `REST namespace reserved by this section: /v1/cameras, /v1/plates, /v1/tracks, /v1/alerts, /v1/health, /v1/stream`
- `OpenAPI contract: FastAPI pydantic models are source of truth; generated to packages/shared/src/api.d.ts via scripts/gen_ts_types.ps1 (never hand-edited)`
- `config file: infra/cameras.yaml with keys version, defaults, cameras[], links[]`
- `cameras[] keys: id, name, rtsp_url, source_file, replay_offset_s, lat, lon, bearing_deg, tier(hot|cold), analyse_fps, zone_tags[], roi[[x,y]] normalised 0-1, lines[{id,name,p1,p2,positive_dir,purpose[]}], speed_calibration{method,second_line_id,separation_m,max_plausible_kmh}`
- `links[] keys: from, to, road_distance_m, min_travel_s (min_travel_s = road_distance_m / (max_plausible_kmh/3.6); primitive for the cloned-plate rule)`
- `camera_id format: 'cam-NN' (stable primary key used in DB, streams, storage paths and UI)`
- `supabase tables this section assumes exist (types owned by the schema section): cameras, vehicle_tracks, plate_reads, detections, alerts, evidence`
- `plate_reads.evidence_status enum must include 'dropped' (evidence upload shed under backpressure)`
- `supabase storage buckets: evidence (plate crops/thumbnails), clips (10s anomaly clips)`
- `config file: infra/docker-compose.yml, compose project name 'sih-anpr', services mediamtx, redis, postgres(profile 'localdb')`
- `config file: infra/mediamtx.yml (rtspTransports: [tcp], api on :9997)`
- `ports: 8554 RTSP, 1935 RTMP, 8888 HLS, 9997 MediaMTX API, 6379 Redis, 8000 FastAPI, 3000 Next.js, 54322 fallback Postgres`
- `rtsp url pattern: rtsp://127.0.0.1:8554/<camera_id> (TCP transport mandatory)`
- `env: APP_ENV, TZ, LOG_LEVEL, SCHEMA_VERSION`
- `env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_DB_URL, SUPABASE_STORAGE_BUCKET_EVIDENCE, SUPABASE_STORAGE_BUCKET_CLIPS, SUPABASE_SIGNED_URL_TTL_S`
- `env: REDIS_URL, REDIS_STREAM_READS, REDIS_STREAM_TRACKS, REDIS_STREAM_ANOMALY, REDIS_STREAM_DLQ, REDIS_CONSUMER_GROUP, REDIS_STREAM_MAXLEN, REDIS_STREAM_ALARM, REDIS_HEARTBEAT_TTL_S`
- `env: API_HOST, API_PORT, API_CORS_ORIGINS, API_INGEST_TOKEN, API_JWT_SECRET, API_INGEST_BATCH_SIZE, API_INGEST_BLOCK_MS, API_RULES_TICK_MS`
- `env: EDGE_WORKER_ID, EDGE_TRANSPORT(redis|http), EDGE_CAMERAS_FILE, EDGE_ORT_PROVIDER, EDGE_ORT_DEVICE_ID, EDGE_BATCH_SIZE, EDGE_TARGET_LOOP_HZ, EDGE_MIN_ANALYSE_FPS, EDGE_JPEG_QUALITY, EDGE_EVIDENCE_QUEUE_MAX, EDGE_ENABLE_VAD`
- `env: MODEL_VEHICLE_PLATE_HOT, MODEL_VEHICLE_PLATE_COLD, MODEL_OCR_REC, MODEL_OCR_CHARSET, MODEL_VAD, DET_CONF_VEHICLE, DET_CONF_PLATE, DET_IOU_NMS, OCR_MIN_CHAR_CONF, OCR_MIN_VOTES`
- `env: MEDIAMTX_RTSP_BASE, MEDIAMTX_API, RTSP_TRANSPORT, REPLAY_LOOP`
- `env: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, NEXT_PUBLIC_API_BASE_URL, NEXT_PUBLIC_MAPBOX_TOKEN, NEXT_PUBLIC_MAP_CENTER_LAT, NEXT_PUBLIC_MAP_CENTER_LON, NEXT_PUBLIC_MAP_ZOOM, NEXT_PUBLIC_REALTIME_MODE(supabase|sse)`
- `env: RULE_SPEED_LIMIT_KMH, RULE_SPEED_TOLERANCE_KMH, RULE_CLONE_MIN_TRAVEL_MARGIN, RULE_LOITER_DWELL_S, RULE_LOITER_MIN_REVISITS, RULE_HOTLIST_MIN_PLATE_CONF, RULE_ALERT_DEDUP_WINDOW_S`
- `env: DPDP_RETENTION_DAYS_RAW_EVIDENCE, DPDP_RETENTION_DAYS_PLATE_READS, DPDP_AUDIT_LOG_ENABLED, DPDP_FACE_BLUR_ENABLED`
- `model files: models/onnx/yolov8s_vehicle_plate_768_fp16.onnx, models/onnx/yolov8n_vehicle_plate_640_fp16.onnx, models/onnx/ppocrv4_rec_en_number.onnx, models/onnx/videomae_small_rtfm.onnx, models/labels/plate_charset.txt`
- `scripts: scripts/bootstrap.ps1, scripts/dev_up.ps1, scripts/supervise.ps1, scripts/replay.ps1, scripts/seed_cameras.py, scripts/export_onnx.py, scripts/gen_ts_types.ps1, scripts/smoke_e2e.py`
- `edge module entrypoint: python -m edge --mode anpr|vad --config infra/cameras.yaml [--cameras cam-02,cam-05]`
- `bench entrypoint: python -m edge.bench --all -> writes docs/bench.md`
- `api entrypoint: uvicorn api.main:app --app-dir apps/api --port 8000`
- `python dependency rule: install onnxruntime-directml ONLY; installing onnxruntime alongside it disables DmlExecutionProvider`
- `supabase migrations directory: infra/supabase/migrations/ (0001_init.sql onward), seed at infra/supabase/seed.sql`
- `design tokens single source: apps/web/styles/tokens.css`

## Appendix — MVP vs Stretch

- MVP: Redis Streams transport with anpr.reads / anpr.tracks streams and the g:ingest consumer group, XACK on success, DLQ on validation failure.
- MVP: exactly one native Windows edge process holding 4 cameras at 5 analysed fps (2 of them a calibrated pair for the cloned-plate demo).
- MVP: infra/docker-compose.yml running MediaMTX + Redis only; hosted Supabase free tier for DB/Storage/Realtime.
- MVP: infra/cameras.yaml driving both the edge worker and the seeded cameras table, including links[] with road_distance_m and min_travel_s.
- MVP: latest-frame-wins depth-1 ring buffer per camera plus MAXLEN ~ 20000 stream trimming.
- MVP: heartbeat keys hb:* with 15s TTL and GET /v1/health/processes returning drop counters, rendered as a status strip in the UI.
- MVP: scripts/supervise.ps1 restart-with-backoff wrapper and scripts/replay.ps1 ffmpeg loop publisher.
- MVP: scripts/smoke_e2e.py end-to-end assertion passing before any feature work starts.
- MVP: .env.example complete and .env gitignored; SUPABASE_SERVICE_ROLE_KEY never referenced from apps/web.
- MVP: packages/shared/src/api.d.ts generated from FastAPI /openapi.json, no hand-written duplicate types in apps/web.
- STRETCH: apps/edge --mode vad as a second killable GPU process running VideoMAE on 2 cameras (EDGE_ENABLE_VAD=true).
- STRETCH: cold-tier YOLOv8n cameras to push the demo from 4 to 6-8 cameras.
- STRETCH: EDGE_TRANSPORT=http fallback path actually exercised, not just written.
- STRETCH: GET /v1/stream/alerts SSE fallback wired into the dashboard and demoed with the Realtime WebSocket blocked.
- STRETCH: docker compose --profile localdb offline Postgres+PostGIS path rehearsed with a restored snapshot.
- STRETCH: NSSM-registered Windows services instead of PowerShell supervision.
- STRETCH: INT8 quantised detector to raise per-camera analyse_fps.

## Appendix — Risks

- DirectML EP not actually loading (a stray `pip install onnxruntime` shadows onnxruntime-directml) silently demotes inference to CPU at ~3 fps. Mitigation: bootstrap step 2 asserts 'DmlExecutionProvider' in ort.get_available_providers() and the edge worker refuses to start in APP_ENV=demo if it is absent.
- 4 GB VRAM exhaustion when the ANPR and VAD sessions coexist; DirectML arena allocators over-reserve. Mitigation: VAD runs in a separate process that can be killed without touching ANPR, and EDGE_ENABLE_VAD defaults to false.
- Someone dockerises the edge worker 'for consistency' and loses the GPU (WSL2 + ROCm does not support gfx1034). Mitigation: explicit table in this section, plus no Dockerfile is committed under apps/edge.
- RTSP over UDP through Docker Desktop NAT on Windows drops packets and produces green-smear frames that wreck OCR. Mitigation: rtspTransports: [tcp] in mediamtx.yml and RTSP_TRANSPORT=tcp forced in every consumer.
- Venue Wi-Fi blocks the Supabase Realtime WebSocket or is simply down, killing the live dashboard on stage. Mitigation: NEXT_PUBLIC_REALTIME_MODE=sse fallback via GET /v1/stream/alerts, docker compose --profile localdb offline Postgres, nightly db dump, and a phone hotspot as primary uplink.
- Unbounded queues turn a 200 ms alert into a 40 s alert as soon as inference falls behind. Mitigation: depth-1 ring buffer, MAXLEN ~ trimming, bounded evidence queue with drop-on-full, and visible drop counters.
- Local Supabase (`supabase start`, ~10 containers, 3.5-5 GB) starves the 16 GB machine and makes the GPU worker swap. Mitigation: hosted free tier is the default; local stack is fallback-only under a compose profile.
- The FPS numbers in the topology table are estimates, not measurements; thresholds tuned against them will be wrong. Mitigation: `python -m edge.bench --all` is a gated day-zero step whose output replaces the table before any tuning.
- Type drift between FastAPI responses and the Next.js dashboard once three people edit in parallel. Mitigation: packages/shared/src/api.d.ts is generated from /openapi.json and hand-written duplicates are banned.
- SUPABASE_SERVICE_ROLE_KEY leaking into apps/web via a NEXT_PUBLIC_ prefix or a client component import. Mitigation: it is absent from the [7] WEB group in .env.example and .env is gitignored; a grep for it under apps/web is part of the pre-demo checklist.

## Appendix — Open Questions

- Which 6 source clips will be used, and are their resolutions/framerates consistent enough that one letterbox size works for all cameras? This determines whether the hot/cold tier split is 4+2 or 3+3.
- Are the road_distance_m values in links[] going to be measured off satellite imagery for the real filming locations, or invented for the replay? The cloned-plate and speed rules inherit their credibility from this number.
- Does the team have a Supabase project with Storage enabled on the free tier, and is the 1 GB storage / 500 MB DB quota enough for 36 hours of demo evidence crops, or do we need aggressive DPDP_RETENTION_DAYS_RAW_EVIDENCE pruning during the build?
- Is ByteTrack being used via the Ultralytics tracker API (which wants a torch model) or a standalone numpy implementation fed by raw ONNX detections? The second is required if we are running pure ONNX Runtime with no torch on the edge machine - confirm with the CV lead.
- Should operator authentication use Supabase Auth (gotrue) or a simple API_JWT_SECRET-signed session issued by FastAPI? This section reserves both env vars but the auth/RLS section owns the decision.
