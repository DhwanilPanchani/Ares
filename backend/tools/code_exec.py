"""
Tool: run_python

Executes a Python code string in a subprocess with a 10-second timeout.
Uses subprocess.run — never eval or exec — for safety and isolation.

stdout is returned on success.
stderr is returned on error.
A timeout message is returned if the code runs too long.
The agent always gets a string back, never an exception.
"""

from __future__ import annotations

import logging
import subprocess
import sys

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10


@tool
def run_python(code: str) -> str:
    """
    Execute a Python code snippet and return the output.

    Use this tool to run calculations, data processing, or any task that
    requires actual code execution. The code runs in a subprocess with a
    10-second timeout. Print statements produce output. Import any standard
    library module freely. Third-party packages available in the current
    environment can also be imported.

    Args:
        code: Valid Python source code to execute. Use print() to produce output.

    Returns:
        stdout on success, stderr on error, or a timeout message.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return (
            f"Error: code execution timed out after {_TIMEOUT_SECONDS} seconds. "
            "Break your code into smaller steps or avoid infinite loops."
        )
    except Exception as exc:
        return f"Error: failed to start Python subprocess: {exc}"

    if result.returncode == 0:
        output = result.stdout.strip()
        return output if output else "(code ran successfully with no output)"
    else:
        stderr = result.stderr.strip()
        return f"Error (exit code {result.returncode}):\n{stderr}"
