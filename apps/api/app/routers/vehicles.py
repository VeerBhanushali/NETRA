"""Vehicle search and trajectory reconstruction — the investigative core."""
from __future__ import annotations

from fastapi import APIRouter, Query

from .. import db

router = APIRouter(tags=["vehicles"])


@router.get("/vehicles/search")
def search_plates(
    q: str = Query(..., min_length=2, description="Full or partial plate"),
    limit: int = Query(25, le=200),
    actor: str = "operator",
    reason: str | None = Query(None, description="Why this search is being run"),
) -> dict:
    """Partial-plate search.

    Every call is written to the audit log *before* results are returned.
    "Who searched this plate and why" is the most important record in a
    surveillance system, so it must not depend on the request succeeding.
    """
    needle = q.upper().replace(" ", "").replace("-", "")
    db.audit(actor, "plate_search", needle, reason)

    rows = db.query(
        "SELECT v.plate, v.first_seen_at, v.last_seen_at, v.sighting_count,"
        "       v.color, v.vehicle_type,"
        "       EXISTS(SELECT 1 FROM watchlist w WHERE w.plate = v.plate"
        "              AND w.is_active = 1) AS on_watchlist,"
        "       (SELECT COUNT(*) FROM alerts a WHERE a.plate = v.plate) AS alert_count"
        " FROM vehicles v WHERE v.plate LIKE ?"
        " ORDER BY v.last_seen_at DESC LIMIT ?",
        (f"%{needle}%", limit),
    )
    return {"query": needle, "count": len(rows), "results": rows}


@router.get("/vehicles/{plate}")
def vehicle_detail(plate: str) -> dict:
    plate = plate.upper()
    vehicle = db.query_one("SELECT * FROM vehicles WHERE plate = ?", (plate,))
    watch = db.query_one(
        "SELECT * FROM watchlist WHERE plate = ? AND is_active = 1", (plate,))
    alerts = db.query(
        "SELECT * FROM alerts WHERE plate = ? ORDER BY occurred_at DESC LIMIT 50", (plate,))
    return {"plate": plate, "vehicle": vehicle, "watchlist": watch, "alerts": alerts}


@router.get("/vehicles/{plate}/trajectory")
def trajectory(
    plate: str,
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
    limit: int = Query(500, le=2000),
    actor: str = "operator",
) -> dict:
    """Chronological path of one vehicle across the camera network.

    Returns points in ascending time so the client can draw the polyline
    directly, plus per-leg distance and implied speed so the map can
    label each segment without a second round trip.
    """
    plate = plate.upper()
    db.audit(actor, "trajectory_view", plate)

    where = ["s.plate = ?"]
    params: list = [plate]
    if from_ts:
        where.append("s.seen_at >= ?")
        params.append(from_ts)
    if to_ts:
        where.append("s.seen_at <= ?")
        params.append(to_ts)
    params.append(limit)

    points = db.query(
        "SELECT s.id, s.seen_at, s.confidence, s.speed_kmh, s.crop_path,"
        "       s.camera_id, c.name AS camera_name, c.lat, c.lon, c.zone_id"
        " FROM sightings s JOIN cameras c ON c.id = s.camera_id"
        f" WHERE {' AND '.join(where)} ORDER BY s.seen_at ASC LIMIT ?",
        params,
    )

    # Per-leg geometry, computed once here rather than in the browser.
    legs = []
    for a, b in zip(points, points[1:]):
        metres = db.haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        seconds = (db.parse_ts(b["seen_at"]) - db.parse_ts(a["seen_at"])).total_seconds()
        legs.append({
            "from_camera": a["camera_id"],
            "to_camera": b["camera_id"],
            "distance_m": round(metres),
            "elapsed_s": round(seconds),
            "implied_kmh": round((metres / seconds) * 3.6, 1) if seconds > 0 else None,
        })

    return {
        "plate": plate,
        "point_count": len(points),
        "points": points,
        "legs": legs,
        "bounds": _bounds(points),
    }


def _bounds(points: list[dict]) -> dict | None:
    """Bounding box so the map can fit the route on first paint instead of
    animating from a default viewport."""
    if not points:
        return None
    lats = [p["lat"] for p in points]
    lons = [p["lon"] for p in points]
    return {"min_lat": min(lats), "max_lat": max(lats),
            "min_lon": min(lons), "max_lon": max(lons)}


@router.get("/sightings")
def recent_sightings(
    limit: int = Query(100, le=1000),
    camera_id: str | None = None,
    since: str | None = None,
) -> dict:
    """The live feed backing the dashboard table."""
    where, params = [], []
    if camera_id:
        where.append("s.camera_id = ?")
        params.append(camera_id)
    if since:
        where.append("s.seen_at > ?")
        params.append(since)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)

    rows = db.query(
        "SELECT s.*, c.name AS camera_name, c.lat, c.lon,"
        "       EXISTS(SELECT 1 FROM watchlist w WHERE w.plate = s.plate"
        "              AND w.is_active = 1) AS on_watchlist"
        f" FROM sightings s JOIN cameras c ON c.id = s.camera_id {clause}"
        " ORDER BY s.seen_at DESC LIMIT ?",
        params,
    )
    return {"count": len(rows), "results": rows}
