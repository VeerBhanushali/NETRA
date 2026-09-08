"""SQLite access layer.

Why SQLite and not Postgres/PostGIS for the runnable system: it needs no
server, no account and no network, so the demo survives a dead venue
wifi. Everything geographic that PostGIS would do is done by the
`haversine_m` UDF registered below, which is registered as a SQL function
so the queries read the same as their PostGIS equivalents.
"""
from __future__ import annotations

import math
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

_local = threading.local()


def utcnow() -> str:
    """ISO-8601 UTC with a trailing Z. The only time format in the system."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts: str) -> datetime:
    """Parse our stored timestamps back to an aware datetime."""
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres.

    Postgres equivalent:
        ST_Distance(a.geog, b.geog)
    """
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    # WAL lets the ingest writer and the dashboard readers run concurrently
    # instead of serialising behind each other.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function("haversine_m", 4, haversine_m)
    return conn


def get_conn() -> sqlite3.Connection:
    """One connection per thread; FastAPI's threadpool reuses threads."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = _connect()
    return conn


def init_db() -> None:
    """Create the schema. Idempotent — safe to call on every boot."""
    conn = get_conn()
    conn.executescript(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


# --- small query helpers ---------------------------------------------

def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in get_conn().execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    row = get_conn().execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = get_conn()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


def insert(sql: str, params: Iterable[Any] = ()) -> int | None:
    """Insert, returning the new rowid, or None if a UNIQUE constraint
    swallowed it. Used for idempotent ingest: a retried publish hits the
    dedupe_key index and is silently ignored rather than duplicating."""
    try:
        return execute(sql, params).lastrowid
    except sqlite3.IntegrityError:
        return None


def audit(actor: str, action: str, target: str | None = None,
          reason: str | None = None, meta: str = "{}") -> None:
    """Append-only. Never updated, never deleted."""
    execute(
        "INSERT INTO audit_log (ts, actor, action, target, reason, meta_json)"
        " VALUES (?,?,?,?,?,?)",
        (utcnow(), actor, action, target, reason, meta),
    )
