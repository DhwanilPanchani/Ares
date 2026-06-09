"""
Critic Agent — post-run trust scoring via phi4-mini.

Called once per run AFTER all DAG nodes complete. Never runs while
qwen2.5:3b is loaded in memory — enforces the hard memory constraint
defined in 00-INDEX.md by polling the Ollama /api/ps endpoint and
waiting up to _UNLOAD_TIMEOUT_S seconds before proceeding.

Flow:
  1. Wait for qwen2.5:3b to unload from Ollama memory.
  2. Build the critic prompt (goal + final output + tool summary + node previews).
  3. Call phi4-mini via Ollama /api/chat with JSON format enabled.
  4. Validate the JSON response against TrustScore (up to 3 retries).
  5. Persist the score via scores_repo.
  6. Emit a trust_scored SSE event.
  7. Append to critic_flagged.jsonl if any span IDs are flagged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from backend.agents.prompts import CRITIC_SYSTEM_PROMPT, CRITIC_TASK_TEMPLATE
from backend.config import settings
from backend.models import CriticError, TrustScore
from backend.store import nodes_repo, scores_repo
from backend.store.events import event_bus

logger = logging.getLogger(__name__)

# How long to wait for qwen2.5:3b to unload before giving up (seconds)
_UNLOAD_TIMEOUT_S = 30
# Poll interval while waiting for model to unload
_POLL_INTERVAL_S = 3
# Max retries for phi4-mini validation failures
_MAX_RETRIES = 3

OLLAMA_PS_URL = "{base}/api/ps"
OLLAMA_CHAT_URL = "{base}/api/chat"


# ==============================================================================
# Public entry point
# ==============================================================================


async def score_run(
    run_id: str,
    goal: str,
    final_output: str,
    tool_calls_log: list[dict[str, Any]],
) -> TrustScore:
    """
    Score a completed run using the Critic (phi4-mini).

    Args:
        run_id:         The run to score — used for DB writes and SSE.
        goal:           Original user goal.
        final_output:   Aggregated output from all DAG nodes.
        tool_calls_log: All tool invocations made during the run.

    Returns:
        The validated TrustScore.

    Raises:
        CriticError: If all 3 retry attempts fail.
    """
    # --- Step 1: wait for orchestrator model to unload ---
    await _wait_for_model_unload(settings.ollama_orchestrator_model)

    # --- Step 2: gather node summaries from DB ---
    node_rows = await nodes_repo.list_for_run(run_id)
    node_outputs_summary = _format_node_outputs(node_rows)
    tool_calls_summary = _format_tool_calls(tool_calls_log)

    # --- Step 3: call phi4-mini with retries ---
    score = await _call_critic(goal, final_output, tool_calls_summary, node_outputs_summary)

    # --- Step 4: persist ---
    await scores_repo.create(
        run_id=run_id,
        factual_grounding=score.factual_grounding,
        goal_completion=score.goal_completion,
        tool_error_rate=score.tool_error_rate,
        trust_score=score.trust_score,
        critique_text=score.critique_text,
        flagged_span_ids=score.flagged_span_ids,
    )

    # --- Step 5: emit SSE ---
    await event_bus.emit(
        run_id,
        "trust_scored",
        {
            "factual_grounding": score.factual_grounding,
            "goal_completion": score.goal_completion,
            "tool_error_rate": score.tool_error_rate,
            "trust_score": score.trust_score,
            "critique_text": score.critique_text,
            "flagged_span_ids": score.flagged_span_ids,
        },
    )

    # --- Step 6: auto-append flagged spans ---
    if score.flagged_span_ids:
        try:
            from backend.tests.evals.golden_set import append_to_flagged
            append_to_flagged(run_id, goal, score.flagged_span_ids)
            logger.info(
                "Critic flagged %d spans for run %s — appended to critic_flagged.jsonl",
                len(score.flagged_span_ids),
                run_id,
            )
        except Exception as exc:
            logger.warning("Failed to append flagged spans to JSONL: %s", exc)

    logger.info(
        "Run %s scored: trust=%.2f factual=%.2f completion=%.2f tool_errors=%.2f",
        run_id,
        score.trust_score,
        score.factual_grounding,
        score.goal_completion,
        score.tool_error_rate,
    )
    return score


# ==============================================================================
# Model unload check
# ==============================================================================


async def _wait_for_model_unload(model_name: str) -> None:
    """
    Poll the Ollama /api/ps endpoint until model_name is no longer
    listed as loaded. Waits up to _UNLOAD_TIMEOUT_S seconds.

    If the model is not loaded from the start, returns immediately.
    If the timeout is exceeded, logs a warning and proceeds anyway
    (better to score with both models than to never score).
    """
    url = OLLAMA_PS_URL.format(base=settings.ollama_base_url)
    deadline = asyncio.get_running_loop().time() + _UNLOAD_TIMEOUT_S

    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                loaded = {m.get("name", "") for m in resp.json().get("models", [])}
                if not any(model_name in name for name in loaded):
                    logger.debug("Model %s is unloaded — starting Critic", model_name)
                    return
                logger.debug(
                    "Waiting for %s to unload (loaded: %s)", model_name, loaded
                )
            except Exception as exc:
                # If we can't reach Ollama, proceed — don't block the score forever
                logger.warning("Cannot check Ollama loaded models: %s — proceeding", exc)
                return

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                logger.warning(
                    "Timed out waiting for %s to unload after %ds — proceeding anyway",
                    model_name,
                    _UNLOAD_TIMEOUT_S,
                )
                return

            await asyncio.sleep(min(_POLL_INTERVAL_S, remaining))


# ==============================================================================
# LLM call with retries
# ==============================================================================


async def _call_critic(
    goal: str,
    final_output: str,
    tool_calls_summary: str,
    node_outputs_summary: str,
) -> TrustScore:
    """
    Call phi4-mini via Ollama and validate the response as a TrustScore.
    Retries up to _MAX_RETRIES times on parse or validation errors.
    """
    url = OLLAMA_CHAT_URL.format(base=settings.ollama_base_url)
    task_content = CRITIC_TASK_TEMPLATE.format(
        goal=goal,
        final_output=final_output[:3000],  # cap to avoid overwhelming context
        tool_calls_summary=tool_calls_summary,
        node_outputs_summary=node_outputs_summary,
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
        {"role": "user", "content": task_content},
    ]

    last_raw = ""
    last_error = ""

    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(1, _MAX_RETRIES + 1):
            logger.info(
                "Critic attempt %d/%d for run", attempt, _MAX_RETRIES
            )

            if attempt > 1 and last_error:
                messages.append({"role": "assistant", "content": last_raw})
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your response had this validation error:\n\n{last_error}\n\n"
                        "Fix the JSON and return ONLY the corrected JSON object with "
                        "exactly these six keys: factual_grounding, goal_completion, "
                        "tool_error_rate, trust_score, critique_text, flagged_span_ids."
                    ),
                })

            payload: dict[str, Any] = {
                "model": settings.ollama_critic_model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "options": {"num_ctx": 4096, "temperature": 0.1},
            }

            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                logger.warning("Critic HTTP error attempt %d: %s", attempt, last_error)
                if attempt == _MAX_RETRIES:
                    raise CriticError(f"Ollama HTTP error after {_MAX_RETRIES} attempts: {last_error}")
                await asyncio.sleep(2)
                continue
            except httpx.RequestError as exc:
                last_error = f"Connection error: {exc}"
                logger.warning("Critic connection error attempt %d: %s", attempt, exc)
                if attempt == _MAX_RETRIES:
                    raise CriticError(f"Cannot connect to Ollama: {last_error}")
                await asyncio.sleep(2)
                continue

            last_raw = response.json().get("message", {}).get("content", "")
            if not last_raw:
                last_error = "Empty response from Ollama."
                continue

            # Strip markdown fences if present
            cleaned = _strip_fences(last_raw)

            try:
                raw_dict = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error: {exc}. Raw:\n{cleaned[:300]}"
                logger.warning("Critic JSON decode failed attempt %d: %s", attempt, exc)
                continue

            try:
                score = TrustScore.model_validate(raw_dict, strict=False)
                logger.info("Critic scored successfully on attempt %d", attempt)
                return score
            except ValidationError as exc:
                last_error = f"Pydantic validation error:\n{exc}"
                logger.warning("Critic validation failed attempt %d:\n%s", attempt, exc)
                continue

    raise CriticError(
        f"Critic failed after {_MAX_RETRIES} attempts. Last error: {last_error}"
    )


# ==============================================================================
# Formatting helpers
# ==============================================================================


def _format_tool_calls(tool_calls_log: list[dict[str, Any]]) -> str:
    if not tool_calls_log:
        return "No tool calls were made."

    lines: list[str] = []
    for i, tc in enumerate(tool_calls_log, 1):
        tool = tc.get("tool", "unknown")
        args = tc.get("args", {})
        result = tc.get("result", "")
        error = tc.get("error")

        # Truncate long results so we don't blow the critic's context
        result_preview = str(result)[:300] if result else ""
        status = f"ERROR: {error}" if error else f"OK: {result_preview}"
        lines.append(f"{i}. {tool}({json.dumps(args)[:200]}) → {status}")

    return "\n".join(lines)


def _format_node_outputs(node_rows: list[dict[str, Any]]) -> str:
    if not node_rows:
        return "No node outputs available."

    parts: list[str] = []
    for n in node_rows:
        name = n.get("name", n.get("id", "unknown"))
        status = n.get("status", "unknown")
        output = (n.get("output") or "").strip()
        preview = output[:500] if output else "(no output)"
        parts.append(f"[{name}] ({status}): {preview}")

    return "\n\n".join(parts)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    return text
