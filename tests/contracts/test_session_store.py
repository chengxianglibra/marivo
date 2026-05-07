from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.local.sqlite_session_store import SqliteSessionStore
from app.contracts.ids import SessionId, UserId
from app.contracts.session import SessionEvent


def _make_sqlite_session_store(tmp_path: Path) -> SqliteSessionStore:
    db_path = tmp_path / "state.db"
    return SqliteSessionStore(db_path)


session_store_factories = [
    ("SqliteSessionStore", _make_sqlite_session_store),
]


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_append_and_load_events(name, factory, tmp_path):
    store = factory(tmp_path)
    session_id = SessionId("sess-001")
    event = SessionEvent(
        session_id=session_id,
        event_type="session_created",
        timestamp="2026-05-07T10:00:00Z",
        payload={"goal": "test"},
        actor=None,
    )
    store.append_event(session_id, event)
    events = store.load_events(session_id)
    assert len(events) == 1
    assert events[0].event_type == "session_created"
    assert events[0].session_id == session_id


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_load_events_returns_empty_for_unknown_session(name, factory, tmp_path):
    store = factory(tmp_path)
    events = store.load_events(SessionId("nonexistent"))
    assert events == []


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_multiple_events_ordered_by_seq(name, factory, tmp_path):
    store = factory(tmp_path)
    session_id = SessionId("sess-002")
    for i in range(5):
        store.append_event(
            session_id,
            SessionEvent(
                session_id=session_id,
                event_type=f"event_{i}",
                timestamp=f"2026-05-07T10:00:0{i}Z",
                payload={"index": i},
                actor=None,
            ),
        )
    events = store.load_events(session_id)
    assert len(events) == 5
    for i, event in enumerate(events):
        assert event.event_type == f"event_{i}"


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_separate_sessions_isolated(name, factory, tmp_path):
    store = factory(tmp_path)
    s1 = SessionId("sess-a")
    s2 = SessionId("sess-b")
    store.append_event(
        s1,
        SessionEvent(
            session_id=s1, event_type="e1", timestamp="2026-01-01T00:00:00Z", payload={}, actor=None
        ),
    )
    store.append_event(
        s2,
        SessionEvent(
            session_id=s2, event_type="e2", timestamp="2026-01-01T00:00:00Z", payload={}, actor=None
        ),
    )
    assert len(store.load_events(s1)) == 1
    assert len(store.load_events(s2)) == 1
    assert store.load_events(s1)[0].event_type == "e1"


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_actor_preserved(name, factory, tmp_path):
    store = factory(tmp_path)
    session_id = SessionId("sess-003")
    event = SessionEvent(
        session_id=session_id,
        event_type="step_inserted",
        timestamp="2026-05-07T10:00:00Z",
        payload={},
        actor=UserId("test_user"),
    )
    store.append_event(session_id, event)
    loaded = store.load_events(session_id)
    assert loaded[0].actor == "test_user"
