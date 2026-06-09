"""
Unit tests for backend/agents/worker.py

Covers: tool call execution, tool error handling, OTel span creation.
All Ollama / LLM calls are mocked — no real model required.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models import DAGNode


# ==============================================================================
# Helpers
# ==============================================================================


def _make_dag_node(
    node_id: str = "test_node",
    description: str = "Test task",
    tool_hint: str | None = None,
) -> DAGNode:
    return DAGNode(
        id=node_id,
        name="Test node",
        description=description,
        depends_on=[],
        tool_hint=tool_hint,
    )


def _make_ai_message(content: str = "", tool_calls: list | None = None):
    """Return a minimal LangChain AIMessage-like object."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    return msg


def _make_chunk(content: str = "", tool_call_chunks: list | None = None):
    """Return a streaming chunk mock."""
    chunk = MagicMock()
    chunk.content = content
    chunk.tool_call_chunks = tool_call_chunks or []
    return chunk


async def _aiter(items):
    """Convert a list into an async iterable for mocking astream."""
    for item in items:
        yield item


# ==============================================================================
# Test: no tool calls → returns content directly
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_returns_text_when_no_tool_calls():
    """When LLM returns plain text with no tool calls, output equals the text."""
    dag_node = _make_dag_node()

    chunks = [
        _make_chunk(content="Hello "),
        _make_chunk(content="world"),
    ]

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.astream = MagicMock(return_value=_aiter(chunks))

    with (
        patch("backend.agents.worker.ChatOllama", return_value=mock_llm),
        patch("backend.store.events.event_bus.emit", new_callable=AsyncMock),
    ):
        from backend.agents.worker import run_worker

        output, tool_calls_log = await run_worker(dag_node, "run-1", {})

    assert output == "Hello world"
    assert tool_calls_log == []


# ==============================================================================
# Test: LLM requests a tool → tool is called, result fed back
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_calls_tool_when_requested():
    """When LLM requests a tool call, the tool is invoked and its result is added to the log."""
    dag_node = _make_dag_node(tool_hint="run_python")

    # Round 1: LLM requests run_python
    tool_call_chunk = {
        "index": 0,
        "id": "call_123",
        "name": "run_python",
        "args": '{"code": "print(42)"}',
    }
    round1_chunks = [_make_chunk(tool_call_chunks=[tool_call_chunk])]
    # Round 2: LLM returns final text
    round2_chunks = [_make_chunk(content="The answer is 42")]

    stream_calls = [_aiter(round1_chunks), _aiter(round2_chunks)]
    call_count = 0

    async def mock_astream(messages):
        nonlocal call_count
        gen = stream_calls[call_count]
        call_count += 1
        async for item in gen:
            yield item

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.astream = mock_astream

    mock_tool = MagicMock()
    mock_tool.name = "run_python"
    mock_tool.arun = AsyncMock(return_value="42")

    with (
        patch("backend.agents.worker.ChatOllama", return_value=mock_llm),
        patch("backend.store.events.event_bus.emit", new_callable=AsyncMock),
        patch("backend.tools.registry.ALL_TOOLS", [mock_tool]),
        patch("backend.tools.registry.ALL_TOOLS", [mock_tool]),
    ):
        from backend.agents.worker import run_worker

        output, tool_calls_log = await run_worker(dag_node, "run-2", {})

    assert output == "The answer is 42"
    assert len(tool_calls_log) == 1
    assert tool_calls_log[0]["tool"] == "run_python"
    assert tool_calls_log[0]["result"] == "42"
    assert tool_calls_log[0]["error"] is None


