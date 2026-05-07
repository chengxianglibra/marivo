from __future__ import annotations

from app.contracts.session import SessionEvent, SessionState


def rebuild_session_state(events: list[SessionEvent]) -> SessionState:
    """Pure function: reconstruct SessionState from event log.

    Handles:
    - Session status transitions (created -> active -> terminated)
    - updated_at = timestamp of last event
    """
    if not events:
        raise ValueError("Cannot rebuild state from empty event list")

    first = events[0]
    session_id = first.session_id
    created_at = first.timestamp
    goal: str | None = None
    status = "active"
    updated_at = first.timestamp

    for event in events:
        if event.event_type == "session_created":
            goal = event.payload.get("goal")
            status = "active"
        elif event.event_type == "session_terminated":
            status = "terminated"
        updated_at = event.timestamp

    return SessionState(
        session_id=session_id,
        status=status,
        goal=goal,
        created_at=created_at,
        updated_at=updated_at,
    )
