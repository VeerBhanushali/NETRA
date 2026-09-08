<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## Privacy, Security, Ethics & Operations

This section is the part of the system that a judge with a law or policing background will attack. Everything here is buildable inside the 36 hours; the MVP subset is marked per-subsection.

### 1. Legal framing (India)

The platform is a **state-operated surveillance system**. Two bodies of law matter, and neither gives you a free pass.

**Digital Personal Data Protection Act, 2023.** A number plate string is personal data the moment it is joinable to the RTO registry; a face crop is personal data on its own. The obligations that bind our design:

| DPDP concept | What it forces in code |
|---|---|
| Lawful purpose (s.4) | Every watchlist row and every registry lookup carries a stated purpose string. No purpose, no row. |
| Data minimisation / purpose limitation (s.8) | We store plate text + vehicle crop, **not** continuous video, and we blur faces and pedestrians in retained evidence. |
| Storage limitation (s.8(7)) | Hard retention clocks (§3) enforced by a nightly job, not by policy prose. |
| Security safeguards (s.8(5)) | RLS + service-key isolation + signed ingest + audit chain (§5–§8). |
| Breach notification (s.8(6)) | Runbook item; Board + affected principals. |
| Data Principal rights (Ch. III) | Grievance route implemented as the dispute API (§7). |

> **Verify:** DPDP s.17(2)(a) lets the Central Government **notify** instrumentalities of the State as exempt from most of Chapters II and III in the interests of sovereignty, security of the State, public order, and prevention of offences. Confirm the exact notification status for a state police department before claiming the exemption, and confirm the commencement status of the DPDP Rules (draft published Jan 2025) — do not assert dates on stage.

**We engineer to the stricter standard anyway, and we say so.** Three reasons, all defensible in a viva:

1. The exemption is *conditional and notification-based*. A system that only works when exempt is a system that becomes illegal the day the notification lapses or is read down.
2. *K.S. Puttaswamy v. Union of India* (2017) put privacy in Art. 21 and imposed a three-part test on any state intrusion: **legality, necessity, proportionality**, with procedural safeguards. Exemption from a statute does not exempt you from Art. 21. Our audit log, watchlist expiry and human-in-the-loop rule are literally the proportionality argument, in schema form.
3. **India has no dedicated surveillance statute for video/ANPR.** Interception of communications is governed by the Indian Telegraph Act s.5(2) with the *PUCL* (1997) safeguards, and IT Act s.69 with the 2009 Rules. There is no equivalent authorisation-and-review ladder for mass plate capture. That vacuum means the safeguards are **ours to build** — a designated approving officer, an expiry date, a reviewable log — because no external body will impose them. Say this in the pitch: it converts a legal gap into a design feature.

> **Verify:** Any CCTNS / ICJS / VAHAN integration path and its MoU requirements. Treat VAHAN as an external registry we *mock* for the demo (§4).

**DPIA-lite artefact (MVP, 1 page in `/docs/dpia.md`):** purpose, categories of data, lawful basis, retention, recipients, risks (false accusation, function creep, bulk exfiltration, bias), mitigations, named Grievance Officer. Judges ask "did you think about this" — hand them the file.

### 2. Privacy engineering

**Redaction at the edge, before persistence.** The worker never uploads an unredacted frame to the durable bucket. Faces and full pedestrian boxes are blurred in the same process that produced the crop.

```python
# apps/edge/redact.py
import cv2, numpy as np

def redact_frame(frame: np.ndarray, person_boxes, face_boxes, plate_box=None) -> np.ndarray:
    """Gaussian-blur people and faces. Plate region is explicitly re-pasted sharp:
    the plate is the lawful target, the human beside it is not."""
    out = frame.copy()
    sharp_plate = None
    if plate_box is not None:
        x1, y1, x2, y2 = map(int, plate_box)
        sharp_plate = (x1, y1, x2, y2, frame[y1:y2, x1:x2].copy())

    for (x1, y1, x2, y2) in list(face_boxes) + list(person_boxes):
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(out.shape[1], int(x2)), min(out.shape[0], int(y2))
        if x2 <= x1 or y2 <= y1:
            continue
        roi = out[y1:y2, x1:x2]
        k = max(11, (min(roi.shape[:2]) // 4) | 1)   # kernel scales with box; always odd
        out[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)

    if sharp_plate:
        x1, y1, x2, y2, patch = sharp_plate
        out[y1:y2, x1:x2] = patch
    return out
```

Face detection source: reuse the person class from the YOLOv8 detector already running and blur the **upper 35 % of each person box** as a face proxy — an extra face model does not fit in 4 GB VRAM alongside the plate model. MVP = person-box blur. Stretch = a dedicated ONNX face model on CPU.

**Retention clocks.** Continuous video never leaves the edge machine: a 15-minute on-disk ring buffer, overwritten in place. Everything else is on a clock enforced by `pg_cron`.

| Store | Retention | Rationale |
|---|---|---|
| Edge ring buffer (raw MP4 segments) | 15 min, rolling | Enough to cut a clip after an alert; too short to be a dragnet archive. |
| `evidence-raw` bucket (unredacted crop) | **72 h**, service-role only | An investigator may need the unblurred frame; three days is one working cycle. |
| `evidence` bucket (redacted crop/clip) | 30 days; 365 days if `case_ref` set | Untriaged noise dies in a month; case material survives a charge-sheet cycle. |
| `plate_read` rows | 90 days, then aggregate to `plate_read_daily` | Trajectory analytics need weeks, not years. |
| `watchlist` entry | ≤ 90 days, `expires_at` NOT NULL | Forces re-justification. |
| `audit_log`, `registry_lookup` | 5 years, never purged by the job | The oversight record must outlive the data it describes. |

```sql
select cron.schedule('retention-nightly', '15 2 * * *', $$ select public.run_retention(); $$);
```

**Identity separation.** The plate→owner registry lives in a **separate Postgres schema** with no grant to `authenticated`, so no browser JWT can ever read it, RLS bug or not.

