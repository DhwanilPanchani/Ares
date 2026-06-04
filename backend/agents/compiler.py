"""
DAG Compiler — converts a natural language goal into a validated DAGPlan.

Uses qwen2.5:3b via Ollama's HTTP API.
Retries up to 3 times on ValidationError, feeding the error back to the model.
Raises CompilerError after 3 consecutive failures.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from backend.agents.prompts import ORCHESTRATOR_SYSTEM_PROMPT
from backend.config import settings
from backend.models import CompilerError, DAGPlan

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
OLLAMA_GENERATE_URL = "{base}/api/chat"


async def compile_dag(goal: str) -> DAGPlan:
    """
    Convert a natural language goal into a validated DAGPlan.

    Calls qwen2.5:3b via Ollama with JSON mode enabled.
    Retries up to MAX_RETRIES times on parse or validation errors.

    Args:
        goal: The natural language goal string (10–500 chars).

    Returns:
        A validated DAGPlan with at least 2 nodes.

    Raises:
        CompilerError: If all retry attempts fail.
    """
    url = OLLAMA_GENERATE_URL.format(base=settings.ollama_base_url)
    last_raw: str = ""
    last_error: str = ""

    # Build the initial messages list
    messages: list[dict[str, str]] = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Break down this goal into a DAG of tasks:\n\n{goal}"
            ),
        },
    ]

    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            logger.info(
                "compile_dag attempt %d/%d for goal: %.80s",
                attempt,
                MAX_RETRIES,
                goal,
            )

            # If this is a retry, append the previous error as user feedback
            if attempt > 1 and last_error:
                messages.append({
                    "role": "assistant",
                    "content": last_raw,
                })
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous response had this validation error:\n\n"
                        f"{last_error}\n\n"
                        f"Fix the JSON and return ONLY the corrected JSON object."
                    ),
                })

            payload: dict[str, Any] = {
                "model": settings.ollama_orchestrator_model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "options": {
                    "num_ctx": 4096,
                    "temperature": 0.1,  # Low temperature for deterministic JSON
                },
            }

            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Ollama HTTP error on attempt %d: %s", attempt, exc
                )
                last_error = f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                if attempt == MAX_RETRIES:
                    raise CompilerError(
                        f"Ollama unavailable after {MAX_RETRIES} attempts: {last_error}",
                        last_raw=last_raw,
                    ) from exc
                continue
            except httpx.RequestError as exc:
                logger.warning(
                    "Ollama connection error on attempt %d: %s", attempt, exc
                )
                last_error = f"Connection error: {exc}"
                if attempt == MAX_RETRIES:
                    raise CompilerError(
                        f"Cannot connect to Ollama after {MAX_RETRIES} attempts. "
                        f"Is Ollama running at {settings.ollama_base_url}?",
                        last_raw=last_raw,
                    ) from exc
                continue

            # Extract text content from the response
            resp_json = response.json()
            last_raw = resp_json.get("message", {}).get("content", "")

            if not last_raw:
                last_error = "Empty response from Ollama."
                logger.warning("Empty Ollama response on attempt %d", attempt)
                continue

            # Strip accidental markdown fences if the model added them
            cleaned = _strip_markdown_fences(last_raw)

            # Parse JSON
            try:
                raw_dict = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error: {exc}. Raw response:\n{cleaned[:300]}"
                logger.warning(
                    "JSON decode failed on attempt %d: %s", attempt, exc
                )
                continue

            # Inject the goal if the model omitted it
            if "goal" not in raw_dict:
                raw_dict["goal"] = goal

            # Validate against DAGPlan Pydantic model
            try:
                dag_plan = DAGPlan.model_validate(raw_dict, strict=False)
                logger.info(
                    "compile_dag succeeded on attempt %d — %d nodes",
                    attempt,
                    len(dag_plan.nodes),
                )
                return dag_plan
            except ValidationError as exc:
                last_error = f"Pydantic validation error:\n{exc}"
                logger.warning(
                    "DAGPlan validation failed on attempt %d:\n%s",
                    attempt,
                    exc,
                )
                continue

    # All retries exhausted
    raise CompilerError(
        f"DAG compilation failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}",
        last_raw=last_raw,
    )


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (``` or ```json) and last line (```)
        inner_lines = lines[1:]
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        text = "\n".join(inner_lines).strip()
    return text
