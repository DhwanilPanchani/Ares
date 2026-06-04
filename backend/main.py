"""
Project Ares — FastAPI application entry point.

Starts the backend API server with:
  - CORS for the Next.js frontend at localhost:3000
  - Lifespan handler initialising SQLite DB and OTel tracing
  - /api/health endpoint
  - All phase routers included
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.store.database import init_db
from backend.tracing.setup import init_tracing

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown logic."""
    logger.info("Starting Project Ares backend")

    # Initialise database (creates tables if they don't exist)
    await init_db()
    logger.info("Database ready")

    # Initialise OpenTelemetry tracer
    init_tracing()

    yield

    logger.info("Shutting down Project Ares backend")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Project Ares",
    description="Local-first observable multi-agent execution platform",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["health"])
async def health() -> dict[str, str]:
    """
    Returns backend health status and configured model names.

    Used by the frontend to verify backend availability on page load.
    """
    return {
        "status": "ok",
        "orchestrator_model": settings.ollama_orchestrator_model,
        "critic_model": settings.ollama_critic_model,
        "embed_model": settings.ollama_embed_model,
        "database": "sqlite",
    }


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from backend.api.runs import router as runs_router       # noqa: E402
from backend.api.stream import router as stream_router   # noqa: E402
from backend.api.traces import router as traces_router   # noqa: E402
from backend.api.replay import router as replay_router   # noqa: E402

app.include_router(runs_router, prefix="/api")
app.include_router(stream_router, prefix="/api")
app.include_router(traces_router, prefix="/api")
app.include_router(replay_router, prefix="/api")