```sql
create schema registry;
revoke all on schema registry from anon, authenticated;
grant usage on schema registry to service_role;

create table registry.vehicle_owner (
  plate_text      text primary key,
  owner_name      text not null,
  owner_address   text,
  vehicle_make    text, vehicle_model text, vehicle_colour text,
  vehicle_class   text,          -- 2W | 3W | LMV | HCV
  registered_at   date,
  source          text not null default 'MOCK_VAHAN'
);

-- The join is an *event*, not a view.
create table public.registry_lookup (
  id           bigserial primary key,
  actor_id     uuid not null references public.app_user(id),
  plate_text   text not null,
  case_ref     text not null,
  reason       text not null check (length(reason) >= 20),
  alert_id     uuid references public.alert(id),
  looked_up_at timestamptz not null default now()
);
```

There is **no SQL view joining `plate_read` to `registry.vehicle_owner`.** The only path is `POST /api/v1/registry/lookup`, which writes `registry_lookup` + `audit_log` inside the same transaction as the read. If the log write fails, the lookup fails.

**Purpose-bound watchlist.** `watchlist(plate_text, purpose enum('stolen_vehicle','fir_linked','court_order','test_demo'), fir_number, authorised_by, expires_at not null default now()+interval '30 days', is_active)`. A partial index `where is_active and expires_at > now()` is what the matcher queries, so an expired entry stops generating alerts with zero extra code. `check (expires_at <= created_at + interval '90 days')` makes indefinite watchlisting impossible at the DB level.

### 3. Access control — role matrix

`create type app_role as enum ('operator','investigator','supervisor','admin','auditor');`

Deliberate separation of duty: **`admin` cannot read surveillance data, `auditor` cannot read anything but the log.** The person who can grant roles must not be the person who can search plates.

| Capability | operator | investigator | supervisor | admin | auditor |
|---|:--:|:--:|:--:|:--:|:--:|
| Live map, camera tiles | ✅ own zones | ✅ all | ✅ all | ❌ | ❌ |
| Read `plate_read` stream | ✅ own zones | ✅ | ✅ | ❌ | ❌ |
| Historical plate search | ❌ | ✅ | ✅ | ❌ | ❌ |
| View redacted evidence | ✅ | ✅ | ✅ | ❌ | ❌ |
| View **raw** (unblurred) evidence | ❌ | ❌ | ✅ +reason | ❌ | ❌ |
| Registry lookup (owner) | ❌ | ✅ +case_ref | ✅ | ❌ | ❌ |
| Confirm / dismiss alert | ✅ | ✅ | ✅ | ❌ | ❌ |
| Escalate alert → case | ❌ | ✅ | ✅ | ❌ | ❌ |
| Create / edit watchlist | ❌ | ✅ (≤30 d) | ✅ (≤90 d) | ❌ | ❌ |
| Bulk CSV export | ❌ | ❌ | ✅ +reason | ❌ | ❌ |
| Manage users / roles | ❌ | ❌ | ❌ | ✅ | ❌ |
| Camera config, thresholds | ❌ | ❌ | ✅ | ✅ | ❌ |
| Read `audit_log` | ❌ | ❌ | ✅ own zone | ❌ | ✅ all |
| Verify audit hash chain | ❌ | ❌ | ❌ | ❌ | ✅ |

Roles land in the JWT via a Supabase **custom access token hook** so RLS never has to sub-select the user table on every row.

> **Verify:** exact hook registration for your Supabase version (`auth.custom_access_token_hook`, enabled in Dashboard → Auth → Hooks).

```sql
create or replace function public.jwt_role() returns app_role
language sql stable as $$
  select coalesce(
    nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'app_role',''),
    'operator')::app_role
$$;

create or replace function public.jwt_zones() returns int[]
language sql stable as $$
  select coalesce(
    array(select jsonb_array_elements_text(
      (current_setting('request.jwt.claims', true)::jsonb -> 'zones'))::int),
    '{}'::int[])
$$;

alter table public.plate_read enable row level security;

create policy plate_read_zone_scoped on public.plate_read
for select to authenticated using (
  case public.jwt_role()
    when 'operator'     then camera_id in (select id from public.camera
                                           where zone_id = any(public.jwt_zones()))
    when 'investigator' then true
    when 'supervisor'   then true
    else false
  end
);

alter table public.evidence enable row level security;
create policy evidence_meta_read on public.evidence
for select to authenticated
using (public.jwt_role() in ('operator','investigator','supervisor') and redaction_state = 'redacted');

alter table public.watchlist enable row level security;
create policy watchlist_write on public.watchlist
for insert to authenticated
with check (public.jwt_role() in ('investigator','supervisor')
            and expires_at > now()
            and expires_at <= now() + (case public.jwt_role()
                  when 'investigator' then interval '30 days'
                  else interval '90 days' end));
```

**Storage buckets have no public policy at all.** `evidence` and `evidence-raw` are private; the browser never holds a storage token. Every object is fetched through `GET /api/v1/evidence/{evidence_id}/url`, which mints a service-role signed URL after logging.

API-side guard (defence in depth — RLS is the floor, not the ceiling):

```python
# apps/api/deps.py
from fastapi import Depends, Header, HTTPException, Request

ROLE_RANK = {"operator": 1, "investigator": 2, "supervisor": 3, "admin": 3, "auditor": 1}

def require_role(*allowed: str):
    def _dep(user = Depends(current_user)):
        if user.role not in allowed:
            raise HTTPException(403, f"role '{user.role}' not permitted")
        return user
    return _dep

def require_reason(x_justification: str = Header(..., alias="X-Justification")) -> str:
    r = x_justification.strip()
    if len(r) < 20:
        raise HTTPException(422, "X-Justification must be >= 20 characters")
    return r
```

Any endpoint that reveals a person requires **both** `require_role` and `require_reason`. A free-text reason that nobody reads is still the highest-value field in the system: it is the thing an officer must type, and therefore the thing an inquiry can quote back.

### 4. Audit logging — the single most important table

"Who searched this plate, when, and why" is the log that decides whether this platform is a policing tool or a stalking tool. Officers misusing lawful access is the dominant real-world abuse mode, far more common than external hackers.

