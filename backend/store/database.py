"""
Async SQLite connection management for Project Ares.

Usage:
    async with get_db() as db:
        await db.execute(...)
        await db.commit()

Call init_db() once at application startup (via FastAPI lifespan).
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from backend.config import settings

logger = logging.getLogger(__name__)

# Path to the schema SQL file (relative to this file)
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Path to the SQLite database file
_DB_PATH: Path = settings.db_path


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager that yields a configured aiosqlite connection."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Enable WAL mode and foreign keys for every connection
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA foreign_keys = ON")
        yield db


async def init_db() -> None:
    """
    Read schema.sql and execute it against the database.

    Safe to call multiple times — all CREATE TABLE statements use IF NOT EXISTS.
    Creates the output directory if it does not exist.
    """
    # Ensure the output directory exists
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)

    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")

    async with get_db() as db:
        await db.executescript(schema_sql)
        await db.commit()

    logger.info("Database initialised at %s", _DB_PATH)
