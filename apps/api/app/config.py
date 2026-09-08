"""Central configuration.

Every tunable number the rules engine depends on lives here, not scattered
through the code, because during a demo you will want to change one
threshold and immediately see the effect.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- paths ------------------------------------------------------------
def _find_root() -> Path:
    """Locate the project root by walking up for infra/schema.sql.

    A fixed `parents[3]` breaks the moment the layout differs — which it
    does inside the container image, where the app is not nested under
    apps/api. Searching upward works in both, and NETRA_ROOT overrides it
    for anything exotic.
    """
    if env := os.getenv("NETRA_ROOT"):
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infra" / "schema.sql").is_file():
            return parent
    return here.parents[2]          # sensible fallback: apps/api


ROOT = _find_root()
DB_PATH = Path(os.getenv("NETRA_DB", ROOT / "data" / "netra.db"))
SCHEMA_PATH = Path(os.getenv("NETRA_SCHEMA", ROOT / "infra" / "schema.sql"))
EVIDENCE_DIR = Path(os.getenv("NETRA_EVIDENCE_DIR", ROOT / "data" / "evidence"))

# --- api --------------------------------------------------------------
API_HOST = os.getenv("NETRA_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("NETRA_API_PORT", "8000"))
# Shared secret the edge worker signs ingest with. Dev default is
# intentionally obvious so nobody mistakes it for a real secret.
INGEST_TOKEN = os.getenv("NETRA_INGEST_TOKEN", "dev-ingest-token")
CORS_ORIGINS = os.getenv("NETRA_CORS", "http://localhost:3000,http://127.0.0.1:3000").split(",")

# --- temporal voting --------------------------------------------------
# A voted plate at or above this confidence is published as fact.
AUTO_ACCEPT_CONFIDENCE = float(os.getenv("NETRA_AUTO_ACCEPT", "0.90"))
# Below auto-accept but at or above this goes to a human. Below this the
# read is discarded entirely — publishing a coin-flip as a sighting is
# worse than having no sighting.
REVIEW_CONFIDENCE = float(os.getenv("NETRA_REVIEW_MIN", "0.55"))
MIN_FRAMES_FOR_VOTE = 3

# --- rules engine -----------------------------------------------------
# Straight-line distance always understates real road distance. 1.35 is a
# common urban circuity factor (road distance / crow-flight distance);
# using it makes the cloned-plate rule *conservative*, i.e. it needs a
# more extreme implied speed before it will accuse anyone.
CIRCUITY_FACTOR = float(os.getenv("NETRA_CIRCUITY", "1.35"))

# Above this implied point-to-point speed, a single vehicle could not
# physically have made the trip. 150 km/h is far above any plausible
# Indian urban journey, so crossing it means the plate exists twice.
CLONE_SPEED_KMH = float(os.getenv("NETRA_CLONE_SPEED", "150.0"))
# Two cameras closer than this are effectively the same junction; the
# distance/time maths is meaningless there, so never raise a clone alert.
CLONE_MIN_DISTANCE_M = 400.0
# Tolerance for unsynchronised camera clocks. Without this, two cameras
# 20 s out of sync manufacture cloned-plate alerts all day.
CLOCK_SKEW_S = 15.0
# A clone accusation is serious, so only trust confident reads.
CLONE_MIN_CONFIDENCE = 0.92

SPEED_LIMIT_KMH = float(os.getenv("NETRA_SPEED_LIMIT", "60.0"))
# Only flag a meaningful exceedance — measurement error near the limit
# would otherwise bury operators in noise.
SPEED_ALERT_MARGIN_KMH = 15.0

# Loitering: N passes through the same zone inside the window.
# Tuned against seeded background traffic: a commercial zone has cameras
# only a few hundred metres apart, so 4 passes is an ordinary shopping
# trip and produced a flood of false positives. 5 passes sustained over
# at least 8 minutes is the point where "passing through" stops being a
# plausible explanation.
LOITER_WINDOW_MIN = int(os.getenv("NETRA_LOITER_WINDOW", "30"))
LOITER_MIN_SIGHTINGS = int(os.getenv("NETRA_LOITER_COUNT", "5"))
# Guards against a stuck tracker emitting five passes in ninety seconds.
LOITER_MIN_DWELL_S = float(os.getenv("NETRA_LOITER_DWELL", "480"))

# Suppress a repeat of the same alert for the same subject this long.
ALERT_COOLDOWN_S = 600

# --- AI arbitration layer --------------------------------------------
AI_PROVIDER = os.getenv("NETRA_AI_PROVIDER", "stub")   # stub | gemini | openai | anthropic | grok
AI_API_KEY = os.getenv("NETRA_AI_API_KEY", "")
AI_MODEL = os.getenv("NETRA_AI_MODEL", "")
AI_ENABLED = os.getenv("NETRA_AI_ENABLED", "false").lower() == "true"
# Hard ceiling. On breach the service degrades to local-only rather than
# silently spending money.
AI_DAILY_BUDGET_USD = float(os.getenv("NETRA_AI_BUDGET", "2.00"))
AI_TIMEOUT_S = 20.0
# Serve from cache only — the switch you flip when venue wifi dies.
AI_CACHE_ONLY = os.getenv("NETRA_AI_CACHE_ONLY", "false").lower() == "true"
