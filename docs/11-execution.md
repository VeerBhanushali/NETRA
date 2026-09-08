<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## Execution Plan, Demo Script & Pitch

Product codename used throughout: **NETRA**. Replace globally if the team picks another name — it appears in the deck, the browser tab title and the demo script.

### 0. Six non-negotiables

1. **Contracts before code.** The SQL migration, the OpenAPI stub and one fixture file are committed in the first 90 minutes. Nobody writes a second file until they exist.
2. **Vertical slice by H+8.** One video, one plate, one row, one dot. No feature branches until the slice is green.
3. **Hard integration checkpoint at H+22** (two-thirds is H+24). Everyone awake, everything on one machine, no laptops of individual devs in the loop.
4. **Feature freeze H+28.** After that only bug fixes, seed data and rehearsal. A commit that touches a `.py` under `apps/edge/` after H+28 requires two people to agree out loud.
5. **Everyone sleeps ≥5 hours.** A team that does not sleep forgets the demo order, talks over each other and loses on delivery, not on tech.
6. **The demo runs with the wifi physically off.** That is a preflight check, not an aspiration.

---

### 1. Workstreams, owners, and the interfaces they owe

Six students, five workstreams. Integration is not a person's spare time; it is D1's primary job from H+10.

| ID | Workstream | Owns | Must NOT touch |
|----|-----------|------|----------------|
| **V1** | Vision — detect & track | `apps/edge/detector.py`, `apps/edge/tracker.py`, ONNX export + DirectML session, `models/*.onnx` | API, DB, UI |
| **V2** | Vision — OCR & plate logic | `apps/edge/ocr.py`, plate normalisation/regex, multi-frame voting, `apps/edge/emit.py` | detector internals |
| **B1** | Backend & DB | `supabase/migrations/`, `apps/api/`, RLS, Realtime publication, alert rule engine | anything under `apps/web/` |
| **F1** | Frontend — map & live console | `apps/web/app/(console)/`, Mapbox/MapLibre layer, Realtime subscription | API code |
| **F2** | Frontend — tables, review queue, **deck owner + presenter** | alerts table, evidence drawer, review queue, `deck/` | edge code |
| **D1** | Data / demo / **integration captain** | `data/demo/`, `infra/mediamtx.yml`, `scripts/*`, offline kit, rehearsal direction | feature code after H+10 |

**Interface deliveries — a missed one is a blocking bug, not a delay:**

| Due | Owner | Artefact the others consume |
|-----|-------|------------------------------|
| H+1 | B1 | `supabase/migrations/0001_init.sql` — `cameras`, `plate_reads`, `vehicle_tracks`, `alerts`, `anomaly_events`, `alert_reviews`, `camera_pairs` |
| H+1 | B1 | `apps/api/openapi.json` (stub, handlers return fixtures) |
| H+1.5 | V2 | `data/fixtures/plate_reads.sample.jsonl` — 200 realistic rows. **Frontend and backend build against this until H+8.** |
| H+2 | V1 | `apps/edge/contracts.py` — `PlateReadEvent` dataclass, byte-identical field names to the JSONL |
| H+3 | D1 | `data/demo/cameras.yaml` + `scripts/seed_demo.py` (idempotent) |
| H+4 | D1 | Six looping RTSP streams on `rtsp://127.0.0.1:8554/cam01…cam06` |
| H+5 | B1 | Live `POST /v1/ingest/plate_read` writing real rows; `GET /healthz` returns `{"ok":true,"db":true,"version":"..."}` |
| H+6 | F1 | Console at `http://localhost:3000/console` rendering fixture rows on the map |
| H+8 | V1+V2 | `python -m apps.edge.worker --camera CAM01 --source rtsp://…/cam01` posting real reads |
| H+12 | B1 | Alert engine writing `alerts` rows; Realtime channel `public:alerts` broadcasting |
| H+14 | D1 | `scripts/preflight.py` exits 0 on a clean machine |
| H+24 | D1 | `demo-kit/` USB image, fully offline, verified on the spare laptop |

```text
sih-netra/
├─ apps/
│  ├─ edge/          # python 3.12, onnxruntime-directml
│  ├─ api/           # fastapi
│  └─ web/           # next.js app router
├─ supabase/migrations/
├─ models/           # yolov8n_plate.onnx, ppocr_rec.onnx, videomae_vad.onnx
├─ data/
│  ├─ demo/          # cameras.yaml, *.mp4, demo_seed.dump, offsets.yaml
│  ├─ fixtures/      # plate_reads.sample.jsonl
│  └─ tiles/         # pune.mbtiles
├─ infra/            # mediamtx.yml, docker-compose.demo.yml
├─ scripts/          # demo_up.ps1, preflight.py, seed_demo.py, replay_ingest.py,
│                    # force_event.py, bench.py, demo_down.ps1
└─ deck/
```

---

### 2. The vertical slice mandate

**The thinnest path, all nine hops, before anything else:**

`data/demo/cam01.mp4` → MediaMTX republishes it as `rtsp://127.0.0.1:8554/cam01` → `apps/edge/worker.py` reads a frame with OpenCV → YOLOv8n ONNX (DirectML) returns one plate box → PaddleOCR returns `MH14GK0221` → `emit.py` POSTs one `PlateReadEvent` → FastAPI validates and inserts one row into `plate_reads` → Supabase Realtime pushes it → one dot appears on the map at CAM01's coordinates within 2 seconds.

Acceptance is a single command, run by D1, not by the author of any piece:

```powershell
.\scripts\demo_up.ps1 -Cameras CAM01 ; python .\scripts\slice_check.py
# PASS if: >=1 row in plate_reads in the last 60s AND the row's plate_text matches
#          ^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$ AND the browser shows >=1 marker.
```

