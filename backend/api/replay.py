"""Replay / retry-node endpoint — full implementation in Phase 6."""
from fastapi import APIRouter

router = APIRouter(tags=["replay"])


@router.post("/runs/{run_id}/retry-node")
async def retry_node(run_id: str, body: dict) -> dict:
    return {"stub": "Phase 6"}
