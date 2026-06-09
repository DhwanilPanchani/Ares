"""
pytest fixtures shared across all test modules.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest
import pytest_asyncio


@pytest.fixture
def tmp_run_id() -> str:
    """Generate a fresh UUID for test isolation."""
    return str(uuid.uuid4())


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    """
    Point the database module at a fresh temp SQLite file for each test.
    Initialises the schema and restores the original path on teardown.
    """
    import backend.store.database as db_module
    from backend.store.database import init_db

    original_path = db_module._DB_PATH
    db_module._DB_PATH = tmp_path / "test_ares.db"

    await init_db()
    yield

    db_module._DB_PATH = original_path


@pytest.fixture(scope="session")
def sre_demo_server():
    """
    Start a minimal HTTP server on port 9999 for the SRE eval case.

    Provides:
      GET /health    → 200 {"status": "ok"}
      GET /slow      → 200 after a 2-second delay (slow endpoint)
      GET /broken    → 500 Internal Server Error (broken route)

    The server runs in a daemon thread for the entire test session.
    """
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    demo_app = FastAPI()

    @demo_app.get("/health")
    def health():
        return {"status": "ok"}

    @demo_app.get("/slow")
    def slow():
        time.sleep(2)
        return {"status": "slow but ok"}

    @demo_app.get("/broken")
    def broken():
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    config = uvicorn.Config(
        demo_app,
        host="127.0.0.1",
        port=9999,
        log_level="error",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to come up
    import httpx
    for _ in range(20):
        try:
            httpx.get("http://127.0.0.1:9999/health", timeout=1.0)
            break
        except Exception:
            time.sleep(0.25)

    yield

    server.should_exit = True
