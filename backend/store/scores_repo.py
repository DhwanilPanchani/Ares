"""
Async repository for the scores table.

Trust scores are written by the Critic agent (Phase 4).
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
    run_id: str,
    factual_grounding: float,
    goal_completion: float,
    tool_error_rate: float,
    trust_score: float,
    critique_text: str,
    flagged_span_ids: list[str],
) -> str:
    """Persist a trust score for a run. Returns the score ID."""
    score_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    async with get_db() as db:
        await db.execute(
            """INSERT INTO scores
               (id, run_id, factual_grounding, goal_completion, tool_error_rate,
                trust_score, critique_text, flagged_span_ids, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                score_id,
                run_id,
                factual_grounding,
                goal_completion,
                tool_error_rate,
                trust_score,
                critique_text,
                json.dumps(flagged_span_ids),
                now,
            ),
        )
        await db.commit()

    logger.debug("Stored trust score %.2f for run %s", trust_score, run_id)
    return score_id


async def get_for_run(run_id: str) -> dict[str, Any] | None:
    """Return the most recent trust score for a run, or None if not yet scored."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT * FROM scores WHERE run_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (run_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        return None
    return _row_to_dict(row)


async def list_for_run(run_id: str) -> list[dict[str, Any]]:
    """Return all scores for a run (usually just one)."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM scores WHERE run_id = ? ORDER BY created_at DESC",
            (run_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    if d.get("flagged_span_ids") and isinstance(d["flagged_span_ids"], str):
        try:
            d["flagged_span_ids"] = json.loads(d["flagged_span_ids"])
        except json.JSONDecodeError:
            d["flagged_span_ids"] = []
    return d
