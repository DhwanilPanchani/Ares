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


async def _migrate_nodes_primary_key(db: aiosqlite.Connection) -> None:
    """
    Migrate the nodes table from a single-column PK (id TEXT PRIMARY KEY)
    to a composite PK (id, run_id).

    SQLite does not support ALTER TABLE … DROP/ADD PRIMARY KEY, so we use
    the standard SQLite rename-and-recreate migration pattern.

    This is a no-op if the table already has the correct schema.
    """
    # Check the current CREATE TABLE SQL for the nodes table
    cursor = await db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='nodes'"
    )
    row = await cursor.fetchone()
    if row is None:
        # Table does not exist yet — schema.sql will create it correctly
        return

    current_sql: str = row[0] or ""

    # If the existing schema already has a composite PK we are done
    if "PRIMARY KEY (id, run_id)" in current_sql:
        return

    logger.warning(
        "nodes table has old single-column PRIMARY KEY — running migration to "
        "composite (id, run_id). All existing node data will be dropped."
    )

    # Disable FK enforcement during migration
    await db.execute("PRAGMA foreign_keys = OFF")

    # Drop old indexes that reference nodes.id
    await db.execute("DROP INDEX IF EXISTS idx_nodes_run_id")
    await db.execute("DROP INDEX IF EXISTS idx_nodes_status")

    # Drop the spans FK on node_id (spans will be recreated via schema below)
    # We simply drop spans too since they reference stale node IDs
    await db.execute("DROP TABLE IF EXISTS spans")

    # Drop and recreate nodes with the correct composite PK
    await db.execute("DROP TABLE IF EXISTS nodes")

    await db.commit()

    # Re-enable FK enforcement
    await db.execute("PRAGMA foreign_keys = ON")

    logger.info("nodes table migration complete — table dropped and will be recreated by schema.sql")


async def init_db() -> None:
    """
    Read schema.sql and execute it against the database.

    Safe to call multiple times — all CREATE TABLE statements use IF NOT EXISTS.
    Runs a migration to fix the nodes PRIMARY KEY if the existing DB has the
    old single-column schema.

    Creates the output and chroma directories if they do not exist.
    """
    # Ensure the output directory exists
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)

    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")

    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Run migration before applying schema so the old table is gone
        await _migrate_nodes_primary_key(db)

        # Apply the full schema (CREATE TABLE IF NOT EXISTS is idempotent)
        await db.executescript(schema_sql)
        await db.commit()

    logger.info("Database initialised at %s", _DB_PATH)