```sql
create extension if not exists pgcrypto;

create table public.audit_log (
  id          bigserial primary key,
  ts          timestamptz not null default now(),
  actor_id    uuid,                    -- null only for system actions
  actor_role  app_role,
  action      text not null,           -- see taxonomy below
  target_type text,                    -- 'plate' | 'evidence' | 'watchlist' | 'user' | 'alert'
  target_id   text,
  reason      text,
  ip          inet,
  user_agent  text,
  request_id  uuid,
  payload     jsonb not null default '{}'::jsonb,
  prev_hash   bytea,
  row_hash    bytea not null
);
```

Action taxonomy (closed set, checked in the API layer): `auth.login`, `auth.login_failed`, `plate.search`, `plate_read.view`, `evidence.view`, `evidence.download`, `evidence.unseal_raw`, `registry.lookup`, `watchlist.create`, `watchlist.update`, `watchlist.expire`, `alert.confirm`, `alert.dismiss`, `alert.escalate`, `dispute.open`, `dispute.resolve`, `export.csv`, `user.role_change`, `config.update`, `retention.purge`, `camera.offline`.

**Tamper evidence via hash chain.** Each row commits to its predecessor, so deleting or editing row *n* invalidates every row after it.

```sql
create or replace function public.audit_chain() returns trigger
language plpgsql as $$
declare p bytea;
begin
  perform pg_advisory_xact_lock(hashtext('audit_log'));  -- serialise the chain
  select row_hash into p from public.audit_log order by id desc limit 1;
  new.prev_hash := p;
  new.row_hash := digest(
      coalesce(encode(p,'hex'),'GENESIS') || '|' ||
      to_char(new.ts,'YYYY-MM-DD"T"HH24:MI:SS.USOF') || '|' ||
      coalesce(new.actor_id::text,'-') || '|' || new.action || '|' ||
      coalesce(new.target_type,'-') || '|' || coalesce(new.target_id,'-') || '|' ||
      coalesce(new.reason,'-') || '|' || new.payload::text, 'sha256');
  return new;
end $$;

create trigger trg_audit_chain before insert on public.audit_log
for each row execute function public.audit_chain();

-- Append-only, enforced twice: privileges and a trigger.
revoke update, delete, truncate on public.audit_log from anon, authenticated, service_role;
create or replace function public.audit_immutable() returns trigger
language plpgsql as $$ begin raise exception 'audit_log is append-only'; end $$;
create trigger trg_audit_immutable before update or delete on public.audit_log
for each row execute function public.audit_immutable();

-- Auditor-facing verifier.
create or replace function public.audit_verify(from_id bigint default 1)
returns table(broken_at bigint, expected bytea, found bytea)
language plpgsql as $$
declare r record; p bytea; calc bytea;
begin
  select row_hash into p from public.audit_log where id < from_id order by id desc limit 1;
  for r in select * from public.audit_log where id >= from_id order by id loop
    calc := digest(coalesce(encode(p,'hex'),'GENESIS') || '|' ||
            to_char(r.ts,'YYYY-MM-DD"T"HH24:MI:SS.USOF') || '|' ||
            coalesce(r.actor_id::text,'-') || '|' || r.action || '|' ||
            coalesce(r.target_type,'-') || '|' || coalesce(r.target_id,'-') || '|' ||
            coalesce(r.reason,'-') || '|' || r.payload::text, 'sha256');
    if calc <> r.row_hash then
      broken_at := r.id; expected := calc; found := r.row_hash; return next; return;
    end if;
    p := r.row_hash;
  end loop;
end $$;
```

**Anchoring (stretch, 20 min):** a nightly `pg_cron` job appends `(date, head_id, head_hash)` to `audit_anchor` and posts the head hash to an external channel (email/Slack/GitHub commit). Once the head hash is published outside the DB, even a DB admin cannot rewrite history undetected.

**Demo move:** in the UI, an auditor screen with a "Verify chain" button that calls `POST /api/v1/audit/verify` and prints `OK — 12,481 entries, head 9f3a…`. Then `UPDATE` a row in psql live, click again, watch it name the broken row. That thirty-second demo wins the section.

### 5. Chain of custody

For a clip to survive a defence lawyer, you must show it is the same bytes the camera produced and that every hand that touched it is named.

1. **Hash at capture.** The edge worker computes `sha256` of the exact bytes it uploads and includes it in the ingest payload. `evidence.sha256 bytea not null`, `evidence.storage_path text unique`.
2. **Capture manifest.** `evidence.manifest jsonb` = `{camera_id, started_at, ended_at, fps, frame_count, codec, worker_id, model_versions:{detector, ocr, vad}, clock_offset_ms, ntp_source}`. Model version strings matter: "which model said this" is a question that will be asked.
3. **Immutable path.** `evidence/{camera_id}/{yyyy}/{mm}/{dd}/{evidence_id}.jpg`. Never overwritten; corrections create a new row with `supersedes_id`.
4. **Short-TTL signed URLs.** 300 s. Long enough to render, short enough that a URL pasted into WhatsApp is dead on arrival.
5. **Access logged before the URL is minted**, in the same transaction.

```python
@router.get("/api/v1/evidence/{evidence_id}/url")
async def evidence_url(evidence_id: UUID, request: Request,
                       user = Depends(require_role("operator","investigator","supervisor")),
                       reason: str = Depends(require_reason)):
    async with db.transaction():
        ev = await db.fetchrow(
            "select storage_path, sha256, redaction_state from evidence where id = $1", evidence_id)
        if not ev:
            raise HTTPException(404)
        if ev["redaction_state"] == "raw" and user.role != "supervisor":
            raise HTTPException(403, "raw evidence requires supervisor")
        await audit(db, actor=user, action="evidence.view", target_type="evidence",
                    target_id=str(evidence_id), reason=reason, request=request,
                    payload={"sha256": ev["sha256"].hex()})
        url = sb_admin.storage.from_("evidence").create_signed_url(
            ev["storage_path"], expires_in=300)   # service role, server side only
    return {"url": url["signedURL"], "sha256": ev["sha256"].hex(), "expires_in": 300}
```

