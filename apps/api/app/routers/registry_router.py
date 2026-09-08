"""Registration lookup endpoints.

Separate from vehicles.py on purpose. Everything in vehicles.py is data
NETRA observed itself and owns; everything here is personal data about a
citizen fetched from an external registry. Different rules apply, and
putting them in different files makes that hard to forget.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import db, registry

router = APIRouter(tags=["registry"])


@router.get("/registry/status")
def registry_status() -> dict:
    """What this deployment can look up, and whether it is real data."""
    return registry.available()


@router.get("/registry/{plate}")
def registry_lookup(
    plate: str,
    reason: str = Query(..., min_length=8,
                        description="Why this owner is being looked up. Recorded."),
    actor: str = "operator",
) -> dict:
    """Owner, RC, insurance, PUC and fitness for one plate.

    `reason` is mandatory and has a minimum length because "check" is not
    a reason. The audit row is written BEFORE the lookup, so an attempt
    that fails is still on the record — an unlogged failed search is how
    you probe a database quietly.
    """
    plate = plate.upper().replace(" ", "").replace("-", "")
    db.audit(actor, "registry_lookup", plate, reason)

    try:
        record = registry.lookup(plate)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:                               # noqa: BLE001
        raise HTTPException(502, f"registry provider failed: {exc}") from exc

    # Correlate with what NETRA itself knows. This is the part a purchased
    # API cannot give you and the reason the lookup lives inside the
    # console at all.
    seen = db.query_one(
        "SELECT COUNT(*) AS n, MIN(seen_at) AS first, MAX(seen_at) AS last"
        " FROM sightings WHERE plate = ?", (plate,))
    wl = db.query_one("SELECT reason, severity, case_ref FROM watchlist"
                      " WHERE plate = ?", (plate,))

    return {
        **record,
        "netra": {
            "sightings": (seen or {}).get("n", 0),
            "first_seen": (seen or {}).get("first"),
            "last_seen": (seen or {}).get("last"),
            "watchlisted": bool(wl),
            "watchlist_reason": (wl or {}).get("reason"),
        },
        "retention": "Not stored. Held in memory for "
                     f"{int(registry.CACHE_TTL_S)}s, then discarded.",
    }
