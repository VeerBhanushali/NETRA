"""The spatio-temporal rules engine.

Runs synchronously on every accepted sighting. At demo scale that is a
handful of indexed lookups per sighting — far simpler than a background
poller, and it means an alert appears on the dashboard in the same second
the vehicle is seen.

Design rule that matters more than any individual rule: **every alert
carries the numbers that produced it** in `evidence_json`. An alert an
operator cannot interrogate is an alert they will learn to ignore.
"""
from __future__ import annotations

import json
from datetime import timedelta

from . import config, db
from .db import haversine_m, parse_ts, utcnow


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def _camera(cam_id: str) -> dict | None:
    return db.query_one("SELECT * FROM cameras WHERE id = ?", (cam_id,))


def _road_distance_m(cam_a: dict, cam_b: dict) -> tuple[float, str]:
    """Best available road distance between two cameras.

    Prefers a surveyed value from camera_links; otherwise inflates the
    crow-flight distance by the urban circuity factor. Returns the
    distance and which method produced it, because the operator deserves
    to know whether an accusation rests on a measurement or an estimate.
    """
    link = db.query_one(
        "SELECT road_distance_m FROM camera_links WHERE from_camera = ? AND to_camera = ?",
        (cam_a["id"], cam_b["id"]),
    ) or db.query_one(
        "SELECT road_distance_m FROM camera_links WHERE from_camera = ? AND to_camera = ?",
        (cam_b["id"], cam_a["id"]),
    )
    if link:
        return float(link["road_distance_m"]), "surveyed"
    straight = haversine_m(cam_a["lat"], cam_a["lon"], cam_b["lat"], cam_b["lon"])
    return straight * config.CIRCUITY_FACTOR, "estimated"


def _raise(alert_type: str, severity: str, plate: str | None, camera_id: str | None,
           occurred_at: str, title: str, detail: str, evidence: dict,
           dedupe_key: str, confidence: float = 1.0) -> dict | None:
    """Insert an alert unless an identical one is already in cooldown.

    The dedupe_key is what stops a stationary car in a monitored zone from
    generating an alert every thirty seconds for an hour.
    """
    existing = db.query_one(
        "SELECT id, occurred_at FROM alerts WHERE dedupe_key = ?", (dedupe_key,)
    )
    if existing:
        return None
    row_id = db.insert(
        "INSERT INTO alerts (alert_type, severity, status, plate, camera_id, occurred_at,"
        " title, detail, evidence_json, confidence, dedupe_key, created_at)"
        " VALUES (?,?,'new',?,?,?,?,?,?,?,?,?)",
        (alert_type, severity, plate, camera_id, occurred_at, title, detail,
         json.dumps(evidence), confidence, dedupe_key, utcnow()),
    )
    if row_id is None:
        return None
    return db.query_one("SELECT * FROM alerts WHERE id = ?", (row_id,))


def _cooldown_bucket(seconds: int, ts: str) -> int:
    """Quantise a timestamp into cooldown-sized buckets so repeated events
    inside one window collapse onto the same dedupe_key."""
    return int(parse_ts(ts).timestamp()) // seconds


# ---------------------------------------------------------------------
# RULE 1 — Cloned / fake plate  (the flagship)
# ---------------------------------------------------------------------

