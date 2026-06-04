"""
In-memory SSE event bus for Project Ares.

One asyncio.Queue per active run_id. A replay buffer keeps all events so
late-joining clients (clients that connect after a run completes) can catch up.

Usage:
    # Producer side (inside execute_run pipeline)
    event_bus.create_queue(run_id)
    await event_bus.emit(run_id, "node_started", {"node_id": "...", "name": "..."})
    await event_bus.close(run_id)

    # Consumer side (SSE endpoint)
    async for sse_msg in event_bus.subscribe(run_id):
        yield sse_msg
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Sentinel object that signals end-of-stream to subscribers
_SENTINEL: object = object()


class EventBus:
    def __init__(self) -> None:
        # Active run queues (removed when closed)
        self._queues: dict[str, asyncio.Queue] = {}
        # Full replay buffer — kept even after close for late subscribers
        self._replay: dict[str, list[str]] = {}
        # Set of run_ids that have been closed
        self._closed: set[str] = set()

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def create_queue(self, run_id: str) -> None:
        """Create a new event queue for a run. Must be called before any emit()."""
        self._queues[run_id] = asyncio.Queue(maxsize=1000)
        self._replay[run_id] = []
        self._closed.discard(run_id)
        logger.debug("SSE queue created for run %s", run_id)

    async def emit(self, run_id: str, event_type: str, data: dict) -> None:
        """
        Emit one SSE event to all subscribers of run_id.

        The event is also appended to the replay buffer so late joiners
        can catch up on already-completed runs.
        """
        payload = json.dumps({"event": event_type, "run_id": run_id, "data": data})
        # SSE wire format: "event: <type>\ndata: <json>\n\n"
        sse_msg = f"event: {event_type}\ndata: {payload}\n\n"

        # Append to replay buffer regardless of queue state
        if run_id in self._replay:
            self._replay[run_id].append(sse_msg)

        # Push to live queue if the run is still active
        if run_id in self._queues and run_id not in self._closed:
            try:
                self._queues[run_id].put_nowait(sse_msg)
            except asyncio.QueueFull:
                logger.warning(
                    "SSE queue full for run %s — dropping event '%s'",
                    run_id,
                    event_type,
                )

    async def close(self, run_id: str) -> None:
        """
        Mark a run as complete and wake up any blocked subscribers.

        The replay buffer is kept so late subscribers can still read the history.
        """
        self._closed.add(run_id)
        queue = self._queues.pop(run_id, None)
        if queue is not None:
            # Sentinel wakes any subscriber blocked on queue.get()
            await queue.put(_SENTINEL)
        logger.debug("SSE queue closed for run %s", run_id)

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    async def subscribe(self, run_id: str) -> AsyncIterator[str]:
        """
        Async generator yielding SSE-formatted strings for run_id.

        - If the run is already complete: replays the entire buffer, then returns.
        - If the run is active: yields live events until the run closes,
          sending a keep-alive comment every 25 s to prevent proxy timeouts.
        - If run_id is unknown: returns immediately (empty stream).
        """
        # Already-completed run: serve from replay buffer
        if run_id in self._closed:
            for msg in self._replay.get(run_id, []):
                yield msg
            return

        # Unknown run (neither active nor closed): empty stream
        if run_id not in self._queues:
            logger.warning("subscribe() called for unknown run_id %s", run_id)
            return

        # Hold a direct reference to the queue object — it may be popped from
        # self._queues by close() while we are still reading, and that is fine.
        queue = self._queues[run_id]

        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=25.0)
            except asyncio.TimeoutError:
                # Keep-alive comment — invisible to EventSource but prevents
                # nginx/load-balancer timeouts
                yield ": keep-alive\n\n"
                continue

            if msg is _SENTINEL:
                break
            yield msg

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_replay(self, run_id: str) -> list[str]:
        """Return all buffered SSE strings for a run (empty list if unknown)."""
        return self._replay.get(run_id, [])

    def cleanup(self, run_id: str) -> None:
        """Free memory for a run whose replay buffer is no longer needed."""
        self._queues.pop(run_id, None)
        self._replay.pop(run_id, None)
        self._closed.discard(run_id)


# ---------------------------------------------------------------------------
# Module-level singleton — imported by all producers and consumers
# ---------------------------------------------------------------------------
event_bus = EventBus()
