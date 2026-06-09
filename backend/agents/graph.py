"""
LangGraph execution engine for Project Ares.

Builds a dynamic StateGraph from a compiled DAGPlan and executes it
with an AsyncSqliteSaver checkpointer wired to ares.db.

Key design decisions:
- AresState uses operator.or_ for dict merges (parallel nodes write different keys)
- AresState uses operator.add for list merges (tool_calls_log accumulates)
- Each DAG node becomes a LangGraph node function
- SSE events are emitted from within node functions — callers don't stream state
- Exceptions inside node functions are caught and turned into failure states
  so the graph continues executing other branches
"""

from __future__ import annotations

import json
import logging
import operator
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from backend.agents.prompts import WORKER_SYSTEM_PROMPT
from backend.agents.worker import run_worker
from backend.config import settings
from backend.models import DAGNode, DAGPlan
from backend.store import nodes_repo, runs_repo
from backend.store.events import event_bus

logger = logging.getLogger(__name__)


# ==============================================================================
# State Definition
# ==============================================================================


class AresState(TypedDict):
    """
    Typed state that flows through the LangGraph StateGraph.

    Merge strategies:
    - node_outputs:  operator.or_ — parallel nodes each write their own key,
                     the merge keeps all keys (last-write-wins per key is fine
                     since each node writes only its own id).
    - node_statuses: operator.or_ — same pattern.
    - tool_calls_log: operator.add — every node appends its tool calls; the
                      accumulated list grows across the whole run.
    """

    run_id: str
    goal: str
    dag: dict  # DAGPlan serialised to a plain dict

    node_outputs: Annotated[dict[str, str], operator.or_]
    node_statuses: Annotated[dict[str, str], operator.or_]
    tool_calls_log: Annotated[list[dict[str, Any]], operator.add]

    final_output: str
    error: str | None


# ==============================================================================
# Graph Construction
# ==============================================================================


def _make_node_fn(dag_node: DAGNode, run_id: str):
    """
    Factory: return an async LangGraph node function for dag_node.

    The returned function:
      1. Emits node_started SSE.
      2. Calls run_worker() (which streams token chunks internally).
      3. Persists output to SQLite.
      4. Emits node_completed SSE.
      5. Returns a partial AresState update.
    On any exception, emits node_failed and returns a failure state update.
    """

    async def node_fn(state: AresState) -> dict[str, Any]:
        node_id = dag_node.id
        started_at = datetime.now(timezone.utc).isoformat()

        # ---- Mark running ----
        await nodes_repo.update_status(node_id, run_id, "running", started_at=started_at)
        await event_bus.emit(
            run_id,
            "node_started",
            {"node_id": node_id, "name": dag_node.name},
        )

        try:
            # Gather upstream outputs that this node declared it depends on
            upstream_outputs: dict[str, str] = {
                dep: state["node_outputs"].get(dep, "")
                for dep in dag_node.depends_on
            }

            # Run the worker agent (streams token_chunk events internally)
            output, tool_calls = await run_worker(dag_node, run_id, upstream_outputs)

            completed_at = datetime.now(timezone.utc).isoformat()

            # Persist to SQLite
            await nodes_repo.update_status(
                node_id,
                run_id,
                "success",
                completed_at=completed_at,
                output=output,
            )
            if tool_calls:
                await nodes_repo.update_tool_calls(node_id, run_id, tool_calls)

            # Emit completion event
            await event_bus.emit(
                run_id,
                "node_completed",
                {"node_id": node_id, "output": output},
            )

            return {
                "node_outputs": {node_id: output},
                "node_statuses": {node_id: "success"},
                "tool_calls_log": tool_calls,
            }

        except Exception as exc:
            logger.error(
                "Node %s failed in run %s: %s", node_id, run_id, exc, exc_info=True
            )
            error_msg = str(exc)
            completed_at = datetime.now(timezone.utc).isoformat()

            await nodes_repo.update_status(
                node_id, run_id, "failed", completed_at=completed_at, error=error_msg
            )
            await event_bus.emit(
                run_id,
                "node_failed",
                {"node_id": node_id, "error": error_msg},
            )

            # Return a failure state — the graph continues other branches
            return {
                "node_outputs": {node_id: ""},
                "node_statuses": {node_id: "failed"},
                "tool_calls_log": [],
            }

    # Give the function a unique name so LangGraph can distinguish nodes
    node_fn.__name__ = dag_node.id
    return node_fn


