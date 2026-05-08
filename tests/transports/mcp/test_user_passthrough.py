"""X-Marivo-User passthrough test for MCP middleware."""

from __future__ import annotations

import pytest

from app.api.middleware import UserIdentityMiddleware
from app.identity import current_user


async def _run_through_middleware(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Run a request through UserIdentityMiddleware and return current_user value."""
    captured_user: str | None = None

    async def capture_app(scope, receive, send):
        nonlocal captured_user
        captured_user = current_user.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    wrapped = UserIdentityMiddleware(capture_app)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers,
        "query_string": b"",
        "server": ("test", 80),
        "asgi": {"version": "3.0"},
    }

    async def _receive():
        return {"type": "http.request", "body": b""}

    sent_messages: list[dict] = []

    async def _send(message: dict) -> None:
        sent_messages.append(message)

    await wrapped(scope, _receive, _send)
    return captured_user


@pytest.mark.asyncio
async def test_user_header_passthrough():
    """X-Marivo-User: alice → current_user is alice."""
    headers = [(b"x-marivo-user", b"alice")]
    user = await _run_through_middleware(headers)
    assert user == "alice"


@pytest.mark.asyncio
async def test_no_user_header():
    """No X-Marivo-User header → current_user is None."""
    headers = []
    user = await _run_through_middleware(headers)
    assert user is None


@pytest.mark.asyncio
async def test_whitespace_only_user_header():
    """X-Marivo-User: '   ' → current_user is None."""
    headers = [(b"x-marivo-user", b"   ")]
    user = await _run_through_middleware(headers)
    assert user is None