6. **Custody receipt.** `GET /api/v1/evidence/{id}/custody` returns JSON (stretch: PDF) with `sha256`, manifest, and the full ordered list of `audit_log` rows touching that evidence id — capture → every view → every download, each with actor, role, timestamp and reason.

### 6. Bias and fairness

**Accuracy is not a scalar.** A single "98.4 % accuracy" number is the claim most likely to be dismantled in questioning. Report a stratified table, computed by `scripts/eval_stratified.py` over a held-out labelled set, with **Wilson 95 % confidence intervals** and the sample count per cell; suppress any cell with n < 50 as "insufficient data" rather than reporting a noisy figure.

Required strata: **vehicle class** (2W / 3W / LMV / HCV — two-wheelers have smaller, more tilted, more often obscured plates and will be your worst cell); **plate background** (white private / yellow commercial / green EV / red temporary / black rental / BH-series); **lighting** (day / dusk / night-with-IR / backlit-headlight-glare); **plate condition** (clean / dirty / bent / non-standard font); **camera** (per-site, because one bad mount can carry the whole error budget).

Report both **plate-level exact match** and **character error rate**, plus a **rejection rate** — a system that abstains on hard plates is honest; one that guesses is dangerous.

**Fairness gate in CI (MVP):** fail the build if `max(class_accuracy) - min(class_accuracy) > 10 pp` across vehicle class, or if the two-wheeler cell falls below 0.85 exact match. If you cannot fix it in 36 hours, ship it with the gap **printed on the dashboard**. Disclosed weakness reads as competence; hidden weakness reads as a lie.

**Disparate impact of deployment.** Alert counts are a function of camera density, not of criminality. If eight of ten cameras sit in one neighbourhood, that neighbourhood generates 80 % of alerts and the map "proves" it is a crime hotspot — a feedback loop that ends in over-policing. Mitigations: (a) normalise every heatmap by **alerts per 1,000 vehicle-reads at that camera**, never raw counts; (b) show camera coverage density as a toggleable map layer so the operator sees the sampling bias directly; (c) `camera.zone_id` + a `zone_alert_rate` view that supervisors review weekly.

**No autonomous action, ever.** No alert dispatches a unit, issues a challan, opens a barrier or notifies a citizen by itself. Every alert lands in `alert.status = 'pending_review'` and requires a human `alert.confirm` with an actor id. This is enforced in schema — there is no code path from the detector to an outbound action — and it is the single sentence to say when a judge asks about accountability.

### 7. False-positive harm analysis

Take the worst case concretely: a citizen's plate is read as `MH12DE1433`, matched against a real sighting elsewhere, and flagged as a **cloned plate**. If that flows straight to a patrol, an innocent driver is stopped, possibly detained, their vehicle possibly impounded, and their name enters a police record that is far easier to create than to erase. The most likely actual cause is not crime — it is one OCR character (`8`↔`B`, `0`↔`O`↔`D`, `1`↔`I`, `2`↔`Z`, `5`↔`S`, `6`↔`G`).

Safeguards, in order of the pipeline:

1. **Confidence gating.** Auto-commit a read only when `mean_char_conf ≥ 0.85` **and** `min_char_conf ≥ 0.60` **and** the string matches the RTO/BH regex. `0.60–0.85` → `review_queue`. `< 0.60` → discarded (crop retained 7 days for drift analysis only). *Calibrate these on your own 500-plate labelled set: pick the lowest threshold at which precision ≥ 0.98, then ship that number and the curve.*
2. **Confusion-neighbour suppression.** Before raising a clone alert, generate the edit-distance-1 confusion neighbours of the read. If any neighbour was seen at either camera within ±10 minutes, the "clone" is almost certainly a misread of that vehicle — suppress and route to review. This one rule removes the majority of clone false positives.
3. **Physical-plausibility threshold.** Implied speed between the two cameras > **150 km/h** on road distance. Urban free-flow tops out near 80 km/h; 150 leaves ~2× headroom for road-distance error, clock skew and timestamp jitter. Both reads must have `conf ≥ 0.92`.
4. **Corroboration.** Require a second independent signal: vehicle class mismatch, or dominant-colour histogram distance above threshold, or implied speed > 250 km/h. A clone claim from OCR alone is not a claim.
5. **Mandatory human confirmation** with side-by-side crops and both confidence scores rendered numerically — the reviewer sees `0.71` and hesitates in a way they never would at "HIGH CONFIDENCE".
6. **Alerts expire.** `alert.expires_at = created_at + 72 h`; unconfirmed alerts auto-transition to `expired` and stop appearing. An accusation with no shelf life is a permanent shadow record.
7. **Dispute path.** Every confirmed alert shown to a citizen carries a reference `ALT-2026-000481`. `POST /api/v1/disputes` (public, rate-limited, captcha-free but token-gated) writes `dispute(alert_id, contact, statement, status, sla_due_at = now() + 7 days)`. Resolution sets `plate_read.corrected_text`, marks the alert `false_positive`, and — crucially — **feeds the correction back into the eval set**, so the dispute mechanism is also the retraining pipeline.

### 8. Security hardening

**The service key must never reach the browser.** `SUPABASE_SERVICE_ROLE_KEY` bypasses all RLS. It lives only in the FastAPI process environment. In Next.js, anything prefixed `NEXT_PUBLIC_` is inlined into the client bundle, so only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` may carry that prefix. Enforce it in CI:

```bash
# scripts/check-no-secrets.sh — run in CI and in a pre-commit hook
set -e
npm --prefix apps/web run build
if grep -rIl "service_role" apps/web/.next/static 2>/dev/null; then
  echo "FATAL: service_role key or string leaked into client bundle"; exit 1
fi
grep -rn "NEXT_PUBLIC_.*SERVICE\|NEXT_PUBLIC_.*SECRET" apps/web/ && { echo "FATAL: secret behind NEXT_PUBLIC_"; exit 1; } || true
```

**Signed ingest.** Edge workers are not authenticated users; they carry a per-worker HMAC secret (`EDGE_HMAC_SECRET_<worker_id>`, stored in Supabase Vault, mirrored to the worker's `.env`).

```python
# apps/api/security/hmac_ingest.py
import hmac, hashlib, time
from fastapi import Header, HTTPException, Request

