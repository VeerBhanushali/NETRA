"""Alert triage."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query

from .. import db, events
from ..db import utcnow
from ..schemas import AlertUpdate

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
def list_alerts(
    status: str | None = Query(None, description="new|acknowledged|investigating|resolved|false_positive"),
    severity: str | None = None,
    alert_type: str | None = None,
    plate: str | None = None,
    limit: int = Query(100, le=500),
    cursor: int | None = Query(None, description="Return alerts with id < cursor"),
) -> dict:
    """Cursor pagination, not offset.

    The alerts table only grows, and OFFSET makes the database walk and
    discard every skipped row — page 50 gets slower than page 1. A cursor
    on the primary key is a constant-cost index seek.
    """
    where, params = [], []
    for col, val in (("status", status), ("severity", severity),
                     ("alert_type", alert_type), ("plate", plate)):
        if val:
            where.append(f"a.{col} = ?")
            params.append(val.upper() if col == "plate" else val)
    if cursor:
        where.append("a.id < ?")
        params.append(cursor)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)

    rows = db.query(
        "SELECT a.*, c.name AS camera_name, c.lat, c.lon"
        f" FROM alerts a LEFT JOIN cameras c ON c.id = a.camera_id {clause}"
        " ORDER BY a.id DESC LIMIT ?",
        params,
    )
    for r in rows:
        r["evidence"] = json.loads(r.pop("evidence_json") or "{}")

    return {
        "count": len(rows),
        "results": rows,
        # Null when the page is not full, so the client knows to stop.
        "next_cursor": rows[-1]["id"] if len(rows) == limit else None,
    }


@router.get("/summary")
def alert_summary() -> dict:
    """Counts for the dashboard header tiles."""
    by_sev = db.query(
        "SELECT severity, COUNT(*) AS n FROM alerts WHERE status = 'new'"
        " GROUP BY severity")
    by_type = db.query(
        "SELECT alert_type, COUNT(*) AS n FROM alerts GROUP BY alert_type ORDER BY n DESC")
    return {
        "open_by_severity": {r["severity"]: r["n"] for r in by_sev},
        "by_type": {r["alert_type"]: r["n"] for r in by_type},
        "open_total": sum(r["n"] for r in by_sev),
    }


@router.get("/{alert_id}")
def get_alert(alert_id: int) -> dict:
    row = db.query_one(
        "SELECT a.*, c.name AS camera_name, c.lat, c.lon"
        " FROM alerts a LEFT JOIN cameras c ON c.id = a.camera_id WHERE a.id = ?",
        (alert_id,))
    if not row:
        raise HTTPException(status_code=404, detail="alert not found")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")

    # Surrounding context: the vehicle's other recent sightings, so the
    # investigator sees the pattern, not just the single event.
    if row["plate"]:
        row["recent_sightings"] = db.query(
            "SELECT s.seen_at, s.camera_id, c.name AS camera_name, c.lat, c.lon"
            " FROM sightings s JOIN cameras c ON c.id = s.camera_id"
            " WHERE s.plate = ? ORDER BY s.seen_at DESC LIMIT 20", (row["plate"],))
    return row


@router.patch("/{alert_id}")
def update_alert(alert_id: int, body: AlertUpdate) -> dict:
    """Move an alert through its lifecycle.

    Every transition is audited. An operator marking something a false
    positive is the most valuable signal the system produces — it is how
    thresholds get tuned — so it must never be silently discarded.
    """
    current = db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    if not current:
        raise HTTPException(status_code=404, detail="alert not found")

    now = utcnow()
    fields = ["status = ?"]
    params: list = [body.status]
    if body.status == "acknowledged":
        fields += ["acknowledged_by = ?", "acknowledged_at = ?"]
        params += [body.actor, now]
    if body.status in ("resolved", "false_positive"):
        fields.append("resolved_at = ?")
        params.append(now)
    params.append(alert_id)

    db.execute(f"UPDATE alerts SET {', '.join(fields)} WHERE id = ?", params)
    db.audit(body.actor, f"alert_{body.status}", str(alert_id), body.note,
             json.dumps({"from": current["status"], "to": body.status}))

    updated = db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    events.publish("alert.updated", updated)
    return updated
