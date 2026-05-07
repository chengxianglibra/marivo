from __future__ import annotations

from typing import Any

import pytest

from app.contracts.ids import SessionId
from app.contracts.session import SessionEvent
from app.core.session.rebuild import rebuild_session_state


def _event(
    session_id: str, event_type: str, ts: str, payload: dict[str, Any] | None = None
) -> SessionEvent:
    return SessionEvent(
        session_id=SessionId(session_id),
        event_type=event_type,
        timestamp=ts,
        payload=payload or {},
        actor=None,
    )


def test_empty_events_raises() -> None:
    with pytest.raises(ValueError, match="empty event"):
        rebuild_session_state([])


def test_session_created() -> None:
    events = [_event("s1", "session_created", "2026-01-01T00:00:00Z", {"goal": "test"})]
    state = rebuild_session_state(events)
    assert state.session_id == "s1"
    assert state.status == "active"
    assert state.goal == "test"
    assert state.created_at == "2026-01-01T00:00:00Z"


def test_session_terminated() -> None:
    events = [
        _event("s1", "session_created", "2026-01-01T00:00:00Z"),
        _event("s1", "session_terminated", "2026-01-02T00:00:00Z"),
    ]
    state = rebuild_session_state(events)
    assert state.status == "terminated"
    assert state.updated_at == "2026-01-02T00:00:00Z"


def test_updated_at_is_last_event_timestamp() -> None:
    events = [
        _event("s1", "session_created", "2026-01-01T00:00:00Z"),
        _event("s1", "step_inserted", "2026-01-01T01:00:00Z"),
        _event("s1", "step_inserted", "2026-01-01T02:00:00Z"),
    ]
    state = rebuild_session_state(events)
    assert state.updated_at == "2026-01-01T02:00:00Z"