def check_cloned_plate(s: dict) -> dict | None:
    """The same plate cannot be in two places at once.

    Take the previous sighting of this plate at a *different* camera,
    compute the road distance and the elapsed time, and derive the speed
    the vehicle would have needed. Above a physically impossible
    threshold, the only explanation is that the plate exists twice.

    Guards, each of which exists because it is a real false-positive source:
      - low-confidence OCR never accuses anyone
      - cameras at the same junction are excluded (distance floor)
      - camera clock skew is subtracted from elapsed time (worst case)
    """
    if s["confidence"] < config.CLONE_MIN_CONFIDENCE:
        return None

    # `seen_at <= ?` with an id tiebreak, not `<`. Our timestamps have
    # one-second resolution, so a plate genuinely appearing at two distant
    # cameras in the same second — the most blatant clone possible — would
    # be invisible to a strict `<` comparison.
    prev = db.query_one(
        "SELECT * FROM sightings WHERE plate = ? AND camera_id != ? AND id != ?"
        " AND seen_at <= ? ORDER BY seen_at DESC, id DESC LIMIT 1",
        (s["plate"], s["camera_id"], s["id"], s["seen_at"]),
    )
    if not prev or prev["confidence"] < config.CLONE_MIN_CONFIDENCE:
        return None

    cam_now, cam_prev = _camera(s["camera_id"]), _camera(prev["camera_id"])
    if not cam_now or not cam_prev:
        return None

    distance_m, method = _road_distance_m(cam_prev, cam_now)
    if distance_m < config.CLONE_MIN_DISTANCE_M:
        return None

    elapsed_s = (parse_ts(s["seen_at"]) - parse_ts(prev["seen_at"])).total_seconds()
    # Assume the clocks are skewed in the direction that makes the vehicle
    # look fastest, then refuse to alert unless it is STILL impossible.
    #
    # The floor matters: two cameras kilometres apart reporting the same
    # plate within the skew window is the *most* blatant clone there is —
    # the vehicle is in two places at once. Returning early here would
    # have silently discarded exactly the case we most want to catch, so
    # we clamp instead and let the distance decide.
    elapsed_s = max(1.0, elapsed_s - config.CLOCK_SKEW_S)

    implied_kmh = (distance_m / elapsed_s) * 3.6
    if implied_kmh <= config.CLONE_SPEED_KMH:
        return None

    evidence = {
        "implied_speed_kmh": round(implied_kmh, 1),
        "threshold_kmh": config.CLONE_SPEED_KMH,
        "distance_m": round(distance_m),
        "distance_method": method,
        "elapsed_s": round(elapsed_s, 1),
        "clock_skew_allowance_s": config.CLOCK_SKEW_S,
        "from": {"camera": cam_prev["id"], "name": cam_prev["name"], "at": prev["seen_at"]},
        "to": {"camera": cam_now["id"], "name": cam_now["name"], "at": s["seen_at"]},
        "ocr_confidence": [prev["confidence"], s["confidence"]],
    }
    return _raise(
        "cloned_plate", "critical", s["plate"], s["camera_id"], s["seen_at"],
        f"Cloned plate suspected — {s['plate']}",
        (f"Seen at {cam_prev['name']} then {cam_now['name']}, "
         f"{round(distance_m)} m apart, {round(elapsed_s)} s later. That requires "
         f"{implied_kmh:.0f} km/h — physically impossible, so this plate is "
         f"almost certainly duplicated on two vehicles."),
        evidence,
        dedupe_key=f"clone:{s['plate']}:{prev['camera_id']}:{s['camera_id']}:"
                   f"{_cooldown_bucket(config.ALERT_COOLDOWN_S, s['seen_at'])}",
        confidence=min(prev["confidence"], s["confidence"]),
    )


# ---------------------------------------------------------------------
# RULE 2 — Speeding between two cameras
# ---------------------------------------------------------------------

