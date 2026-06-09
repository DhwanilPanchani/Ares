"""
Tracing middleware — injects a per-request OTel trace context.

Each incoming HTTP request becomes a root span named "http.request".
All spans created during the request handling (LLM calls, tool calls)
become children of this request span automatically via OTel's context
propagation through contextvars.

This is critical for correct parent-child relationships in the span tree.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from backend.tracing.setup import get_tracer

logger = logging.getLogger(__name__)


class TracingMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware that wraps each request in an OTel span.

    The span is named "http.request" and carries standard HTTP attributes.
    Child spans (LLM calls, tool calls) are automatically nested beneath it
    because OTel propagates context via Python's contextvars — which asyncio
    preserves across await boundaries and asyncio.gather() calls.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        tracer = get_tracer()
        route = request.url.path
        method = request.method

        with tracer.start_as_current_span(
            "http.request",
            kind=SpanKind.SERVER,
            attributes={
                "http.method": method,
                "http.route": route,
                "http.url": str(request.url),
            },
        ):
            response: Response = await call_next(request)
            # Attach status code to span after response is available
            trace.get_current_span().set_attribute(
                "http.status_code", response.status_code
            )
            return response
