from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .ids import SessionId, UserId


class SessionEvent(BaseModel):
    """Append-only event in the session event log.

    Owner invariant: a session's owner is the actor of its `session_created`
    event.
    """

    session_id: SessionId
    event_type: str
    timestamp: str  # ISO-8601
    payload: dict[str, Any] = {}
    actor: UserId | None = None


class SessionState(BaseModel):
    """Derived view of session state, rebuilt from events."""

    session_id: SessionId
    status: str
    goal: str | None = None
    created_at: str
    updated_at: str
