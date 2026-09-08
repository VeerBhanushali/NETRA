"""Operator-facing endpoints for the AI arbitration layer."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import ai, config, db
from ..schemas import NLQueryIn

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status")
def ai_status() -> dict:
    """What the admin screen's cost meter reads.

    Exposed deliberately: a surveillance system calling a third-party
    model should make that fact visible to its own operators, not bury it.
    """
    spent = ai.spent_today_usd()
    row = db.query_one("SELECT COUNT(*) AS n, SUM(cached) AS c FROM ai_inferences")
    return {
        "enabled": config.AI_ENABLED,
        "provider": config.AI_PROVIDER if config.AI_ENABLED else "stub (local only)",
        "cache_only": config.AI_CACHE_ONLY,
        "budget_usd": config.AI_DAILY_BUDGET_USD,
        "spent_24h_usd": round(spent, 4),
        "budget_remaining_usd": round(max(0.0, config.AI_DAILY_BUDGET_USD - spent), 4),
        "calls_total": (row or {}).get("n", 0),
        "calls_served_from_cache": (row or {}).get("c", 0) or 0,
    }


@router.get("/ledger")
def ai_ledger(limit: int = 100) -> dict:
    rows = db.query(
        "SELECT * FROM ai_inferences ORDER BY id DESC LIMIT ?", (min(limit, 500),))
    return {"count": len(rows), "results": rows}


@router.post("/query")
def natural_language_query(body: NLQueryIn) -> dict:
    """Natural-language investigative search.

    The model only ever proposes SQL; `guard_sql` decides whether it runs.
    The generated statement is returned alongside the rows so the
    operator can see exactly what was executed — an investigator should
    never act on numbers whose derivation is hidden from them.
    """
    db.audit(body.actor, "nl_query", None, body.question)

    verdict = ai.nl_to_sql(body.question)
    if not verdict or verdict.get("_stub"):
        raise HTTPException(
            status_code=503,
            detail="AI provider is disabled. Set NETRA_AI_ENABLED=true and "
                   "NETRA_AI_API_KEY to use natural-language search.")

    try:
        safe_sql = ai.guard_sql(verdict["sql"])
    except ValueError as exc:
        # The guard rejected it. Surface why rather than failing opaquely.
        raise HTTPException(status_code=400,
                            detail=f"generated SQL rejected: {exc}") from exc

    try:
        rows = db.query(safe_sql)
    except Exception as exc:                          # noqa: BLE001
        raise HTTPException(status_code=400,
                            detail=f"generated SQL failed: {exc}") from exc

    return {
        "question": body.question,
        "sql": safe_sql,
        "explanation": verdict.get("explanation", ""),
        "count": len(rows),
        "results": rows,
    }
