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
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode, obj, read


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "period-alignment.json")
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
        for view in (restored.absolute, restored.relative):
            assert set(view.to_pandas().coord_0) == {1, 2, 3}
        assert run_ids(session) == before
        logical = values["current"].compare(values["baseline"], align=mv.outer(time=mv.ordinal()))
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
        assert session._runtime.store.resources(session.id) == ()
        return {"phase": phase, "pid": os.getpid(), "result": snapshot(result)}


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