REPLAY_WINDOW_S = 300
_seen: dict[str, float] = {}          # MVP in-process; stretch: Redis / Postgres nonce table

async def verify_ingest(request: Request,
                        x_edge_id: str = Header(...),
                        x_timestamp: str = Header(...),
                        x_nonce: str = Header(...),
                        x_signature: str = Header(...)) -> str:
    if abs(time.time() - float(x_timestamp)) > REPLAY_WINDOW_S:
        raise HTTPException(401, "stale timestamp")
    key = f"{x_edge_id}:{x_nonce}"
    now = time.time()
    for k, t in list(_seen.items()):
        if now - t > REPLAY_WINDOW_S: _seen.pop(k, None)
    if key in _seen:
        raise HTTPException(401, "replay detected")
    body = await request.body()
    secret = EDGE_SECRETS.get(x_edge_id)
    if not secret:
        raise HTTPException(401, "unknown edge worker")
    mac = hmac.new(secret.encode(), f"{x_timestamp}.{x_nonce}.".encode() + body, hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), x_signature):
        raise HTTPException(401, "bad signature")
    _seen[key] = now
    return x_edge_id
```

**RTSP credentials** never appear in the `camera` table as plaintext and are never returned by any API. Store the full URL in Supabase Vault and keep only `camera.rtsp_secret_id uuid` in the table; the API returns `rtsp_display` (`rtsp://cam-14.***@10.20.3.14/stream1`). Redact credentials in every log line — a stack trace containing an RTSP URL is a camera takeover.

> **Verify:** Supabase Vault API surface (`vault.create_secret`, `vault.decrypted_secrets`) for your project version.

**Injection-safe plate search.** Plate strings are user input concatenated into a `LIKE` in every naive implementation. Normalise, validate, parameterise:

```python
PLATE_RE = re.compile(r'^[A-Z0-9]{3,11}$')

async def search_plates(q: str, limit: int = 50):
    q = re.sub(r'[^A-Za-z0-9]', '', q).upper()
    if not PLATE_RE.match(q):
        raise HTTPException(422, "plate query must be 3-11 alphanumeric characters")
    if len(q) < 4:
        raise HTTPException(422, "minimum 4 characters — prefix wildcards disabled to prevent bulk export")
    return await db.fetch(
        """select id, plate_text, camera_id, captured_at, ocr_conf
             from plate_read
            where plate_text like $1 || '%'
               or plate_text % $1                 -- pg_trgm fuzzy, index-backed
         order by captured_at desc
            limit $2""", q, min(limit, 200))
```

The 4-character minimum is a **security control**, not UX: a 2-character query returns the whole city.

**Rate limits and exfiltration ceilings.** Bulk exfiltration by an authorised insider is the realistic breach, so cap by *distinct plates*, not just requests.

| Endpoint | Limit | Enforcement |
|---|---|---|
| `POST /api/v1/plates/search` | 60/hour and 500/day per user | `slowapi` + `search_quota(actor_id, day, distinct_plates)` |
| distinct plates viewed/day | 200 (investigator), 500 (supervisor) | DB counter; on breach → 429 + `audit_log` + supervisor notification |
| `POST /api/v1/registry/lookup` | 20/day; > 5 in 10 min → supervisor approval (stretch) | DB counter |
| `POST /api/v1/auth/login` | 5/15 min per IP+email | `slowapi` |
| `POST /api/v1/disputes` | 3/hour per IP | `slowapi` |
| `GET /api/v1/evidence/*/url` | 120/hour per user | `slowapi` |

Also: CORS locked to the dashboard origin; `TrustedHostMiddleware`; `Content-Security-Policy` with no `unsafe-inline`; JWT TTL 60 min with refresh; forced re-auth for `evidence.unseal_raw`.

**Supply chain.** Pin everything: `pip-compile --generate-hashes`, `npm ci` against a committed lockfile. Run `pip-audit` and `npm audit --omit=dev` in CI. **Pin model weights by hash** — verify `sha256` of `yolov8n_plate.onnx` at load and refuse to start on mismatch; a swapped weights file is a silent, undetectable backdoor into a vision system. Disable Ultralytics telemetry/auto-download at startup.

> **Verify:** current Ultralytics settings keys — approximately `from ultralytics import settings; settings.update({"sync": False})` plus `YOLO_OFFLINE=1`.

Never commit `.env`; commit `.env.example` with empty values; add `gitleaks` as a pre-commit hook (stretch).

### 9. Operational runbook

**Health.** `GET /api/v1/health` (liveness, no DB) and `GET /api/v1/health/ready` (DB + storage + Realtime round-trip, degrades to 503). Every edge worker POSTs a heartbeat to `/api/v1/edge/heartbeat` every **10 s** with `{worker_id, camera_id, fps_in, fps_processed, queue_depth, dropped_frames, gpu_mem_mb, clock_offset_ms, model_versions}` → `camera_health`.

**Camera dark.** `last_heartbeat > 60 s` → `degraded` (amber on map). `> 180 s` → `offline` (grey, banner, `camera.offline` audit row). 60 s tolerates a GC pause or a Wi-Fi blip; 180 s means it is genuinely gone. Runbook: (1) confirm it is one camera, not all — all cameras dark means the API or MediaMTX died, not the field; (2) `ffprobe -rtsp_transport tcp -i <url>` from the edge box to separate network failure from decoder crash; (3) restart the single worker (`supervisord`/`pm2` restart, workers are stateless and reconnect with exponential backoff 1 s → 30 s); (4) if the stream is up but `fps_processed` ≈ 0, the ONNX/DirectML session has wedged — restart the process, do not debug live; (5) log the gap in `camera_outage(camera_id, started_at, ended_at, cause)` — **coverage gaps must be visible on the timeline**, because "the camera was down" is a valid and necessary answer in court, and a silent gap looks like deletion.

