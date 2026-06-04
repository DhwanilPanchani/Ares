"""
Golden eval set and auto-append from Critic flags.

Full implementation in Phase 4.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FLAGGED_JSONL_PATH = Path(__file__).parent / "critic_flagged.jsonl"


@dataclass
class EvalCase:
    id: str
    goal: str
    min_trust_score: float
    required_tools: list[str] = field(default_factory=list)
    expected_output_files: list[str] = field(default_factory=list)
    expected_keywords: list[str] = field(default_factory=list)
    min_parallel_nodes: int = 1


# Five canonical eval cases (pass criteria from 10-EVAL-HARNESS.md)
GOLDEN_EVALS: list[EvalCase] = [
    EvalCase(
        id="sre_diagnosis",
        goal=(
            "Check the health of a local test HTTP server at http://localhost:9999, "
            "identify any slow or failing endpoints, and write a diagnosis report to output/sre_report.md."
        ),
        min_trust_score=0.70,
        required_tools=["http_get", "write_file"],
        expected_output_files=["sre_report.md"],
    ),
    EvalCase(
        id="parallel_research",
        goal=(
            "Research the history of Python programming language and the history of "
            "JavaScript simultaneously, then write a comparison document to output/lang_comparison.md."
        ),
        min_trust_score=0.65,
        required_tools=["web_search", "write_file"],
        min_parallel_nodes=2,
    ),
    EvalCase(
        id="code_fix",
        goal=(
            "Read the file output/buggy_code.py, identify all bugs, and write a fixed "
            "version to output/fixed_code.py."
        ),
        min_trust_score=0.75,
        required_tools=["read_file", "write_file"],
        expected_output_files=["fixed_code.py"],
    ),
    EvalCase(
        id="data_pipeline",
        goal=(
            "Write a Python script that generates 100 random numbers, calculates mean, "
            "median, and standard deviation, and saves the results as JSON to output/stats.json."
        ),
        min_trust_score=0.75,
        required_tools=["run_python", "write_file"],
        expected_output_files=["stats.json"],
    ),
    EvalCase(
        id="multi_step_analysis",
        goal=(
            "Search for recent developments in large language model quantization techniques, "
            "analyse the findings, and produce a structured markdown summary in output/llm_quantization.md."
        ),
        min_trust_score=0.68,
        required_tools=["web_search", "write_file"],
        expected_keywords=["quantization", "model"],
    ),
]


def append_to_flagged(
    run_id: str,
    goal: str,
    flagged_span_ids: list[str],
) -> None:
    """
    Atomically append a flagged run record to critic_flagged.jsonl.

    Uses write-to-temp-then-rename to prevent corruption on interruption.
    """
    record: dict[str, Any] = {
        "run_id": run_id,
        "goal": goal,
        "flagged_span_ids": flagged_span_ids,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    line = json.dumps(record) + "\n"

    # Write to a temp file in the same directory, then rename (atomic on POSIX)
    dir_path = FLAGGED_JSONL_PATH.parent
    dir_path.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        # Copy existing content if file exists
        if FLAGGED_JSONL_PATH.exists():
            with open(FLAGGED_JSONL_PATH, "r", encoding="utf-8") as f:
                existing = f.read()
        else:
            existing = ""

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(existing)
            f.write(line)

        os.replace(tmp_path, FLAGGED_JSONL_PATH)
    except Exception:
        os.unlink(tmp_path)
        raise