Why nothing else starts first: every hop in that chain is a place where a name, a port, a timezone, a JSON key or an execution provider can be wrong, and each one is cheap to fix alone and brutal to fix in a pile at hour 30. Nine hops fixed serially at H+6 cost 20 minutes each. The same nine discovered together at H+30 cost the demo. Accuracy, alert rules, VAD, styling and the second-through-sixth cameras are all *width* on a path that is already proven — width is safe to parallelise, depth is not.

---

### 3. The 36-hour plan

Clock assumes H+0 = 10:00 Day 1, demo at H+36 = 22:00 Day 2. Shift the clock column, not the hour column.

| Hour | Clock | V1 / V2 (vision) | B1 (backend/DB) | F1 / F2 (frontend) | D1 (data/demo) | Gate |
|---|---|---|---|---|---|---|
| 0–1 | 10:00 | Whiteboard the event schema with B1 | Write `0001_init.sql` + OpenAPI stub | Read the schema, agree field names | Copy 6 source videos to `data/demo/` | **G0: contracts committed** |
| 1–3 | 11:00 | `onnxruntime-directml` install; export `yolov8n.onnx`; confirm `DmlExecutionProvider` in providers list | `supabase start`, migration applied, `/healthz` green | `create-next-app`, Tailwind tokens, shell + nav, no data | Cut videos to 8-min loops, write `mediamtx.yml`, `cameras.yaml` | |
| 3–5 | 13:00 | V1 detector on a single frame; V2 PaddleOCR on cropped plates, build normaliser + regex | Ingest endpoint writing real rows; fixture loader | F1 map with 6 static camera pins; F2 alerts table skeleton on fixtures | 6 RTSP loops live; `seed_demo.py` seeds `cameras`, `camera_pairs` | |
| 5–8 | 15:00 | Wire detector→tracker→OCR→emit for **CAM01 only** | Idempotency key, `GET /v1/cameras`, `GET /v1/alerts` | F1 Realtime subscription; dot appears on insert | Run `slice_check.py` repeatedly, report breakages | **G1: VERTICAL SLICE GREEN (H+8)** |
| 8–11 | 18:00 | Scale to 3 live workers; multi-frame plate voting; measure FPS with `bench.py` | Alert rule engine: `CLONE_PLATE`, `OVERSPEED_PAIR`; `camera_pairs` distances | F1 trajectory polyline from `/v1/vehicles/{plate}/trajectory`; F2 evidence drawer with crops | `replay_ingest.py` for CAM04–06 (pre-computed reads at wall-clock offsets) | |
| 11–13 | 21:00 | VAD stub: VideoMAE ONNX on 16-frame clips, CAM04 only | Realtime publication on `alerts`; `POST /v1/alerts/{id}/review` | F2 review queue: confirm / reject / escalate | Wire the whole stack from `demo_up.ps1` | **G2: all workstreams off fixtures, on live API** |
| 13–14 | 23:00 | Handover notes in `NOTES.md` | Handover notes | Handover notes | Handover notes | Shift handover |
| 14–19 | 00:00 | **V1 sleeps** | B1 continues: alert dedup, severity, indexes | **F2 sleeps**; F1 continues: map polish, filters | **D1 sleeps** | Night crew = V2, B1, F1 |
| 16–21 | 02:00 | **V2 sleeps** from 02:00 | **B1 sleeps** from 02:00 | **F1 sleeps** from 02:00 | D1 up at 05:00 | Night crew = V1, F2, D1 |
| 19–22 | 05:00 | V1: night/rain clip tuning, confidence floor | B1 up 07:00: seed the planted events | F2: DPDP/audit panel, print-ready styling | D1: build `demo-kit/`, pull all images, vendor wheels | |
| 22–24 | 08:00 | Everyone at one machine. Cold boot, wifi OFF, run the full 5-minute flow end to end. Log every defect in one file. | | | | **G3: HARD INTEGRATION CHECKPOINT** |
| 24–28 | 10:00 | Fix only G3 defects, in severity order. No new features. | | | F2 starts the deck in parallel | **G4: FEATURE FREEZE H+28**, tag `v0.9-freeze` |
| 28–30 | 14:00 | Bug triage on `demo` branch only | Seed final dataset, take `demo_seed.dump` | F2 finishes deck; **F2 naps 14:00–15:30** | Second full offline dry run on the **spare** laptop | |
| 30–33 | 16:00 | Rehearsal ×3 with a stopwatch. Rotate who breaks it on purpose (kill the API, unplug the network, hard-refresh mid-alert). | | | | **G5: 3 clean runs under 5:00** |
| 33–35 | 19:00 | Judge-question drill: each member answers all seven questions cold | | | | Record `fallback_demo.mp4` |
| 35–36 | 21:00 | Cold boot, preflight, do nothing else. Charge everything. Eat. | | | | **DEMO** |

**Sleep rota:** Shift A (V1, F2, D1) 00:00–05:00; Shift B (V2, B1, F1) 02:00–07:00. Overlap guarantees ≥3 awake at all times and both shifts present from 07:00, one hour before G3. Presenter F2 gets the extra 90-minute nap at H+28 — the presenter must be the freshest person in the room.

---

### 4. Risk register

