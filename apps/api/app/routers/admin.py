"""Cameras, zones, watchlist, review queue, audit trail and dashboard stats."""
from __future__ import annotations

import json
import time
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query

from .. import config, db, events, rules
from ..db import parse_ts, utcnow
from ..schemas import ReviewDecision, WatchlistIn

router = APIRouter(tags=["console"])


# --- cameras & zones --------------------------------------------------

@router.get("/cameras")
def list_cameras() -> dict:
    """Camera registry with a liveness signal.

    `is_streaming` is derived from the age of the annotated frame the edge
    worker writes, not from the database. A camera row can exist with no
    worker attached, and the wall must be able to tell the difference —
    otherwise it shows dead tiles that look like failed cameras.

    rtsp_url is deliberately never selected — camera credentials must not
    reach the browser.
    """
    rows = db.query(
        "SELECT c.id, c.name, c.lat, c.lon, c.bearing_deg, c.zone_id, c.road_name,"
        "       c.is_active, z.name AS zone_name, z.kind AS zone_kind,"
        "       (SELECT MAX(s.seen_at) FROM sightings s WHERE s.camera_id = c.id)"
        "         AS last_sighting_at,"
        "       (SELECT COUNT(*) FROM sightings s WHERE s.camera_id = c.id)"
        "         AS sighting_count"
        " FROM cameras c LEFT JOIN zones z ON z.id = c.zone_id"
        " ORDER BY c.id")

    live_dir = config.EVIDENCE_DIR / "live"
    now = time.time()
    for r in rows:
        frame = live_dir / f"{r['id']}.jpg"
        try:
            age = now - frame.stat().st_mtime
        except OSError:
            age = None
        # A frame older than this means the worker died or was never
        # started; treat it as not streaming rather than showing a frozen
        # image as though it were live.
        r["is_streaming"] = age is not None and age < 30
        r["frame_age_s"] = round(age, 1) if age is not None else None

    return {"count": len(rows),
            "streaming": sum(1 for r in rows if r["is_streaming"]),
            "results": rows}


@router.get("/zones")
def list_zones() -> dict:
    rows = db.query("SELECT * FROM zones ORDER BY name")
    for r in rows:
        r["polygon"] = json.loads(r.pop("polygon_json") or "[]")
    return {"count": len(rows), "results": rows}


# --- watchlist --------------------------------------------------------

@router.get("/watchlist")
def list_watchlist(include_inactive: bool = False) -> dict:
    clause = "" if include_inactive else "WHERE is_active = 1"
    rows = db.query(f"SELECT * FROM watchlist {clause} ORDER BY added_at DESC")
    return {"count": len(rows), "results": rows}


@router.post("/watchlist")
def add_watchlist(body: WatchlistIn) -> dict:
    """Add a plate to the watchlist.

    Adding a vehicle to a police watchlist is a consequential act, so it
    is audited with the stated reason and carries an expiry. Entries that
    never expire are how a watchlist becomes permanent surveillance.
    """
    plate = body.plate.upper().replace(" ", "").replace("-", "")
    db.execute(
        "INSERT INTO watchlist (plate, reason, severity, case_ref, added_by,"
        " added_at, expires_at, is_active) VALUES (?,?,?,?,?,?,?,1)"
        " ON CONFLICT(plate) DO UPDATE SET reason = excluded.reason,"
        "   severity = excluded.severity, case_ref = excluded.case_ref,"
        "   expires_at = excluded.expires_at, is_active = 1",
        (plate, body.reason, body.severity, body.case_ref, body.added_by,
         utcnow(), body.expires_at),
    )
    db.audit(body.added_by, "watchlist_add", plate, body.reason)
    return db.query_one("SELECT * FROM watchlist WHERE plate = ?", (plate,))


@router.delete("/watchlist/{plate}")
def remove_watchlist(plate: str, actor: str = "operator", reason: str | None = None) -> dict:
    """Deactivate rather than delete — the record that a vehicle was once
    watched is itself accountability evidence."""
    plate = plate.upper()
    db.execute("UPDATE watchlist SET is_active = 0 WHERE plate = ?", (plate,))
    db.audit(actor, "watchlist_remove", plate, reason)
    return {"plate": plate, "is_active": False}


# --- review queue (human in the loop) ---------------------------------

@router.get("/review")
def list_review(status: str = "pending", limit: int = Query(50, le=200)) -> dict:
    rows = db.query(
        "SELECT r.*, c.name AS camera_name FROM review_queue r"
        " JOIN cameras c ON c.id = r.camera_id"
        " WHERE r.status = ? ORDER BY r.created_at ASC LIMIT ?",
        (status, limit))
    for r in rows:
        r["candidates"] = json.loads(r.pop("candidates_json") or "[]")
    return {"count": len(rows), "results": rows}


