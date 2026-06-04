"""
Unit tests for backend/agents/compiler.py

Covers:
- Valid goal returns DAGPlan with correct structure
- DAGPlan.get_parallel_groups() returns correct topological groups
- Retry logic retries on ValidationError up to 3 times then raises CompilerError
- Response with malformed JSON triggers retry
- Markdown fence stripping works

All Ollama HTTP calls are mocked.
"""

from __future__ import annotations

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from backend.agents.compiler import compile_dag, _strip_markdown_fences, MAX_RETRIES
from backend.models import CompilerError, DAGPlan, DAGNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ollama_response(content: str) -> MagicMock:
    """Build a mock httpx.Response that looks like an Ollama chat response."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "model": "qwen2.5:3b",
        "message": {"role": "assistant", "content": content},
        "done": True,
    }
    return mock_resp


def _valid_dag_json(goal: str = "Research and write a report on Python history") -> str:
    return json.dumps({
        "goal": goal,
        "nodes": [
            {
                "id": "search_topic",
                "name": "Search topic",
                "description": "Search the web for Python history.",
                "depends_on": [],
                "tool_hint": "web_search",
            },
            {
                "id": "write_report",
                "name": "Write report",
                "description": "Write a report based on the search results.",
                "depends_on": ["search_topic"],
                "tool_hint": "write_file",
            },
        ],
    })


# ---------------------------------------------------------------------------
# _strip_markdown_fences
# ---------------------------------------------------------------------------


class TestStripMarkdownFences:
    def test_plain_json_unchanged(self):
        assert _strip_markdown_fences('{"a":1}') == '{"a":1}'

    def test_json_fence_stripped(self):
        result = _strip_markdown_fences("```json\n{\"a\":1}\n```")
        assert result == '{"a":1}'

    def test_plain_fence_stripped(self):
        result = _strip_markdown_fences("```\n{\"a\":1}\n```")
        assert result == '{"a":1}'

    def test_leading_trailing_whitespace_stripped(self):
        assert _strip_markdown_fences("  {\"a\":1}  ") == '{"a":1}'

    def test_multiline_json_preserved(self):
        inner = '{\n  "goal": "test",\n  "nodes": []\n}'
        fenced = f"```json\n{inner}\n```"
        assert _strip_markdown_fences(fenced) == inner


# ---------------------------------------------------------------------------
# DAGPlan.get_parallel_groups
# ---------------------------------------------------------------------------


class TestDAGPlanParallelGroups:
    def test_single_chain(self):
        plan = DAGPlan(
            goal="A then B then C — a three step sequential task",
            nodes=[
                DAGNode(id="a", name="A", description="Step A.", depends_on=[], tool_hint="none"),
                DAGNode(id="b", name="B", description="Step B.", depends_on=["a"], tool_hint="none"),
                DAGNode(id="c", name="C", description="Step C.", depends_on=["b"], tool_hint="none"),
            ],
        )
        groups = plan.get_parallel_groups()
        assert len(groups) == 3
        assert [n.id for n in groups[0]] == ["a"]
        assert [n.id for n in groups[1]] == ["b"]
        assert [n.id for n in groups[2]] == ["c"]

    def test_parallel_roots_then_merge(self):
        plan = DAGPlan(
            goal="Research two topics simultaneously then combine the results",
            nodes=[
                DAGNode(id="r1", name="R1", description="Research topic one.", depends_on=[], tool_hint="web_search"),
                DAGNode(id="r2", name="R2", description="Research topic two.", depends_on=[], tool_hint="web_search"),
                DAGNode(id="merge", name="Merge", description="Merge results.", depends_on=["r1", "r2"], tool_hint="write_file"),
            ],
        )
        groups = plan.get_parallel_groups()
        assert len(groups) == 2
        assert set(n.id for n in groups[0]) == {"r1", "r2"}
        assert groups[1][0].id == "merge"

    def test_cycle_raises_value_error(self):
        # Use model_construct to skip validators so we can inject a cyclic graph
        # directly (validators would catch the dangling ref before we can test the topo sort)
        nodes = [
            DAGNode.model_construct(id="a", name="A", description="Step A.", depends_on=["b"], tool_hint="none"),
            DAGNode.model_construct(id="b", name="B", description="Step B.", depends_on=["a"], tool_hint="none"),
        ]
        plan = DAGPlan.model_construct(
            goal="Cycle test goal — long enough to pass validation",
            nodes=nodes,
        )
        with pytest.raises(ValueError, match="cycle"):
            plan.get_parallel_groups()

    def test_diamond_dag(self):
        """A → B, A → C, B → D, C → D"""
        plan = DAGPlan(
            goal="Diamond DAG test — start search then two parallel steps then finish",
            nodes=[
                DAGNode(id="a", name="A", description="Start.", depends_on=[], tool_hint="none"),
                DAGNode(id="b", name="B", description="Branch B.", depends_on=["a"], tool_hint="none"),
                DAGNode(id="c", name="C", description="Branch C.", depends_on=["a"], tool_hint="none"),
                DAGNode(id="d", name="D", description="Finish.", depends_on=["b", "c"], tool_hint="none"),
            ],
        )
        groups = plan.get_parallel_groups()
        assert len(groups) == 3
        assert groups[0][0].id == "a"
        assert set(n.id for n in groups[1]) == {"b", "c"}
        assert groups[2][0].id == "d"


# ---------------------------------------------------------------------------
# compile_dag — mocked Ollama
# ---------------------------------------------------------------------------


class TestCompileDag:
    @pytest.mark.asyncio
    async def test_valid_response_returns_dag_plan(self):
        """compile_dag returns a DAGPlan when Ollama returns valid JSON."""
        mock_resp = _make_ollama_response(_valid_dag_json())

        with patch("backend.agents.compiler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await compile_dag("Research and write a report on Python history")

        assert isinstance(result, DAGPlan)
        assert len(result.nodes) == 2
        assert result.nodes[0].id == "search_topic"

    @pytest.mark.asyncio
    async def test_malformed_json_triggers_retry(self):
        """compile_dag retries when response is not valid JSON."""
        bad_resp = _make_ollama_response("This is not JSON at all { broken")
        good_resp = _make_ollama_response(_valid_dag_json())

        with patch("backend.agents.compiler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            # First call returns bad JSON, second returns valid
            mock_client.post = AsyncMock(side_effect=[bad_resp, good_resp])
            mock_client_cls.return_value = mock_client

            result = await compile_dag("Research and write a report on Python history")

        assert isinstance(result, DAGPlan)
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises_compiler_error(self):
        """compile_dag raises CompilerError after MAX_RETRIES failures."""
        bad_resp = _make_ollama_response("not json at all { broken { bad")

        with patch("backend.agents.compiler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=bad_resp)
            mock_client_cls.return_value = mock_client

            with pytest.raises(CompilerError):
                await compile_dag("Research and write a report on Python history")

        assert mock_client.post.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_validation_error_triggers_retry_with_error_in_prompt(self):
        """On ValidationError, the next retry message includes the error text."""
        # First response: invalid schema (missing 'description' on a node)
        invalid_dag = json.dumps({
            "goal": "Research and write a report on Python history",
            "nodes": [
                {"id": "a", "name": "A", "depends_on": [], "tool_hint": "none"},  # missing 'description'
            ],
        })
        valid_dag = _valid_dag_json()
        bad_resp = _make_ollama_response(invalid_dag)
        good_resp = _make_ollama_response(valid_dag)

        with patch("backend.agents.compiler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=[bad_resp, good_resp])
            mock_client_cls.return_value = mock_client

            result = await compile_dag("Research and write a report on Python history")

        assert isinstance(result, DAGPlan)
        # On the second call, the messages payload should include the error feedback
        second_call_kwargs = mock_client.post.call_args_list[1][1]
        messages = second_call_kwargs["json"]["messages"]
        # There should be a user message containing 'validation error'
        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        assert any("validation error" in m.lower() for m in user_messages), \
            f"Expected error feedback in retry messages, got: {user_messages}"

    @pytest.mark.asyncio
    async def test_empty_ollama_response_triggers_retry(self):
        """compile_dag retries on empty response content."""
        empty_resp = _make_ollama_response("")
        good_resp = _make_ollama_response(_valid_dag_json())

        with patch("backend.agents.compiler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=[empty_resp, good_resp])
            mock_client_cls.return_value = mock_client

            result = await compile_dag("Research and write a report on Python history")

        assert isinstance(result, DAGPlan)
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_ollama_connection_error_retries_then_raises(self):
        """compile_dag raises CompilerError when Ollama is unreachable."""
        with patch("backend.agents.compiler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_cls.return_value = mock_client

            with pytest.raises(CompilerError, match="Cannot connect to Ollama"):
                await compile_dag("Research and write a report on Python history")

        assert mock_client.post.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_goal_injected_when_missing_from_response(self):
        """compile_dag injects the goal if the model omitted it from the JSON."""
        dag_without_goal = json.dumps({
            "nodes": [
                {"id": "step_one", "name": "Step one", "description": "Do step one.", "depends_on": [], "tool_hint": "none"},
                {"id": "step_two", "name": "Step two", "description": "Do step two.", "depends_on": ["step_one"], "tool_hint": "none"},
            ]
        })
        mock_resp = _make_ollama_response(dag_without_goal)

        with patch("backend.agents.compiler.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await compile_dag("Research and write a report on Python history")

        assert result.goal == "Research and write a report on Python history"
