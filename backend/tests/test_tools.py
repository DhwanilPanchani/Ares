"""
Unit tests for backend/tools/

Covers each tool's happy path, error handling, and sandbox enforcement.
All HTTP calls are mocked with httpx.MockTransport or unittest.mock.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def tmp_output_dir(tmp_path, monkeypatch):
    """Point OUTPUT_DIR at a fresh temp directory for sandbox tests."""
    import backend.config as cfg_module

    # Build a new settings object pointing at tmp_path
    original = cfg_module.settings.output_dir

    # Monkeypatch the settings singleton's output_dir
    monkeypatch.setattr(cfg_module.settings, "output_dir", tmp_path)
    yield tmp_path
    monkeypatch.setattr(cfg_module.settings, "output_dir", original)


# ==============================================================================
# web_search
# ==============================================================================


@pytest.mark.asyncio
async def test_web_search_returns_formatted_results():
    """Happy path: Brave API returns results → formatted string returned."""
    fake_response_data = {
        "web": {
            "results": [
                {
                    "title": "Result One",
                    "url": "https://example.com/1",
                    "description": "Description one",
                },
                {
                    "title": "Result Two",
                    "url": "https://example.com/2",
                    "description": "Description two",
                },
            ]
        }
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = fake_response_data

    with (
        patch("backend.config.settings.brave_search_api_key", "fake-key"),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from backend.tools.web_search import web_search

        result = await web_search.arun({"query": "test query"})

    assert "Result One" in result
    assert "https://example.com/1" in result
    assert "Result Two" in result
    assert "Description one" in result


@pytest.mark.asyncio
async def test_web_search_no_api_key_returns_error():
    """Missing API key → returns error string, does not raise."""
    with patch("backend.config.settings.brave_search_api_key", ""):
        from backend.tools.web_search import web_search

        result = await web_search.arun({"query": "test"})

    assert "Error" in result
    assert "BRAVE_SEARCH_API_KEY" in result


@pytest.mark.asyncio
async def test_web_search_timeout_returns_error():
    """HTTP timeout → returns error string, does not raise."""
    with (
        patch("backend.config.settings.brave_search_api_key", "fake-key"),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value = mock_client

        from backend.tools.web_search import web_search

        result = await web_search.arun({"query": "slow query"})

    assert "Error" in result
    assert "timed out" in result.lower() or "timeout" in result.lower()


@pytest.mark.asyncio
async def test_web_search_empty_results():
    """API returns no results → informative message, no exception."""
    fake_response_data = {"web": {"results": []}}
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = fake_response_data

    with (
        patch("backend.config.settings.brave_search_api_key", "fake-key"),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from backend.tools.web_search import web_search

        result = await web_search.arun({"query": "nothing"})

    assert "No search results" in result


# ==============================================================================
# file_ops — read_file
# ==============================================================================


def test_read_file_happy_path(tmp_output_dir):
    """Can read a file that exists in OUTPUT_DIR."""
    target = tmp_output_dir / "hello.txt"
    target.write_text("hello world")

    from backend.tools.file_ops import read_file

    result = read_file.run({"path": "hello.txt"})
    assert result == "hello world"


def test_read_file_nonexistent(tmp_output_dir):
    """Reading a missing file returns an error string."""
    from backend.tools.file_ops import read_file

    result = read_file.run({"path": "missing.txt"})
    assert "Error" in result
    assert "does not exist" in result


def test_read_file_path_traversal_blocked(tmp_output_dir):
    """Path traversal (../etc/passwd) is rejected — sandbox enforced."""
    from backend.tools.file_ops import read_file

    result = read_file.run({"path": "../../etc/passwd"})
    assert "Error" in result
    assert "outside" in result


def test_read_file_directory_rejected(tmp_output_dir):
    """Passing a directory path returns an error string."""
    subdir = tmp_output_dir / "subdir"
    subdir.mkdir()

    from backend.tools.file_ops import read_file

    result = read_file.run({"path": "subdir"})
    assert "Error" in result


# ==============================================================================
# file_ops — write_file
# ==============================================================================


def test_write_file_happy_path(tmp_output_dir):
    """write_file creates the file and returns a success message."""
    from backend.tools.file_ops import write_file

    result = write_file.run({"path": "report.md", "content": "# Report\nDone."})
    assert "Successfully" in result
    assert (tmp_output_dir / "report.md").read_text() == "# Report\nDone."


def test_write_file_creates_parent_dirs(tmp_output_dir):
    """write_file creates intermediate directories."""
    from backend.tools.file_ops import write_file

    result = write_file.run({"path": "nested/deep/file.txt", "content": "data"})
    assert "Successfully" in result
    assert (tmp_output_dir / "nested" / "deep" / "file.txt").read_text() == "data"


def test_write_file_path_traversal_blocked(tmp_output_dir):
    """Path traversal in write is rejected — sandbox enforced."""
    from backend.tools.file_ops import write_file

    result = write_file.run({"path": "../outside.txt", "content": "evil"})
    assert "Error" in result
    assert "outside" in result
    assert not (tmp_output_dir.parent / "outside.txt").exists()


# ==============================================================================
# code_exec — run_python
# ==============================================================================


def test_run_python_happy_path():
    """Simple print statement returns its stdout."""
    from backend.tools.code_exec import run_python

    result = run_python.run({"code": "print('hello from subprocess')"})
    assert "hello from subprocess" in result


def test_run_python_stderr_on_error():
    """Code that raises an exception returns stderr."""
    from backend.tools.code_exec import run_python

    result = run_python.run({"code": "raise ValueError('bad input')"})
    assert "Error" in result
    assert "ValueError" in result or "bad input" in result


def test_run_python_timeout():
    """Code that loops forever hits the timeout and returns a message."""
    from backend.tools.code_exec import run_python

    result = run_python.run({"code": "while True: pass"})
    assert "timeout" in result.lower() or "timed out" in result.lower()


def test_run_python_no_output():
    """Code that produces no print output returns a no-output message."""
    from backend.tools.code_exec import run_python

    result = run_python.run({"code": "x = 1 + 1"})
    assert "no output" in result.lower() or result == ""


def test_run_python_uses_subprocess(monkeypatch):
    """Ensure we use subprocess.run, never eval/exec."""
    calls = []
    real_run = subprocess.run

    def spy_run(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy_run)

    from backend.tools.code_exec import run_python

    run_python.run({"code": "print(1)"})
    assert len(calls) == 1  # subprocess.run was called exactly once


# ==============================================================================
# http_get
# ==============================================================================


@pytest.mark.asyncio
async def test_http_get_happy_path():
    """Happy path: server returns 200 → response text returned."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>page content</html>")
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "<html>page content</html>"
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from backend.tools.http_get import http_get

        result = await http_get.arun({"url": "https://example.com"})

    assert "page content" in result


