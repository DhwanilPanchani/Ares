"""
Custom OpenTelemetry SpanExporter that writes completed spans to SQLite.

Called by the OTel SDK after every span ends. Runs synchronously inside
the SDK's export loop, so we use asyncio.run_coroutine_threadsafe to
dispatch to the existing event loop (FastAPI's loop).

Maps OTel SDK span fields to the spans table columns:
  id           → hex(span.context.span_id)
  trace_id     → hex(span.context.trace_id)
  parent_id    → hex(span.parent.span_id) if parent else None
  run_id       → span.attributes["run_id"]
  node_id      → span.attributes["node_id"] (optional)
  name         → span.name
  kind         → "llm" | "tool" | "agent" | "system"  (from span name prefix)
  attributes   → dict(span.attributes) minus run_id/node_id sentinel keys
  started_at   → iso8601 from span.start_time (nanoseconds)
  ended_at     → iso8601 from span.end_time (nanoseconds)
  status_code  → "ERROR" if span.status.status_code == ERROR else "OK"
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode

logger = logging.getLogger(__name__)

# Nanoseconds → seconds
_NS_PER_SEC = 1_000_000_000


def _ns_to_iso(ns: int | None) -> str | None:
    """Convert a nanosecond Unix timestamp to an ISO-8601 string."""
    if ns is None:
        return None
    secs = ns / _NS_PER_SEC
    return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()


def _hex_id(value: int, width: int = 16) -> str:
    """Format an integer span/trace ID as a zero-padded hex string."""
    return format(value, f"0{width}x")


def _classify_kind(name: str) -> str:
    """
    Derive the 'kind' column value from the span name.

    Convention (from PHASE-3 spec):
      "llm.call"    → "llm"
      "tool.*"      → "tool"
      "agent.*"     → "agent"
      anything else → "system"
    """
    if name.startswith("llm."):
        return "llm"
    if name.startswith("tool."):
        return "tool"
    if name.startswith("agent."):
        return "agent"
    return "system"


class SQLiteSpanExporter(SpanExporter):
    """
    Exports OTel spans to the SQLite spans table via spans_repo.

    Thread-safety: export() is called from the SDK background thread.
    We grab the running event loop (FastAPI's loop) and schedule the
    coroutine on it, then block until it finishes.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Write all spans in this batch to SQLite."""
        for span in spans:
            try:
                self._export_one(span)
            except Exception as exc:
                logger.warning("SQLiteSpanExporter: failed to export span %s: %s", span.name, exc)
        return SpanExportResult.SUCCESS

    def _export_one(self, span: ReadableSpan) -> None:
        """Schedule a single span write on the FastAPI event loop."""
        from backend.store import spans_repo  # late import avoids circular deps at startup

        ctx = span.context
        if ctx is None:
            return

        span_id = _hex_id(ctx.span_id, 16)
        trace_id = _hex_id(ctx.trace_id, 32)

        parent_id: str | None = None
        if span.parent is not None:
            parent_id = _hex_id(span.parent.span_id, 16)

        attrs: dict = dict(span.attributes or {})
        run_id: str = attrs.pop("run_id", "")
        node_id: str | None = attrs.pop("node_id", None) or None

        # Skip infrastructure spans (e.g. http.request from middleware) that
        # have no run_id — they cannot satisfy the runs FK constraint.
        if not run_id:
            return

        kind = _classify_kind(span.name)

        status_code = (
            "ERROR"
            if span.status.status_code == StatusCode.ERROR
            else "OK"
        )

        coro = spans_repo.create(
            span_id=span_id,
            trace_id=trace_id,
            run_id=run_id,
            name=span.name,
            kind=kind,
            started_at=_ns_to_iso(span.start_time) or "",
            parent_id=parent_id,
            node_id=node_id,
            attributes=attrs,
            ended_at=_ns_to_iso(span.end_time),
            status_code=status_code,
        )

        # Submit to the running FastAPI event loop from this sync thread
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:
            # Loop is closed (server shutdown or test teardown) — discard cleanly
            coro.close()
            return
        try:
            future.result(timeout=5.0)
        except Exception as exc:
            logger.warning("SQLiteSpanExporter: DB write failed for span '%s': %s", span.name, exc)

    def shutdown(self) -> None:
        pass  # nothing to close — connections are short-lived per spans_repo call

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True