**Model drift via review-queue correction rate.** This is the cheapest live accuracy signal you will ever get: humans are already correcting OCR in the review queue, so measure them.

```sql
create or replace view public.ocr_drift as
select date_trunc('hour', reviewed_at) as hour,
       count(*)                                            as reviewed,
       count(*) filter (where corrected_text is distinct from raw_text) as corrections,
       round(100.0 * count(*) filter (where corrected_text is distinct from raw_text)
             / nullif(count(*),0), 2)                       as correction_rate_pct
from public.review_queue
where reviewed_at > now() - interval '7 days'
group by 1 order by 1 desc;
```

Freeze a baseline from the first 1,000 reviewed items. Alert when the 24-hour rolling correction rate exceeds `baseline + 5 pp` with n ≥ 100 (below n = 100 the binomial noise swamps a 5 pp shift). Then check the per-camera breakdown first — drift is usually **one** camera that got knocked, refocused, or is now facing the setting sun, not the model decaying.

**Backup / restore.** Nightly `pg_dump -Fc` to local disk **and** to a `backups` storage bucket, 7 daily + 4 weekly retained. Storage buckets: weekly `rclone`/`supabase storage cp` mirror of `evidence` only (raw and ring buffers are intentionally not backed up — backing up data you promised to delete is how retention policies die). **Do a restore drill once before the demo** into a scratch Supabase project and time it; an untested backup is not a backup. Target RPO 24 h, RTO 2 h.

> **Verify:** Supabase PITR availability on your plan; on free tier, `pg_dump` via `supabase db dump` is the fallback.

**Operator metrics dashboard** (`/ops`, same minimalist white grid as the rest of the UI — hairline-bordered tiles, tabular numerals, semantic colour only on the status dot):

`cameras_online / cameras_total` · `reads_per_min` (1 h sparkline) · `p50 / p95 end-to-end latency` (frame capture → row visible) · `open_alerts` by severity · `oldest_unreviewed_alert_age` · `review_queue_depth` and 1 h throughput · `ocr_auto_accept_rate` · `ocr_correction_rate_24h` vs baseline · `storage_used_gb` and projected days to full · `failed_ingest_signatures_1h` (non-zero = investigate, this is your intrusion canary) · `rate_limit_429s_by_user_24h` (a user hitting the ceiling daily is either a broken integration or an exfiltration attempt) · `audit_chain_last_verified_at`.

**Kill switches (MVP, 10 minutes to build, disproportionate credibility):** `system_config.ingest_enabled boolean` checked by the ingest endpoint, and `camera.is_enabled` per camera. A supervisor can stop city-wide collection from the UI without an SSH session. Every toggle writes `config.update` to the audit log. The ability to *stop* is as much a safeguard as any policy document.

### MVP vs stretch summary

**Must exist for the demo:** role enum + JWT claim hook + the RLS policies above; `audit_log` with hash chain, immutability trigger, and the live "Verify chain" screen; registry schema separation with logged `registry.lookup`; watchlist `expires_at`; face/person blur on retained evidence; retention job; HMAC-signed ingest; parameterised + length-gated plate search with rate limits; the CI secret-leak check; camera heartbeat, offline detection and the ops tiles; stratified accuracy table; alert `pending_review` + expiry + human confirm.

**Stretch:** external audit anchoring; single-use signed-URL nonces; PDF custody receipt; dedicated ONNX face model; 4-eyes approval for bulk registry lookups; `gitleaks`; Redis-backed distributed rate limiting.

---

## Appendix — Interface Contracts Declared by This Section

