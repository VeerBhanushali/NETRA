"""Ingest — the only write path from the edge workers into the platform."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import config, db, events, rules
from ..db import utcnow
from ..schemas import AnomalyIn, IngestBatch, IngestResult, SightingIn

router = APIRouter(prefix="/ingest", tags=["ingest"])


def require_ingest_token(x_ingest_token: str = Header(default="")) -> None:
    """Shared-secret auth for workers.

    Deliberately separate from operator auth: a camera worker can write
    sightings but must never be able to read the watchlist or an
    investigation. Compared with compare_digest so a wrong token cannot
    be recovered by timing the response.
    """
    import hmac
    if not hmac.compare_digest(x_ingest_token, config.INGEST_TOKEN):
        raise HTTPException(status_code=401, detail="invalid ingest token")


def _dedupe_key(s: SightingIn) -> str:
    """Stable identity for one vehicle pass, so a retried publish is a
    no-op instead of a duplicate row."""
    if s.dedupe_key:
        return s.dedupe_key
    basis = f"{s.plate}|{s.camera_id}|{s.track_id or ''}|{s.seen_at}"
    return hashlib.sha1(basis.encode()).hexdigest()


def _store_sighting(s: SightingIn) -> tuple[str, dict | None]:
    """Route one voted plate by confidence.

    This is where the accuracy policy is actually enforced:
      >= AUTO_ACCEPT  -> published as fact
      >= REVIEW_MIN   -> a human decides; nothing is published yet
      below           -> discarded; a coin-flip plate is worse than silence
    """
    if s.confidence < config.REVIEW_CONFIDENCE:
        return "discarded", None

    if s.confidence < config.AUTO_ACCEPT_CONFIDENCE:
        candidates = sorted(
            ({"plate": r.raw_text, "score": round(r.confidence, 3)} for r in s.reads),
            key=lambda c: -c["score"],
        )[:5]
        db.insert(
            "INSERT INTO review_queue (track_id, camera_id, best_guess, confidence,"
            " candidates_json, crop_path, frame_count, seen_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (s.track_id or "", s.camera_id, s.plate, s.confidence,
             json.dumps(candidates), s.crop_path, s.frame_count, s.seen_at, utcnow()),
        )
        events.publish("review.queued", {"plate": s.plate, "camera_id": s.camera_id,
                                         "confidence": s.confidence})
        return "review", None

    key = _dedupe_key(s)
    row_id = db.insert(
        "INSERT INTO sightings (plate, camera_id, seen_at, confidence, track_id,"
        " frame_count, color, vehicle_type, crop_path, source, dedupe_key, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (s.plate, s.camera_id, s.seen_at, s.confidence, s.track_id, s.frame_count,
         s.color, s.vehicle_type, s.crop_path, s.source, key, utcnow()),
    )
    if row_id is None:
        return "duplicate", None       # idempotent replay

    # Keep the vehicle registry current.
    db.execute(
        "INSERT INTO vehicles (plate, first_seen_at, last_seen_at, sighting_count,"
        " color, vehicle_type) VALUES (?,?,?,1,?,?)"
        " ON CONFLICT(plate) DO UPDATE SET"
        "   last_seen_at   = excluded.last_seen_at,"
        "   sighting_count = vehicles.sighting_count + 1,"
        "   color          = COALESCE(excluded.color, vehicles.color),"
        "   vehicle_type   = COALESCE(excluded.vehicle_type, vehicles.vehicle_type)",
        (s.plate, s.seen_at, s.seen_at, s.color, s.vehicle_type),
    )

    # Raw per-frame reads: the evidence behind the voted plate.
    for r in s.reads:
        db.insert(
            "INSERT INTO plate_reads (track_id, camera_id, raw_text, confidence,"
            " quality, frame_ts) VALUES (?,?,?,?,?,?)",
            (s.track_id or "", s.camera_id, r.raw_text, r.confidence, r.quality, r.frame_ts),
        )

    stored = db.query_one("SELECT * FROM sightings WHERE id = ?", (row_id,))
    return "accepted", stored


@router.post("/sightings", response_model=IngestResult,
             dependencies=[Depends(require_ingest_token)])
def ingest_sightings(batch: IngestBatch) -> IngestResult:
    """Batch endpoint. Workers buffer locally and POST in small batches so
    a brief API outage costs latency, not data."""
    accepted = duplicates = review = discarded = 0
    fired: list[dict] = []

    for s in batch.sightings:
        if not db.query_one("SELECT id FROM cameras WHERE id = ?", (s.camera_id,)):
            raise HTTPException(status_code=400, detail=f"unknown camera {s.camera_id}")

        outcome, stored = _store_sighting(s)
        if outcome == "accepted" and stored:
            accepted += 1
            events.publish("sighting.created", stored)
            for alert in rules.evaluate(stored):
                fired.append(alert)
                events.publish("alert.created", alert)
        elif outcome == "duplicate":
            duplicates += 1
        elif outcome == "review":
            review += 1
        else:
            discarded += 1

    return IngestResult(accepted=accepted, duplicates=duplicates,
                        queued_for_review=review, discarded=discarded, alerts=fired)


@router.post("/anomaly", dependencies=[Depends(require_ingest_token)])
def ingest_anomaly(a: AnomalyIn) -> dict:
    """Behavioural / video-anomaly output.

    Hysteresis lives in the worker, not here: by the time an event
    reaches this endpoint it has already survived smoothing and a minimum
    duration, so a single noisy frame cannot create an alert.
    """
    row_id = db.insert(
        "INSERT INTO anomaly_events (camera_id, occurred_at, kind, score, clip_path,"
        " detail, source) VALUES (?,?,?,?,?,?,?)",
        (a.camera_id, a.occurred_at, a.kind, a.score, a.clip_path, a.detail, a.source),
    )
    cam = db.query_one("SELECT * FROM cameras WHERE id = ?", (a.camera_id,))
    cam_name = cam["name"] if cam else a.camera_id

    severity = "critical" if a.score >= 0.85 else "high" if a.score >= 0.7 else "medium"
    # Vehicles present at that camera in the surrounding minute — the link
    # between "something happened" and "who was there". Bounds are built in
    # Python: SQLite's datetime() emits 'YYYY-MM-DD HH:MM:SS', which would
    # never compare correctly against our stored 'YYYY-MM-DDTHH:MM:SSZ'.
    centre = db.parse_ts(a.occurred_at)
    lo = (centre - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = (centre + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    nearby = db.query(
        "SELECT plate, seen_at, confidence FROM sightings WHERE camera_id = ?"
        " AND seen_at BETWEEN ? AND ? ORDER BY seen_at DESC LIMIT 10",
        (a.camera_id, lo, hi),
    )

    alert = rules._raise(
        "anomaly", severity, None, a.camera_id, a.occurred_at,
        # "Weapon detected" is an assertion the model has not earned. Every
        # anomaly arriving here is evidence awaiting an operator, and the
        # title an operator reads first should say so.
        f"{a.kind.replace('_', ' ').title()} candidate — {cam_name}",
        a.detail or f"Behavioural model scored {a.score:.2f} for '{a.kind}' at {cam_name}.",
        {"kind": a.kind, "score": round(a.score, 3), "source": a.source,
         "clip_path": a.clip_path, "vehicles_present": nearby},
        dedupe_key=f"anomaly:{a.camera_id}:{a.kind}:"
                   f"{rules._cooldown_bucket(config.ALERT_COOLDOWN_S, a.occurred_at)}",
        confidence=a.score,
    )
    if alert:
        events.publish("alert.created", alert)
    return {"anomaly_id": row_id, "alert": alert}
