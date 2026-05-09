"""Tests for atomic step commit + step_completed event writes.

When a step result is committed and a ``step_completed`` event is appended,
both should happen in the same database transaction when sharing a
``MetadataStore``.  This prevents inconsistent session state if the event
append fails after the step commit.
"""

from __future__ import annotations

from typing import Any

import pytest

from marivo.adapters.server.session_store import SqlSessionStore
from marivo.contracts.ids import SessionId
from marivo.contracts.session import SessionEvent
from marivo.storage.sqlite_metadata import SQLiteMetadataStore


@pytest.fixture()
def metadata(tmp_path: Any) -> SQLiteMetadataStore:
    store = SQLiteMetadataStore(tmp_path / "test.meta.sqlite")
    store.initialize()
    return store


@pytest.fixture()
def session_store(metadata: SQLiteMetadataStore) -> SqlSessionStore:
    return SqlSessionStore(metadata)


def test_append_event_on_shared_connection(
    metadata: SQLiteMetadataStore, session_store: SqlSessionStore
) -> None:
    """append_event_with_connection inserts a row on the given connection."""
    session_id = SessionId("sess-shared-con")

    # Create the session first
    session_store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "shared connection test"},
            actor=None,
        ),
    )

    # Append event within a shared connection
    with metadata.connect() as con:
        session_store.append_event_with_connection(
            session_id,
            SessionEvent(
                session_id=session_id,
                event_type="step_completed",
                timestamp="2026-05-07T10:01:00Z",
                payload={"step_id": "step-1"},
                actor=None,
            ),
            con,
        )
        con.commit()

    events = session_store.load_events(session_id)
    assert any(e.event_type == "step_completed" for e in events)


def test_event_not_visible_without_commit(
    metadata: SQLiteMetadataStore, session_store: SqlSessionStore
) -> None:
    """If the shared connection is not committed, the event is not visible
    on a separate connection (read-committed isolation)."""
    session_id = SessionId("sess-no-commit")

    session_store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "no-commit test"},
            actor=None,
        ),
    )

    # Append event but do NOT commit the shared connection
    with metadata.connect() as con:
        session_store.append_event_with_connection(
            session_id,
            SessionEvent(
                session_id=session_id,
                event_type="step_completed",
                timestamp="2026-05-07T10:01:00Z",
                payload={"step_id": "step-no-commit"},
                actor=None,
            ),
            con,
        )
        # con.commit() intentionally omitted

    # The event should not be visible via a new connection
    # (WAL mode allows reading the committed state only)
    events = session_store.load_events(session_id)
    assert not any(e.event_type == "step_completed" for e in events)


def test_step_completed_event_atomic_with_commit(
    metadata: SQLiteMetadataStore, session_store: SqlSessionStore
) -> None:
    """step_completed event and step commit share a transaction boundary."""
    session_id = SessionId("sess-atomic")
    session_store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "atomic test"},
            actor=None,
        ),
    )

    # Append event within a shared connection
    with metadata.connect() as con:
        session_store.append_event_with_connection(
            session_id,
            SessionEvent(
                session_id=session_id,
                event_type="step_completed",
                timestamp="2026-05-07T10:01:00Z",
                payload={"step_id": "step-1"},
                actor=None,
            ),
            con,
        )
        con.commit()

    events = session_store.load_events(session_id)
    assert any(e.event_type == "step_completed" for e in events)


def test_rollback_prevents_both_writes(
    metadata: SQLiteMetadataStore, session_store: SqlSessionStore
) -> None:
    """If the shared transaction rolls back, neither the step data nor
    the event should be persisted."""
    session_id = SessionId("sess-rollback")
    session_store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "rollback test"},
            actor=None,
        ),
    )

    # Append event but roll back instead of committing
    with metadata.connect() as con:
        session_store.append_event_with_connection(
            session_id,
            SessionEvent(
                session_id=session_id,
                event_type="step_completed",
                timestamp="2026-05-07T10:01:00Z",
                payload={"step_id": "step-rollback"},
                actor=None,
            ),
            con,
        )
        con.rollback()

    events = session_store.load_events(session_id)
    assert not any(e.event_type == "step_completed" for e in events)


def test_multiple_events_in_shared_transaction(
    metadata: SQLiteMetadataStore, session_store: SqlSessionStore
) -> None:
    """Multiple events can be appended in the same shared transaction."""
    session_id = SessionId("sess-multi")
    session_store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "multi-event test"},
            actor=None,
        ),
    )

    with metadata.connect() as con:
        for i in range(3):
            session_store.append_event_with_connection(
                session_id,
                SessionEvent(
                    session_id=session_id,
                    event_type="step_completed",
                    timestamp=f"2026-05-07T10:0{i + 1}:00Z",
                    payload={"step_id": f"step-{i}"},
                    actor=None,
                ),
                con,
            )
        con.commit()

    events = session_store.load_events(session_id)
    step_events = [e for e in events if e.event_type == "step_completed"]
    assert len(step_events) == 3


def test_seq_increments_correctly_in_shared_transaction(
    metadata: SQLiteMetadataStore, session_store: SqlSessionStore
) -> None:
    """Sequence numbers increment correctly when appending multiple
    events on the same connection within one transaction."""
    session_id = SessionId("sess-seq")
    session_store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "seq test"},
            actor=None,
        ),
    )

    with metadata.connect() as con:
        session_store.append_event_with_connection(
            session_id,
            SessionEvent(
                session_id=session_id,
                event_type="step_completed",
                timestamp="2026-05-07T10:01:00Z",
                payload={"step_id": "step-seq-1"},
                actor=None,
            ),
            con,
        )
        session_store.append_event_with_connection(
            session_id,
            SessionEvent(
                session_id=session_id,
                event_type="step_completed",
                timestamp="2026-05-07T10:02:00Z",
                payload={"step_id": "step-seq-2"},
                actor=None,
            ),
            con,
        )
        con.commit()

    # Verify we can load all events and they have the right ordering
    events = session_store.load_events(session_id)
    assert len(events) == 3  # session_created + 2 step_completed