- `enum: app_role = ('operator','investigator','supervisor','admin','auditor')`
- `table: public.app_user(id uuid pk -> auth.users, full_name text, badge_no text, role app_role, zone_ids int[], is_active boolean, created_at timestamptz)`
- `table: public.audit_log(id bigserial pk, ts timestamptz, actor_id uuid, actor_role app_role, action text, target_type text, target_id text, reason text, ip inet, user_agent text, request_id uuid, payload jsonb, prev_hash bytea, row_hash bytea) — APPEND ONLY, no UPDATE/DELETE grants`
- `table: public.audit_anchor(anchor_date date pk, head_id bigint, head_hash bytea, published_to text)`
- `table: public.registry.vehicle_owner(plate_text text pk, owner_name text, owner_address text, vehicle_make text, vehicle_model text, vehicle_colour text, vehicle_class text, registered_at date, source text) — schema 'registry', service_role only, NO grant to authenticated`
- `table: public.registry_lookup(id bigserial pk, actor_id uuid, plate_text text, case_ref text, reason text CHECK length>=20, alert_id uuid, looked_up_at timestamptz)`
- `table: public.watchlist(id uuid pk, plate_text text, purpose text CHECK in ('stolen_vehicle','fir_linked','court_order','test_demo'), fir_number text, authorised_by uuid, created_at timestamptz, expires_at timestamptz NOT NULL CHECK expires_at <= created_at + interval '90 days', is_active boolean)`
- `table: public.evidence(id uuid pk, camera_id text, plate_read_id uuid, storage_path text unique, bucket text, sha256 bytea NOT NULL, redaction_state text in ('redacted','raw'), manifest jsonb, case_ref text, supersedes_id uuid, created_at timestamptz, expires_at timestamptz)`
- `table: public.dispute(id uuid pk, alert_id uuid, reference text unique, contact text, statement text, status text in ('open','upheld','rejected'), sla_due_at timestamptz, resolved_by uuid, resolved_at timestamptz)`
- `table: public.camera_health(camera_id text pk, worker_id text, last_heartbeat timestamptz, fps_in numeric, fps_processed numeric, queue_depth int, dropped_frames bigint, gpu_mem_mb int, clock_offset_ms int, model_versions jsonb, status text in ('online','degraded','offline'))`
- `table: public.camera_outage(id bigserial pk, camera_id text, started_at timestamptz, ended_at timestamptz, cause text)`
- `table: public.search_quota(actor_id uuid, day date, request_count int, distinct_plates int, primary key(actor_id, day))`
- `table: public.review_queue(id uuid pk, plate_read_id uuid, raw_text text, corrected_text text, reviewer_id uuid, reviewed_at timestamptz, ocr_conf numeric)`
- `table: public.system_config(key text pk, value jsonb, updated_by uuid, updated_at timestamptz) — includes key 'ingest_enabled'`
- `column dependency: public.plate_read(id, plate_text, camera_id, captured_at, ocr_conf, min_char_conf, corrected_text)`
- `column dependency: public.camera(id, zone_id int, is_enabled boolean, rtsp_secret_id uuid, rtsp_display text, lat, lon)`
- `column dependency: public.alert(id uuid, status text in ('pending_review','confirmed','dismissed','expired','false_positive'), severity, expires_at timestamptz = created_at + 72h, confirmed_by uuid, reference text 'ALT-YYYY-NNNNNN')`
- `table: public.plate_read_daily (aggregate rollup target after 90-day plate_read purge)`
- `sql function: public.jwt_role() returns app_role`
- `sql function: public.jwt_zones() returns int[]`
- `sql function: public.audit_chain() trigger, public.audit_immutable() trigger`
- `sql function: public.audit_verify(from_id bigint) returns table(broken_at bigint, expected bytea, found bytea)`
- `sql function: public.run_retention() — invoked by pg_cron job 'retention-nightly' at 02:15`
- `sql view: public.ocr_drift(hour, reviewed, corrections, correction_rate_pct)`
- `sql view: public.zone_alert_rate (alerts per 1000 reads per camera/zone)`
- `rls policy names: plate_read_zone_scoped, evidence_meta_read, watchlist_write`
- `supabase auth hook: auth.custom_access_token_hook adding claims 'app_role' and 'zones'`
- `storage bucket: evidence (private, redacted, 30d / 365d if case_ref)`
- `storage bucket: evidence-raw (private, service_role only, 72h lifecycle)`
- `storage bucket: backups (private, nightly pg_dump)`
- `storage path convention: evidence/{camera_id}/{yyyy}/{mm}/{dd}/{evidence_id}.jpg`
- `endpoint: POST /api/v1/plates/search (investigator+, rate-limited, min 4 chars, parameterised)`
- `endpoint: POST /api/v1/registry/lookup (investigator+, requires case_ref + reason, writes registry_lookup + audit_log in one txn)`
- `endpoint: GET /api/v1/evidence/{evidence_id}/url -> {url, sha256, expires_in:300}`
- `endpoint: GET /api/v1/evidence/{evidence_id}/custody -> manifest + hash + ordered access chain`
- `endpoint: POST /api/v1/evidence/{evidence_id}/unseal (supervisor only, raw bucket)`
- `endpoint: POST /api/v1/audit/verify (auditor) -> chain verification result`
- `endpoint: GET /api/v1/audit (auditor all, supervisor own-zone)`
- `endpoint: POST /api/v1/disputes (public, 3/hr per IP)`
- `endpoint: POST /api/v1/edge/heartbeat (HMAC-signed, every 10s)`
- `endpoint: POST /api/v1/ingest/detections (HMAC-signed)`
- `endpoint: GET /api/v1/health (liveness), GET /api/v1/health/ready (DB+storage+realtime)`
- `endpoint: GET /api/v1/ops/metrics (operator dashboard tiles)`
- `http header: X-Justification (>=20 chars) required on all person-revealing endpoints`
- `http headers (ingest): X-Edge-Id, X-Timestamp, X-Nonce, X-Signature = HMAC-SHA256(secret, '{ts}.{nonce}.' + raw_body)`
- `env var: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (server only, never NEXT_PUBLIC_)`
- `env var: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY (only permitted NEXT_PUBLIC_ secrets)`
- `env var: EDGE_HMAC_SECRET_<worker_id>`
- `env var: MODEL_SHA256_<model_name> (weights pinned by hash, verified at load)`
- `audit action taxonomy (closed set): auth.login, auth.login_failed, plate.search, plate_read.view, evidence.view, evidence.download, evidence.unseal_raw, registry.lookup, watchlist.create, watchlist.update, watchlist.expire, alert.confirm, alert.dismiss, alert.escalate, dispute.open, dispute.resolve, export.csv, user.role_change, config.update, retention.purge, camera.offline`
- `threshold: OCR auto-accept mean_char_conf >= 0.85 AND min_char_conf >= 0.60 AND regex valid; 0.60-0.85 -> review_queue; <0.60 -> drop`
- `threshold: cloned-plate implied speed > 150 km/h AND both reads conf >= 0.92 AND corroborating signal (class/colour mismatch or >250 km/h)`
- `threshold: alert expiry 72 h; signed URL TTL 300 s; heartbeat 10 s, degraded 60 s, offline 180 s`
- `threshold: drift alert when 24h correction rate > baseline + 5 pp with n >= 100`
- `threshold: fairness gate max-min vehicle-class accuracy gap <= 10 pp; 2W exact match >= 0.85; suppress cells with n < 50`
- `rate limits: plates/search 60/hr 500/day; distinct plates/day 200 investigator 500 supervisor; registry/lookup 20/day; auth/login 5 per 15 min per IP+email; disputes 3/hr per IP; evidence url 120/hr`
- `file: /docs/dpia.md (DPIA-lite artefact)`
- `file: scripts/check-no-secrets.sh (CI gate against service_role in client bundle)`
- `file: scripts/eval_stratified.py (per-class accuracy table with Wilson CIs)`
- `file: apps/edge/redact.py :: redact_frame(frame, person_boxes, face_boxes, plate_box)`
- `file: apps/api/deps.py :: require_role(*allowed), require_reason(x_justification)`
- `file: apps/api/security/hmac_ingest.py :: verify_ingest(...)`
- `python helper: audit(db, actor, action, target_type, target_id, reason, request, payload) — must be called inside the same txn as the read it logs`

## Appendix — MVP vs Stretch