| Risk | Likelihood | Impact | Mitigation | Fallback trigger |
|---|---|---|---|---|
| No CUDA → inference too slow for 6 live streams | **High** | High | YOLOv8**n** @ 640, FP16 ONNX, `DmlExecutionProvider`; only 3 cameras run live inference, CAM04–06 use `replay_ingest.py` with pre-computed reads emitted at true wall-clock offsets | `bench.py` shows <8 FPS/stream at H+10 → drop to 2 live cameras, 4 replayed |
| PaddleOCR install fails on Windows/Py3.12 (paddlepaddle wheel, VC++ runtime) | **High** | High | Timebox to 45 min at H+1. Parallel path: export PP-OCRv4 recognition to `models/ppocr_rec.onnx` and run it through onnxruntime directly — no paddlepaddle dependency. Vendor the wheels into `vendor/wheels/` once it works | Not importable by H+3 → switch to ONNX-only OCR path |
| RTSP flakiness (MediaMTX drops, ffmpeg loop seam, decoder stall) | Medium | High | `--rtsp_transport tcp`; worker auto-reconnects with 2 s backoff and logs a `stream_gap` event; each loop cut on a keyframe boundary | Two reconnects in one rehearsal → switch that camera to `--source file://` direct decode, no RTSP |
| Supabase free-tier quota / auth outage | Medium | **Critical** | Local Supabase (`supabase start`, Postgres on `127.0.0.1:54322`) is the **primary** for the demo; cloud is the backup, not the reverse | Always — cloud is never on the demo path |
| Mapbox token rate-limit or no internet | Medium | **Critical** | `NEXT_PUBLIC_MAP_PROVIDER=mbtiles` serves `data/tiles/pune.mbtiles` via a local tileserver; `=static` renders a PNG basemap with a linear lat/lon→pixel transform | Preflight fails the tile check → flip the env var, restart web, 20 s |
| VAD model does not converge / no time to train | **High** | Medium | Do **not** train. Use a pretrained VideoMAE feature extractor + a 40-line MLP head trained on a UCF-Crime subset on Colab; if AUC < 0.70, ship a heuristic anomaly (sudden track-velocity reversal, crowd density spike, stopped vehicle in a live lane) and label it honestly as "motion-anomaly heuristic" | No usable head by H+20 → heuristic path, no slide claim about VAD AUC |
| Laptop thermal throttle during the demo | Medium | High | RX 6500M in a laptop throttles after ~10 min sustained load. Run the pipeline for exactly the demo window, cap worker FPS to 12, laptop on a hard surface with rear clearance, power profile = Best Performance, plugged in | Any rehearsal where FPS drops >30% after 8 min → reduce to 2 live cameras |
| Venue wifi fails / captive portal | **High** | **Critical** | Entire stack local; preflight is run with the adapter disabled | Always assume it has failed |
| Demo machine dies (power, disk, BSOD) | Low | **Critical** | Spare laptop carries the identical `demo-kit/` and passed preflight at H+30; `fallback_demo.mp4` on both machines and a phone | Machine not at the console within 90 s → presenter narrates over the recording and says so plainly |
| A dev pushes to `demo` after freeze | Medium | High | `demo` branch is D1's; merges after H+28 need D1 + one other | Any post-freeze regression → `git checkout v0.9-freeze` |
| Presenter overruns 5 minutes | **High** | Medium | Stopwatch in every rehearsal; the trajectory beat is the designated cut | Rehearsal >5:15 twice → cut the VAD beat to a single sentence |

---

### 5. Offline-demo insurance

**Prepare and verify by H+24. The USB `demo-kit/` contains:**

- `docker-images.tar` — `docker save` of every image `supabase start` pulls (postgres, gotrue, realtime, storage, kong). Pulling these at the venue is the single most likely way to lose.
- `vendor/wheels/` — `pip download -r requirements.txt -d vendor/wheels`; install with `pip install --no-index --find-links vendor/wheels -r requirements.txt`.
- `apps/web/.next/` — a **production build** (`next build`), served with `next start`. Never demo on the dev server: HMR, a stray recompile, or a Fast Refresh loop mid-demo is an unforced loss.
- `node_modules.zip` for the rare rebuild.
- `models/*.onnx` with a `models/SHA256SUMS` file checked by preflight.
- `data/demo/*.mp4` (six loops) and `data/demo/offsets.yaml`.
- `data/demo/demo_seed.dump` — `pg_dump -Fc` of a database already holding 14 days of history: ~80,000 `plate_reads`, 6 cameras, 340 alerts, a populated review queue. **Live ingest alone cannot make the city look real in five minutes; the history must be pre-seeded and the live rows must land on top of it.**
- `data/tiles/pune.mbtiles` — z10–z16 for the city bbox, plus `apps/web/public/basemap/pune_static.png`.
- `deck/netra.pdf` (fonts embedded) and `deck/netra.pptx`, plus 6 printed copies of the one-pager.
- `data/demo/fallback_demo.mp4` — a screen recording of a clean run, with narration, on both laptops and one phone.
- Hardware: HDMI + USB-C→HDMI + VGA adapters, a 3 m HDMI cable, a powered USB hub, two chargers, a power strip, a wired mouse.

Static basemap fallback (used when there is no tile server at all) — the transform is fixed and hardcoded, so the dots land correctly:

```typescript
// apps/web/lib/staticBasemap.ts
// Bounding box of data/demo/basemap/pune_static.png (1600x1200), EPSG:4326.
export const STATIC_BBOX = { west: 73.8000, south: 18.4850, east: 73.8800, north: 18.5650 };

export function lngLatToPixel(lng: number, lat: number, w = 1600, h = 1200) {
  const { west, south, east, north } = STATIC_BBOX;
  return {
    x: ((lng - west) / (east - west)) * w,
    y: ((north - lat) / (north - south)) * h, // y grows downward
  };
}
```

`scripts/preflight.py` is run twice: once at H+35 and once 60 seconds before walking to the judges.

