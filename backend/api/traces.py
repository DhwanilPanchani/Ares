"""
Trace endpoint — GET /api/runs/{run_id}/trace

Returns the full span tree for a run ordered by started_at.
Used by the replay UI and observability tooling.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.store import spans_repo

router = APIRouter(tags=["traces"])


@router.get("/runs/{run_id}/trace")
async def get_trace(run_id: str) -> dict:
    """
    Return all OTel spans for a run, ordered by started_at.

    Each span has: id, trace_id, parent_id, node_id, name, kind,
    attributes, started_at, ended_at, status_code.

    Returns 404 if no spans exist yet (run hasn't started tracing).
    """
    spans = await spans_repo.list_for_run(run_id)
    return {"run_id": run_id, "spans": spans}