def check_speeding(s: dict) -> dict | None:
    """Average speed over a known camera-to-camera segment.

    This is *indicative*, not legally defensible: it is an average over
    the segment, so it cannot prove the instantaneous speed at any point,
    and it is only as good as the surveyed distance. The UI labels it
    accordingly. Only surveyed pairs are used — an estimated distance
    would make the speed figure meaningless.
    """
    prev = db.query_one(
        "SELECT * FROM sightings WHERE plate = ? AND camera_id != ? AND seen_at < ?"
        " ORDER BY seen_at DESC LIMIT 1",
        (s["plate"], s["camera_id"], s["seen_at"]),
    )
    if not prev:
        return None

    link = db.query_one(
        "SELECT road_distance_m, min_travel_s FROM camera_links"
        " WHERE from_camera = ? AND to_camera = ?",
        (prev["camera_id"], s["camera_id"]),
    )
    if not link:
        return None      # unsurveyed pair: no speed claim

    elapsed_s = (parse_ts(s["seen_at"]) - parse_ts(prev["seen_at"])).total_seconds()
    if elapsed_s <= 1.0:
        return None

    speed_kmh = (float(link["road_distance_m"]) / elapsed_s) * 3.6
    # Above the clone threshold this is not speeding, it is a cloned
    # plate — let rule 1 own it rather than raising both.
    if speed_kmh >= config.CLONE_SPEED_KMH:
        return None
    if speed_kmh <= config.SPEED_LIMIT_KMH + config.SPEED_ALERT_MARGIN_KMH:
        return None

    db.execute("UPDATE sightings SET speed_kmh = ? WHERE id = ?", (round(speed_kmh, 1), s["id"]))

    over = speed_kmh - config.SPEED_LIMIT_KMH
    evidence = {
        "avg_speed_kmh": round(speed_kmh, 1),
        "speed_limit_kmh": config.SPEED_LIMIT_KMH,
        "over_by_kmh": round(over, 1),
        "segment_m": link["road_distance_m"],
        "elapsed_s": round(elapsed_s, 1),
        "from_camera": prev["camera_id"],
        "to_camera": s["camera_id"],
        "measurement": "segment average — indicative, not instantaneous",
    }
    return _raise(
        "speeding", "high" if over > 30 else "medium", s["plate"], s["camera_id"],
        s["seen_at"],
        f"Speeding — {s['plate']} at {speed_kmh:.0f} km/h",
        (f"Average {speed_kmh:.0f} km/h over the {int(link['road_distance_m'])} m segment "
         f"{prev['camera_id']} to {s['camera_id']} (limit {config.SPEED_LIMIT_KMH:.0f})."),
        evidence,
        dedupe_key=f"speed:{s['plate']}:{prev['camera_id']}:{s['camera_id']}:"
                   f"{_cooldown_bucket(config.ALERT_COOLDOWN_S, s['seen_at'])}",
    )


# ---------------------------------------------------------------------
# RULE 3 — Loitering / casing a sensitive zone
# ---------------------------------------------------------------------

