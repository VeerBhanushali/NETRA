"""The AI Arbitration Service.

Principle, and the answer to "did you just wrap Gemini?":
local models handle 100% of the per-frame volume at zero marginal cost.
A hosted model is a *bounded escalation path*, called only when

  1. temporal voting lands below the auto-accept threshold  (~2-5% of vehicles)
  2. a cheap local motion trigger fires on a clip           (rare)
  3. an operator explicitly asks a question                 (human-initiated)

Everything here degrades to local-only rather than failing: no key, no
budget, no network, or NETRA_AI_CACHE_ONLY=true all produce a usable
answer without a call.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import timedelta
from typing import Any, Protocol

from . import config, db
from .db import parse_ts, utcnow

# In-process response cache keyed by a hash of the input. A repeated crop
# is never paid for twice, and cache-warming before the demo means the
# whole thing runs with the network cable pulled.
_CACHE: dict[str, dict] = {}


class AIProvider(Protocol):
    """Swapping provider is a config change, not a rewrite."""
    name: str

    def complete_json(self, prompt: str, schema: dict,
                      images: list[bytes] | None = None) -> dict: ...


class StubProvider:
    """Deterministic offline provider — the default.

    It is not a mock for tests only: it is the fallback that keeps the
    demo working when there is no key and no wifi.
    """
    name = "stub"

    def complete_json(self, prompt: str, schema: dict,
                      images: list[bytes] | None = None) -> dict:
        return {"_stub": True,
                "note": "AI provider disabled; local result used unchanged."}


class GeminiProvider:
    """Google Gemini. Requires `pip install google-genai` and a key.

    > **Verify:** model id and pricing before relying on either; both
    > change faster than this document does.
    """
    name = "gemini"

    def __init__(self, api_key: str, model: str = "") -> None:
        self.api_key = api_key
        self.model = model or "gemini-2.0-flash"

    def complete_json(self, prompt: str, schema: dict,
                      images: list[bytes] | None = None) -> dict:
        from google import genai                     # imported lazily: optional dep
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        parts: list[Any] = [prompt]
        for img in images or []:
            parts.append(types.Part.from_bytes(data=img, mime_type="image/jpeg"))
        resp = client.models.generate_content(
            model=self.model,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.0,          # this is extraction, not creativity
            ),
        )
        return json.loads(resp.text)


def get_provider() -> AIProvider:
    if not config.AI_ENABLED or not config.AI_API_KEY:
        return StubProvider()
    if config.AI_PROVIDER == "gemini":
        return GeminiProvider(config.AI_API_KEY, config.AI_MODEL)
    # Unknown provider name must not crash ingest.
    return StubProvider()


# --- budget guard -----------------------------------------------------

def spent_today_usd() -> float:
    since = (parse_ts(utcnow()) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = db.query_one(
        "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM ai_inferences WHERE ts >= ?", (since,))
    return float(row["s"]) if row else 0.0


def _record(provider: str, use_case: str, input_hash: str, cached: bool,
            latency_ms: int, cost: float, ok: bool, detail: str = "") -> None:
    db.execute(
        "INSERT INTO ai_inferences (ts, provider, use_case, input_hash, cached,"
        " latency_ms, cost_usd, ok, detail) VALUES (?,?,?,?,?,?,?,?,?)",
        (utcnow(), provider, use_case, input_hash, int(cached), latency_ms,
         cost, int(ok), detail))


def call(use_case: str, prompt: str, schema: dict,
         images: list[bytes] | None = None, est_cost: float = 0.002) -> dict | None:
    """Single entry point for every hosted call.

    Returns None whenever the call did not happen — disabled, cached-only
    with a cache miss, over budget, or failed. Callers must treat None as
    "use the local answer", never as an error.
    """
    key = hashlib.sha256(
        (use_case + prompt + str(len(images or []))).encode()).hexdigest()[:32]

    if key in _CACHE:
        _record("cache", use_case, key, True, 0, 0.0, True)
        return _CACHE[key]

    provider = get_provider()
    if isinstance(provider, StubProvider) or config.AI_CACHE_ONLY:
        return None
    if spent_today_usd() + est_cost > config.AI_DAILY_BUDGET_USD:
        _record(provider.name, use_case, key, False, 0, 0.0, False, "budget exceeded")
        return None

    started = time.monotonic()
    try:
        result = provider.complete_json(prompt, schema, images)
    except Exception as exc:                          # noqa: BLE001
        _record(provider.name, use_case, key, False,
                int((time.monotonic() - started) * 1000), 0.0, False, repr(exc)[:200])
        return None

    _CACHE[key] = result
    _record(provider.name, use_case, key, False,
            int((time.monotonic() - started) * 1000), est_cost, True)
    return result


# --- use case 1: plate arbitration ------------------------------------

PLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "plate": {"type": "string"},
        "confidence": {"type": "number"},
        "unreadable": {"type": "boolean"},
    },
    "required": ["plate", "confidence", "unreadable"],
}


def arbitrate_plate(candidates: list[dict], crops: list[bytes] | None = None) -> dict | None:
    """Escalate a low-confidence voted plate.

    The model gets the local candidates and their vote margins, not a
    blank slate, so it is adjudicating rather than guessing. It may not
    overrule a confident local consensus — see merge_arbitration.
    """
    listing = "\n".join(f"  {c['plate']}  (local score {c['score']:.2f})" for c in candidates)
    prompt = (
        "You are reading an Indian vehicle number plate from CCTV crops.\n"
        "Format: 2 letters (state) + 1-2 digits (RTO) + 1-3 letters (series) "
        "+ 4 digits, e.g. MH12DE1433. BH-series looks like 22BH1234AA.\n"
        "Local OCR produced these candidates by voting across video frames:\n"
        f"{listing}\n\n"
        "Pick the correct plate, or correct it if all candidates are wrong. "
        "Set unreadable=true rather than guessing if the crops do not support "
        "a confident reading. Return JSON only."
    )
    return call("plate_arbitration", prompt, PLATE_SCHEMA, crops, est_cost=0.001)


def merge_arbitration(local_plate: str, local_conf: float, verdict: dict | None) -> tuple[str, float, str]:
    """Combine the hosted verdict with the local vote.

    Rule: the API assists on genuinely uncertain reads and is ignored
    otherwise. Letting a remote model silently overwrite a strong local
    consensus would make the system's accuracy unexplainable — and the
    local vote is the part we can actually measure.
    """
    if not verdict or verdict.get("_stub"):
        return local_plate, local_conf, "local"
    if verdict.get("unreadable"):
        return local_plate, local_conf, "local (AI: unreadable)"
    if local_conf >= config.AUTO_ACCEPT_CONFIDENCE:
        return local_plate, local_conf, "local (AI not consulted)"

    ai_plate = str(verdict.get("plate", "")).upper().replace(" ", "")
    ai_conf = float(verdict.get("confidence", 0.0))
    if ai_plate == local_plate:
        # Independent agreement is genuine evidence, but cap the boost —
        # two correlated readers are not two independent ones.
        return local_plate, min(0.99, max(local_conf, ai_conf) + 0.05), "local+AI agree"
    if ai_conf >= 0.9:
        return ai_plate, ai_conf, "AI override (local uncertain)"
    return local_plate, local_conf, "local (AI disagreed weakly)"


# --- use case 4: natural-language search ------------------------------

SQL_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}, "explanation": {"type": "string"}},
    "required": ["sql", "explanation"],
}

# The model may only ever see these. Querying a view rather than a table
# means content it reads cannot reach columns we did not choose to expose.
ALLOWED_VIEWS = ("v_sightings", "v_alerts", "v_cameras")

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace|"
    r"truncate|grant|vacuum)\b", re.I)


def guard_sql(sql: str) -> str:
    """Reject anything that is not a single, bounded, read-only SELECT.

    Prompt injection through database content is the real threat here: a
    plate string or an alert note could contain instructions. The defence
    is not to ask the model nicely, it is that nothing it can emit is
    permitted to do damage.
    """
    s = sql.strip().rstrip(";").strip()
    if not s.lower().startswith("select"):
        raise ValueError("only SELECT statements are permitted")
    if ";" in s:
        raise ValueError("multiple statements are not permitted")
    if _FORBIDDEN.search(s):
        raise ValueError("statement contains a forbidden keyword")
    if not any(v in s for v in ALLOWED_VIEWS):
        raise ValueError(f"query must read from one of {ALLOWED_VIEWS}")
    if not re.search(r"\blimit\b", s, re.I):
        s += " LIMIT 200"                 # never let a query return the world
    return s


def nl_to_sql(question: str) -> dict | None:
    prompt = (
        "Translate the investigator's question into ONE read-only SQLite SELECT.\n"
        "You may only read these views:\n"
        "  v_sightings(plate, camera_id, camera_name, lat, lon, seen_at, confidence,"
        " color, vehicle_type, speed_kmh, zone_id)\n"
        "  v_alerts(id, alert_type, severity, status, plate, camera_id, camera_name,"
        " occurred_at, title, detail)\n"
        "  v_cameras(id, name, lat, lon, zone_id, zone_name, is_active)\n"
        "Timestamps are ISO-8601 UTC text, e.g. '2026-09-06T14:03:00Z', and sort\n"
        "lexicographically. Always include a LIMIT. Return JSON only.\n\n"
        f"Question: {question}"
    )
    return call("nl_query", prompt, SQL_SCHEMA, est_cost=0.0005)
