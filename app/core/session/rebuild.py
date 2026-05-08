from __future__ import annotations

from typing import Any

from app.contracts.session import SessionEvent, SessionState


def rebuild_session_state(events: list[SessionEvent]) -> SessionState:
    """Pure function: reconstruct SessionState from event log.

    Handles:
    - Session status transitions (created -> active -> terminated)
    - updated_at = timestamp of last event
    - owner_user derived from session_created event's actor
    - constraints and budget from session_created event payload
    """
    if not events:
        raise ValueError("Cannot rebuild state from empty event list")

    first = events[0]
    session_id = first.session_id
    created_at = first.timestamp
    goal: str | None = None
    status = "active"
    updated_at = first.timestamp
    owner_user = None
    constraints: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    terminal_reason: str | None = None
    ended_at: str | None = None

    for event in events:
        if event.event_type == "session_created":
            goal = event.payload.get("goal")
            status = "active"
            owner_user = event.actor
            constraints = event.payload.get("constraints")
            budget = event.payload.get("budget")
        elif event.event_type == "session_terminated":
            status = "terminated"
            terminal_reason = event.payload.get("terminal_reason")
            ended_at = event.timestamp
        updated_at = event.timestamp

    return SessionState(
        session_id=session_id,
        status=status,
        goal=goal,
        owner_user=owner_user,
        constraints=constraints,
        budget=budget,
        terminal_reason=terminal_reason,
        ended_at=ended_at,
        created_at=created_at,
        updated_at=updated_at,
    )
