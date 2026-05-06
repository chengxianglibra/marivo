from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.identity import current_user


class UserIdentityMiddleware(BaseHTTPMiddleware):
    """Extract X-Marivo-User header and set it on the current_user ContextVar.

    Empty or whitespace-only values are normalized to None so downstream code
    only needs to check for None, not None + empty string.
    The ContextVar is always reset to its previous value after the request.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        user = request.headers.get("x-marivo-user")
        if user is not None:
            user = user.strip()
            if not user:
                user = None
        token = current_user.set(user)
        try:
            return await call_next(request)
        finally:
            current_user.reset(token)
