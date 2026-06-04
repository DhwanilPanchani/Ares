"""
Runs API router — POST /runs, GET /runs, GET /runs/{id}

Full implementation: Phase 2.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["runs"])


@router.get("/runs")
async def list_runs() -> list[dict]:
    """List all runs ordered by created_at descending. (Phase 2 stub)"""
    return []


@router.post("/runs", status_code=201)
async def create_run(body: dict) -> dict:
    """Create and start a new run. (Phase 2 stub)"""
    return {"status": "stub — full implementation in Phase 2"}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    """Get a single run with all its nodes. (Phase 2 stub)"""
    return {"id": run_id, "status": "stub — full implementation in Phase 2"}
