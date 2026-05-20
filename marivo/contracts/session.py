from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .ids import SessionId, StepId, UserId


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
    owner_user: UserId | None = None
    constraints: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    terminal_reason: str | None = None
    ended_at: str | None = None
    created_at: str
    updated_at: str


class Step(BaseModel):
    step_id: StepId
    session_id: SessionId
    step_type: str
    summary: str
    result: dict[str, Any]
    provenance: dict[str, Any] | None = None
    semantic_metadata: dict[str, Any] | None = None
    reasoning: str | None = None
    sql_texts: list[dict[str, str | float]] | None = None
    created_at: str
