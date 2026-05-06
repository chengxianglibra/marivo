from __future__ import annotations

import os
from contextvars import ContextVar

current_user: ContextVar[str | None] = ContextVar("current_user", default=None)


def resolve_user() -> str | None:
    """Return the current user, falling back to MARIVO_DEFAULT_USER env var.

    Normalizes empty/whitespace-only strings to None so downstream code
    only needs to check for None, not None + empty string.
    """
    user = current_user.get()
    if user is not None:
        user = user.strip()
        if user:
            return user
    env_user = os.environ.get("MARIVO_DEFAULT_USER")
    if env_user:
        env_user = env_user.strip()
        if env_user:
            return env_user
    return None
