from __future__ import annotations

import os
from contextvars import ContextVar, Token

_UNSET = object()
current_user: ContextVar[str | None] = ContextVar("current_user", default=None)
# When set to _UNSET sentinel, resolve_user() skips env var fallback.
_user_explicitly_set: ContextVar[bool] = ContextVar("_user_explicitly_set", default=False)


def resolve_user() -> str | None:
    user = current_user.get()
    if user is not None:
        user = user.strip()
        if user:
            return user
    # If the ContextVar was explicitly set (e.g. by HTTP middleware), skip env fallback.
    if _user_explicitly_set.get():
        return None
    env_user = os.environ.get("MARIVO_DEFAULT_USER", "").strip()
    if env_user:
        return env_user
    return None


def set_current_user(user: str | None) -> tuple[Token[str | None], Token[bool]]:
    """Set current_user and mark it as explicitly set. Returns a reset token pair."""
    t1 = current_user.set(user)
    t2 = _user_explicitly_set.set(True)
    return (t1, t2)


def reset_current_user(tokens: tuple[Token[str | None], Token[bool]]) -> None:
    t1, t2 = tokens
    _user_explicitly_set.reset(t2)
    current_user.reset(t1)