@router.post("/review/{review_id}")
def decide_review(review_id: int, body: ReviewDecision) -> dict:
    """Resolve one low-confidence read.

    A confirmed or corrected plate is promoted into a real sighting and
    then run through the rules engine — so a human correction can itself
    raise a watchlist hit. Rejection records that the read was garbage,
    which is what lets the correction rate act as a live accuracy metric.
    """
    item = db.query_one("SELECT * FROM review_queue WHERE id = ?", (review_id,))
    if not item:
        raise HTTPException(status_code=404, detail="review item not found")
    if item["status"] != "pending":
        raise HTTPException(status_code=409, detail="already reviewed")

    final_plate = None
    if body.decision == "confirmed":
        final_plate = item["best_guess"]
    elif body.decision == "corrected":
        if not body.corrected_plate:
            raise HTTPException(status_code=400,
                                detail="corrected_plate required when correcting")
        final_plate = body.corrected_plate.upper().replace(" ", "").replace("-", "")

    db.execute(
        "UPDATE review_queue SET status = ?, corrected_plate = ?, reviewed_by = ?,"
        " reviewed_at = ? WHERE id = ?",
        (body.decision, final_plate, body.reviewed_by, utcnow(), review_id))
    db.audit(body.reviewed_by, f"review_{body.decision}", final_plate or item["best_guess"],
             f"was {item['best_guess']} @ {item['confidence']:.2f}")

    alerts: list[dict] = []
    stored = None
    if final_plate:
        row_id = db.insert(
            "INSERT INTO sightings (plate, camera_id, seen_at, confidence, track_id,"
            " frame_count, crop_path, source, dedupe_key, created_at)"
            " VALUES (?,?,?,?,?,?,?,'manual',?,?)",
            (final_plate, item["camera_id"], item["seen_at"], 1.0, item["track_id"],
             item["frame_count"], item["crop_path"], f"review:{review_id}", utcnow()))
        if row_id:
            db.execute(
                "INSERT INTO vehicles (plate, first_seen_at, last_seen_at, sighting_count)"
                " VALUES (?,?,?,1) ON CONFLICT(plate) DO UPDATE SET"
                " last_seen_at = excluded.last_seen_at,"
                " sighting_count = vehicles.sighting_count + 1",
                (final_plate, item["seen_at"], item["seen_at"]))
            stored = db.query_one("SELECT * FROM sightings WHERE id = ?", (row_id,))
            events.publish("sighting.created", stored)
            for a in rules.evaluate(stored):
                alerts.append(a)
                events.publish("alert.created", a)

    events.publish("review.decided", {"id": review_id, "decision": body.decision})
    return {"id": review_id, "decision": body.decision, "plate": final_plate,
            "sighting": stored, "alerts": alerts}


# --- audit ------------------------------------------------------------

@router.get("/audit")
def list_audit(limit: int = Query(100, le=500), action: str | None = None) -> dict:
    where, params = [], []
    if action:
        where.append("action = ?")
        params.append(action)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    rows = db.query(f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ?", params)
    return {"count": len(rows), "results": rows}


# --- stats ------------------------------------------------------------

@router.get("/stats")
def stats() -> dict:
    """Everything the dashboard header needs, in one round trip."""
    now = utcnow()
    day_ago = (parse_ts(now) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hour_ago = (parse_ts(now) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def scalar(sql: str, params=()) -> int:
        row = db.query_one(sql, params)
        return int(list(row.values())[0]) if row else 0

    total = scalar("SELECT COUNT(*) FROM sightings")
    reviewed = scalar("SELECT COUNT(*) FROM review_queue WHERE status IN"
                      " ('confirmed','corrected')")
    corrected = scalar("SELECT COUNT(*) FROM review_queue WHERE status = 'corrected'")

    return {
        "generated_at": now,
        "sightings_total": total,
        "sightings_24h": scalar("SELECT COUNT(*) FROM sightings WHERE seen_at >= ?", (day_ago,)),
        "sightings_1h": scalar("SELECT COUNT(*) FROM sightings WHERE seen_at >= ?", (hour_ago,)),
        "vehicles_total": scalar("SELECT COUNT(*) FROM vehicles"),
        "cameras_total": scalar("SELECT COUNT(*) FROM cameras"),
        "cameras_active": scalar("SELECT COUNT(*) FROM cameras WHERE is_active = 1"),
        "alerts_open": scalar("SELECT COUNT(*) FROM alerts WHERE status = 'new'"),
        "alerts_total": scalar("SELECT COUNT(*) FROM alerts"),
        "watchlist_active": scalar("SELECT COUNT(*) FROM watchlist WHERE is_active = 1"),
        "review_pending": scalar("SELECT COUNT(*) FROM review_queue WHERE status = 'pending'"),
        "accuracy": {
            # Share of reads confident enough to publish without a human.
            "auto_accept_rate": round(total / (total + scalar(
                "SELECT COUNT(*) FROM review_queue")) , 4) if total else 0.0,
            # Operator corrections are the live accuracy signal: if this
            # climbs, the model has drifted or conditions have changed.
            "operator_correction_rate": round(corrected / reviewed, 4) if reviewed else 0.0,
            "reviewed_count": reviewed,
            "auto_accept_threshold": config.AUTO_ACCEPT_CONFIDENCE,
        },
        "live_subscribers": events.subscriber_count(),
    }


@router.get("/stats/timeline")
def timeline(hours: int = Query(24, le=168)) -> dict:
    """Hourly sighting counts for the dashboard sparkline."""
    since = (parse_ts(utcnow()) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = db.query(
        "SELECT substr(seen_at, 1, 13) AS hour, COUNT(*) AS n FROM sightings"
        " WHERE seen_at >= ? GROUP BY hour ORDER BY hour", (since,))
    return {"buckets": rows}
