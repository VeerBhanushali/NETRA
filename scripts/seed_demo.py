"""Seed a realistic demo city.

Generates a plausible day of traffic across a camera network, then plants
one guaranteed instance of every alert type so the demo cannot silently
show an empty alert list. This is the demo's insurance policy: if live
inference stumbles on stage, the story still works.

    python scripts/seed_demo.py            # rebuild demo data
    python scripts/seed_demo.py --reset    # wipe first

Deterministic: a fixed RNG seed means the same demo every rehearsal.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app import db, rules                                    # noqa: E402
from app.db import utcnow                                    # noqa: E402

RNG = random.Random(20260907)
TS = "%Y-%m-%dT%H:%M:%SZ"

# --- the demo city: Chandigarh -----------------------------------------
ZONES = [
    ("zone-sec17", "Sector 17 Commercial", "sensitive"),
    ("zone-isbt",  "ISBT-43 Transit Hub",  "monitored"),
    ("zone-ind",   "Industrial Area Ph-1", "restricted"),
]

CAMERAS = [
    # id, name, lat, lon, bearing, zone, road
    ("cam-01", "Sector 17 Plaza North",   30.7410, 76.7822, 180, "zone-sec17", "Jan Marg"),
    ("cam-02", "Sector 17 Bank Square",   30.7395, 76.7801,  90, "zone-sec17", "Udyog Path"),
    ("cam-03", "Sector 17/22 Junction",   30.7362, 76.7795, 270, "zone-sec17", "Himalaya Marg"),
    ("cam-04", "Matka Chowk",             30.7461, 76.7862,   0, None,         "Jan Marg"),
    ("cam-05", "ISBT-43 Approach",        30.7192, 76.7601, 225, "zone-isbt",  "Dakshin Marg"),
    ("cam-06", "Sector 43/42 Light Pt",   30.7228, 76.7554, 135, "zone-isbt",  "Vikas Marg"),
    ("cam-07", "Tribune Chowk",           30.7046, 76.8012,  45, None,         "Purv Marg"),
    ("cam-08", "Industrial Ph-1 Gate",    30.7015, 76.8104, 315, "zone-ind",   "Ind. Area Rd"),
    ("cam-09", "Zirakpur Toll Approach",  30.6425, 76.8173, 180, None,         "NH-5"),
    ("cam-10", "PGI Rotary",              30.7649, 76.7742, 270, None,         "Madhya Marg"),
]

# Surveyed road distances (metres) and fastest legal traversal (seconds).
# Only surveyed pairs may produce a speeding figure.
LINKS = [
    ("cam-01", "cam-02", 620,  40),
    ("cam-02", "cam-03", 780,  50),
    ("cam-01", "cam-04", 1150, 70),
    ("cam-03", "cam-06", 4900, 300),
    ("cam-05", "cam-06", 950,  60),
    ("cam-07", "cam-08", 1400, 90),
    ("cam-08", "cam-09", 7200, 420),
    ("cam-04", "cam-10", 2600, 160),
    ("cam-03", "cam-07", 5200, 320),
]

STATE_CODES = ["CH", "PB", "HR", "DL", "UP", "RJ", "MH", "HP"]
SERIES = ["AA", "AB", "AC", "BK", "CD", "DE", "DK", "GH", "JK", "MN"]
COLORS = ["white", "silver", "black", "red", "blue", "grey", "brown"]
VTYPES = ["car", "car", "car", "motorcycle", "truck", "bus"]

# Plates with a story. Everything else is background traffic.
PLATE_CLONE = "PB65AR4412"     # will appear in two impossible places
PLATE_SPEEDER = "HR26DK8337"   # will cross a surveyed segment far too fast
PLATE_LOITER = "CH01AV9021"    # will circle Sector 17 repeatedly
PLATE_STOLEN = "DL8CAF5030"    # on the watchlist
PLATE_FUZZY = "DL8CAF5O30"     # same plate misread: O for 0


def rand_plate() -> str:
    return (f"{RNG.choice(STATE_CODES)}{RNG.randint(1, 68):02d}"
            f"{RNG.choice(SERIES)}{RNG.randint(1000, 9999)}")


def iso(dt: datetime) -> str:
    return dt.strftime(TS)


def add_sighting(plate: str, cam: str, when: datetime, conf: float,
                 color: str | None = None, vtype: str | None = None) -> dict | None:
    """Insert one sighting and run the rules engine over it, exactly as the
    live ingest path would."""
    row_id = db.insert(
        "INSERT INTO sightings (plate, camera_id, seen_at, confidence, track_id,"
        " frame_count, color, vehicle_type, source, dedupe_key, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,'sim',?,?)",
        (plate, cam, iso(when), round(conf, 3), f"{cam}:sim{RNG.randint(1, 99999)}",
         RNG.randint(8, 34), color, vtype,
         f"sim:{plate}:{cam}:{iso(when)}", utcnow()))
    if row_id is None:
        return None
    db.execute(
        "INSERT INTO vehicles (plate, first_seen_at, last_seen_at, sighting_count,"
        " color, vehicle_type) VALUES (?,?,?,1,?,?)"
        " ON CONFLICT(plate) DO UPDATE SET last_seen_at = excluded.last_seen_at,"
        " sighting_count = vehicles.sighting_count + 1,"
        " color = COALESCE(vehicles.color, excluded.color),"
        " vehicle_type = COALESCE(vehicles.vehicle_type, excluded.vehicle_type)",
        (plate, iso(when), iso(when), color, vtype))
    stored = db.query_one("SELECT * FROM sightings WHERE id = ?", (row_id,))
    rules.evaluate(stored)
    return stored


def reset() -> None:
    for table in ("alerts", "sightings", "plate_reads", "review_queue",
                  "anomaly_events", "vehicles", "watchlist", "audit_log",
                  "ai_inferences", "camera_links", "cameras", "zones"):
        db.execute(f"DELETE FROM {table}")
    print("[seed] wiped existing data")


def seed_network() -> None:
    now = utcnow()
    for zid, name, kind in ZONES:
        db.execute(
            "INSERT OR REPLACE INTO zones (id, name, kind, polygon_json, created_at)"
            " VALUES (?,?,?,'[]',?)", (zid, name, kind, now))
    for cid, name, lat, lon, bearing, zone, road in CAMERAS:
        db.execute(
            "INSERT OR REPLACE INTO cameras (id, name, lat, lon, bearing_deg, zone_id,"
            " road_name, is_active, rtsp_url, created_at) VALUES (?,?,?,?,?,?,?,1,?,?)",
            (cid, name, lat, lon, bearing, zone, road,
             f"rtsp://127.0.0.1:8554/{cid}", now))
    for a, b, dist, tmin in LINKS:
        for x, y in ((a, b), (b, a)):
            db.execute(
                "INSERT OR REPLACE INTO camera_links (from_camera, to_camera,"
                " road_distance_m, min_travel_s) VALUES (?,?,?,?)", (x, y, dist, tmin))
    print(f"[seed] {len(ZONES)} zones, {len(CAMERAS)} cameras, {len(LINKS)*2} links")


def seed_watchlist() -> None:
    now = utcnow()
    entries = [
        (PLATE_STOLEN, "Stolen vehicle — FIR 118/2026, Sector 34 PS", "critical", "FIR-118/2026"),
        ("UP16BT7788", "Wanted in connection with chain snatching", "high", "FIR-092/2026"),
        ("RJ14CV2210", "Suspect vehicle — narcotics surveillance", "high", "OPS-2026-11"),
    ]
    for plate, reason, sev, ref in entries:
        db.execute(
            "INSERT OR REPLACE INTO watchlist (plate, reason, severity, case_ref,"
            " added_by, added_at, expires_at, is_active) VALUES (?,?,?,?,?,?,?,1)",
            (plate, reason, sev, ref, "insp.sharma", now,
             iso(datetime.now(timezone.utc) + timedelta(days=90))))
    print(f"[seed] {len(entries)} watchlist entries")


def seed_background(hours: int = 8) -> int:
    """Ordinary traffic. Without a believable background, every alert looks
    planted — the noise is what makes the signal meaningful."""
    start = datetime.now(timezone.utc) - timedelta(hours=hours)
    fleet = [(rand_plate(), RNG.choice(COLORS), RNG.choice(VTYPES)) for _ in range(140)]
    cam_ids = [c[0] for c in CAMERAS]
    n = 0

    # Adjacency with the surveyed minimum traversal time, so background
    # vehicles obey physics. Without this the generator teleports cars
    # between junctions and manufactures cloned-plate alerts — which would
    # bury the one real planted clone in noise and make the false-positive
    # story indefensible.
    neighbours: dict[str, list[tuple[str, float]]] = {}
    for a, b, dist, tmin in LINKS:
        neighbours.setdefault(a, []).append((b, tmin))
        neighbours.setdefault(b, []).append((a, tmin))

    for plate, color, vtype in fleet:
        # Each vehicle makes a few passes along a plausible short route.
        t = start + timedelta(minutes=RNG.randint(0, hours * 60 - 40))
        here = RNG.choice(cam_ids)
        for _ in range(RNG.randint(1, 4)):
            # Most reads are confident; a realistic tail is not.
            conf = RNG.choice([0.99, 0.98, 0.97, 0.96, 0.94, 0.93, 0.91])
            if add_sighting(plate, here, t, conf, color, vtype):
                n += 1
            options = neighbours.get(here)
            if options:
                nxt, min_s = RNG.choice(options)
                # Normal traffic takes between the free-flow time and ~3x
                # it in congestion — always slower than the legal minimum.
                gap = min_s * RNG.uniform(1.15, 3.0)
            else:
                nxt = RNG.choice(cam_ids)
                # Unsurveyed hop: leave a wide gap so no rule can infer an
                # impossible speed from an unknown route.
                gap = RNG.uniform(1200, 3000)
            here = nxt
            t += timedelta(seconds=gap)
    print(f"[seed] {n} background sightings across {len(fleet)} vehicles")
    return n


def seed_review_queue() -> None:
    """Low-confidence reads awaiting a human. The screen that proves the
    system refuses to publish a plate it is not sure about."""
    now = datetime.now(timezone.utc)
    items = [
        ("PB10DK21 4", 0.61, "cam-05", [("PB10DK2144", 0.61), ("PB10DK2141", 0.55), ("PB10OK2144", 0.42)]),
        ("HR51BC773",  0.68, "cam-09", [("HR51BC7738", 0.68), ("HR51BC7736", 0.59), ("HR51BC1738", 0.38)]),
        ("CH04M 8812", 0.57, "cam-02", [("CH04MJ8812", 0.57), ("CH04MU8812", 0.52), ("CH04NJ8812", 0.44)]),
        ("UP32EE10O5", 0.72, "cam-07", [("UP32EE1005", 0.72), ("UP32EE10O5", 0.66), ("UP32FE1005", 0.31)]),
    ]
    import json
    for guess, conf, cam, cands in items:
        seen = now - timedelta(minutes=RNG.randint(2, 45))
        db.insert(
            "INSERT INTO review_queue (track_id, camera_id, best_guess, confidence,"
            " candidates_json, frame_count, seen_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (f"{cam}:rev{RNG.randint(100,999)}", cam, guess.replace(" ", ""), conf,
             json.dumps([{"plate": p, "score": s} for p, s in cands]),
             RNG.randint(4, 12), iso(seen), utcnow()))
    print(f"[seed] {len(items)} items queued for human review")


def plant_incidents() -> None:
    """One guaranteed instance of every alert type."""
    now = datetime.now(timezone.utc)

    # 1. CLONED PLATE — Sector 17 then the Zirakpur toll, ~16 km apart, 90 s
    #    apart. That needs ~640 km/h, so the plate must exist twice.
    t = now - timedelta(minutes=42)
    add_sighting(PLATE_CLONE, "cam-02", t, 0.98, "white", "car")
    add_sighting(PLATE_CLONE, "cam-09", t + timedelta(seconds=90), 0.97, "white", "car")

    # 2. SPEEDING — cam-08 to cam-09 is a surveyed 7.2 km segment whose
    #    fastest legal traversal is 420 s. Doing it in 190 s is ~136 km/h.
    t = now - timedelta(minutes=31)
    add_sighting(PLATE_SPEEDER, "cam-08", t, 0.98, "red", "car")
    add_sighting(PLATE_SPEEDER, "cam-09", t + timedelta(seconds=190), 0.97, "red", "car")

    # 3. LOITERING — five passes through the Sector 17 sensitive zone in
    #    22 minutes. Ordinary traffic passes through; it does not circle.
    t = now - timedelta(minutes=26)
    for cam, offset in (("cam-01", 0), ("cam-02", 5), ("cam-03", 10),
                        ("cam-02", 16), ("cam-01", 22)):
        add_sighting(PLATE_LOITER, cam, t + timedelta(minutes=offset),
                     0.96, "black", "car")

    # 4. WATCHLIST HIT — exact match on the stolen vehicle.
    add_sighting(PLATE_STOLEN, "cam-06", now - timedelta(minutes=14), 0.98, "silver", "car")

    # 5. WATCHLIST HIT (fuzzy) — the same vehicle read with O instead of 0.
    #    Proves the system still catches it, but flags the read as uncertain.
    add_sighting(PLATE_FUZZY, "cam-10", now - timedelta(minutes=9), 0.93, "silver", "car")

    # 6. ANOMALY — a behavioural detection with no readable plate.
    db.insert(
        "INSERT INTO anomaly_events (camera_id, occurred_at, kind, score, detail, source)"
        " VALUES (?,?,?,?,?,?)",
        ("cam-07", iso(now - timedelta(minutes=6)), "accident", 0.91,
         "Sudden motion collapse and crowd convergence consistent with a collision.",
         "heuristic"))
    rules._raise(
        "anomaly", "critical", None, "cam-07", iso(now - timedelta(minutes=6)),
        "Accident detected — Tribune Chowk",
        "Behavioural model scored 0.91 for 'accident'. Two vehicles were in frame.",
        {"kind": "accident", "score": 0.91, "source": "heuristic",
         "vehicles_present": [{"plate": PLATE_SPEEDER, "seen_at": iso(now - timedelta(minutes=6))}]},
        dedupe_key=f"anomaly:cam-07:accident:seed", confidence=0.91)

    print("[seed] planted: cloned plate, speeding, loitering, watchlist hit "
          "(exact + fuzzy), accident anomaly")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe all data first")
    ap.add_argument("--hours", type=int, default=8, help="hours of background traffic")
    args = ap.parse_args()

    db.init_db()
    if args.reset:
        reset()
    seed_network()
    seed_watchlist()
    seed_background(args.hours)
    seed_review_queue()
    plant_incidents()

    alerts = db.query("SELECT alert_type, COUNT(*) AS n FROM alerts GROUP BY alert_type")
    total = db.query_one("SELECT COUNT(*) AS n FROM sightings")["n"]
    print(f"\n[seed] done — {total} sightings")
    for a in alerts:
        print(f"        {a['n']:>3}  {a['alert_type']}")


if __name__ == "__main__":
    main()
