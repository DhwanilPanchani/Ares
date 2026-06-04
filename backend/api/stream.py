"""SSE event stream endpoint — full implementation in Phase 2."""
from fastapi import APIRouter

router = APIRouter(tags=["stream"])


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> dict:
    return {"stub": "Phase 2"}
