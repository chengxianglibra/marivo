from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.contracts.errors import ErrorCode, NotFoundError
from app.contracts.ids import SessionId, UserId
from app.contracts.session import SessionEvent
from tests.contracts.contract_cases import ContractCase


def _run_append_and_load(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-1")
    adapter.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "test"},
            actor=None,
        ),
    )
    events = adapter.load_events(sid)
    assert len(events) == 1
    assert events[0].event_type == "session_created"


def _run_not_found(adapter, tmp_path: Path) -> None:
    with pytest.raises(NotFoundError) as exc_info:
        adapter.load_events(SessionId("nonexistent"))
    assert exc_info.value.code == ErrorCode.SESSION_NOT_FOUND


def _run_owner_isolation(adapter, tmp_path: Path) -> None:
    adapter.append_event(
        SessionId("s-a"),
        SessionEvent(
            session_id=SessionId("s-a"),
            event_type="session_created",
            timestamp="2026-05-07T10:00:01Z",
            payload={"goal": "g1"},
            actor=UserId("alice"),
        ),
    )
    adapter.append_event(
        SessionId("s-b"),
        SessionEvent(
            session_id=SessionId("s-b"),
            event_type="session_created",
            timestamp="2026-05-07T10:00:02Z",
            payload={"goal": "g2"},
            actor=UserId("bob"),
        ),
    )
    alice = adapter.list_sessions(UserId("alice"))
    assert len(alice) == 1
    assert alice[0].session_id == "s-a"


def _run_event_ordering(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-ord")
    adapter.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={},
            actor=None,
        ),
    )
    adapter.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="step_completed",
            timestamp="2026-05-07T10:01:00Z",
            payload={"step": "s1"},
            actor=None,
        ),
    )
    events = adapter.load_events(sid)
    assert [e.event_type for e in events] == ["session_created", "step_completed"]


def _run_other_event_types(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-other")
    adapter.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "g"},
            actor=None,
        ),
    )
    adapter.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="step_completed",
            timestamp="2026-05-07T10:01:00Z",
            payload={"step": "s1"},
            actor=None,
        ),
    )
    events = adapter.load_events(sid)
    assert len(events) == 2
    assert events[1].event_type == "step_completed"


def _run_concurrent_retry(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-concurrent")
    adapter.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "concurrent"},
            actor=None,
        ),
    )
    results: dict[str, str | None] = {"t1": None, "t2": None}

    def append_event(thread_id: str) -> None:
        try:
            adapter.append_event(
                sid,
                SessionEvent(
                    session_id=sid,
                    event_type="step_completed",
                    timestamp="2026-05-07T10:00:01Z",
                    payload={"thread": thread_id},
                    actor=None,
                ),
            )
            results[thread_id] = "ok"
        except Exception:
            results[thread_id] = "failed"

    t1 = threading.Thread(target=append_event, args=("t1",))
    t2 = threading.Thread(target=append_event, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    events = adapter.load_events(sid)
    assert len(events) == 3


def _run_step_completed_guarantee(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-step")
    adapter.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "step test"},
            actor=None,
        ),
    )
    adapter.append_event(
        sid,
        SessionEvent(
            session_id=sid,
            event_type="step_completed",
            timestamp="2026-05-07T10:01:00Z",
            payload={"step_id": "step-1"},
            actor=None,
        ),
    )
    events = adapter.load_events(sid)
    step_events = [e for e in events if e.event_type == "step_completed"]
    assert len(step_events) == 1


SESSION_STORE_CASES: list[ContractCase] = [
    ContractCase(name="append_and_load", run=_run_append_and_load),
    ContractCase(name="not_found", run=_run_not_found),
    ContractCase(name="owner_isolation", run=_run_owner_isolation),
    ContractCase(name="event_ordering", run=_run_event_ordering),
    ContractCase(name="other_event_types", run=_run_other_event_types),
    ContractCase(name="concurrent_retry", run=_run_concurrent_retry),
    ContractCase(name="step_completed_guarantee", run=_run_step_completed_guarantee),
]