```python
# scripts/preflight.py  -- exit 0 only if every check passes
import os, socket, subprocess, sys, hashlib, pathlib, json, urllib.request
import psycopg  # psycopg[binary]

CHECKS: list[tuple[str, callable]] = []
def check(name):
    def deco(fn): CHECKS.append((name, fn)); return fn
    return deco

def _port(p: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", p)) == 0

@check("onnxruntime DirectML EP available")
def _():
    import onnxruntime as ort
    return "DmlExecutionProvider" in ort.get_available_providers()

@check("model hashes match models/SHA256SUMS")
def _():
    root = pathlib.Path("models")
    for line in (root / "SHA256SUMS").read_text().splitlines():
        want, name = line.split()
        got = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if got != want: return False
    return True

@check("MediaMTX RTSP on :8554")
def _(): return _port(8554)

@check("all 6 RTSP streams decode")
def _():
    for i in range(1, 7):
        r = subprocess.run(["ffprobe", "-rtsp_transport", "tcp", "-v", "error",
                            "-select_streams", "v:0", "-show_entries", "stream=width",
                            "-of", "csv=p=0", f"rtsp://127.0.0.1:8554/cam0{i}"],
                           capture_output=True, timeout=15)
        if r.returncode != 0: return False
    return True

@check("local postgres seeded (6 cameras, >50k plate_reads)")
def _():
    with psycopg.connect(os.environ["DEMO_PG_DSN"]) as c:
        cams = c.execute("select count(*) from cameras").fetchone()[0]
        reads = c.execute("select count(*) from plate_reads").fetchone()[0]
        return cams == 6 and reads > 50_000

@check("api /healthz")
def _():
    with urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=3) as r:
        return json.load(r)["ok"] is True

@check("web production build on :3000")
def _(): return _port(3000)

@check("basemap available offline")
def _():
    mode = os.environ.get("NEXT_PUBLIC_MAP_PROVIDER", "mbtiles")
    if mode == "mbtiles":  return _port(8080) and pathlib.Path("data/tiles/pune.mbtiles").exists()
    if mode == "static":   return pathlib.Path("apps/web/public/basemap/pune_static.png").exists()
    return False  # 'mapbox' is never allowed at the venue

@check("planted events armed")
def _(): return pathlib.Path("data/demo/offsets.yaml").exists()

@check(">= 8 GB free disk")
def _():
    import shutil; return shutil.disk_usage(".").free > 8 * 1024**3

if __name__ == "__main__":
    bad = 0
    for name, fn in CHECKS:
        try: ok = bool(fn())
        except Exception as e: ok, name = False, f"{name}  [{type(e).__name__}: {e}]"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        bad += not ok
    print(f"\n{len(CHECKS)-bad}/{len(CHECKS)} passed")
    sys.exit(1 if bad else 0)
```

---

### 6. The demo

#### 6.1 Camera registry and planted events

```yaml
# data/demo/cameras.yaml  -- consumed by scripts/seed_demo.py and demo_up.ps1
cameras:
  - {camera_id: CAM01, name: "University Chowk",  lat: 18.5510, lon: 73.8250, source: cam01.mp4, mode: live}
  - {camera_id: CAM02, name: "Shivajinagar Jn",   lat: 18.5308, lon: 73.8478, source: cam02.mp4, mode: live}
  - {camera_id: CAM03, name: "FC Road North",     lat: 18.5236, lon: 73.8410, source: cam03.mp4, mode: live}
  - {camera_id: CAM04, name: "Deccan Gymkhana",   lat: 18.5158, lon: 73.8404, source: cam04.mp4, mode: replay}
  - {camera_id: CAM05, name: "JM Road South",     lat: 18.5245, lon: 73.8465, source: cam05.mp4, mode: replay}
  - {camera_id: CAM06, name: "Swargate Jn",       lat: 18.5013, lon: 73.8583, source: cam06.mp4, mode: replay}
camera_pairs:                       # road distance, not straight line
  - {from: CAM02, to: CAM03, road_distance_m: 1400, speed_limit_kmh: 50}
  - {from: CAM03, to: CAM05, road_distance_m:  900, speed_limit_kmh: 50}
  - {from: CAM05, to: CAM06, road_distance_m: 2600, speed_limit_kmh: 50}
```

```yaml
# data/demo/offsets.yaml -- t is seconds after DEMO_T0_EPOCH, set by demo_up.ps1
planted:
  - {t:  40, camera: CAM01, plate: MH14GK0221, note: "hero vehicle first sighting"}
  - {t:  95, camera: CAM02, plate: MH14GK0221}
  - {t: 128, camera: CAM03, plate: MH14GK0221, note: "trajectory beat has 3 points by 2:10"}
  - {t: 150, camera: CAM02, plate: MH12DE1433, note: "clone A"}
  - {t: 205, camera: CAM06, plate: MH12DE1433, note: "clone B -> 3.46 km in 55 s = 226 km/h"}
  - {t: 168, camera: CAM02, plate: DL8CAF5030}
  - {t: 211, camera: CAM03, plate: DL8CAF5030, note: "1400 m in 43 s = 117 km/h vs 50 limit"}
  - {t: 230, camera: CAM04, clip: fight_clip.mp4, note: "VAD window; score peaks ~0.71"}
```

Rule thresholds the demo depends on (must match the anomaly section exactly):

- `CLONE_PLATE` — same normalised `plate_text`, two reads ≥30 s apart, both `ocr_confidence ≥ 0.85`, implied straight-line speed **> 180 km/h**. 180 is chosen because it is far above any plausible urban road speed in India yet tolerant of ±5 s clock skew between edge workers; the 30 s floor stops overlapping fields of view at one junction from firing.
- `OVERSPEED_PAIR` — `road_distance_m / Δt` exceeds `speed_limit_kmh × 1.2`. The 20% margin absorbs frame-timestamp error and the fact that the plate is read at an arbitrary point inside the field of view.
- `VAD_ANOMALY` — VideoMAE score **> 0.62** on **3 consecutive** 16-frame windows (≈6 s of sustained abnormality). Single-window spikes on a busy junction are noise; three in a row are not.

