"""Independent offline period continuation and cached cold recovery."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.numeric.test_time_alignment import time_key
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode, obj, read


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "period-alignment.json")
    kind = state["kind"]
    assert kind == "ordinal" or kind == "holiday" or kind == "holiday_then_weekday"
    timestamp = state["timestamp"]
    assert isinstance(timestamp, bool)
    session_id = state["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
    ):
        session = mv.session.resume(session_id, by="id")
        values = {}
        for name, raw in obj(state["inputs"]).items():
            saved = obj(raw)
            ref = saved["artifact"]
            assert isinstance(ref, str)
            value = session.artifact(ref)
            assert isinstance(value, mv.MaterializedNumericRelation)
            assert snapshot(value) == saved
            values[name] = value
        source = obj(state["source"])
        source_ref = source["artifact"]
        assert isinstance(source_ref, str)
        restored = session.artifact(source_ref)
        assert isinstance(restored, mv.MaterializedComparisonResult)
        before = run_ids(session)
        assert snapshot(restored) == source
        restored.contract().show()
        for view in (restored.absolute, restored.relative):
            view.contract().show()
            assert set(view.to_pandas().coord_0) == (
                {"ordinal:1", "ordinal:2", "ordinal:3"}
                if kind == "ordinal"
                else {"holiday:-1", "holiday:0", "holiday:1"}
                if kind == "holiday"
                else {
                    "holiday_then_weekday:before:6:1",
                    "holiday_then_weekday:before:3:1",
                    "holiday_then_weekday:holiday:0",
                    "holiday_then_weekday:holiday:1",
                }
            )
        assert run_ids(session) == before
        logical = values["current"].compare(
            values["baseline"],
            align=mv.outer(
                time=time_key(
                    "ordinal"
                    if kind == "ordinal"
                    else "holiday"
                    if kind == "holiday"
                    else "holiday_then_weekday",
                    timestamp=timestamp,
                )
            ),
        )
        if phase == "cold":
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                result = logical.execute()
            assert snapshot(result) == obj(state["fixed"])
            assert run_ids(session) == before
        else:
            result = logical.execute()
            fixed = snapshot(result)
            assert fixed["rows"] == source["rows"]
            assert fixed["parts"] == source["parts"]
            state["fixed"] = fixed
            (root / "period-alignment.json").write_bytes(encode(state))
            selected = result.relative.where(result.relative.value.is_defined()).execute()
            assert len(selected.to_pandas()) == 0
        result.contract().show()
        result.absolute.contract().show()
        result.relative.contract().show()
        assert session._runtime.store.resources(session.id) == ()
        return {"phase": phase, "pid": os.getpid(), "result": snapshot(result)}


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
