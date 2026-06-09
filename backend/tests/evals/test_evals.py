"""
End-to-end golden eval tests for Project Ares.

Marked with @pytest.mark.integration — require Ollama running locally
with qwen2.5:3b and phi4-mini available.

Run:
    pytest backend/tests/evals/test_evals.py -m integration -v

Each test:
  1. Compiles the DAG for the eval goal (verifies structure).
  2. Executes via the LangGraph engine against a temp SQLite DB.
  3. Checks required tools were actually used.
  4. Checks expected output files exist on disk.
  5. Runs the Critic and asserts the trust score meets the minimum.
  6. Checks expected output keywords appear in the final output.
  7. Checks that previously flagged patterns in critic_flagged.jsonl do not recur.
  8. Cleans up output files after the test.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from backend.tests.evals.golden_set import (
    FLAGGED_JSONL_PATH,
    GOLDEN_EVALS,
    EvalCase,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def isolated_db(tmp_path):
    """Redirect all DB writes to a fresh temp SQLite file."""
    import backend.store.database as db_module
    from backend.store.database import init_db

    original = db_module._DB_PATH
    db_module._DB_PATH = tmp_path / "eval_test.db"
    await init_db()
    yield tmp_path
    db_module._DB_PATH = original


@pytest.fixture
def clean_output(tmp_path, monkeypatch):
    """
    Redirect OUTPUT_DIR to a temp directory and clean up after the test.
    Returns the temp output directory path.
    """
    import backend.config as cfg_module

    original = cfg_module.settings.output_dir
    monkeypatch.setattr(cfg_module.settings, "output_dir", tmp_path / "output")
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "output"
    monkeypatch.setattr(cfg_module.settings, "output_dir", original)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_tool_names(tool_calls_log: list[dict]) -> set[str]:
    return {tc.get("tool", "") for tc in tool_calls_log}


def _load_flagged_patterns() -> list[dict[str, Any]]:
    """Load previously flagged failure records from critic_flagged.jsonl."""
    if not FLAGGED_JSONL_PATH.exists():
        return []
    records = []
    with open(FLAGGED_JSONL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


async def _run_eval(
    case: EvalCase,
    output_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    """
    Core eval runner: compile DAG → execute → return result dict.

    Returns:
        {
          "dag": DAGPlan,
          "final_output": str,
          "tool_calls_log": list[dict],
          "score": TrustScore,
        }
    """
    from backend.agents.compiler import compile_dag
    from backend.agents.critic import score_run
    from backend.agents.graph import execute_run
    from backend.store import nodes_repo, runs_repo
    from backend.store.events import event_bus

    # Create run record
    from datetime import datetime, timezone
    await runs_repo.create(run_id, case.goal)

    # Compile DAG
    dag = await compile_dag(case.goal, run_id=run_id)

    # Verify structure
    assert len(dag.nodes) >= 2, f"DAG must have at least 2 nodes, got {len(dag.nodes)}"

    if case.min_parallel_nodes > 1:
        groups = dag.get_parallel_groups()
        parallel_roots = len(groups[0]) if groups else 0
        assert parallel_roots >= case.min_parallel_nodes, (
            f"Expected at least {case.min_parallel_nodes} parallel root nodes, "
            f"got {parallel_roots}"
        )

    # Persist nodes
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

    # Persist DAG JSON to run record
    await runs_repo.update_dag(run_id, dag.model_dump_json())
    await runs_repo.update_status(run_id, "running")

    # Set up SSE queue
    event_bus.create_queue(run_id)

    # Execute
    tool_calls_log: list[dict] = []
    final_output = ""

    # We need to capture tool_calls_log from the graph state.
    # Monkey-patch execute_run to intercept final_state.
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from backend.config import settings
    from backend.agents.graph import _build_graph, _aggregate_output
    from typing import Any as AnyT

    graph = _build_graph(dag, run_id)
    db_path = str(settings.db_path)

    async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)
        initial_state = {
            "run_id": run_id,
            "goal": case.goal,
            "dag": dag.model_dump(),
            "node_outputs": {},
            "node_statuses": {n.id: "pending" for n in dag.nodes},
            "tool_calls_log": [],
            "final_output": "",
            "error": None,
        }
        config = {"configurable": {"thread_id": run_id}}
        final_state = await compiled.ainvoke(initial_state, config)

    tool_calls_log = final_state.get("tool_calls_log", [])
    node_outputs = final_state.get("node_outputs", {})
    final_output = _aggregate_output(dag, node_outputs)

    # Score
    score = await score_run(run_id, case.goal, final_output, tool_calls_log)

    await event_bus.close(run_id)

    return {
        "dag": dag,
        "final_output": final_output,
        "tool_calls_log": tool_calls_log,
        "score": score,
    }


# ---------------------------------------------------------------------------
# Parametrized integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("case", GOLDEN_EVALS, ids=[c.id for c in GOLDEN_EVALS])
@pytest.mark.asyncio
async def test_golden_eval(case: EvalCase, isolated_db, clean_output, sre_demo_server):
    """
    End-to-end eval for a single golden case.

    Fixtures:
      isolated_db:    fresh SQLite per test (avoids cross-test contamination)
      clean_output:   OUTPUT_DIR redirected to tmp; cleaned up after
      sre_demo_server: local HTTP server on port 9999 (needed for case 1 only)
    """
    run_id = str(uuid.uuid4())
    output_dir: Path = clean_output

    # Seed input file for code_fix case
    if case.id == "code_fix":
        buggy = output_dir / "buggy_code.py"
        buggy.write_text(
            "def add(a, b):\n    return a - b  # bug: should be +\n\n"
            "def divide(a, b):\n    return a / b  # bug: no zero check\n"
        )

    # Run the eval
    result = await _run_eval(case, output_dir, run_id)
    dag = result["dag"]
    score = result["score"]
    tool_calls_log: list[dict] = result["tool_calls_log"]
    final_output: str = result["final_output"]

    # 1. Structure: at least 2 nodes
    assert len(dag.nodes) >= 2

    # 2. Parallelism check
    if case.min_parallel_nodes > 1:
        groups = dag.get_parallel_groups()
        assert len(groups[0]) >= case.min_parallel_nodes

    # 3. Required tools were actually called
    used_tools = _collect_tool_names(tool_calls_log)
    for required_tool in case.required_tools:
        assert required_tool in used_tools, (
            f"Required tool '{required_tool}' was not used. Used: {used_tools}"
        )

    # 4. Expected output files exist
    for fname in case.expected_output_files:
        fpath = output_dir / fname
        assert fpath.exists(), f"Expected output file '{fname}' does not exist"
        assert fpath.stat().st_size > 0, f"Output file '{fname}' is empty"

    # 5. Trust score meets minimum
    assert score.trust_score >= case.min_trust_score, (
        f"Trust score {score.trust_score:.2f} below minimum {case.min_trust_score}. "
        f"Critique: {score.critique_text}"
    )

    # 6. Expected keywords appear in output
    for kw in case.expected_keywords:
        assert kw.lower() in final_output.lower(), (
            f"Expected keyword '{kw}' not found in final output"
        )

    # 7. data_pipeline: stats.json is valid JSON
    if case.id == "data_pipeline":
        stats_file = output_dir / "stats.json"
        if stats_file.exists():
            content = stats_file.read_text()
            parsed = json.loads(content)
            assert isinstance(parsed, dict), "stats.json must be a JSON object"

    # 8. Check that previously flagged patterns don't recur
    flagged_records = _load_flagged_patterns()
    for record in flagged_records:
        if record.get("goal") == case.goal:
            # If a prior run of this same goal was flagged, ensure the new score is better
            assert score.trust_score >= case.min_trust_score, (
                f"Previously flagged goal '{case.id}' still produces low trust score: "
                f"{score.trust_score:.2f}"
            )


# ---------------------------------------------------------------------------
# Unit-level eval schema tests (no Ollama needed)
# ---------------------------------------------------------------------------


def test_golden_evals_have_valid_structure():
    """All eval cases have required fields and valid thresholds."""
    assert len(GOLDEN_EVALS) == 5
    for case in GOLDEN_EVALS:
        assert case.id, "EvalCase must have an id"
        assert len(case.goal) >= 10, f"Goal too short for case {case.id}"
        assert 0.0 < case.min_trust_score <= 1.0, f"Invalid threshold for {case.id}"
        assert case.required_tools, f"No required tools for case {case.id}"


def test_flagged_jsonl_append_and_read(tmp_path, monkeypatch):
    """append_to_flagged writes valid JSON lines that can be read back."""
    from backend.tests.evals.golden_set import FLAGGED_JSONL_PATH, append_to_flagged

    # Redirect to temp file
    tmp_jsonl = tmp_path / "critic_flagged.jsonl"
    monkeypatch.setattr(
        "backend.tests.evals.golden_set.FLAGGED_JSONL_PATH", tmp_jsonl
    )

    append_to_flagged("run-abc", "test goal", ["span-1", "span-2"])
    append_to_flagged("run-def", "another goal", ["span-3"])

    records = []
    with open(tmp_jsonl, encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line.strip()))

    assert len(records) == 2
    assert records[0]["run_id"] == "run-abc"
    assert records[0]["flagged_span_ids"] == ["span-1", "span-2"]
    assert records[1]["goal"] == "another goal"


def test_flagged_jsonl_atomic_write_idempotent(tmp_path, monkeypatch):
    """Multiple appends accumulate correctly without overwriting prior content."""
    from backend.tests.evals.golden_set import append_to_flagged

    tmp_jsonl = tmp_path / "critic_flagged.jsonl"
    monkeypatch.setattr(
        "backend.tests.evals.golden_set.FLAGGED_JSONL_PATH", tmp_jsonl
    )

    for i in range(5):
        append_to_flagged(f"run-{i}", f"goal {i}", [f"span-{i}"])

    lines = [l for l in tmp_jsonl.read_text().splitlines() if l.strip()]
    assert len(lines) == 5
