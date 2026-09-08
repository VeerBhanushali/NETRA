"""Server-Sent Events: the live feed the command centre subscribes to."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .. import events

router = APIRouter(tags=["stream"])

HEARTBEAT_S = 20.0


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """Long-lived SSE connection.

    A comment heartbeat every 20 s keeps proxies from closing an idle
    connection — the failure mode where the dashboard looks alive but has
    silently stopped receiving anything.
    """
    queue = events.subscribe()

    async def generator():
        try:
            yield 'event: hello\ndata: {"ok":true}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx buffers SSE into uselessness without this.
            "X-Accel-Buffering": "no",
        },
    )