def check_loitering(s: dict) -> dict | None:
    """Repeated passes through the same zone inside a rolling window.

    A vehicle circling a bank four times in half an hour is doing
    something a vehicle passing through does not do.
    """
    cam = _camera(s["camera_id"])
    if not cam or not cam.get("zone_id"):
        return None

    window_start = (parse_ts(s["seen_at"]) - timedelta(minutes=config.LOITER_WINDOW_MIN)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = db.query(
        "SELECT s.id, s.seen_at, s.camera_id FROM sightings s"
        " JOIN cameras c ON c.id = s.camera_id"
        " WHERE s.plate = ? AND c.zone_id = ? AND s.seen_at >= ? AND s.seen_at <= ?"
        " ORDER BY s.seen_at",
        (s["plate"], cam["zone_id"], window_start, s["seen_at"]),
    )
    if len(rows) < config.LOITER_MIN_SIGHTINGS:
        return None

    dwell_s = (parse_ts(rows[-1]["seen_at"]) - parse_ts(rows[0]["seen_at"])).total_seconds()
    # Five passes crammed into a minute is a tracking artefact, not a
    # vehicle circling a bank.
    if dwell_s < config.LOITER_MIN_DWELL_S:
        return None

    zone = db.query_one("SELECT * FROM zones WHERE id = ?", (cam["zone_id"],))
    severity = "high" if zone and zone["kind"] in ("restricted", "sensitive") else "medium"

    evidence = {
        "zone": cam["zone_id"],
        "zone_name": zone["name"] if zone else cam["zone_id"],
        "zone_kind": zone["kind"] if zone else "monitored",
        "pass_count": len(rows),
        "threshold": config.LOITER_MIN_SIGHTINGS,
        "window_min": config.LOITER_WINDOW_MIN,
        "dwell_s": round(dwell_s),
        "cameras": sorted({r["camera_id"] for r in rows}),
        "passes": [r["seen_at"] for r in rows],
    }
    return _raise(
        "loitering", severity, s["plate"], s["camera_id"], s["seen_at"],
        f"Loitering — {s['plate']} in {evidence['zone_name']}",
        (f"{len(rows)} passes through {evidence['zone_name']} in "
         f"{config.LOITER_WINDOW_MIN} minutes across "
         f"{len(evidence['cameras'])} cameras — consistent with casing behaviour."),
        evidence,
        dedupe_key=f"loiter:{s['plate']}:{cam['zone_id']}:"
                   f"{_cooldown_bucket(config.ALERT_COOLDOWN_S, s['seen_at'])}",
    )


# ---------------------------------------------------------------------
# RULE 4 — Watchlist hit (exact, plus one-character-off fuzzy)
# ---------------------------------------------------------------------

def _one_edit_apart(a: str, b: str) -> bool:
    """True when a and b differ by exactly one substitution of the same
    length. Catches the residual OCR error on a stolen-vehicle plate,
    which is exactly the case you cannot afford to miss."""
    if len(a) != len(b) or a == b:
        return False
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def check_watchlist(s: dict) -> dict | None:
    now = utcnow()
    exact = db.query_one(
        "SELECT * FROM watchlist WHERE plate = ? AND is_active = 1"
        " AND (expires_at IS NULL OR expires_at > ?)",
        (s["plate"], now),
    )
    entry, match_kind, severity = exact, "exact", None
    if entry:
        severity = entry["severity"]
    else:
        # Fuzzy pass, deliberately restricted to same-length single
        # substitutions and downgraded a severity level, because a fuzzy
        # match is a lead, not a fact.
        for w in db.query(
            "SELECT * FROM watchlist WHERE is_active = 1"
            " AND (expires_at IS NULL OR expires_at > ?) AND length(plate) = ?",
            (now, len(s["plate"])),
        ):
            if _one_edit_apart(s["plate"], w["plate"]):
                entry, match_kind = w, "fuzzy_1_char"
                severity = {"critical": "high", "high": "medium"}.get(w["severity"], "low")
                break
    if not entry:
        return None

    cam = _camera(s["camera_id"])
    evidence = {
        "match": match_kind,
        "watchlist_plate": entry["plate"],
        "observed_plate": s["plate"],
        "reason": entry["reason"],
        "case_ref": entry["case_ref"],
        "camera": s["camera_id"],
        "camera_name": cam["name"] if cam else s["camera_id"],
        "ocr_confidence": s["confidence"],
    }
    note = "" if match_kind == "exact" else \
        f" Read as {s['plate']}, one character from {entry['plate']} — confirm visually."
    return _raise(
        "watchlist_hit", severity or "high", s["plate"], s["camera_id"], s["seen_at"],
        f"Watchlist vehicle located — {entry['plate']}",
        f"{entry['reason']} (case {entry['case_ref'] or 'n/a'}) seen at "
        f"{evidence['camera_name']}.{note}",
        evidence,
        dedupe_key=f"watch:{entry['plate']}:{s['camera_id']}:"
                   f"{_cooldown_bucket(config.ALERT_COOLDOWN_S, s['seen_at'])}",
        confidence=s["confidence"],
    )


# ---------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------

RULES = (check_cloned_plate, check_speeding, check_loitering, check_watchlist)


def evaluate(sighting: dict) -> list[dict]:
    """Run every rule against a newly stored sighting.

    A failing rule must never break ingest — a crashed loitering check
    should not stop the vehicle being recorded.
    """
    fired: list[dict] = []
    for rule in RULES:
        try:
            alert = rule(sighting)
            if alert:
                fired.append(alert)
        except Exception as exc:                       # noqa: BLE001
            print(f"[rules] {rule.__name__} failed on sighting "
                  f"{sighting.get('id')}: {exc!r}")
    return fired
