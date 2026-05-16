from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from marivo.identity import current_user
from marivo.transports.http.middleware import UserIdentityMiddleware


def _capture_user(request: Request) -> JSONResponse:
    return JSONResponse({"user": current_user.get()})


def _ping(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


_app = Starlette(
    routes=[Route("/capture", _capture_user), Route("/ping", _ping)],
)
_app.add_middleware(UserIdentityMiddleware)

_client = TestClient(_app)


def test_sets_contextvar_from_header():
    response = _client.get("/capture", headers={"X-Marivo-User": "alice"})
    assert response.status_code == 200
    assert response.json()["user"] == "alice"


def test_empty_header_treated_as_none():
    response = _client.get("/capture", headers={"X-Marivo-User": ""})
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_whitespace_header_treated_as_none():
    response = _client.get("/capture", headers={"X-Marivo-User": "   "})
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_no_header_no_error():
    response = _client.get("/capture")
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_header_value_stripped():
    response = _client.get("/capture", headers={"X-Marivo-User": "  alice  "})
    assert response.status_code == 200
    assert response.json()["user"] == "alice"


def test_contextvar_reset_after_request():
    token = current_user.set(None)
    try:
        _client.get("/ping", headers={"X-Marivo-User": "bob"})
        assert current_user.get() is None
    finally:
        current_user.reset(token)


def test_is_not_base_http_middleware():
    """UserIdentityMiddleware must be pure ASGI, not BaseHTTPMiddleware.

    BaseHTTPMiddleware buffers the full response body, which breaks
    SSE streaming (e.g. FastMCP streamable-http transport).
    """
    assert not issubclass(UserIdentityMiddleware, BaseHTTPMiddleware)


@pytest.mark.asyncio
async def test_sse_streaming_not_buffered():
    """Response chunks pass through immediately, not buffered by middleware."""
    chunks_sent: list[str] = []

    async def sse_app(scope: object, receive: object, send: object) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [[b"content-type", b"text/event-stream"]],
            }
        )
        await send({"type": "http.response.body", "body": b"data: chunk1\n\n", "more_body": True})
        chunks_sent.append("chunk1")
        await send({"type": "http.response.body", "body": b"data: chunk2\n\n", "more_body": False})
        chunks_sent.append("chunk2")

    received: list[dict] = []

    async def capture_send(message: dict) -> None:
        received.append(message)

    wrapped = UserIdentityMiddleware(sse_app)
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "asgi": {"version": "3.0"},
    }

    await wrapped(scope, lambda: None, capture_send)

    body_messages = [m for m in received if m["type"] == "http.response.body"]
    assert len(body_messages) == 2, f"Expected 2 body chunks, got {len(body_messages)}"
    assert body_messages[0]["body"] == b"data: chunk1\n\n"
    assert body_messages[0].get("more_body", False) is True
    assert body_messages[1]["body"] == b"data: chunk2\n\n"
    # Both chunks were produced by the downstream app
    assert chunks_sent == ["chunk1", "chunk2"]