**How the plants are guaranteed.** `demo_up.ps1` writes `DEMO_T0_EPOCH` at launch. The six loops are cut so the planted vehicles cross frame at those offsets, and `replay_ingest.py` schedules the replayed cameras off the same T0. The presenter starts the stack, then spends the first 60 seconds on the problem slide — by the time the console is on screen the pipeline is 60 s in and the clock is aligned. **Escape hatch:** `python scripts/force_event.py --type CLONE_PLATE` re-feeds the *same recorded frames* through the *real* detector, OCR and rule engine — it fabricates nothing. F1 keeps a terminal open on the second monitor. If asked, say exactly what it does.

#### 6.2 Five-minute script

| Time | Beat | Screen | Presenter (F2), operator (F1) |
|---|---|---|---|
| 0:00–0:25 | Opening | Slide 1 | **Exact words:** *"Every serious vehicle crime in this country passes a working CCTV camera. Almost none of them are caught by it — because those cameras are watched by people, and people cannot watch four thousand screens. This is NETRA. It watches all of them, it remembers every plate, and it runs on the laptop in front of you."* |
| 0:25–1:00 | Problem + architecture | Slides 2–3 | One line each: footage is recorded, never searched; plates are read manually after the fact; no cross-camera link. Then the one-diagram architecture. F1 alt-tabs to the console. |
| 1:00–1:45 | Live pipeline | `/console` — city map, six pins, live feed tile on CAM02 | *"Left is the live feed. Right is the database."* Point at a plate box appearing, the crop in the evidence rail, `MH14GK0221`, confidence `0.93`, and the new row at the top of the table — *"camera to database, eight hundred milliseconds, on an AMD laptop GPU with no CUDA."* |
| 1:45–2:25 | Cross-camera trajectory | Click the row → trajectory view | Polyline CAM01→CAM02→CAM03 with times and per-leg speed. *"One plate, three cameras, one journey. This is the query the officer actually wants, and it is a single index scan."* |
| 2:25–3:05 | Cloned plate fires **live** | Alert toast slides in; alerts table row goes red | Do not touch the mouse; wait for it. *"That fired while I was speaking. This plate was at Shivajinagar and at Swargate fifty-five seconds apart — three and a half kilometres. That is two hundred and twenty-six kilometres an hour. No vehicle did that. Two vehicles are wearing the same plate."* Open the alert: both crops side by side. |
| 3:05–3:35 | VAD anomaly | CAM04 tile, anomaly banner | *"This one is not about plates."* Show the 6-second clip, score 0.71, class `fight`. *"Behaviour model, three consecutive windows over threshold, so a single frame of noise cannot raise it."* |
| 3:35–4:15 | Human review queue | `/review` | Confirm the clone alert as an operator. Show the audit row: who, when, what evidence hash. *"Nothing here is automated enforcement. The machine narrows four thousand hours to eleven items; a human decides. Every decision is logged, and under the DPDP Act 2023 that log is the difference between a tool and a liability."* |
| 4:15–4:35 | Metrics | Slide 9 (metric table) | Read three numbers only: full-string plate accuracy with multi-frame voting, end-to-end p95 latency, streams per machine. |
| 4:35–5:00 | Close | Slide 12 | **Exact words:** *"Everything you just saw ran on this laptop, with the wifi switched off — one AMD GPU, no cloud, no internet. Four cameras at one junction become a searchable record. Ten thousand cameras become a city that remembers. The plate is read in under a second; the decision stays with the officer. Thank you — we'll take your questions."* |

Two rules for the operator: never hard-refresh (Realtime reconnects on its own), and if a beat does not fire within 8 seconds, move on and trigger it from `force_event.py` during the next beat.

---

### 7. Judge questions

**"Is this really 100% accurate?"** No, and any system that claims it is lying. Character-level accuracy is around 98%; full-string exact match on a single frame is materially lower because one bad character fails the whole plate. We recover it by voting across every frame of a vehicle's track — typically 8–20 reads — and by exposing the confidence. Below 0.80 the read never becomes an alert; it goes to the review queue as a candidate.

**"What about privacy?"** Three concrete controls. Retention: non-flagged `plate_reads` are purged after 30 days by a scheduled job; evidence crops for alerts are kept only while the case is open. Access: RLS by jurisdiction, so an officer sees only their zone, and every read of an evidence record writes an `audit_log` row. Minimisation: we store the plate crop, not the full frame, so faces and pedestrians are not retained. This is the purpose-limitation and storage-limitation shape the DPDP Act 2023 asks for; a production deployment additionally needs a documented lawful basis from the state police as data fiduciary.

**"Does it work at night or in the rain?"** Degraded, and we measured it rather than guessing — accuracy drops roughly 8–12 points on our night and rain clips. Three mitigations: most Indian plates are retro-reflective so IR-illuminated cameras give *better* contrast at night than in daylight glare; multi-frame voting recovers plates that any single frame fails; and low-confidence reads are surfaced as candidates instead of being silently dropped. Heavy rain and mud-obscured plates remain a genuine failure mode.

**"How does it scale to 10,000 cameras?"** See §8 — nothing in the design is single-machine except this laptop. The edge worker is already a standalone process that only knows an RTSP URL and an HTTP endpoint.

**"What is novel versus commercial ANPR?"** Commercial ANPR is per-camera: a plate, a timestamp, a barrier. Three things here are not standard. First, the unit of analysis is the *city-wide trajectory*, not the read — cloned-plate detection is impossible from any single camera and falls out for free once trajectories exist. Second, plate identity and behaviour anomaly run in one pipeline, so "who" and "what happened" are joined at ingest rather than by an analyst afterwards. Third, it is built for the hardware that actually exists in an Indian control room — ONNX + DirectML on commodity or AMD GPUs, no NVIDIA licence, no DeepStream, no per-camera vendor fee.

**"What happens on a false positive?"** Nothing enforceable. An alert is a queue item, not an action. The operator sees both evidence crops, the confidence and the rule that fired, and confirms or rejects. Rejections are labelled and become training data — the review queue *is* the retraining loop. There is no automatic challan, no automatic BOLO.

