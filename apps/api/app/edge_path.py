"""Import edge modules from the API process.

apps/edge is a sibling package, not a dependency of apps/api, because the
edge workers are meant to be deployable on their own hardware without the
API. The API still needs a handful of shared definitions — matching
thresholds, single-frame analysis — and duplicating them would let the
two halves drift apart and disagree about what a match is.

One place does the path insert so there is one thing to change if the
layout moves.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

EDGE_ROOT = Path(__file__).resolve().parents[2] / "edge"


def edge_import(name: str):
    """Import `edge.<something>`, making apps/edge importable first."""
    p = str(EDGE_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)
    return importlib.import_module(name)
