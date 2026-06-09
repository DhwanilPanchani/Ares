"""
OpenTelemetry tracer provider initialisation — Phase 3.

Replaces the Phase 2 stub. Now registers the SQLiteSpanExporter so
every completed span is persisted to the spans table in ares.db.

Call init_tracing(loop) once from the FastAPI lifespan handler,
passing the running event loop so the exporter can dispatch async DB writes.
"""

from __future__ import annotations

import asyncio
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None


def init_tracing(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """
    Set up the global OTel TracerProvider with SQLite export.

    Args:
        loop: The running asyncio event loop (FastAPI's loop).
              If None, attempts to get the running loop automatically.
    """
    global _provider

    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "init_tracing() called outside an async context — "
                "span export will not work. Pass the loop explicitly."
            )
            # Fall back to a no-op provider so the app still starts cleanly
            _provider = TracerProvider()
            trace.set_tracer_provider(_provider)
            return

    from backend.tracing.exporter import SQLiteSpanExporter

    exporter = SQLiteSpanExporter(loop)
    processor = BatchSpanProcessor(
        exporter,
        # Flush quickly so spans appear in the DB before the SSE stream closes
        schedule_delay_millis=200,
        max_export_batch_size=64,
    )

    _provider = TracerProvider()
    _provider.add_span_processor(processor)
    trace.set_tracer_provider(_provider)

    logger.info("OpenTelemetry tracer initialised with SQLiteSpanExporter")


def get_tracer() -> trace.Tracer:
    """Return the module-level Ares tracer, initialising a default one if needed."""
    if _provider is not None:
        return _provider.get_tracer("ares")
    return trace.get_tracer("ares")


def shutdown_tracing() -> None:
    """Flush and shut down the tracer provider. Call from the FastAPI lifespan shutdown."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None
        logger.info("OpenTelemetry tracer shut down")
