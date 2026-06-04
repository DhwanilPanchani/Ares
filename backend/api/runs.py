"""
Runs REST API — POST /api/runs, GET /api/runs, GET /api/runs/{id}

POST /api/runs:
  Validates the goal, creates a run record (status=pending), starts the
  full execution pipeline as a FastAPI BackgroundTask, and returns 201
  immediately so the client can connect to the SSE stream.

GET /api/runs:
  Returns all runs ordered by created_at descending, each with its trust
  score if available.

GET /api/runs/{id}:
  Returns a single run with all its node records.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend.agents.compiler import compile_dag
from backend.agents.graph import execute_run
from backend.models import (
    CompilerError,
    NodeResponse,
    RunCreate,
    RunResponse,
    ToolCallRecord,
    TrustScore,
)
from backend.store import nodes_repo, runs_repo, scores_repo
from backend.store.events import event_bus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])


# ==============================================================================
# Background pipeline
# ==============================================================================


async def _run_pipeline(run_id: str, goal: str) -> None:
    """
    Full execution pipeline run as a FastAPI BackgroundTask.

    Steps:
      1. Create SSE queue.
      2. Emit run_started.
      3. Compile DAG (calls Ollama).
      4. Persist nodes to SQLite.
      5. Emit dag_compiled.
      6. Execute the LangGraph graph (execute_run).
      7. On any error: emit run_failed, update run status.
      8. Always close the SSE queue.
    """
    event_bus.create_queue(run_id)

    try:
        # Notify frontend that execution has started
        await event_bus.emit(run_id, "run_started", {"goal": goal, "node_count": 0})

        # ---- Phase 1: Compile DAG ----
        await runs_repo.update_status(run_id, "compiling")

        try:
            dag: DAGPlan = await compile_dag(goal)
        except CompilerError as exc:
            logger.error("DAG compilation failed for run %s: %s", run_id, exc)
            await runs_repo.update_status(run_id, "failed", error=str(exc))
            await event_bus.emit(run_id, "run_failed", {"error": str(exc)})
            return

        # Persist the compiled DAG JSON
        dag_json_str = dag.model_dump_json()
        await runs_repo.update_dag(run_id, dag_json_str)

        # ---- Phase 2: Create node records ----
        # Use the DAG node id directly as the DB primary key so that graph.py
        # can update node status by dag_node.id without a separate mapping.
        node_records = [
            {
                "id": n.id,
                "run_id": run_id,
                "name": n.name,
                "description": n.description,
                "depends_on": n.depends_on,
                "prompt": None,
            }
            for n in dag.nodes
        ]
        await nodes_repo.create_many(node_records)


        # ---- Notify frontend of compiled DAG ----
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

        # Update run status to running
        await runs_repo.update_status(run_id, "running")

        # ---- Phase 3: Execute via LangGraph ----
        # execute_run handles node events, run_completed/run_failed, and status updates
        await execute_run(run_id, dag, goal)

    except Exception as exc:
        logger.error("Pipeline error for run %s: %s", run_id, exc, exc_info=True)
        try:
            await runs_repo.update_status(run_id, "failed", error=str(exc))
            await event_bus.emit(run_id, "run_failed", {"error": str(exc)})
        except Exception:
            pass  # best-effort cleanup

    finally:
        # Always close the SSE queue so subscribers wake up
        await event_bus.close(run_id)



# ==============================================================================
# Endpoints
# ==============================================================================


@router.post("/runs", status_code=201, response_model=RunResponse)
async def create_run(
    body: RunCreate,
    background_tasks: BackgroundTasks,
) -> RunResponse:
    """
    Create a new run and start the execution pipeline.

    Returns 201 immediately with status=pending. The client should then
    connect to GET /api/runs/{id}/stream to follow execution in real time.
    """
    run_id = str(uuid.uuid4())
    await runs_repo.create(run_id, body.goal)

    # Start full pipeline in background — never blocks the response
    background_tasks.add_task(_run_pipeline, run_id, body.goal)

    return RunResponse(
        id=run_id,
        goal=body.goal,
        status="pending",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/runs", response_model=list[RunResponse])
async def list_runs() -> list[RunResponse]:
    """Return all runs ordered by created_at descending, each with trust score."""
    runs = await runs_repo.list_all()
    result = []
    for run in runs:
        score_row = await scores_repo.get_for_run(run["id"])
        result.append(_build_run_response(run, score_row=score_row))
    return result


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str) -> RunResponse:
    """Return a single run with all its node records."""
    run = await runs_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    nodes = await nodes_repo.list_for_run(run_id)
    score_row = await scores_repo.get_for_run(run_id)

    return _build_run_response(run, nodes=nodes, score_row=score_row)


# ==============================================================================
# Response builders
# ==============================================================================


def _build_run_response(
    run: dict[str, Any],
    *,
    nodes: list[dict[str, Any]] | None = None,
    score_row: dict[str, Any] | None = None,
) -> RunResponse:
    """Convert raw DB dicts into a RunResponse Pydantic model."""
    trust_score: TrustScore | None = None
    if score_row:
        trust_score = TrustScore(
            factual_grounding=score_row["factual_grounding"],
            goal_completion=score_row["goal_completion"],
            tool_error_rate=score_row["tool_error_rate"],
            trust_score=score_row["trust_score"],
            critique_text=score_row["critique_text"],
            flagged_span_ids=score_row.get("flagged_span_ids", []),
        )

    node_responses: list[NodeResponse] = []
    for n in nodes or []:
        raw_tool_calls = n.get("tool_calls") or []
        if isinstance(raw_tool_calls, str):
            try:
                raw_tool_calls = json.loads(raw_tool_calls)
            except json.JSONDecodeError:
                raw_tool_calls = []

        node_responses.append(
            NodeResponse(
                id=n["id"],
                run_id=n["run_id"],
                name=n["name"],
                description=n["description"],
                status=n["status"],
                depends_on=n.get("depends_on") or [],
                prompt=n.get("prompt"),
                output=n.get("output"),
                tool_calls=[
                    ToolCallRecord(
                        tool=tc.get("tool", ""),
                        args=tc.get("args", {}),
                        result=tc.get("result"),
                        error=tc.get("error"),
                    )
                    for tc in raw_tool_calls
                ],
                started_at=n.get("started_at"),
                completed_at=n.get("completed_at"),
                error=n.get("error"),
            )
        )

    dag_json = run.get("dag_json")
    if isinstance(dag_json, str):
        try:
            dag_json = json.loads(dag_json)
        except json.JSONDecodeError:
            dag_json = None

    return RunResponse(
        id=run["id"],
        goal=run["goal"],
        status=run["status"],
        dag_json=dag_json,
        created_at=run["created_at"],
        completed_at=run.get("completed_at"),
        error=run.get("error"),
        trust_score=trust_score,
        nodes=node_responses,
    )
