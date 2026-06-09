"""
Tools: read_file, write_file

Both tools are sandboxed to OUTPUT_DIR from config. Any path that resolves
outside OUTPUT_DIR is rejected immediately with an error string — no exception
is raised to the agent so it can reason about the failure and try a different path.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import tool

from backend.config import settings

logger = logging.getLogger(__name__)


def _resolve_sandboxed(relative_path: str) -> Path | str:
    """
    Resolve relative_path inside OUTPUT_DIR.

    Returns the absolute Path if safe, or an error string if the path
    would escape the sandbox (path traversal attempt).
    """
    output_dir: Path = settings.output_dir.resolve()
    # Ensure the output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the requested path relative to the output dir
    requested = (output_dir / relative_path).resolve()

    # The resolved path must start with output_dir (prevents ../.. escapes)
    try:
        requested.relative_to(output_dir)
    except ValueError:
        return (
            f"Error: path '{relative_path}' is outside the allowed output directory. "
            f"Only paths inside '{output_dir}' are permitted."
        )

    return requested


@tool
def read_file(path: str) -> str:
    """
    Read the contents of a file from the output directory.

    Use this tool to read files that were previously written by write_file,
    or to read reference files that exist in the output directory.
    The path must be relative to the output directory.

    Args:
        path: Relative file path within the output directory (e.g. "report.md").

    Returns:
        The file contents as a string, or an error message.
    """
    resolved = _resolve_sandboxed(path)
    if isinstance(resolved, str):
        return resolved  # error message

    if not resolved.exists():
        return f"Error: file '{path}' does not exist in the output directory."

    if not resolved.is_file():
        return f"Error: '{path}' is a directory, not a file."

    try:
        content = resolved.read_text(encoding="utf-8")
        return content
    except PermissionError:
        return f"Error: permission denied reading '{path}'."
    except Exception as exc:
        return f"Error reading '{path}': {exc}"


@tool
def write_file(path: str, content: str) -> str:
    """
    Write content to a file in the output directory.

    Use this tool to save your work: reports, summaries, code, data files,
    or any other output. Creates parent directories automatically if needed.
    The path must be relative to the output directory.

    Args:
        path:    Relative file path within the output directory (e.g. "report.md").
        content: The text content to write to the file.

    Returns:
        A success message with the full file path, or an error message.
    """
    resolved = _resolve_sandboxed(path)
    if isinstance(resolved, str):
        return resolved  # error message

    try:
        # Create parent directories if they don't exist
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} characters to '{path}'."
    except PermissionError:
        return f"Error: permission denied writing to '{path}'."
    except OSError as exc:
        return f"Error writing '{path}': {exc}"
    except Exception as exc:
        return f"Unexpected error writing '{path}': {exc}"
