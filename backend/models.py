"""
All Pydantic v2 data models for Project Ares.

Mirrors the shapes defined in 05-DATA-MODELS.md.
Strict mode is enabled on all models.
"""

from __future__ import annotations

import json
import operator
from collections import defaultdict, deque
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ==============================================================================
# Exceptions
# ==============================================================================


class CompilerError(Exception):
    """Raised when the DAG compiler fails all 3 retry attempts."""

    def __init__(self, message: str, last_raw: str = "") -> None:
        super().__init__(message)
        self.last_raw = last_raw


class WorkerError(Exception):
    """Raised when a Worker agent fails all 3 retry attempts."""


class CriticError(Exception):
    """Raised when the Critic agent fails all 3 retry attempts."""


# ==============================================================================
# DAG Models
# ==============================================================================

VALID_TOOL_HINTS = frozenset(
    {"web_search", "write_file", "read_file", "run_python", "http_get", "none"}
)


class DAGNode(BaseModel):
    """A single node in the execution DAG."""

    model_config = {"strict": True}

    id: str = Field(
        ...,
        description="Snake_case unique identifier for this node.",
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="One actionable sentence describing exactly what this node must do.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="IDs of nodes that must complete before this one can run.",
    )
    tool_hint: str | None = Field(
        default=None,
        description="Advisory hint for which tool the worker should prefer.",
    )

    @field_validator("tool_hint", mode="before")
    @classmethod
    def normalise_tool_hint(cls, v: Any) -> str | None:
        if v is None or v == "none":
            return None
        if isinstance(v, str) and v in VALID_TOOL_HINTS:
            return v
        return None  # silently coerce unknown hints to None


class DAGPlan(BaseModel):
    """The compiled execution plan produced by the Orchestrator agent."""

    model_config = {"strict": True}

    goal: str = Field(..., min_length=10, max_length=500)
    nodes: list[DAGNode] = Field(..., min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_depends_on_references(self) -> "DAGPlan":
        node_ids = {n.id for n in self.nodes}
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in node_ids:
                    raise ValueError(
                        f"Node '{node.id}' depends on '{dep}' which does not exist in the DAG."
                    )
        return self

    def get_parallel_groups(self) -> list[list[DAGNode]]:
        """
        Return a topologically sorted list of groups.

        Each group contains nodes that can execute concurrently because all
        their dependencies have been satisfied by earlier groups.

        Raises ValueError if the DAG contains a cycle.
        """
        # Build adjacency and in-degree maps
        node_map: dict[str, DAGNode] = {n.id: n for n in self.nodes}
        in_degree: dict[str, int] = {n.id: 0 for n in self.nodes}
        dependents: dict[str, list[str]] = defaultdict(list)

        for node in self.nodes:
            in_degree[node.id] = len(node.depends_on)
            for dep in node.depends_on:
                dependents[dep].append(node.id)

        # Kahn's algorithm — group nodes by wave
        queue: deque[str] = deque(
            nid for nid, deg in in_degree.items() if deg == 0
        )
        groups: list[list[DAGNode]] = []
        visited: int = 0

        while queue:
            # All nodes currently in the queue form one parallel group
            wave = list(queue)
            queue.clear()
            groups.append([node_map[nid] for nid in wave])
            visited += len(wave)

            for nid in wave:
                for dependent in dependents[nid]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        if visited != len(self.nodes):
            raise ValueError("DAG contains a cycle — topological sort failed.")

        return groups


# ==============================================================================
# Run / API Models
# ==============================================================================


class RunCreate(BaseModel):
    """Request body for POST /api/runs."""

    model_config = {"strict": True}

    goal: str = Field(..., min_length=10, max_length=500)


class ToolCallRecord(BaseModel):
    """A record of a single tool invocation by a worker agent."""

    model_config = {"strict": False}  # tool args can be arbitrary dicts

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str | None = None
    error: str | None = None


class NodeResponse(BaseModel):
    """API response shape for a single DAG node."""

    model_config = {"strict": False}

    id: str
    run_id: str
    name: str
    description: str
    status: Literal["pending", "running", "success", "failed"]
    depends_on: list[str]
    prompt: str | None = None
    output: str | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


class TrustScore(BaseModel):
    """Trust score produced by the Critic agent."""

    model_config = {"strict": False}

    factual_grounding: float = Field(..., ge=0.0, le=1.0)
    goal_completion: float = Field(..., ge=0.0, le=1.0)
    tool_error_rate: float = Field(..., ge=0.0, le=1.0)
    trust_score: float = Field(..., ge=0.0, le=1.0)
    critique_text: str
    flagged_span_ids: list[str] = Field(default_factory=list)


class RunResponse(BaseModel):
    """API response shape for a run."""

    model_config = {"strict": False}

    id: str
    goal: str
    status: Literal["pending", "compiling", "running", "completed", "failed"]
    dag_json: dict[str, Any] | None = None
    created_at: str
    completed_at: str | None = None
    error: str | None = None
    trust_score: TrustScore | None = None
    nodes: list[NodeResponse] = Field(default_factory=list)


# ==============================================================================
# Span / Trace Models
# ==============================================================================


class SpanResponse(BaseModel):
    """API response shape for an OTel span."""

    model_config = {"strict": False}

    id: str
    trace_id: str
    parent_id: str | None = None
    run_id: str
    node_id: str | None = None
    name: str
    kind: Literal["llm", "tool", "agent", "system"]
    attributes: dict[str, Any] = Field(default_factory=dict)
    started_at: str
    ended_at: str | None = None
    status_code: Literal["OK", "ERROR"] = "OK"


# ==============================================================================
# SSE Event Models
# ==============================================================================


class SSEEvent(BaseModel):
    """Base shape for all SSE events emitted by the backend."""

    model_config = {"strict": False}

    event: str
    run_id: str
    data: dict[str, Any]
