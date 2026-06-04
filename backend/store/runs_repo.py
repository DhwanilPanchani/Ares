"""
Async repository for the runs table.

All methods open their own short-lived database connection via get_db().
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.store.database import get_db

logger = logging.getLogger(__name__)


async def create(run_id: str, goal: str) -> None:
    """Insert a new run record with status 'pending'."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO runs (id, goal, status, created_at)
               VALUES (?, ?, 'pending', ?)""",
            (run_id, goal, now),
        )
        await db.commit()
    logger.debug("Created run %s", run_id)


async def get(run_id: str) -> dict[str, Any] | None:
    """Return a single run row as a dict, or None if not found."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()

    if row is None:
        return None
    return _row_to_dict(row)


async def list_all() -> list[dict[str, Any]]:
    """Return all runs ordered by created_at descending."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM runs ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def update_status(
    run_id: str,
    status: str,
    *,
    completed_at: str | None = None,
    error: str | None = None,
) -> None:
    """Update the status (and optionally completed_at / error) of a run."""
    parts: list[str] = ["status = ?"]
    params: list[Any] = [status]

    if completed_at is not None:
        parts.append("completed_at = ?")
        params.append(completed_at)
    if error is not None:
        parts.append("error = ?")
        params.append(error)

    params.append(run_id)
    sql = f"UPDATE runs SET {', '.join(parts)} WHERE id = ?"

    async with get_db() as db:
        await db.execute(sql, params)
        await db.commit()
    logger.debug("Run %s → %s", run_id, status)


async def update_dag(run_id: str, dag_json: str) -> None:
    """Persist the compiled DAG JSON string."""
    async with get_db() as db:
        await db.execute(
            "UPDATE runs SET dag_json = ? WHERE id = ?",
            (dag_json, run_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    # Parse dag_json back to a dict if present
    if d.get("dag_json") and isinstance(d["dag_json"], str):
        try:
            d["dag_json"] = json.loads(d["dag_json"])
        except json.JSONDecodeError:
            pass
    return d
