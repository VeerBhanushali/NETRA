"""Phone-as-camera ingest.

A browser on a phone becomes a NETRA camera: it posts JPEG frames here,
they appear on the live wall like any other feed, and they can be run
through face recognition on the way in.

Why a browser rather than an app: no store, no install, no signing, and
the same code runs on Android and iOS. The cost is that camera access
requires a secure context — see scripts/connect.py.
"""
from __future__ import annotations

import base64
import json
import os
import time

from fastapi import APIRouter, HTTPException

from .. import config, db, events
from ..db import utcnow
from ..schemas import DeviceFrameIn

router = APIRouter(prefix="/devices", tags=["devices"])

LIVE_DIR = config.EVIDENCE_DIR / "live"


def _decode(data_url: str) -> bytes:
    raw = data_url.split(",", 1)[-1] if "," in data_url else data_url
    try:
        return base64.b64decode(raw)
    except Exception as exc:                               # noqa: BLE001
        raise HTTPException(400, f"frame is not valid base64: {exc}") from exc


@router.post("/frame")
def push_frame(body: DeviceFrameIn) -> dict:
    """Accept one frame from a phone and publish it as a camera feed.

    The camera row is created on first frame rather than requiring
    pre-registration: a phone that walks up to the system should just
    work, and an operator can rename it afterwards.
    """
    cam_id = body.camera_id
    cam = db.query_one("SELECT id FROM cameras WHERE id = ?", (cam_id,))
    if not cam:
        # An unlocated camera at (0, 0) is a marker in the Atlantic, which
        # looks like a bug rather than a missing GPS fix. Put it where the
        # rest of the network is until it reports a real position.
        if body.lat is None or body.lon is None:
            centre = db.query_one(
                "SELECT AVG(lat) AS lat, AVG(lon) AS lon FROM cameras"
                " WHERE lat != 0 OR lon != 0")
            fallback_lat = (centre or {}).get("lat") or 0.0
            fallback_lon = (centre or {}).get("lon") or 0.0
        else:
            fallback_lat = fallback_lon = 0.0

        db.execute(
            "INSERT INTO cameras (id, name, lat, lon, bearing_deg, zone_id,"
            " road_name, is_active, rtsp_url, created_at)"
            " VALUES (?,?,?,?,NULL,NULL,NULL,1,NULL,?)",
            (cam_id, body.name or f"Mobile {cam_id}",
             body.lat if body.lat is not None else fallback_lat,
             body.lon if body.lon is not None else fallback_lon, utcnow()))
        db.audit(body.actor, "device_register", cam_id,
                 f"mobile camera joined: {body.name or cam_id}")
        events.publish("camera.health",
                       {"camera_id": cam_id, "status": "online", "source": "mobile"})
    elif body.lat is not None and body.lon is not None:
        # Phones move. Keep the map honest.
        db.execute("UPDATE cameras SET lat = ?, lon = ? WHERE id = ?",
                   (body.lat, body.lon, cam_id))

    jpg = _decode(body.image)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LIVE_DIR / f".{cam_id}.tmp.jpg"
    dst = LIVE_DIR / f"{cam_id}.jpg"
    try:
        tmp.write_bytes(jpg)
        for attempt in range(3):
            try:
                os.replace(tmp, dst)
                break
            except PermissionError:
                # Windows refuses the rename while the wall is reading it.
                time.sleep(0.04 * (attempt + 1))
    except Exception as exc:                               # noqa: BLE001
        raise HTTPException(500, f"could not store frame: {exc}") from exc

    out: dict = {"camera_id": cam_id, "bytes": len(jpg),
                 "faces": [], "plates": [], "threat": None, "objects": []}

    # `analyse` is a comma list: face, plate, threat, objects. The phone
    # chooses, because it is the thing holding the battery.
    want = {w.strip() for w in (body.analyse or "").split(",") if w.strip()} - {"none"}

    # Analysis is opt-in per frame. The phone decides how often to ask,
    # because it knows its own battery and network.
    if want:
        import numpy as np
        import cv2

        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "frame is not a decodable image")
    else:
        img = None

    if img is not None and "face" in want:
        from .faces_router import engine, load_gallery
        from ..edge_path import edge_import

        gallery = load_gallery()
        faces_mod = edge_import("edge.faces")

        for f in engine().detect(img):
            ranked = gallery.ranked(f["embedding"], k=3)
            pid, score = (ranked[0] if ranked else (None, 0.0))
            hit = None
            if pid and score >= faces_mod.MATCH_REVIEW:
                p = db.query_one("SELECT * FROM persons WHERE id = ?", (pid,))
                hit = {"person_id": pid,
                       "name": p["name"] if p else pid,
                       "category": (p or {}).get("category"),
                       "case_ref": (p or {}).get("case_ref"),
                       "expires_at": (p or {}).get("expires_at"),
                       "score": round(float(score), 4),
                       "decision": ("confirm" if score >= faces_mod.MATCH_CONFIRM
                                    else "review")}
                mid = db.insert(
                    "INSERT INTO face_matches (person_id, camera_id, track_id,"
                    " score, frame_count, seen_at, status)"
                    " VALUES (?,?,?,?,1,?,'pending')",
                    (pid, cam_id, "mobile", float(score), utcnow()))
                hit["match_id"] = mid
                events.publish("face.match_candidate", {**hit, "camera_id": cam_id})
            x1, y1, x2, y2 = f["box"]
            # Enough detail for an operator to judge the match rather than
            # take it on faith: how big and how sharp the face was, what
            # the detector thought of it, and — the part that actually
            # decides it — what the runner-up scored.
            out["faces"].append({
                "box": list(f["box"]),
                "width_px": x2 - x1,
                "height_px": y2 - y1,
                "quality": round(float(f["quality"]), 3),
                "det_score": round(float(f["det_score"]), 3),
                "usable": f["quality"] >= 0.25,
                "candidates": [
                    {"person_id": cid,
                     "name": (db.query_one("SELECT name FROM persons WHERE id = ?",
                                           (cid,)) or {}).get("name", cid),
                     "score": round(float(cs), 4)}
                    for cid, cs in ranked],
                "gallery_size": len(gallery),
                "match": hit,
            })

    if img is not None and want & {"plate", "threat", "objects"}:
        from ..edge_path import edge_import

        snapshot = edge_import("edge.snapshot")
        try:
            found = snapshot.analyse(img, want & {"plate", "threat", "objects"})
        except Exception as exc:                           # noqa: BLE001
            # A model that fails to load must not take the live wall down
            # with it: the frame itself has already been stored.
            out["analysis_error"] = str(exc)
            return out

        for pr in found["plates"]:
            # One frame is not enough evidence to publish a sighting —
            # the whole system rests on temporal voting across frames. A
            # handheld read is a candidate for a human, nothing more.
            rid = db.insert(
                "INSERT INTO review_queue (track_id, camera_id, best_guess,"
                " confidence, candidates_json, crop_path, frame_count,"
                " seen_at, created_at)"
                " VALUES (?,?,?,?,?,NULL,1,?,?)",
                (f"{cam_id}:mobile", cam_id, pr["plate"], pr["confidence"],
                 json.dumps([{"plate": pr["plate"], "score": pr["confidence"]}]),
                 utcnow(), utcnow()))
            pr["review_id"] = rid
            events.publish("review.queued",
                           {"id": rid, "camera_id": cam_id,
                            "best_guess": pr["plate"],
                            "confidence": pr["confidence"], "source": "mobile"})
            db.audit(body.actor, "mobile_plate_read", cam_id,
                     f"{pr['plate']} @ {pr['confidence']:.2f} (single frame)")
        out["plates"] = found["plates"]
        out["objects"] = found.get("objects", [])

        t = found.get("threat")
        if t and t["decision"] != "none":
            db.insert(
                "INSERT INTO anomaly_events (camera_id, occurred_at, kind,"
                " score, clip_path, detail, source)"
                " VALUES (?,?,?,?,NULL,?,'heuristic')",
                (cam_id, utcnow(), "weapon", t["score"], json.dumps(t)))
            events.publish("incident.detected",
                           {"type": "threat_candidate", "camera_id": cam_id,
                            "score": t["score"], "evidence": t["contributions"],
                            "state": "DETECTED"})
        out["threat"] = t

    return out


@router.get("")
def list_devices() -> dict:
    """Mobile cameras, identified by having no RTSP URL and a mobile id."""
    rows = db.query(
        "SELECT id, name, lat, lon, is_active FROM cameras"
        " WHERE id LIKE 'phone-%' ORDER BY id")
    now = time.time()
    for r in rows:
        f = LIVE_DIR / f"{r['id']}.jpg"
        try:
            r["frame_age_s"] = round(now - f.stat().st_mtime, 1)
        except OSError:
            r["frame_age_s"] = None
        r["is_streaming"] = r["frame_age_s"] is not None and r["frame_age_s"] < 30
    return {"count": len(rows), "results": rows}
