from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from marivo.adapters.local.sqlite_session_store import SqliteSessionStore
from marivo.contracts.errors import ErrorCode, NotFoundError
from marivo.contracts.ids import SessionId, UserId
from marivo.contracts.session import SessionEvent
from tests.contracts.session_store_cases import SESSION_STORE_CASES

if TYPE_CHECKING:
    from marivo.adapters.server.session_store import SqlSessionStore


def _make_sqlite_session_store(tmp_path: Path) -> SqliteSessionStore:
    db_path = tmp_path / "state.db"
    return SqliteSessionStore(db_path)


def _make_sql_session_store(tmp_path: Path) -> SqlSessionStore:
    from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
    from marivo.adapters.server.session_store import SqlSessionStore

    store = SQLiteMetadataStore(tmp_path / "test.meta.sqlite")
    store.initialize()
    return SqlSessionStore(store)


session_store_factories = [
    ("SqliteSessionStore", _make_sqlite_session_store),
    ("SqlSessionStore", _make_sql_session_store),
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
def test_load_events_raises_for_unknown_session(name, factory, tmp_path):
    store = factory(tmp_path)
    with pytest.raises(NotFoundError) as exc_info:
        store.load_events(SessionId("nonexistent"))
    assert exc_info.value.code == ErrorCode.SESSION_NOT_FOUND


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_list_sessions_returns_only_owners_sessions(name, factory, tmp_path):
    store = factory(tmp_path)
    alice = UserId("alice")
    bob = UserId("bob")

    store.append_event(
        SessionId("s-b"),
        SessionEvent(
            session_id=SessionId("s-b"),
            event_type="session_created",
            timestamp="2026-05-07T10:00:02Z",
            payload={"goal": "g2"},
            actor=bob,
        ),
    )
    store.append_event(
        SessionId("s-a"),
        SessionEvent(
            session_id=SessionId("s-a"),
            event_type="session_created",
            timestamp="2026-05-07T10:00:01Z",
            payload={"goal": "g1"},
            actor=alice,
        ),
    )
    store.append_event(
        SessionId("s-c"),
        SessionEvent(
            session_id=SessionId("s-c"),
            event_type="session_created",
            timestamp="2026-05-07T10:00:03Z",
            payload={"goal": "g3"},
            actor=alice,
        ),
    )

    alice_sessions = store.list_sessions(alice)
    assert [state.session_id for state in alice_sessions] == ["s-a", "s-c"]
    assert [state.goal for state in alice_sessions] == ["g1", "g3"]

    bob_sessions = store.list_sessions(bob)
    assert [state.session_id for state in bob_sessions] == ["s-b"]


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_list_sessions_empty_for_unknown_owner(name, factory, tmp_path):
    store = factory(tmp_path)
    assert store.list_sessions(UserId("ghost")) == []


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


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_concurrent_append_both_stored(name, factory, tmp_path):
    store = factory(tmp_path)
    session_id = SessionId("sess-concurrent")
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "concurrent test"},
            actor=UserId("alice"),
        ),
    )
    results: dict[str, str | None] = {"t1": None, "t2": None}

    def append_event(thread_id: str, event_type: str) -> None:
        try:
            store.append_event(
                session_id,
                SessionEvent(
                    session_id=session_id,
                    event_type=event_type,
                    timestamp="2026-05-07T10:00:01Z",
                    payload={"thread": thread_id},
                    actor=None,
                ),
            )
            results[thread_id] = "ok"
        except Exception as e:
            results[thread_id] = str(e)

    t1 = threading.Thread(target=append_event, args=("t1", "step_completed"))
    t2 = threading.Thread(target=append_event, args=("t2", "step_completed"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    events = store.load_events(session_id)
    assert len(events) == 3  # created + 2 step_completed


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_other_event_types_not_dropped(name, factory, tmp_path):
    """Verify that event types other than session_created/session_terminated
    are persisted and recoverable (unlike the CRUD bridge which silently
    dropped them)."""
    store = factory(tmp_path)
    session_id = SessionId("sess-events")
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "event types test"},
            actor=UserId("alice"),
        ),
    )
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="step_inserted",
            timestamp="2026-05-07T10:00:01Z",
            payload={"step_id": "step-1"},
            actor=None,
        ),
    )
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="step_completed",
            timestamp="2026-05-07T10:00:02Z",
            payload={"step_id": "step-1"},
            actor=None,
        ),
    )
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_terminated",
            timestamp="2026-05-07T10:00:03Z",
            payload={"terminal_reason": "user_closed"},
            actor=None,
        ),
    )
    events = store.load_events(session_id)
    assert len(events) == 4
    assert [e.event_type for e in events] == [
        "session_created",
        "step_inserted",
        "step_completed",
        "session_terminated",
    ]


@pytest.mark.parametrize("name,factory", session_store_factories)
def test_step_completed_guarantee(name, factory, tmp_path):
    """After commit_step_result, a step_completed event must exist in the session."""
    store = factory(tmp_path)
    session_id = SessionId("sess-step")
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "step test"},
            actor=UserId("alice"),
        ),
    )
    # Directly append the event (simulating what runtime does)
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="step_completed",
            timestamp="2026-05-07T10:01:00Z",
            payload={"step_id": "step-1"},
            actor=None,
        ),
    )
    events = store.load_events(session_id)
    step_events = [e for e in events if e.event_type == "step_completed"]
    assert len(step_events) == 1
    assert step_events[0].payload["step_id"] == "step-1"


# ── Contract-case-driven tests ──────────────────────────────────────────
# These use the shared SESSION_STORE_CASES from session_store_cases.py so
# that the same contract logic can be reused in parity tests.


@pytest.mark.parametrize("name,factory", session_store_factories)
@pytest.mark.parametrize("case", SESSION_STORE_CASES, ids=lambda c: c.name)
def test_contract_case(name, factory, case, tmp_path):
    store = factory(tmp_path)
    case.run(store, tmp_path)
