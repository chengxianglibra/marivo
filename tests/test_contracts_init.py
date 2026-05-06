from __future__ import annotations

from app.contracts import (
    DomainError,
    ErrorCode,
    ModelId,
    SessionId,
    SessionState,
    StepId,
    TimeScopeRange,
)


def test_key_ids_importable() -> None:
    assert SessionId("s") == "s"
    assert ModelId(1) == 1
    assert StepId("step") == "step"


def test_key_value_objects_importable() -> None:
    ts = TimeScopeRange(start="2024-01-01", end="2024-02-01")
    assert ts.kind == "range"


def test_domain_types_importable() -> None:
    state = SessionState(
        session_id=SessionId("s1"),
        status="active",
        created_at="2024-01-01",
        updated_at="2024-01-01",
    )
    assert state.status == "active"


def test_errors_importable() -> None:
    err = DomainError(ErrorCode.NOT_FOUND, "missing")
    assert str(err) == "missing"
