"""Person enrolment and face scanning.

Two operations, deliberately separated:

  ENROL  an authorised photo -> face template -> watchlist
  SCAN   a live frame -> faces -> candidates against that watchlist

A scan that matches nobody stores nothing. There is no code path here
that accumulates a general face database, and that is the single most
important property of this module (SIH26127 §13).

Every response calls a result a *candidate* with a similarity score. The
backend never asserts identity — an operator confirms or rejects.
"""
from __future__ import annotations

import base64
import io
import json
import uuid

import numpy as np
from fastapi import APIRouter, HTTPException

from .. import db, events
from ..db import utcnow
from ..schemas import FaceScanIn, PersonIn, PersonMatchDecision

router = APIRouter(prefix="/persons", tags=["persons"])
scan_router = APIRouter(prefix="/faces", tags=["faces"])

# The engine is expensive to construct (~40 s incl. first-run download),
# so it is built on first use and shared, not created per request.
_engine = None


def engine():
    global _engine
    if _engine is None:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "edge"))
        from edge.faces import FaceEngine
        _engine = FaceEngine(prefer_gpu=True)
    return _engine


def decode_image(data_url: str):
    """Accept a browser canvas data URL or raw base64."""
    import cv2
    raw = data_url.split(",", 1)[-1] if "," in data_url else data_url
    try:
        buf = base64.b64decode(raw)
    except Exception as exc:                               # noqa: BLE001
        raise HTTPException(400, f"image is not valid base64: {exc}") from exc
    arr = np.frombuffer(buf, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "could not decode image")
    return img


