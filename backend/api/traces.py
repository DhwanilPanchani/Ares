"""Trace endpoint — full implementation in Phase 3."""
from fastapi import APIRouter

router = APIRouter(tags=["traces"])


@router.get("/runs/{run_id}/trace")
async def get_trace(run_id: str) -> dict:
    return {"stub": "Phase 3"}