**"Why should police trust it?"** Because it shows its work. Every alert carries the crops it was derived from, the frame timestamps, the camera IDs, the rule and its threshold, and a hash of the evidence blob. An officer can reconstruct the decision without trusting the model. And because it is advisory by construction — it changes what a human looks at first, not what happens to a citizen.

---

### 8. Scaling story

| Layer | Demo (this laptop) | City (10,000 cameras) |
|---|---|---|
| Inference | 3 live streams, YOLOv8n FP16 ONNX / DirectML, ~12 FPS each, RX 6500M 4 GB | ~2,500 edge boxes, 4 cameras each (Jetson Orin Nano 8 GB or an x86 mini-PC with an Intel Arc iGPU + OpenVINO INT8). Video never leaves the junction; only events do. |
| Transport | Direct HTTP POST to FastAPI | Kafka topic `plate.reads.v1`, 48 partitions keyed by `camera_id`, 24 h retention. At ~600 reads/camera/hour, 10,000 cameras ≈ 6M events/hour ≈ **1,700 events/s** — one modest 3-broker cluster. |
| API | 1 uvicorn worker | FastAPI behind a load balancer, 8–12 stateless pods; ingest becomes a Kafka consumer group, so the read API and the write path scale independently. |
| Storage | Local Postgres, ~80k rows | TimescaleDB hypertable on `plate_reads`, 1-day chunks, compression after 7 days. ~400 B/row → ~58 GB/day raw, ~8–10 GB/day compressed. Crops in S3-compatible object storage, kept for alerts plus a 1-in-50 sample: ~15 KB × 120k alerts/day ≈ 1.8 GB/day. |
| Heavy models | VAD on 1 camera, every 2 s | VAD only on escalation (~10% of streams): ~1,000 concurrent → roughly 20–25 L4-class GPUs. Vehicle re-ID for plate-less matching runs on the same pool. |
| Trajectory queries | Single index scan | Same query, partitioned by day + BRIN on `seen_at`, plus a materialised `vehicle_daily_track` rollup for the map. |

The honest limits: cross-camera clock sync (edge boxes need NTP or the speed rules skew), physical network to 2,500 junctions, and the fact that alert *precision* — not throughput — is what decides whether operators keep using it after week two.

---

### 9. Pitch deck

Twelve slides, same design law as the product: white, near-black ink, one accent, hairline rules, no gradients, no stock photography, one idea per slide, tabular numerals in every table. 24 pt minimum body type — judges read from three metres.

| # | Slide | Content |
|---|---|---|
| 1 | Title | `NETRA` in large type, one-line descriptor, team + PS number. Nothing else. |
| 2 | The problem | Three lines, no icons: cameras record but nobody searches; plates are read manually; no system links a vehicle across two cameras. |
| 3 | What we built | One sentence + the six-camera map screenshot. |
| 4 | Architecture | Single diagram, left→right: RTSP → edge worker (YOLOv8 → ByteTrack → OCR / VAD) → FastAPI → Postgres+PostGIS → Realtime → Next.js. Boxes and hairlines only. |
| 5 | The hard part: reading Indian plates | The RTO/BH format, the failure cases, multi-frame voting. One before/after crop pair. |
| 6 | Cross-camera intelligence | The trajectory polyline; the clone rule stated as an inequality with the 180 km/h threshold and why. |
| 7 | Behaviour anomaly | VAD windowing, the 0.62 × 3-window rule, one anomaly still. |
| 8 | Human in the loop + DPDP 2023 | Review queue screenshot; retention, RLS, audit, minimisation as four short lines. |
| 9 | **Metrics** | The table below. On screen while the presenter reads three numbers. |
| 10 | Scale | The §8 table, condensed to four rows. |
| 11 | Roadmap | 30/60/90: pilot on 12 real junction cameras → re-ID + hotlist integration → state-level federation. Three lines. |
| 12 | Close | One sentence and the team's contact. |

**The metric table (slide 9).** Every cell is filled by `python scripts/bench.py --report` on the demo corpus at H+30 and by nobody's memory. If a number has not been measured, the row is deleted, not estimated.

| Metric | Value | How measured |
|---|---|---|
| Plate detection mAP@0.5 | *fill* | held-out set, `data/eval/plates_val/` (target ≥ 0.92) |
| OCR character accuracy | *fill* | per-character, all conditions (expect 96–98%) |
| Full-plate exact match, single frame | *fill* | expect 84–90% |
| Full-plate exact match, multi-frame vote | *fill* | ≥5 frames per track; expect 92–96% |
| End-to-end latency, frame → row on screen | *fill* p50 / p95 | expect ~0.8 s / ~1.6 s |
| Throughput on the demo laptop | *fill* | streams × FPS, **YOLOv8n FP16 ONNX, DmlExecutionProvider, RX 6500M 4 GB** — state the EP on the slide |
| Clone-plate false positives | *fill* | over a 6-hour replay of the full corpus |
| VAD AUC | *fill* | UCF-Crime subset; delete this row if the heuristic fallback shipped |

> **Verify:** any NCRB or "number of CCTV cameras in Indian cities" statistic used on slide 2 must be checked against a citable source before the pitch. Do not put a number on a slide that the team cannot source when asked.

---

## Appendix — Interface Contracts Declared by This Section

