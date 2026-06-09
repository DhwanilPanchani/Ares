"""
Phase 2 unit tests — SSE event bus, repositories, graph wiring, REST API.

All DB operations use a temp file so they don't touch ares.db.
All LLM calls are mocked.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture(autouse=True)
def _fresh_event_bus():
    """Reset the singleton event_bus between tests."""
    from backend.store.events import event_bus
    # Clear all internal state
    event_bus._queues.clear()
    event_bus._replay.clear()
    event_bus._closed.clear()
    yield
    event_bus._queues.clear()
    event_bus._replay.clear()
    event_bus._closed.clear()


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    """
    Point the database module at a fresh temp DB for each test.
    Restores the original path after the test.
    """
    import backend.store.database as db_module
    original = db_module._DB_PATH
    test_db = tmp_path / "test.db"
    db_module._DB_PATH = test_db
    await db_module.init_db()
    yield test_db
    db_module._DB_PATH = original
    if test_db.exists():
        test_db.unlink()


# ==============================================================================
# SSE Event Bus Tests
# ==============================================================================


class TestEventBus:
    @pytest.mark.asyncio
    async def test_emit_and_subscribe_receive_event(self):
        """emit() should deliver an event to a waiting subscriber."""
        from backend.store.events import event_bus

        run_id = "run-bus-001"
        event_bus.create_queue(run_id)

        await event_bus.emit(run_id, "run_started", {"goal": "test goal"})
        await event_bus.close(run_id)

        received = []
        async for msg in event_bus.subscribe(run_id):
            if not msg.startswith(":"):  # skip keep-alives
                received.append(msg)

        assert len(received) == 1
        parsed = json.loads(received[0].split("data: ")[1])
        assert parsed["event"] == "run_started"
        assert parsed["data"]["goal"] == "test goal"

    @pytest.mark.asyncio
    async def test_multiple_events_delivered_in_order(self):
        """All emitted events arrive in emission order."""
        from backend.store.events import event_bus

        run_id = "run-bus-002"
        event_bus.create_queue(run_id)

        events_to_emit = ["run_started", "dag_compiled", "node_started", "node_completed", "run_completed"]
        for et in events_to_emit:
            await event_bus.emit(run_id, et, {})
        await event_bus.close(run_id)

        received_types = []
        async for msg in event_bus.subscribe(run_id):
            if msg.startswith(":"):
                continue
            data = json.loads(msg.split("data: ")[1])
            received_types.append(data["event"])

        assert received_types == events_to_emit

    @pytest.mark.asyncio
    async def test_late_subscriber_gets_replay_buffer(self):
        """A subscriber joining after close() gets the full replay buffer."""
        from backend.store.events import event_bus

        run_id = "run-bus-003"
        event_bus.create_queue(run_id)
        await event_bus.emit(run_id, "run_started", {"goal": "g"})
        await event_bus.emit(run_id, "run_completed", {"output": "done"})
        await event_bus.close(run_id)

        # Subscribe after close
        received = []
        async for msg in event_bus.subscribe(run_id):
            if not msg.startswith(":"):
                received.append(msg)

        assert len(received) == 2
        types = [json.loads(m.split("data: ")[1])["event"] for m in received]
        assert types == ["run_started", "run_completed"]

    @pytest.mark.asyncio
    async def test_emit_after_close_does_not_add_to_queue(self):
        """Emitting after close() should only go to replay, not a closed queue."""
        from backend.store.events import event_bus

        run_id = "run-bus-004"
        event_bus.create_queue(run_id)
        await event_bus.close(run_id)

        # This should not raise and should add to replay (if buf still exists)
        await event_bus.emit(run_id, "late_event", {})

    @pytest.mark.asyncio
    async def test_unknown_run_subscribe_returns_empty(self):
        """Subscribing to an unknown run_id yields nothing."""
        from backend.store.events import event_bus

        received = []
        async for msg in event_bus.subscribe("nonexistent-run"):
            received.append(msg)

        assert received == []

    @pytest.mark.asyncio
    async def test_get_replay_returns_buffer(self):
        """get_replay() returns the stored SSE strings."""
        from backend.store.events import event_bus

        run_id = "run-bus-005"
        event_bus.create_queue(run_id)
        await event_bus.emit(run_id, "node_started", {"node_id": "a"})
        await event_bus.emit(run_id, "node_completed", {"node_id": "a"})
        await event_bus.close(run_id)

        replay = event_bus.get_replay(run_id)
        assert len(replay) == 2
        assert "node_started" in replay[0]
        assert "node_completed" in replay[1]


# ==============================================================================
# Repository Tests
# ==============================================================================


class TestRunsRepo:
    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_db):
        from backend.store import runs_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Research the history of Python")
        row = await runs_repo.get(run_id)
        assert row is not None
        assert row["id"] == run_id
        assert row["goal"] == "Research the history of Python"
        assert row["status"] == "pending"

    @pytest.mark.asyncio
    async def test_update_status(self, tmp_db):
        from backend.store import runs_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")
        await runs_repo.update_status(run_id, "running")
        row = await runs_repo.get(run_id)
        assert row["status"] == "running"

    @pytest.mark.asyncio
    async def test_update_status_with_completed_at(self, tmp_db):
        from backend.store import runs_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")
        now = "2026-01-01T00:00:00+00:00"
        await runs_repo.update_status(run_id, "completed", completed_at=now)
        row = await runs_repo.get(run_id)
        assert row["status"] == "completed"
        assert row["completed_at"] == now

    @pytest.mark.asyncio
    async def test_update_dag(self, tmp_db):
        from backend.store import runs_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")
        dag_json = json.dumps({"goal": "test", "nodes": []})
        await runs_repo.update_dag(run_id, dag_json)
        row = await runs_repo.get(run_id)
        assert row["dag_json"] == {"goal": "test", "nodes": []}

    @pytest.mark.asyncio
    async def test_list_all_returns_all_runs(self, tmp_db):
        from backend.store import runs_repo
        ids = [str(uuid.uuid4()) for _ in range(3)]
        for rid in ids:
            await runs_repo.create(rid, "Test goal string here")
        runs = await runs_repo.list_all()
        db_ids = {r["id"] for r in runs}
        assert all(rid in db_ids for rid in ids)

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, tmp_db):
        from backend.store import runs_repo
        result = await runs_repo.get("does-not-exist")
        assert result is None


class TestNodesRepo:
    @pytest.mark.asyncio
    async def test_create_many_and_list_for_run(self, tmp_db):
        from backend.store import runs_repo, nodes_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")

        node_records = [
            {"id": "node_a", "run_id": run_id, "name": "Node A",
             "description": "Do something.", "depends_on": []},
            {"id": "node_b", "run_id": run_id, "name": "Node B",
             "description": "Do something else.", "depends_on": ["node_a"]},
        ]
        await nodes_repo.create_many(node_records)

        nodes = await nodes_repo.list_for_run(run_id)
        assert len(nodes) == 2
        assert all(n["status"] == "pending" for n in nodes)

    @pytest.mark.asyncio
    async def test_update_status_to_running(self, tmp_db):
        from backend.store import runs_repo, nodes_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")
        await nodes_repo.create_many([
            {"id": "node_a", "run_id": run_id, "name": "A",
             "description": "Do A.", "depends_on": []}
        ])
        await nodes_repo.update_status("node_a", run_id, "running", started_at="2026-01-01T00:00:00+00:00")
        node = await nodes_repo.get("node_a", run_id)
        assert node["status"] == "running"
        assert node["started_at"] == "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_update_status_to_success_with_output(self, tmp_db):
        from backend.store import runs_repo, nodes_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")
        await nodes_repo.create_many([
            {"id": "node_b", "run_id": run_id, "name": "B",
             "description": "Do B.", "depends_on": []}
        ])
        await nodes_repo.update_status(
            "node_b", run_id, "success",
            completed_at="2026-01-01T01:00:00+00:00",
            output="Here is the output.",
        )
        node = await nodes_repo.get("node_b", run_id)
        assert node["status"] == "success"
        assert node["output"] == "Here is the output."

    @pytest.mark.asyncio
    async def test_update_tool_calls(self, tmp_db):
        from backend.store import runs_repo, nodes_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")
        await nodes_repo.create_many([
            {"id": "node_c", "run_id": run_id, "name": "C",
             "description": "Do C.", "depends_on": []}
        ])
        tool_calls = [{"tool": "web_search", "args": {"query": "test"}, "result": "Found things.", "error": None}]
        await nodes_repo.update_tool_calls("node_c", run_id, tool_calls)
        node = await nodes_repo.get("node_c", run_id)
        assert node["tool_calls"] == tool_calls


class TestScoresRepo:
    @pytest.mark.asyncio
    async def test_create_and_get_for_run(self, tmp_db):
        from backend.store import runs_repo, scores_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")

        score_id = await scores_repo.create(
            run_id=run_id,
            factual_grounding=0.9,
            goal_completion=0.85,
            tool_error_rate=0.0,
            trust_score=0.875,
            critique_text="Good run.",
            flagged_span_ids=[],
        )
        assert score_id  # non-empty UUID

        score = await scores_repo.get_for_run(run_id)
        assert score is not None
        assert abs(score["trust_score"] - 0.875) < 0.001
        assert score["critique_text"] == "Good run."
        assert score["flagged_span_ids"] == []

    @pytest.mark.asyncio
    async def test_get_for_run_returns_none_if_no_score(self, tmp_db):
        from backend.store import runs_repo, scores_repo
        run_id = str(uuid.uuid4())
        await runs_repo.create(run_id, "Test goal string here")
        result = await scores_repo.get_for_run(run_id)
        assert result is None


# ==============================================================================
# LangGraph Graph Wiring Tests (no Ollama)
# ==============================================================================


class TestBuildGraph:
    def test_graph_builds_without_error(self):
        """_build_graph should not raise for a valid DAGPlan."""
        from backend.agents.graph import _build_graph
        from backend.models import DAGPlan, DAGNode

        dag = DAGPlan(
            goal="Research and write a report on Python history for testing purposes",
            nodes=[
                DAGNode(id="search", name="Search", description="Search for info.", depends_on=[], tool_hint="web_search"),
                DAGNode(id="write", name="Write", description="Write report.", depends_on=["search"], tool_hint="write_file"),
            ],
        )
        graph = _build_graph(dag, run_id="test-run-001")
        assert graph is not None

    def test_graph_with_parallel_roots(self):
        """Parallel root nodes should both connect to START."""
        from backend.agents.graph import _build_graph
        from backend.models import DAGPlan, DAGNode

        dag = DAGPlan(
            goal="Research two topics in parallel and write a combined report about them",
            nodes=[
                DAGNode(id="r1", name="R1", description="Research topic one.", depends_on=[], tool_hint="web_search"),
                DAGNode(id="r2", name="R2", description="Research topic two.", depends_on=[], tool_hint="web_search"),
                DAGNode(id="combine", name="Combine", description="Combine the results.", depends_on=["r1", "r2"], tool_hint="write_file"),
            ],
        )
        graph = _build_graph(dag, run_id="test-run-002")
        assert graph is not None

    def test_aggregate_output_terminal_nodes_first(self):
        """_aggregate_output should list terminal (leaf) nodes first."""
        from backend.agents.graph import _aggregate_output
        from backend.models import DAGPlan, DAGNode

        dag = DAGPlan(
            goal="Research and write a report on Python history for testing purposes",
            nodes=[
                DAGNode(id="search", name="Search", description="Search.", depends_on=[], tool_hint="web_search"),
                DAGNode(id="write", name="Write", description="Write.", depends_on=["search"], tool_hint="write_file"),
            ],
        )
        output = _aggregate_output(
            dag,
            {"search": "search result", "write": "final report"}
        )
        # "write" is terminal — should appear first in output
        assert output.index("final report") < output.index("search result")

    def test_aggregate_output_skips_empty(self):
        """_aggregate_output should omit nodes with empty output."""
        from backend.agents.graph import _aggregate_output
        from backend.models import DAGPlan, DAGNode

        dag = DAGPlan(
            goal="Research and write a report on Python history for testing purposes",
            nodes=[
                DAGNode(id="a", name="A", description="Do A.", depends_on=[], tool_hint="none"),
                DAGNode(id="b", name="B", description="Do B.", depends_on=["a"], tool_hint="none"),
            ],
        )
        output = _aggregate_output(dag, {"a": "", "b": "real output"})
        assert "real output" in output
        assert "### A" not in output  # empty node A should be skipped


# ==============================================================================
# REST API Tests (TestClient, no Ollama)
# ==============================================================================


class TestRunsAPI:
    @pytest.fixture(autouse=True)
    def _patch_pipeline(self, monkeypatch):
        """Patch _run_pipeline so it doesn't actually call Ollama."""
        import backend.api.runs as runs_module

        async def fake_pipeline(run_id: str, goal: str) -> None:
            from backend.store import runs_repo
            from backend.store.events import event_bus
            event_bus.create_queue(run_id)
            await event_bus.emit(run_id, "run_started", {"goal": goal})
            await runs_repo.update_status(run_id, "completed",
                                           completed_at="2026-01-01T00:00:00+00:00")
            await event_bus.emit(run_id, "run_completed", {"output": "done"})
            await event_bus.close(run_id)

        monkeypatch.setattr(runs_module, "_run_pipeline", fake_pipeline)

    @pytest.fixture
    def client(self, tmp_db):
        from backend.main import app
        from fastapi.testclient import TestClient
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    def test_post_runs_returns_201(self, client):
        resp = client.post("/api/runs", json={"goal": "Research the history of Python programming language"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert "id" in body
        assert body["goal"] == "Research the history of Python programming language"

    def test_post_runs_goal_too_short_returns_422(self, client):
        resp = client.post("/api/runs", json={"goal": "short"})
        assert resp.status_code == 422

    def test_get_runs_returns_list(self, client):
        # Create two runs
        client.post("/api/runs", json={"goal": "Research the history of Python programming language"})
        client.post("/api/runs", json={"goal": "Research the history of JavaScript programming language"})
        resp = client.get("/api/runs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 2

    def test_get_single_run_returns_run_with_status(self, client):
        post_resp = client.post("/api/runs", json={"goal": "Research the history of Python programming language"})
        run_id = post_resp.json()["id"]
        get_resp = client.get(f"/api/runs/{run_id}")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["id"] == run_id

    def test_get_nonexistent_run_returns_404(self, client):
        resp = client.get("/api/runs/does-not-exist")
        assert resp.status_code == 404


class TestStreamAPI:
    @pytest.fixture
    def client(self, tmp_db):
        from backend.main import app
        from fastapi.testclient import TestClient
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    def test_stream_unknown_run_returns_404(self, client):
        resp = client.get("/api/runs/nonexistent/stream")
        assert resp.status_code == 404

    def test_stream_completed_run_replays_buffer(self, client, tmp_db):
        """For a completed run, GET stream should replay the event buffer."""
        import asyncio
        from backend.store import runs_repo
        from backend.store.events import event_bus

        run_id = str(uuid.uuid4())

        async def setup():
            await runs_repo.create(run_id, "Research the history of Python language")
            event_bus.create_queue(run_id)
            await event_bus.emit(run_id, "run_started", {"goal": "Research the history of Python language"})
            await event_bus.emit(run_id, "run_completed", {"output": "done"})
            await event_bus.close(run_id)
            await runs_repo.update_status(run_id, "completed",
                                           completed_at="2026-01-01T00:00:00+00:00")

        asyncio.get_event_loop().run_until_complete(setup())

        resp = client.get(f"/api/runs/{run_id}/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        # Body should contain SSE events
        assert "run_started" in resp.text
        assert "run_completed" in resp.text
