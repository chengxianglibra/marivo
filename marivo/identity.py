from __future__ import annotations

from contextvars import ContextVar, Token

current_user: ContextVar[str | None] = ContextVar("current_user", default=None)


def resolve_user() -> str | None:
    user = current_user.get()
    if user is not None:
        user = user.strip()
        if user:
            return user
    return None


def require_user() -> str:
    """Return the current user or raise if not set."""
    user = resolve_user()
    if user is None:
        raise RuntimeError(
            "User identity not set — transport layer must set user before service calls"
        )
    return user


def set_current_user(user: str | None) -> Token[str | None]:
    """Set current_user. Returns a reset token."""
    return current_user.set(user)


def reset_current_user(token: Token[str | None]) -> None:
    current_user.reset(token)