- MVP: app_role enum, app_user table, Supabase custom access token hook injecting app_role + zones claims
- MVP: RLS policies plate_read_zone_scoped, evidence_meta_read, watchlist_write; storage buckets private with zero public policy
- MVP: audit_log with hash-chain trigger, append-only trigger, revoked UPDATE/DELETE grants, and public.audit_verify()
- MVP: auditor 'Verify chain' screen calling POST /api/v1/audit/verify — the live tamper demo
- MVP: registry schema separation with no authenticated grant; POST /api/v1/registry/lookup writing registry_lookup + audit_log in one transaction
- MVP: watchlist expires_at NOT NULL with 90-day CHECK; matcher queries only the partial index where is_active and expires_at > now()
- MVP: person/face Gaussian blur in apps/edge/redact.py before any upload to the evidence bucket
- MVP: retention job public.run_retention() on pg_cron; 15-min edge ring buffer only, no continuous video persisted
- MVP: HMAC-signed ingest and heartbeat with 300 s replay window and nonce cache
- MVP: parameterised plate search with normalisation, 4-character minimum and pg_trgm fuzzy match
- MVP: slowapi rate limits on search/login/disputes/evidence-url plus search_quota distinct-plate daily ceiling
- MVP: scripts/check-no-secrets.sh in CI — build fails if service_role reaches the client bundle
- MVP: camera heartbeat + degraded/offline state machine + camera_outage rows + ops dashboard tiles
- MVP: alert.status pending_review, 72 h expires_at, mandatory human confirm; no code path from detector to outbound action
- MVP: confidence gating and confusion-neighbour suppression before any cloned-plate alert
- MVP: stratified accuracy table (vehicle class x plate background x lighting x condition x camera) with Wilson CIs and n<50 suppression
- MVP: /docs/dpia.md one-page DPIA-lite with named Grievance Officer
- MVP: system_config.ingest_enabled and camera.is_enabled kill switches, both audited
- MVP: nightly pg_dump to disk + backups bucket, plus one timed restore drill before the demo
- STRETCH: audit_anchor nightly head-hash publication to an external channel
- STRETCH: single-use signed-URL nonces (evidence_grant table) instead of TTL alone
- STRETCH: PDF custody receipt rendering for GET /api/v1/evidence/{id}/custody
- STRETCH: dedicated ONNX face-detection model on CPU replacing the upper-35%-of-person-box blur proxy
- STRETCH: 4-eyes supervisor approval when a user exceeds 5 registry lookups in 10 minutes
- STRETCH: gitleaks pre-commit hook and Dependabot
- STRETCH: Redis-backed distributed rate limiting and nonce store replacing in-process dicts
- STRETCH: citizen-facing dispute status page keyed by ALT-YYYY-NNNNNN reference

## Appendix — Risks

- Hash-chain serialisation: concurrent audit_log inserts without pg_advisory_xact_lock produce two rows claiming the same prev_hash and audit_verify() reports a false break. Mitigation: the advisory lock is inside the BEFORE INSERT trigger; under heavy write load this serialises audit writes — acceptable at demo volume, but if audit insert latency shows up in p95, batch non-security events (plate_read.view) or move to a per-actor chain.
- Audit logging that is not transactional with the read it describes can be bypassed by a crash or an exception between the two statements. Mitigation: audit() and the data read must share one transaction; code review every endpoint that returns personal data for this.
- RLS alone does not stop a service_role key leak. Mitigation: the CI bundle grep, plus keeping every service-role call inside FastAPI, plus buckets with no public policy so a leaked anon token buys nothing.
- Face blur cost on CPU: blurring every person box per frame at 15 fps across 4 replayed streams on a 16 GB / RX 6500M box can eat the frame budget. Mitigation: blur only on the evidence-crop path (a few frames per event), never on the live inference path.
- Confusion-neighbour suppression can hide genuine clones when the real clone's plate happens to be one edit away from a co-located vehicle. Mitigation: suppressed alerts are not deleted — they go to review_queue with reason 'confusion_neighbour', so a human still sees them.
- Retention job deleting storage objects and DB rows can desynchronise (row gone, object orphaned, or vice versa). Mitigation: delete objects first, then rows, and run a weekly orphan reconciliation query against the bucket listing.
- The 150 km/h clone threshold depends on synchronised clocks; the demo's controlled time offsets and any NTP drift on the edge box will corrupt implied speed. Mitigation: record clock_offset_ms in every heartbeat, refuse to raise speed-derived alerts when either camera's offset exceeds 500 ms, and run w32tm sync before the demo.
- A single 'accuracy' number quoted on stage invites an unanswerable follow-up. Mitigation: the stratified table is the only accuracy artefact allowed in the deck; the two-wheeler cell is stated explicitly even if it is the worst.
- Rate limits held in an in-process dict reset on API restart and do not hold across workers. Mitigation: the daily distinct-plate ceiling lives in Postgres (search_quota), which is the limit that actually matters for exfiltration; the per-minute slowapi limits are best-effort.
- Legal overclaim risk: asserting a specific DPDP exemption notification, DPDP Rules commencement date, or CCTNS integration approval that does not exist. Mitigation: every such claim is marked Verify in the document and must be softened to 'subject to notification' in the pitch.

## Appendix — Open Questions

- Is the deploying agency actually a notified instrumentality under DPDP s.17(2)(a), or must the system operate under full Chapter II/III obligations? This changes whether the Data Principal rights endpoints (access/erasure) are mandatory or voluntary.
- Who is the named approving officer for watchlist entries in the demo narrative, and does the team want a two-person rule on court_order-purpose entries?
- Registry source for the demo: fully synthetic CSV, or a mocked VAHAN-shaped API? The contract registry.vehicle_owner assumes a local table; a live external API would change the lookup latency and the audit payload.
- Retention numbers (30/90/365 days) are engineering defaults, not policy — confirm whether any state SOP or tender document specifies different figures the judges may know.
- Does the dispute path need to be reachable without authentication in the demo, and if so what abuse control replaces captcha (which the team should not implement)?
- Should the auditor role be able to see the reason text verbatim, or only its presence? Verbatim is more useful for oversight but the reason field may itself contain case-sensitive detail.
