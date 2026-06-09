"""
Async repository for the nodes table.

Nodes are created in bulk at DAG compile time, then updated individually
as each worker agent runs.

Primary key is composite (id, run_id) — the compiler-generated node name
(e.g. "research_openai") is unique within a run but not across runs.
All write operations require both node_id and run_id to target the correct row.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.store.database import get_db

logger = logging.getLogger(__name__)


async def create_many(nodes: list[dict[str, Any]]) -> None:
    """
    Bulk-insert node records for a run.

    Each dict must have: id, run_id, name, description, depends_on (list).
    Optional: prompt.
    """
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            n["id"],
            n["run_id"],
            n["name"],
            n["description"],
            "pending",
            json.dumps(n.get("depends_on", [])),
            n.get("prompt"),
            now,
        )
        for n in nodes
    ]
    async with get_db() as db:
        await db.executemany(
            """INSERT INTO nodes
               (id, run_id, name, description, status, depends_on, prompt, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await db.commit()
    logger.debug("Inserted %d nodes", len(rows))


async def get(node_id: str, run_id: str) -> dict[str, Any] | None:
    """Return a single node row as a dict, or None if not found."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM nodes WHERE id = ? AND run_id = ?", (node_id, run_id)
        )
        row = await cursor.fetchone()

    if row is None:
        return None
    return _row_to_dict(row)


async def list_for_run(run_id: str) -> list[dict[str, Any]]:
    """Return all nodes for a run, ordered by their creation time."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM nodes WHERE run_id = ?", (run_id,)
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def update_status(
    node_id: str,
    run_id: str,
    status: str,
    *,
    started_at: str | None = None,
    completed_at: str | None = None,
    output: str | None = None,
    error: str | None = None,
) -> None:
    """Update node status and any combination of timing / output / error fields."""
    parts: list[str] = ["status = ?"]
    params: list[Any] = [status]

    if started_at is not None:
        parts.append("started_at = ?")
        params.append(started_at)
    if completed_at is not None:
        parts.append("completed_at = ?")
        params.append(completed_at)
    if output is not None:
        parts.append("output = ?")
        params.append(output)
    if error is not None:
        parts.append("error = ?")
        params.append(error)

    params.extend([node_id, run_id])
    sql = f"UPDATE nodes SET {', '.join(parts)} WHERE id = ? AND run_id = ?"

    async with get_db() as db:
        await db.execute(sql, params)
        await db.commit()


async def update_tool_calls(node_id: str, run_id: str, tool_calls: list[dict]) -> None:
    """Persist the list of tool call records for a node."""
    async with get_db() as db:
        await db.execute(
            "UPDATE nodes SET tool_calls = ? WHERE id = ? AND run_id = ?",
            (json.dumps(tool_calls), node_id, run_id),
        )
        await db.commit()


async def reset_to_pending(node_id: str, run_id: str) -> None:
    """Reset a node back to pending status, clearing all results from a previous run."""
    async with get_db() as db:
        await db.execute(
            """UPDATE nodes
               SET status = 'pending',
                   output = NULL,
                   error = NULL,
                   tool_calls = NULL,
                   started_at = NULL,
                   completed_at = NULL
               WHERE id = ? AND run_id = ?""",
            (node_id, run_id),
        )
        await db.commit()
    logger.debug("Node %s (run %s) reset to pending", node_id, run_id)


async def update_prompt(node_id: str, run_id: str, prompt: str) -> None:
    """Store the system prompt that was sent to the worker agent."""
    async with get_db() as db:
        await db.execute(
            "UPDATE nodes SET prompt = ? WHERE id = ? AND run_id = ?",
            (prompt, node_id, run_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    # Parse JSON columns
    for col in ("depends_on", "tool_calls"):
        if d.get(col) and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except json.JSONDecodeError:
                d[col] = []
    return d
