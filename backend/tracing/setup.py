"""
OpenTelemetry tracer provider initialisation.

Phase 2: Initialises a real TracerProvider with no exporter (spans are
created and ended but not persisted).

Phase 3 will add the custom SQLite SpanExporter so every span is
persisted to the spans table.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)

_tracer: trace.Tracer | None = None


def init_tracing() -> None:
    """
    Set up the global OTel TracerProvider.

    Call once from the FastAPI lifespan handler.
    Phase 3 will register the SQLite exporter here.
    """
    global _tracer
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    _tracer = provider.get_tracer("ares")
    logger.info("OpenTelemetry tracer initialised (no exporter — Phase 3 will add SQLite exporter)")


def get_tracer() -> trace.Tracer:
    """Return the module-level tracer, initialising a default one if needed."""
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer("ares")
    return _tracer
