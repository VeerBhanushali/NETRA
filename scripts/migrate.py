"""Idempotent schema migrations for an existing database.

`CREATE TABLE IF NOT EXISTS` in schema.sql builds a fresh database
correctly but cannot change a table that already exists — so a CHECK
constraint added to schema.sql never reaches a running deployment. SQLite
has no ALTER for constraints; the supported route is to rebuild the table
and copy the rows.

    python scripts/migrate.py

Safe to run repeatedly: each step checks whether it is already applied.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app import config                                    # noqa: E402

NEW_ALERT_STATUSES = ("new", "acknowledged", "investigating", "confirmed",
                      "dispatched", "responding", "resolved", "false_positive")
NEW_ALERT_TYPES = ("cloned_plate", "speeding", "loitering", "watchlist_hit",
                   "hit_and_run", "anomaly", "no_plate", "convoy",
                   "wanted_person", "missing_person", "weapon", "violence",
                   "person_down", "abandoned_object", "accident")


def migrate_alerts(conn: sqlite3.Connection) -> bool:
    """Widen the alerts CHECK constraints to the full incident lifecycle."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()
    if not row:
        return False
    if "dispatched" in row[0] and "wanted_person" in row[0]:
        print("  alerts: already migrated")
        return False

    statuses = ",".join(f"'{s}'" for s in NEW_ALERT_STATUSES)
    types = ",".join(f"'{t}'" for t in NEW_ALERT_TYPES)

    conn.executescript(f"""
        PRAGMA foreign_keys = OFF;
        BEGIN;
        -- v_alerts depends on the table, so SQLite refuses the DROP while
        -- it exists. It is recreated identically at the end.
        DROP VIEW IF EXISTS v_alerts;
        CREATE TABLE alerts_new (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type    TEXT NOT NULL CHECK (alert_type IN ({types})),
            severity      TEXT NOT NULL CHECK (severity IN
                          ('critical','high','medium','low','info')),
            status        TEXT NOT NULL DEFAULT 'new'
                          CHECK (status IN ({statuses})),
            plate         TEXT,
            camera_id     TEXT REFERENCES cameras(id),
            occurred_at   TEXT NOT NULL,
            title         TEXT NOT NULL,
            detail        TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{{}}',
            confidence    REAL NOT NULL DEFAULT 1.0,
            dedupe_key    TEXT UNIQUE,
            acknowledged_by TEXT,
            acknowledged_at TEXT,
            resolved_at   TEXT,
            created_at    TEXT NOT NULL
        );
        INSERT INTO alerts_new SELECT
            id, alert_type, severity, status, plate, camera_id, occurred_at,
            title, detail, evidence_json, confidence, dedupe_key,
            acknowledged_by, acknowledged_at, resolved_at, created_at
        FROM alerts;
        DROP TABLE alerts;
        ALTER TABLE alerts_new RENAME TO alerts;
        CREATE INDEX IF NOT EXISTS idx_alerts_status_time
            ON alerts(status, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_alerts_plate
            ON alerts(plate, occurred_at DESC);
        CREATE VIEW IF NOT EXISTS v_alerts AS
        SELECT a.id, a.alert_type, a.severity, a.status, a.plate, a.camera_id,
               c.name AS camera_name, a.occurred_at, a.title, a.detail,
               a.confidence
        FROM alerts a LEFT JOIN cameras c ON c.id = a.camera_id;
        COMMIT;
        PRAGMA foreign_keys = ON;
    """)
    print(f"  alerts: rebuilt with {len(NEW_ALERT_STATUSES)} statuses, "
          f"{len(NEW_ALERT_TYPES)} types")
    return True


def add_person_tables(conn: sqlite3.Connection) -> bool:
    """Phase-3 entities for authorised person recognition.

    Deliberately minimal: no address, no phone, no free-text notes. An
    enrolment record that can accumulate arbitrary detail becomes an
    unaccountable dossier.
    """
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if {"persons", "person_faces", "face_matches"} <= existing:
        print("  persons/faces: already present")
        return False

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS persons (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            case_ref    TEXT,
            category    TEXT NOT NULL CHECK (category IN
                        ('wanted','missing','person_of_interest')),
            added_by    TEXT NOT NULL,
            added_at    TEXT NOT NULL,
            -- Enrolment is time-bounded. An entry that never expires is
            -- how a watchlist becomes permanent surveillance.
            expires_at  TEXT,
            is_active   INTEGER NOT NULL DEFAULT 1
        );

        -- One row per enrolled photo. The 512-d template is stored; the
        -- photograph itself need not be, and a template cannot be
        -- reversed into a face.
        CREATE TABLE IF NOT EXISTS person_faces (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id   TEXT NOT NULL REFERENCES persons(id),
            embedding   BLOB NOT NULL,
            quality     REAL NOT NULL,
            source      TEXT,
            created_at  TEXT NOT NULL
        );

        -- A candidate match, before a human has judged it. Never an
        -- assertion of identity (SIH26127 §13).
        CREATE TABLE IF NOT EXISTS face_matches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id   TEXT REFERENCES persons(id),
            camera_id   TEXT NOT NULL REFERENCES cameras(id),
            track_id    TEXT NOT NULL,
            score       REAL NOT NULL,
            frame_count INTEGER NOT NULL,
            crop_path   TEXT,
            seen_at     TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','confirmed','rejected')),
            reviewed_by TEXT,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_face_matches_status
            ON face_matches(status, seen_at DESC);
    """)
    conn.commit()
    print("  persons/faces: created")
    return True


def main() -> int:
    if not config.DB_PATH.is_file():
        print(f"no database at {config.DB_PATH} — nothing to migrate")
        print("run: python scripts/seed_demo.py --reset")
        return 0

    print(f"migrating {config.DB_PATH}")
    conn = sqlite3.connect(config.DB_PATH)
    try:
        changed = migrate_alerts(conn)
        changed |= add_person_tables(conn)
        conn.commit()
    finally:
        conn.close()
    print("done" if changed else "already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
