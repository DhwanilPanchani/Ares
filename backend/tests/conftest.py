"""
pytest fixtures shared across all test modules.

Full fixtures for integration tests added in Phase 4.
"""

import uuid

import pytest


@pytest.fixture
def tmp_run_id() -> str:
    """Generate a fresh UUID for test isolation."""
    return str(uuid.uuid4())
