"""
Application middleware: request-id correlation.
"""

from __future__ import annotations

import contextvars
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request  # noqa: TC002 — runtime annotation

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach an X-Request-ID to every request and log line."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


class RequestIdLogFilter(logging.Filter):
    """Inject the current request_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def install_log_filter() -> None:
    """Add the request-id filter to the root logger handlers (idempotent)."""
    root = logging.getLogger()
    flt = RequestIdLogFilter()
    for handler in root.handlers:
        # Don't add the filter twice
        if not any(isinstance(f, RequestIdLogFilter) for f in handler.filters):
            handler.addFilter(flt)