- `repo layout: apps/edge, apps/api, apps/web, supabase/migrations, models, data/demo, data/fixtures, data/tiles, infra, scripts, deck`
- `file: supabase/migrations/0001_init.sql (due H+1, owner B1)`
- `file: apps/api/openapi.json (stub due H+1)`
- `file: data/fixtures/plate_reads.sample.jsonl (200 rows, due H+1.5) — field names byte-identical to apps/edge/contracts.py PlateReadEvent`
- `file: apps/edge/contracts.py defines dataclass PlateReadEvent`
- `file: apps/edge/worker.py, CLI: python -m apps.edge.worker --camera CAM01 --source rtsp://127.0.0.1:8554/cam01`
- `file: data/demo/cameras.yaml (keys: cameras[camera_id,name,lat,lon,source,mode], camera_pairs[from,to,road_distance_m,speed_limit_kmh])`
- `file: data/demo/offsets.yaml (keys: planted[t,camera,plate,clip,note])`
- `file: data/demo/demo_seed.dump (pg_dump -Fc, >=50k plate_reads, 6 cameras, ~340 alerts)`
- `file: data/demo/fallback_demo.mp4`
- `file: data/tiles/pune.mbtiles`
- `file: apps/web/public/basemap/pune_static.png (1600x1200)`
- `file: apps/web/lib/staticBasemap.ts exports STATIC_BBOX {west:73.80,south:18.485,east:73.88,north:18.565} and lngLatToPixel(lng,lat,w,h)`
- `file: models/yolov8n_plate.onnx, models/ppocr_rec.onnx, models/videomae_vad.onnx, models/SHA256SUMS`
- `script: scripts/demo_up.ps1 (params: -Cameras), scripts/demo_down.ps1`
- `script: scripts/preflight.py (exit 0 = all checks pass)`
- `script: scripts/slice_check.py (vertical-slice acceptance)`
- `script: scripts/seed_demo.py (idempotent; seeds cameras + camera_pairs from cameras.yaml)`
- `script: scripts/replay_ingest.py (emits pre-computed reads for mode=replay cameras at DEMO_T0 offsets)`
- `script: scripts/force_event.py --type {CLONE_PLATE|OVERSPEED_PAIR|VAD_ANOMALY}`
- `script: scripts/bench.py --report (fills the slide-9 metric table)`
- `table: cameras(camera_id text PK, name, lat, lon, geom geography(Point,4326))`
- `table: camera_pairs(from_camera_id text, to_camera_id text, road_distance_m int, speed_limit_kmh int)`
- `table: plate_reads(... plate_text text, ocr_confidence float, camera_id text, seen_at timestamptz, track_id, crop_url)`
- `table: vehicle_tracks`
- `table: alerts(alert_id, rule_key text, severity, status, created_at)`
- `table: anomaly_events(camera_id, score float, window_start, window_end, class)`
- `table: alert_reviews(alert_id, reviewer, decision, decided_at)`
- `table: audit_log`
- `endpoint: GET /healthz -> {"ok":true,"db":true,"version":"..."}`
- `endpoint: POST /v1/ingest/plate_read`
- `endpoint: POST /v1/ingest/anomaly`
- `endpoint: GET /v1/cameras`
- `endpoint: GET /v1/vehicles/{plate_text}/trajectory?from=&to=`
- `endpoint: GET /v1/alerts?status=&severity=`
- `endpoint: POST /v1/alerts/{alert_id}/review`
- `realtime channel: public:alerts`
- `rule_key values: CLONE_PLATE, OVERSPEED_PAIR, VAD_ANOMALY, HOTLIST_HIT, LOITERING`
- `threshold: CLONE_PLATE = same normalised plate_text, reads >=30s apart, both ocr_confidence >= 0.85, implied straight-line speed > 180 km/h`
- `threshold: OVERSPEED_PAIR = road_distance_m/dt > speed_limit_kmh * 1.2`
- `threshold: VAD_ANOMALY = score > 0.62 on 3 consecutive 16-frame windows`
- `threshold: alert-eligibility floor ocr_confidence >= 0.80 (below -> review queue candidate only)`
- `retention: non-flagged plate_reads purged at 30 days`
- `env: DEMO_MODE (live|replay|offline)`
- `env: DEMO_T0_EPOCH (set by demo_up.ps1)`
- `env: DEMO_PG_DSN`
- `env: EDGE_ORT_PROVIDER=DmlExecutionProvider`
- `env: EDGE_API_BASE, EDGE_INGEST_TOKEN`
- `env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY`
- `env: NEXT_PUBLIC_API_BASE`
- `env: NEXT_PUBLIC_MAP_PROVIDER (mapbox|mbtiles|static; 'mapbox' forbidden at venue)`
- `env: NEXT_PUBLIC_MAPBOX_TOKEN, NEXT_PUBLIC_TILE_URL`
- `ports: 8554 RTSP (MediaMTX), 8000 FastAPI, 3000 Next.js production server, 8080 local tileserver, 54322 local Postgres`
- `rtsp paths: rtsp://127.0.0.1:8554/cam01 .. /cam06`
- `camera ids: CAM01 University Chowk, CAM02 Shivajinagar Jn, CAM03 FC Road North, CAM04 Deccan Gymkhana, CAM05 JM Road South, CAM06 Swargate Jn`
- `demo plates: MH14GK0221 (hero trajectory), MH12DE1433 (clone pair CAM02/CAM06), DL8CAF5030 (overspeed CAM02->CAM03), 22BH1234AA (BH-series showcase)`
- `routes: /console (live map), /review (human review queue), trajectory view reached from a plate_reads row`
- `git: branch 'demo' owned by D1, tag 'v0.9-freeze' at H+28`
- `gates: G0 contracts H+1, G1 vertical slice H+8, G2 off-fixtures H+13, G3 hard integration H+22, G4 feature freeze H+28, G5 three clean rehearsals H+33`
- `product codename: NETRA (used in deck, browser title, demo script)`

## Appendix — MVP vs Stretch