def load_gallery():
    """Rebuild the match gallery from active, unexpired enrolments.

    Rebuilt per scan rather than cached: a watchlist removal must take
    effect immediately, and at hackathon scale the cost is trivial.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "edge"))
    from edge.faces import Gallery

    g = Gallery()
    now = utcnow()
    rows = db.query(
        "SELECT f.person_id, f.embedding FROM person_faces f"
        " JOIN persons p ON p.id = f.person_id"
        " WHERE p.is_active = 1 AND (p.expires_at IS NULL OR p.expires_at > ?)",
        (now,))
    for r in rows:
        g.add(r["person_id"], np.frombuffer(r["embedding"], dtype=np.float32))
    return g


# ---------------------------------------------------------------- enrol

@router.post("")
def enrol_person(body: PersonIn) -> dict:
    """Register a person and their first face template.

    Enrolment is a consequential act, so it is audited with the stated
    case reference and carries an expiry.
    """
    img = decode_image(body.image)
    faces = engine().detect(img)
    if not faces:
        raise HTTPException(400, "no face found in that image")
    if len(faces) > 1:
        raise HTTPException(
            400, f"{len(faces)} faces found — enrol from a photo of one person")

    face = faces[0]
    if face["quality"] < 0.35:
        raise HTTPException(
            400, f"face quality too low ({face['quality']:.2f}) — move closer "
                 f"or improve lighting")

    pid = body.person_id or f"p-{uuid.uuid4().hex[:8]}"
    existing = db.query_one("SELECT id FROM persons WHERE id = ?", (pid,))
    if not existing:
        db.execute(
            "INSERT INTO persons (id, name, case_ref, category, added_by,"
            " added_at, expires_at, is_active) VALUES (?,?,?,?,?,?,?,1)",
            (pid, body.name, body.case_ref, body.category, body.added_by,
             utcnow(), body.expires_at))

    db.insert(
        "INSERT INTO person_faces (person_id, embedding, quality, source, created_at)"
        " VALUES (?,?,?,?,?)",
        (pid, face["embedding"].astype(np.float32).tobytes(),
         face["quality"], body.source, utcnow()))

    db.audit(body.added_by, "person_enrol", pid,
             f"{body.category}: {body.name} ({body.case_ref or 'no case ref'})")

    n = db.query_one(
        "SELECT COUNT(*) AS n FROM person_faces WHERE person_id = ?", (pid,))["n"]
    return {"person_id": pid, "name": body.name, "templates": n,
            "quality": face["quality"]}


@router.get("")
def list_persons(include_inactive: bool = False) -> dict:
    clause = "" if include_inactive else "WHERE p.is_active = 1"
    rows = db.query(
        "SELECT p.*, (SELECT COUNT(*) FROM person_faces f WHERE f.person_id = p.id)"
        "         AS templates,"
        "       (SELECT COUNT(*) FROM face_matches m WHERE m.person_id = p.id)"
        "         AS matches"
        f" FROM persons p {clause} ORDER BY p.added_at DESC")
    return {"count": len(rows), "results": rows}


@router.delete("/{person_id}")
def remove_person(person_id: str, actor: str = "operator",
                  reason: str | None = None) -> dict:
    """Deactivate and destroy the templates.

    The person record is retained (deactivated) as accountability
    evidence that someone was once enrolled, but the biometric templates
    are deleted outright — keeping a face template for someone no longer
    on a watchlist has no lawful purpose.
    """
    if not db.query_one("SELECT id FROM persons WHERE id = ?", (person_id,)):
        raise HTTPException(404, "person not found")
    db.execute("UPDATE persons SET is_active = 0 WHERE id = ?", (person_id,))
    db.execute("DELETE FROM person_faces WHERE person_id = ?", (person_id,))
    db.audit(actor, "person_remove", person_id, reason)
    return {"person_id": person_id, "is_active": False, "templates_deleted": True}


# ----------------------------------------------------------------- scan

@scan_router.post("/scan")
def scan(body: FaceScanIn) -> dict:
    """Match every face in a frame against the watchlist.

    Non-matching faces produce an embedding that is compared and then
    discarded. Nothing about them is written anywhere.
    """
    img = decode_image(body.image)
    faces = engine().detect(img)
    gallery = load_gallery()

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "apps" / "edge"))
    from edge.faces import MATCH_CONFIRM, MATCH_REVIEW

    results = []
    for f in faces:
        pid, score = gallery.best(f["embedding"])
        hit = None
        if pid and score >= MATCH_REVIEW:
            person = db.query_one("SELECT * FROM persons WHERE id = ?", (pid,))
            decision = "confirm" if score >= MATCH_CONFIRM else "review"
            hit = {"person_id": pid,
                   "name": person["name"] if person else pid,
                   "category": person["category"] if person else None,
                   "case_ref": person["case_ref"] if person else None,
                   "score": round(float(score), 4),
                   "decision": decision}

            if body.camera_id:
                mid = db.insert(
                    "INSERT INTO face_matches (person_id, camera_id, track_id,"
                    " score, frame_count, seen_at, status)"
                    " VALUES (?,?,?,?,?,?,'pending')",
                    (pid, body.camera_id, body.track_id or "scan",
                     float(score), body.frame_count, utcnow()))
                hit["match_id"] = mid
                events.publish("face.match_candidate", {
                    "match_id": mid, "person_id": pid,
                    "name": hit["name"], "score": hit["score"],
                    "camera_id": body.camera_id, "decision": decision})

        results.append({
            "box": list(f["box"]),
            "quality": f["quality"],
            "det_score": round(f["det_score"], 3),
            # None means: compared against the watchlist, no candidate,
            # embedding discarded.
            "match": hit,
        })

    db.audit(body.actor, "face_scan", body.camera_id,
             f"{len(faces)} face(s), {sum(1 for r in results if r['match'])} candidate(s)")
    return {"faces": len(faces), "gallery_size": len(gallery), "results": results}


@scan_router.get("/matches")
def list_matches(status: str = "pending", limit: int = 100) -> dict:
    rows = db.query(
        "SELECT m.*, p.name, p.category, p.case_ref FROM face_matches m"
        " LEFT JOIN persons p ON p.id = m.person_id"
        " WHERE m.status = ? ORDER BY m.seen_at DESC LIMIT ?",
        (status, min(limit, 500)))
    return {"count": len(rows), "results": rows}


@scan_router.post("/matches/{match_id}")
def decide_match(match_id: int, body: PersonMatchDecision) -> dict:
    """An operator confirms or rejects a candidate.

    Only a confirmation creates an alert. The model never escalates on its
    own — that is the difference between a lead and an accusation.
    """
    m = db.query_one("SELECT * FROM face_matches WHERE id = ?", (match_id,))
    if not m:
        raise HTTPException(404, "match not found")
    if m["status"] != "pending":
        raise HTTPException(409, "already reviewed")

    db.execute(
        "UPDATE face_matches SET status = ?, reviewed_by = ?, reviewed_at = ?"
        " WHERE id = ?", (body.decision, body.reviewed_by, utcnow(), match_id))
    db.audit(body.reviewed_by, f"face_match_{body.decision}",
             m["person_id"], f"score {m['score']:.3f}")

    alert = None
    if body.decision == "confirmed":
        person = db.query_one("SELECT * FROM persons WHERE id = ?", (m["person_id"],))
        cam = db.query_one("SELECT name FROM cameras WHERE id = ?", (m["camera_id"],))
        kind = "missing_person" if person and person["category"] == "missing" \
            else "wanted_person"
        row_id = db.insert(
            "INSERT INTO alerts (alert_type, severity, status, plate, camera_id,"
            " occurred_at, title, detail, evidence_json, confidence, dedupe_key,"
            " created_at) VALUES (?,?,'new',NULL,?,?,?,?,?,?,?,?)",
            (kind, "critical" if kind == "wanted_person" else "high",
             m["camera_id"], m["seen_at"],
             f"{(person or {}).get('name', m['person_id'])} identified"
             f" — {(cam or {}).get('name', m['camera_id'])}",
             f"Operator {body.reviewed_by} confirmed a face match at similarity "
             f"{m['score']:.3f} against enrolment "
             f"{(person or {}).get('case_ref') or m['person_id']}.",
             json.dumps({"person_id": m["person_id"], "similarity": m["score"],
                         "frames": m["frame_count"], "match_id": match_id,
                         "confirmed_by": body.reviewed_by}),
             float(m["score"]), f"face:{match_id}", utcnow()))
        alert = db.query_one("SELECT * FROM alerts WHERE id = ?", (row_id,))
        if alert:
            events.publish("alert.created", alert)

    return {"match_id": match_id, "decision": body.decision, "alert": alert}