# ==============================================================================
# Test: tool returns error string → worker handles gracefully, keeps going
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_handles_tool_error_gracefully():
    """When a tool returns an error string (not an exception), it is logged and fed back to LLM."""
    dag_node = _make_dag_node()

    tool_call_chunk = {
        "index": 0,
        "id": "call_err",
        "name": "http_get",
        "args": '{"url": "https://broken.invalid"}',
    }
    round1_chunks = [_make_chunk(tool_call_chunks=[tool_call_chunk])]
    round2_chunks = [_make_chunk(content="Could not fetch the URL.")]

    stream_calls = [_aiter(round1_chunks), _aiter(round2_chunks)]
    call_count = 0

    async def mock_astream(messages):
        nonlocal call_count
        gen = stream_calls[call_count]
        call_count += 1
        async for item in gen:
            yield item

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.astream = mock_astream

    # Tool returns an error STRING — not an exception
    mock_tool = MagicMock()
    mock_tool.name = "http_get"
    mock_tool.arun = AsyncMock(return_value="Error: connection refused")

    with (
        patch("backend.agents.worker.ChatOllama", return_value=mock_llm),
        patch("backend.store.events.event_bus.emit", new_callable=AsyncMock),
        patch("backend.tools.registry.ALL_TOOLS", [mock_tool]),
    ):
        from backend.agents.worker import run_worker

        output, tool_calls_log = await run_worker(dag_node, "run-3", {})

    assert output == "Could not fetch the URL."
    assert len(tool_calls_log) == 1
    assert "Error" in tool_calls_log[0]["result"]


# ==============================================================================
# Test: tool raises an exception → worker catches it, returns error string
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_catches_tool_exception():
    """If a tool raises an exception (not just returns an error string), the worker catches it."""
    dag_node = _make_dag_node()

    tool_call_chunk = {
        "index": 0,
        "id": "call_exc",
        "name": "web_search",
        "args": '{"query": "test"}',
    }
    round1_chunks = [_make_chunk(tool_call_chunks=[tool_call_chunk])]
    round2_chunks = [_make_chunk(content="Search unavailable.")]

    stream_calls = [_aiter(round1_chunks), _aiter(round2_chunks)]
    call_count = 0

    async def mock_astream(messages):
        nonlocal call_count
        gen = stream_calls[call_count]
        call_count += 1
        async for item in gen:
            yield item

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.astream = mock_astream

    # Tool raises an exception (violating the design contract, but we handle it anyway)
    mock_tool = MagicMock()
    mock_tool.name = "web_search"
    mock_tool.arun = AsyncMock(side_effect=RuntimeError("unexpected tool crash"))

    with (
        patch("backend.agents.worker.ChatOllama", return_value=mock_llm),
        patch("backend.store.events.event_bus.emit", new_callable=AsyncMock),
        patch("backend.tools.registry.ALL_TOOLS", [mock_tool]),
    ):
        from backend.agents.worker import run_worker

        output, tool_calls_log = await run_worker(dag_node, "run-4", {})

    # The worker should not crash — it should absorb the exception
    assert output == "Search unavailable."
    assert "Error" in tool_calls_log[0]["result"]


# ==============================================================================
# Test: unknown tool name → error string, does not crash
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_unknown_tool_returns_error():
    """Requesting a tool that is not in ALL_TOOLS returns an error string."""
    dag_node = _make_dag_node()

    tool_call_chunk = {
        "index": 0,
        "id": "call_unk",
        "name": "nonexistent_tool",
        "args": "{}",
    }
    round1_chunks = [_make_chunk(tool_call_chunks=[tool_call_chunk])]
    round2_chunks = [_make_chunk(content="Tool not found.")]

    stream_calls = [_aiter(round1_chunks), _aiter(round2_chunks)]
    call_count = 0

    async def mock_astream(messages):
        nonlocal call_count
        gen = stream_calls[call_count]
        call_count += 1
        async for item in gen:
            yield item

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.astream = mock_astream

    with (
        patch("backend.agents.worker.ChatOllama", return_value=mock_llm),
        patch("backend.store.events.event_bus.emit", new_callable=AsyncMock),
        patch("backend.tools.registry.ALL_TOOLS", []),  # empty registry
    ):
        from backend.agents.worker import run_worker

        output, tool_calls_log = await run_worker(dag_node, "run-5", {})

    assert "Tool not found." in output or len(tool_calls_log) == 1
    if tool_calls_log:
        assert "not registered" in tool_calls_log[0]["result"]


# ==============================================================================
# Test: OTel span is created for each LLM call
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_creates_otel_span_for_llm_call():
    """An llm.call span is started for every worker invocation."""
    dag_node = _make_dag_node()
    chunks = [_make_chunk(content="done")]

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.astream = MagicMock(return_value=_aiter(chunks))

    span_names: list[str] = []

    real_tracer = MagicMock()
    span_ctx = MagicMock()
    span_ctx.__enter__ = MagicMock(return_value=MagicMock())
    span_ctx.__exit__ = MagicMock(return_value=False)

    def capture_span(name, **kwargs):
        span_names.append(name)
        return span_ctx

    real_tracer.start_as_current_span = capture_span

    with (
        patch("backend.agents.worker.ChatOllama", return_value=mock_llm),
        patch("backend.store.events.event_bus.emit", new_callable=AsyncMock),
        patch("backend.agents.worker.get_tracer", return_value=real_tracer),
    ):
        from backend.agents.worker import run_worker

        await run_worker(dag_node, "run-6", {})

    assert "llm.call" in span_names