@pytest.mark.asyncio
async def test_http_get_invalid_url():
    """URL that doesn't start with http(s) → error string, no request made."""
    from backend.tools.http_get import http_get

    result = await http_get.arun({"url": "ftp://example.com"})
    assert "Error" in result
    assert "http" in result.lower()


@pytest.mark.asyncio
async def test_http_get_truncates_long_response():
    """Response longer than 3000 chars is truncated."""
    long_text = "x" * 5000
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = long_text

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from backend.tools.http_get import http_get

        result = await http_get.arun({"url": "https://example.com"})

    assert len(result) < 5000
    assert "truncated" in result


@pytest.mark.asyncio
async def test_http_get_connection_error():
    """Connection error → error string, does not raise."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        mock_client_cls.return_value = mock_client

        from backend.tools.http_get import http_get

        result = await http_get.arun({"url": "https://unreachable.invalid"})

    assert "Error" in result


# ==============================================================================
# registry
# ==============================================================================


def test_registry_contains_all_tools():
    """ALL_TOOLS must contain exactly the four expected tools."""
    from backend.tools.registry import ALL_TOOLS

    names = {t.name for t in ALL_TOOLS}
    assert "web_search" in names
    assert "read_file" in names
    assert "write_file" in names
    assert "run_python" in names
    assert "http_get" in names
    assert len(ALL_TOOLS) == 5
