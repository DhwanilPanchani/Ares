"""
Async repository for the spans table.

Spans are written by the custom OTel exporter (Phase 3).
This module exposes the read interface used by the trace API endpoint.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.store.database import get_db

logger = logging.getLogger(__name__)


async def create(
    span_id: str,
    trace_id: str,
    run_id: str,
    name: str,
    kind: str,
    started_at: str,
    *,
    parent_id: str | None = None,
    node_id: str | None = None,
    attributes: dict | None = None,
    ended_at: str | None = None,
    status_code: str = "OK",
) -> None:
    """Insert a span record (called by the OTel exporter in Phase 3)."""
    async with get_db() as db:
        await db.execute(
            """INSERT INTO spans
               (id, trace_id, parent_id, run_id, node_id, name, kind,
                attributes, started_at, ended_at, status_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                span_id,
                trace_id,
                parent_id,
                run_id,
                node_id,
                name,
                kind,
                json.dumps(attributes or {}),
                started_at,
                ended_at,
                status_code,
            ),
        )
        await db.commit()


async def list_for_run(run_id: str) -> list[dict[str, Any]]:
    """Return all spans for a run ordered by started_at."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM spans WHERE run_id = ? ORDER BY started_at",
            (run_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get(span_id: str) -> dict[str, Any] | None:
    """Return a single span by ID."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM spans WHERE id = ?", (span_id,)
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    if d.get("attributes") and isinstance(d["attributes"], str):
        try:
            d["attributes"] = json.loads(d["attributes"])
        except json.JSONDecodeError:
            d["attributes"] = {}
    return d
