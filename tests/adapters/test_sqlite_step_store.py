from __future__ import annotations

from pathlib import Path

import pytest

from marivo.adapters.local.sqlite_step_store import SqliteStepStore
from marivo.contracts.ids import SessionId, StepId


def _make_sqlite_step_store(tmp_path: Path) -> SqliteStepStore:
    return SqliteStepStore(tmp_path / "state.db")


step_store_factories = [
    ("SqliteStepStore", _make_sqlite_step_store),
]


@pytest.mark.parametrize("name,factory", step_store_factories)
def test_insert_then_list(name, factory, tmp_path):
    store = factory(tmp_path)
    sid = SessionId("s-1")
    store.insert_step(
        step_id=StepId("step-a"),
        session_id=sid,
        step_type="observe",
        summary="hello",
        result={"value": 1},
    )
    store.insert_step(
        step_id=StepId("step-b"),
        session_id=sid,
        step_type="compare",
        summary="world",
        result={"value": 2},
    )
    steps = store.list_steps(sid)
    assert [s.step_id for s in steps] == ["step-a", "step-b"]
    assert steps[0].step_type == "observe"
    assert steps[1].result == {"value": 2}


@pytest.mark.parametrize("name,factory", step_store_factories)
def test_list_empty_for_unknown_session(name, factory, tmp_path):
    store = factory(tmp_path)
    assert store.list_steps(SessionId("nope")) == []


@pytest.mark.parametrize("name,factory", step_store_factories)
def test_list_filters_by_session(name, factory, tmp_path):
    store = factory(tmp_path)
    store.insert_step(StepId("a"), SessionId("s-1"), "observe", "x", {})
    store.insert_step(StepId("b"), SessionId("s-2"), "observe", "y", {})
    assert [s.step_id for s in store.list_steps(SessionId("s-1"))] == ["a"]


@pytest.mark.parametrize("name,factory", step_store_factories)
def test_optional_fields_round_trip(name, factory, tmp_path):
    store = factory(tmp_path)
    store.insert_step(
        StepId("step-x"),
        SessionId("s-1"),
        "observe",
        "with opts",
        {"v": 1},
        provenance={"source": "test"},
        semantic_metadata={"tags": ["a"]},
    )
    store.insert_step(
        StepId("step-y"),
        SessionId("s-1"),
        "observe",
        "without opts",
        {"v": 2},
    )
    steps = store.list_steps(SessionId("s-1"))
    assert steps[0].provenance == {"source": "test"}
    assert steps[0].semantic_metadata == {"tags": ["a"]}
    assert steps[1].provenance is None
    assert steps[1].semantic_metadata is None
