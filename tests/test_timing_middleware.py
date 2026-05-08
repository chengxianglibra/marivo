from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware

from app.observability import TimingMiddleware


def test_is_not_base_http_middleware():
    """TimingMiddleware must be pure ASGI, not BaseHTTPMiddleware.

    BaseHTTPMiddleware buffers the full response body, which breaks
    SSE streaming (e.g. FastMCP streamable-http transport).
    """
    assert not issubclass(TimingMiddleware, BaseHTTPMiddleware)