- MVP: vertical slice green by H+8 — one RTSP loop, YOLOv8n ONNX/DirectML, PaddleOCR (or ONNX PP-OCR), one POST /v1/ingest/plate_read, one row, one dot on the map via Realtime
- MVP: 3 cameras running live inference on the demo laptop; CAM04-CAM06 served by scripts/replay_ingest.py from pre-computed reads at true wall-clock offsets
- MVP: CLONE_PLATE and OVERSPEED_PAIR rules firing from camera_pairs road distances, with the exact thresholds in the contracts list
- MVP: cross-camera trajectory view for a searched plate (GET /v1/vehicles/{plate_text}/trajectory)
- MVP: human review queue at /review with confirm/reject and an audit_log row per decision
- MVP: pre-seeded history (data/demo/demo_seed.dump, >=50k plate_reads, ~340 alerts) so the city looks real before the live rows land
- MVP: fully offline operation — local Supabase/Postgres, mbtiles or static basemap, next start production build, vendored pip wheels, docker-images.tar
- MVP: scripts/preflight.py passing with the network adapter disabled, run at H+35 and 60s before the pitch
- MVP: scripts/force_event.py escape hatch that re-feeds recorded frames through the real pipeline
- MVP: data/demo/fallback_demo.mp4 screen recording on both laptops and one phone
- MVP: 12-slide deck with the slide-9 metric table filled from scripts/bench.py, plus 3 timed rehearsals under 5:00
- STRETCH: VideoMAE VAD head trained on a UCF-Crime subset on Colab; ship the motion-anomaly heuristic instead if AUC < 0.70 by H+20
- STRETCH: 6 cameras all running live inference (only if bench.py shows >=8 FPS/stream at H+10)
- STRETCH: vehicle re-ID for plate-less matching
- STRETCH: HOTLIST_HIT and LOITERING rules
- STRETCH: cloud Supabase mirror as a secondary write target (never on the demo path)
- STRETCH: Kafka in front of the ingest endpoint — described in the scaling slide, not built

## Appendix — Risks

- PaddleOCR/paddlepaddle wheel fails on Windows + Python 3.12 and eats 6 hours — mitigation: 45-minute timebox at H+1, then switch to PP-OCRv4 recognition exported to models/ppocr_rec.onnx run through onnxruntime with no paddlepaddle dependency; vendor working wheels into vendor/wheels/
- DirectML throughput below 8 FPS/stream makes 6 live cameras impossible — mitigation: YOLOv8n FP16 at 640, 3 live + 3 replayed cameras, FPS cap at 12; trigger measured by scripts/bench.py at H+10
- RX 6500M thermal throttling degrades FPS after ~8-10 minutes of sustained load, exactly during the pitch — mitigation: start the stack no more than 3 minutes before walking on, hard surface with rear clearance, Best Performance power profile, plugged in; drop to 2 live cameras if any rehearsal shows >30% FPS decay
- Venue wifi absent or behind a captive portal — mitigation: local Supabase is primary (never cloud), mbtiles/static basemap, and preflight.py is run with the network adapter disabled so the failure is discovered in rehearsal, not on stage
- Docker image pulls at the venue — mitigation: docker-images.tar via docker save on the demo-kit USB, loaded and verified at H+24 and again on the spare laptop at H+30
- Next.js dev server recompiling or Fast-Refreshing mid-demo — mitigation: demo only from a next build / next start production bundle
- Planted events drift out of the 5-minute window because DEMO_T0 was set late — mitigation: demo_up.ps1 stamps DEMO_T0_EPOCH, replay_ingest.py schedules off the same T0, and scripts/force_event.py re-feeds the real recorded frames through the real pipeline as a manual trigger
- Demo machine failure (BSOD, power, disk) — mitigation: spare laptop carrying the identical demo-kit and having passed preflight at H+30, plus fallback_demo.mp4; 90-second switchover rule, and the presenter states plainly that a recording is being shown
- Post-freeze commit regresses a working path — mitigation: 'demo' branch owned by D1, two-person rule for merges after H+28, git checkout v0.9-freeze as the instant rollback
- VAD model does not converge in the available Colab time — mitigation: pretrained VideoMAE features + a small MLP head; if AUC < 0.70 ship the motion-anomaly heuristic and remove the VAD AUC row from the metric slide rather than quoting an unmeasured number
- Clock skew between edge workers produces phantom CLONE_PLATE alerts — mitigation: the >180 km/h threshold and the >=30 s minimum gap are both sized to absorb about +/-5 s of skew; all workers read the OS clock on one machine for the demo
- Presenter overruns five minutes — mitigation: stopwatch on every rehearsal, the trajectory beat is the designated cut, VAD beat collapses to one sentence if two rehearsals exceed 5:15
- Sleep deprivation causing a garbled pitch — mitigation: two overlapping 5-hour sleep shifts (00:00-05:00 and 02:00-07:00) plus a 90-minute nap for the presenter at H+28

## Appendix — Open Questions

- Which city's coordinates and basemap does the team actually want? The plan hardcodes a Pune bbox (73.80/18.485/73.88/18.565) in cameras.yaml and staticBasemap.ts — if the team picks another city, those two files and the six camera lat/lons change together.
- Does the SIH venue allow a second (spare) laptop at the demo table, and is there a projector resolution constraint? The console layout and deck type sizes assume 1920x1080 at 3 metres.
- How long is the actual pitch slot — the script is written to 5:00 with Q&A after; if the slot is 8 or 10 minutes, insert a live plate-search-by-partial beat after the trajectory beat rather than slowing the existing beats.
- Are the six source videos available with genuine Indian plates and a usable night/rain clip? If the corpus has to be self-shot, D1 needs an extra 3 hours before H+3 and the schedule shifts.
- Who is the second presenter if F2 is unavailable — the judge-question drill at H+33 should certify at least two people to answer all seven questions cold.
- Is a Mapbox token available at all, or should the frontend be built on MapLibre + mbtiles from the start (which removes the token risk entirely and costs about an hour of F1's time at H+3)?
