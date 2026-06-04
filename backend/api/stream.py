"""
SSE streaming endpoint — GET /api/runs/{id}/stream

Returns a text/event-stream response that:
  - Replays the full event buffer immediately if the run is already complete.
  - Streams live events until the run finishes if it is still active.
  - Handles client disconnect gracefully (generator is abandoned by FastAPI,
    no orphaned queues because the event_bus queue is owned by the producer).
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.store import runs_repo
from backend.store.events import event_bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stream"])


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    """
    Server-Sent Events stream for a run.

    Content-Type: text/event-stream
    Each event is in SSE wire format: "event: <type>\\ndata: <json>\\n\\n"
    """
    # Verify the run exists before opening a stream
    run = await runs_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    return StreamingResponse(
        _event_generator(run_id, run["status"]),
        media_type="text/event-stream",
        headers={
            # Prevent nginx/load-balancer from buffering the stream
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


async def _event_generator(run_id: str, status: str) -> AsyncIterator[str]:
    """
    Async generator that yields SSE-formatted strings.

    - If the run is already terminal (completed/failed): replay buffer, then stop.
    - Otherwise: subscribe to the live queue until the run closes.

    Client disconnection is handled implicitly: FastAPI cancels the generator
    when the HTTP connection drops, which causes the asyncio.wait_for inside
    event_bus.subscribe() to eventually raise CancelledError and exit cleanly.
    """
    if status in ("completed", "failed"):
        # Already finished — serve replay buffer immediately
        for msg in event_bus.get_replay(run_id):
            yield msg
        return

    # Live streaming — subscribe blocks until each event arrives or keep-alive fires
    try:
        async for msg in event_bus.subscribe(run_id):
            yield msg
    except asyncio.CancelledError:
        # Client disconnected — exit cleanly; the producer still owns the queue
        logger.debug("SSE client disconnected for run %s", run_id)
