"""In-process pub/sub feeding the dashboard's Server-Sent Events stream.

SSE rather than WebSockets: the dashboard only ever *receives* live data,
and SSE reconnects by itself, needs no extra dependency, and survives a
laptop sleeping mid-demo. A WebSocket would be more machinery for a
capability nothing here uses.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

# Canonical event names from SIH26127 Table 3. The console's original
# names are kept as aliases and emitted alongside, so the spec contract is
# satisfied without breaking a dashboard that is already subscribed.
CANONICAL = {
    "sighting.created": "anpr.read",
    "alert.created":    "incident.detected",
    "alert.updated":    "incident.updated",
    "camera.health":    "camera.health",
    "review.queued":    "anpr.review_queued",
    "review.decided":   "anpr.review_decided",
}

# One queue per connected browser tab. Bounded, because a tab that stops
# reading must not grow memory without limit.
_subscribers: set[asyncio.Queue] = set()
_MAX_PENDING = 256


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_PENDING)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: str, data: dict[str, Any]) -> None:
    """Fan out to every listener. Never blocks and never raises: a slow
    consumer loses messages rather than stalling ingest.

    Each envelope carries `event_id` and `emitted_at` so a consumer can
    deduplicate a redelivered event without inspecting its body — the
    idempotency requirement in SIH26127 §8.
    """
    payload = json.dumps({
        "event": event,
        "type": CANONICAL.get(event, event),   # canonical spec name
        "event_id": str(uuid.uuid4()),
        "emitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": data,
    }, default=str)
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the oldest message and retry once; a dashboard that is
            # behind should show recent data, not stale data.
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                pass


def subscriber_count() -> int:
    return len(_subscribers)
