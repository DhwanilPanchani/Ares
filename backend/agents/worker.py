"""
Worker Agent — executes a single DAG node.

Receives a DAGNode, run_id, and upstream node outputs.
Calls qwen2.5:3b via LangChain's ChatOllama wrapper with:
  - num_ctx = 2048 (halves KV-cache memory vs default 4096)
  - Streaming: emits token_chunk SSE events as tokens arrive
  - Tool binding: binds ALL_TOOLS from registry (empty in Phase 2, filled in Phase 3)
  - OTel span wrapping the entire LLM call

Returns (output: str, tool_calls: list[dict])
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama

from backend.agents.prompts import (
    WORKER_SYSTEM_PROMPT,
    WORKER_TASK_TEMPLATE,
    WORKER_UPSTREAM_CONTEXT_TEMPLATE,
)
from backend.config import settings
from backend.models import DAGNode, ToolCallRecord
from backend.store.events import event_bus
from backend.tracing.setup import get_tracer

logger = logging.getLogger(__name__)

# Maximum number of tool-call rounds before forcing a final answer
_MAX_TOOL_ROUNDS = 5


def _build_llm(num_ctx: int = 2048) -> ChatOllama:
    """Construct a ChatOllama instance for the worker model."""
    return ChatOllama(
        model=settings.ollama_orchestrator_model,
        base_url=settings.ollama_base_url,
        num_ctx=num_ctx,
        temperature=0.3,
    )


def _format_upstream_context(upstream_outputs: dict[str, str]) -> str:
    """Format upstream node outputs into a context block for the task message."""
    if not upstream_outputs:
        return ""
    lines = "\n\n".join(
        f"Output from '{dep}':\n{out.strip()}"
        for dep, out in upstream_outputs.items()
        if out.strip()
    )
    if not lines:
        return ""
    return WORKER_UPSTREAM_CONTEXT_TEMPLATE.format(upstream_outputs=lines)


async def run_worker(
    dag_node: DAGNode,
    run_id: str,
    upstream_outputs: dict[str, str],
) -> tuple[str, list[dict[str, Any]]]:
    """
    Execute one DAG node using the Worker LLM.

    Args:
        dag_node:         The node to execute (contains id, description, tool_hint).
        run_id:           Used to emit SSE events and attach OTel spans.
        upstream_outputs: Mapping of upstream node_id → output string.

    Returns:
        (final_output, tool_calls_log)
        - final_output:    The full text output of the worker.
        - tool_calls_log:  List of dicts describing every tool call made.
    """
    tracer = get_tracer()
    node_id = dag_node.id

    # Build the initial message list
    context = _format_upstream_context(upstream_outputs)
    task_msg = WORKER_TASK_TEMPLATE.format(
        description=dag_node.description,
        upstream_context=context,
    )
    messages: list = [
        SystemMessage(content=WORKER_SYSTEM_PROMPT),
        HumanMessage(content=task_msg),
    ]

    # Bind tools if the registry has any (populated in Phase 3)
    from backend.tools.registry import ALL_TOOLS  # imported here to pick up Phase 3 fill

    llm = _build_llm()
    llm_with_tools = llm.bind_tools(ALL_TOOLS) if ALL_TOOLS else llm

    tool_calls_log: list[dict[str, Any]] = []
    final_output = ""

    with tracer.start_as_current_span(
        "llm.call",
        attributes={
            "model": settings.ollama_orchestrator_model,
            "run_id": run_id,
            "node_id": node_id,
        },
    ) as span:
        # ----------------------------------------------------------------
        # ReAct-style loop: stream → check for tool calls → execute → repeat
        # ----------------------------------------------------------------
        for round_idx in range(_MAX_TOOL_ROUNDS):
            logger.debug(
                "Worker node %s round %d/%d", node_id, round_idx + 1, _MAX_TOOL_ROUNDS
            )

            # -- Stream the LLM response --------------------------------
            accumulated_content = ""
            accumulated_tool_call_chunks: list[dict] = []

            async for chunk in llm_with_tools.astream(messages):
                # Text content
                if chunk.content:
                    accumulated_content += chunk.content
                    await event_bus.emit(
                        run_id,
                        "token_chunk",
                        {"node_id": node_id, "chunk": chunk.content},
                    )

                # Tool call chunks (Phase 3: ALL_TOOLS non-empty)
                if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                    accumulated_tool_call_chunks.extend(chunk.tool_call_chunks)

            # Build a proper AIMessage from the accumulated chunks
            # so we can add it to the conversation history
            ai_msg = AIMessage(
                content=accumulated_content,
                tool_calls=_merge_tool_call_chunks(accumulated_tool_call_chunks),
            )
            messages.append(ai_msg)

            # -- No tool calls → we have the final answer ---------------
            if not ai_msg.tool_calls:
                final_output = accumulated_content
                break

            # -- Execute each requested tool call -----------------------
            for tool_call in ai_msg.tool_calls:
                tool_name: str = tool_call["name"]
                tool_args: dict = tool_call["args"]
                tool_call_id: str = tool_call.get("id", f"call_{round_idx}")

                logger.debug("Node %s calling tool '%s'", node_id, tool_name)

                # Emit SSE event
                await event_bus.emit(
                    run_id,
                    "tool_called",
                    {"node_id": node_id, "tool": tool_name, "args": tool_args},
                )

                # Execute the tool in its own OTel span
                result_str = await _call_tool(tool_name, tool_args, tracer, run_id, node_id)

                # Record in log
                tool_calls_log.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "result": result_str,
                        "error": None,
                    }
                )

                # Emit result SSE event
                await event_bus.emit(
                    run_id,
                    "tool_result",
                    {"node_id": node_id, "tool": tool_name, "result": result_str},
                )

                # Add the tool result back into the conversation
                messages.append(
                    ToolMessage(
                        content=result_str,
                        tool_call_id=tool_call_id,
                    )
                )

        else:
            # Exceeded max rounds — use last accumulated content
            logger.warning(
                "Node %s exceeded max tool rounds (%d), using last output",
                node_id,
                _MAX_TOOL_ROUNDS,
            )
            final_output = accumulated_content  # type: ignore[possibly-undefined]

        span.set_attribute("output_length", len(final_output))
        span.set_attribute("tool_call_count", len(tool_calls_log))

    return final_output, tool_calls_log


# ---------------------------------------------------------------------------
# Tool call helpers
# ---------------------------------------------------------------------------


def _merge_tool_call_chunks(chunks: list[dict]) -> list[dict]:
    """
    Merge streaming tool_call_chunks into complete tool call dicts.

    Each chunk has: index, id (optional), name (optional), args (str fragment).
    We merge by index.
    """
    if not chunks:
        return []

    merged: dict[int, dict] = {}
    for chunk in chunks:
        idx = chunk.get("index", 0)
        if idx not in merged:
            merged[idx] = {"id": "", "name": "", "args": ""}
        if chunk.get("id"):
            merged[idx]["id"] = chunk["id"]
        if chunk.get("name"):
            merged[idx]["name"] = chunk["name"]
        if chunk.get("args"):
            merged[idx]["args"] += chunk["args"]

    # Parse args JSON
    result = []
    for tc in merged.values():
        try:
            import json
            tc["args"] = json.loads(tc["args"]) if tc["args"] else {}
        except Exception:
            tc["args"] = {}
        result.append(tc)
    return result


async def _call_tool(
    tool_name: str,
    tool_args: dict,
    tracer: Any,
    run_id: str,
    node_id: str,
) -> str:
    """
    Find and invoke a tool by name, wrapped in an OTel span.

    Returns the tool's string result, or an error message string
    (tools must never raise — they return error strings so the agent can reason).
    """
    from backend.tools.registry import ALL_TOOLS

    tool_fn = next((t for t in ALL_TOOLS if t.name == tool_name), None)
    if tool_fn is None:
        return f"Error: tool '{tool_name}' is not registered."

    with tracer.start_as_current_span(
        f"tool.{tool_name}",
        attributes={"tool": tool_name, "run_id": run_id, "node_id": node_id},
    ):
        try:
            # LangChain tools are synchronous or async via .arun()
            if hasattr(tool_fn, "arun"):
                result = await tool_fn.arun(tool_args)
            else:
                import asyncio
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: tool_fn.run(tool_args)
                )
            return str(result)
        except Exception as exc:
            logger.warning("Tool '%s' raised: %s", tool_name, exc)
            return f"Error executing {tool_name}: {exc}"
