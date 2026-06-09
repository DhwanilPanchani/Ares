"""
Replay / retry-node endpoint — Phase 6 full implementation.

POST /api/runs/{run_id}/retry-node
  Body: { "node_id": "<dag_node_id>" }

Steps:
  1. Load the run record (goal, dag_json) from the DB.
  2. Identify the target node and all downstream nodes (nodes that directly or
     transitively depend on it).
  3. Reset each of those nodes to status='pending' in the DB, clearing their
     output/error/timing fields.
  4. Recreate the SSE queue for the run_id (so the existing browser stream
     subscriber continues to receive new events).
  5. Rebuild the full LangGraph graph and invoke it with the existing
     thread_id / checkpoint so LangGraph replays only from the changed nodes
     forward.

Design note: Because LangGraph's AsyncSqliteSaver checkpoint for this thread
already has the results of the nodes that were NOT reset, those nodes are
effectively cached — the graph only re-runs the reset nodes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from backend.agents.graph import execute_run
from backend.models import DAGPlan
from backend.store import nodes_repo, runs_repo
from backend.store.events import event_bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["replay"])


class RetryNodeRequest(BaseModel):
    node_id: str


@router.post("/runs/{run_id}/retry-node", status_code=202)
async def retry_node(
    run_id: str,
    body: RetryNodeRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """
    Retry a failed node and all of its downstream dependents.

    Returns 202 immediately. The retry runs in a background task and emits
    events on the existing run's SSE stream so the DAG canvas in the browser
    updates without a page reload.
    """
    # --- Load run ---
    run = await runs_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    # dag_json may be a dict already (parsed by runs_repo) or None
    dag_raw = run.get("dag_json")
    if not dag_raw:
        raise HTTPException(status_code=400, detail="Run has no compiled DAG — cannot replay.")

    if isinstance(dag_raw, str):
        try:
            dag_raw = json.loads(dag_raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"DAG JSON is corrupt: {exc}") from exc

    try:
        dag = DAGPlan.model_validate(dag_raw, strict=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot parse DAG: {exc}") from exc

    goal = run.get("goal", "")

    # --- Validate target node exists ---
    node_ids = {n.id for n in dag.nodes}
    if body.node_id not in node_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Node '{body.node_id}' not found in DAG.",
        )

    # --- Compute the node + all downstream nodes to reset ---
    to_reset = _downstream_nodes(dag, body.node_id)
    logger.info(
        "Replay run %s from node %s — resetting nodes: %s",
        run_id,
        body.node_id,
        to_reset,
    )

    # --- Reset those nodes to pending in SQLite ---
    for nid in to_reset:
        await nodes_repo.reset_to_pending(nid, run_id)

    # --- Recreate the SSE queue so the stream subscriber gets new events ---
    event_bus.create_queue(run_id)

    # Emit a synthetic dag_compiled event so the frontend re-renders the canvas
    await event_bus.emit(
        run_id,
        "dag_compiled",
        {
            "nodes": [
                {"id": n.id, "name": n.name, "depends_on": n.depends_on}
                for n in dag.nodes
            ]
        },
    )

    # --- Fire off the execution pipeline ---
    background_tasks.add_task(_replay_pipeline, run_id, dag, goal)

    return {"status": "retrying", "node_id": body.node_id}


async def _replay_pipeline(run_id: str, dag: DAGPlan, goal: str) -> None:
    """
    Re-execute the LangGraph graph for a partial replay.

    Because the SQLite checkpointer already has the outputs of non-reset
    nodes stored under thread_id=run_id, LangGraph will skip those nodes
    and only execute the ones whose state was changed (the reset ones).
    """
    try:
        await runs_repo.update_status(run_id, "running")
        await execute_run(run_id, dag, goal)
    except Exception as exc:
        logger.error("Replay pipeline error for run %s: %s", run_id, exc, exc_info=True)
        try:
            await runs_repo.update_status(run_id, "failed", error=str(exc))
            await event_bus.emit(run_id, "run_failed", {"error": str(exc)})
        except Exception:
            pass
    finally:
        await event_bus.close(run_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _downstream_nodes(dag: DAGPlan, start_node_id: str) -> set[str]:
    """
    Return the set of node IDs that are downstream of (or equal to) start_node_id.

    Uses a simple BFS over the DAG's dependency edges.
    """
    # Build a forward adjacency map: node → set of nodes that depend on it
    dependents: dict[str, list[str]] = {n.id: [] for n in dag.nodes}
    for node in dag.nodes:
        for dep in node.depends_on:
            dependents[dep].append(node.id)

    # BFS from start_node_id
    visited: set[str] = set()
    queue = [start_node_id]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        queue.extend(dependents.get(current, []))

    return visited
