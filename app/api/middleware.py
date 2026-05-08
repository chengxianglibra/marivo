from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from app.identity import current_user


class UserIdentityMiddleware:
    """Pure-ASGI middleware that sets current_user from X-Marivo-User.

    Unlike BaseHTTPMiddleware, this does not buffer the response body,
    so it is compatible with SSE streaming (e.g. FastMCP streamable-http).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw = headers.get(b"x-marivo-user")
        user: str | None = None
        if raw is not None:
            decoded = raw.decode("latin-1").strip()
            if decoded:
                user = decoded

        token = current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(token)