# ==============================================================================
# Test: OTel span is created for each tool call
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_creates_otel_span_for_tool_call():
    """A tool.{name} span is started for each tool invocation."""
    dag_node = _make_dag_node()

    tool_call_chunk = {
        "index": 0,
        "id": "call_t",
        "name": "run_python",
        "args": '{"code": "print(1)"}',
    }
    round1_chunks = [_make_chunk(tool_call_chunks=[tool_call_chunk])]
    round2_chunks = [_make_chunk(content="done")]

    stream_calls = [_aiter(round1_chunks), _aiter(round2_chunks)]
    call_count = 0

    async def mock_astream(messages):
        nonlocal call_count
        gen = stream_calls[call_count]
        call_count += 1
        async for item in gen:
            yield item

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.astream = mock_astream

    mock_tool = MagicMock()
    mock_tool.name = "run_python"
    mock_tool.arun = AsyncMock(return_value="1")

    span_names: list[str] = []
    real_tracer = MagicMock()
    span_ctx = MagicMock()
    span_ctx.__enter__ = MagicMock(return_value=MagicMock())
    span_ctx.__exit__ = MagicMock(return_value=False)

    def capture_span(name, **kwargs):
        span_names.append(name)
        return span_ctx

    real_tracer.start_as_current_span = capture_span

    with (
        patch("backend.agents.worker.ChatOllama", return_value=mock_llm),
        patch("backend.store.events.event_bus.emit", new_callable=AsyncMock),
        patch("backend.tools.registry.ALL_TOOLS", [mock_tool]),
        patch("backend.agents.worker.get_tracer", return_value=real_tracer),
    ):
        from backend.agents.worker import run_worker

        await run_worker(dag_node, "run-7", {})

    assert "llm.call" in span_names
    assert "tool.run_python" in span_names


# ==============================================================================
# Test: upstream context is included in task message
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_includes_upstream_context():
    """Upstream node outputs are formatted and included in the task message."""
    dag_node = _make_dag_node()
    chunks = [_make_chunk(content="used context")]

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    received_messages: list = []

    async def capturing_astream(messages):
        received_messages.extend(messages)
        for c in chunks:
            yield c

    mock_llm.astream = capturing_astream

    with (
        patch("backend.agents.worker.ChatOllama", return_value=mock_llm),
        patch("backend.store.events.event_bus.emit", new_callable=AsyncMock),
    ):
        from backend.agents.worker import run_worker

        await run_worker(dag_node, "run-8", {"dep_node": "output from dep"})

    # Find the human message
    human_msgs = [m for m in received_messages if hasattr(m, "content") and "output from dep" in str(m.content)]
    assert human_msgs, "Upstream context was not included in the task message"


# ==============================================================================
# Test: _merge_tool_call_chunks
# ==============================================================================


def test_merge_tool_call_chunks_empty():
    from backend.agents.worker import _merge_tool_call_chunks

    assert _merge_tool_call_chunks([]) == []


def test_merge_tool_call_chunks_single():
    from backend.agents.worker import _merge_tool_call_chunks

    chunks = [{"index": 0, "id": "abc", "name": "run_python", "args": '{"code": "1+1"}'}]
    result = _merge_tool_call_chunks(chunks)
    assert len(result) == 1
    assert result[0]["name"] == "run_python"
    assert result[0]["args"] == {"code": "1+1"}


def test_merge_tool_call_chunks_streamed_args():
    """Args fragmented across multiple chunks are reassembled correctly."""
    from backend.agents.worker import _merge_tool_call_chunks

    chunks = [
        {"index": 0, "id": "x", "name": "web_search", "args": '{"q'},
        {"index": 0, "id": "",  "name": "",            "args": 'uery": "test"}'},
    ]
    result = _merge_tool_call_chunks(chunks)
    assert result[0]["args"] == {"query": "test"}