def _build_graph(dag: DAGPlan, run_id: str) -> StateGraph:
    """
    Construct a StateGraph from a compiled DAGPlan.

    Wiring rules:
    - Root nodes (empty depends_on) receive edges from START.
    - Non-root nodes receive edges from each of their dependencies.
    - Terminal nodes (nothing depends on them) receive edges to END.
    LangGraph automatically handles fan-in: a node waits for ALL its
    in-edges to complete before executing.
    """
    graph: StateGraph = StateGraph(AresState)

    # Register all node functions
    for dag_node in dag.nodes:
        graph.add_node(dag_node.id, _make_node_fn(dag_node, run_id))

    # Identify which nodes are depended-upon (not terminal)
    depended_on: set[str] = set()
    for n in dag.nodes:
        depended_on.update(n.depends_on)

    # Wire edges
    for dag_node in dag.nodes:
        if not dag_node.depends_on:
            # Root node → runs immediately after START
            graph.add_edge(START, dag_node.id)
        else:
            # Non-root → wait for each declared dependency
            for dep in dag_node.depends_on:
                graph.add_edge(dep, dag_node.id)

        # Terminal node → leads to END
        if dag_node.id not in depended_on:
            graph.add_edge(dag_node.id, END)

    return graph


# ==============================================================================
# Public API
# ==============================================================================


async def execute_run(run_id: str, dag: DAGPlan, goal: str) -> None:
    """
    Execute a compiled DAGPlan using the LangGraph StateGraph.

    This function is called from the background pipeline after compilation.
    It:
      1. Builds the StateGraph from dag.
      2. Compiles it with the AsyncSqliteSaver checkpointer.
      3. Invokes the graph, which emits SSE events from within each node.
      4. Aggregates the final output.
      5. Updates the run status to completed (or failed on exception).

    The SSE queue must already be created (by the pipeline caller) before
    this function is called.
    """
    try:
        graph = _build_graph(dag, run_id)

        db_path = str(settings.db_path)

        async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
            compiled = graph.compile(checkpointer=checkpointer)

            initial_state: AresState = {
                "run_id": run_id,
                "goal": goal,
                "dag": dag.model_dump(),
                "node_outputs": {},
                "node_statuses": {n.id: "pending" for n in dag.nodes},
                "tool_calls_log": [],
                "final_output": "",
                "error": None,
            }

            config: dict[str, Any] = {"configurable": {"thread_id": run_id}}

            # ainvoke runs the graph to completion.
            # SSE events are emitted from within each node function.
            final_state: AresState = await compiled.ainvoke(initial_state, config)  # type: ignore[assignment]

        # Aggregate final output from all node outputs
        node_outputs: dict[str, str] = final_state.get("node_outputs", {})
        final_output = _aggregate_output(dag, node_outputs)

        # Update run to completed
        completed_at = datetime.now(timezone.utc).isoformat()
        await runs_repo.update_status(
            run_id, "completed", completed_at=completed_at
        )

        # Run Critic before emitting run_completed so trust_scored fires first
        try:
            from backend.agents.critic import score_run
            await score_run(run_id, goal, final_output, final_state.get("tool_calls_log", []))
        except Exception as exc:
            logger.warning("Critic failed for run %s (non-fatal): %s", run_id, exc)

        await event_bus.emit(run_id, "run_completed", {"output": final_output})
        logger.info("Run %s completed successfully", run_id)

    except Exception as exc:
        logger.error("execute_run failed for run %s: %s", run_id, exc, exc_info=True)
        await runs_repo.update_status(run_id, "failed", error=str(exc))
        await event_bus.emit(run_id, "run_failed", {"error": str(exc)})


def _aggregate_output(dag: DAGPlan, node_outputs: dict[str, str]) -> str:
    """
    Combine all node outputs into a single readable final output.

    Terminal nodes (leaves of the DAG) are listed first since they
    typically contain the synthesised result.
    """
    depended_on: set[str] = set()
    for n in dag.nodes:
        depended_on.update(n.depends_on)

    # Order: terminal nodes first, then intermediate nodes
    terminal = [n for n in dag.nodes if n.id not in depended_on]
    intermediate = [n for n in dag.nodes if n.id in depended_on]
    ordered = terminal + intermediate

    parts = []
    for n in ordered:
        out = node_outputs.get(n.id, "").strip()
        if out:
            parts.append(f"### {n.name}\n\n{out}")

    return "\n\n---\n\n".join(parts) if parts else "Run completed with no output."
