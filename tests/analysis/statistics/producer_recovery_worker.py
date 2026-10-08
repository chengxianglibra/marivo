"""Bind one physical statistical producer to public fixed and cold recovery."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode, obj, read


def run(root: Path, phase: str) -> dict[str, Json]:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state_path = root / "statistic-producer.json"
    state = read(state_path)
    identity = state["session"]
    original = obj(state["original"])
    reference = original["artifact"]
    assert isinstance(identity, str) and isinstance(reference, str)
    with patch.object(ms, "load", forbidden), patch.object(SourceSession, "__init__", forbidden):
        session = mv.session.resume(identity, by="id")
        value = session.artifact(reference)
        assert isinstance(value, _MaterializedRead) and snapshot(value) == original
        numeric: mv.MaterializedNumericRelation | mv.MaterializedCoefficientRelation
        if isinstance(value, mv.MaterializedDeviationResult):
            numeric = value.score
        elif isinstance(value, mv.MaterializedAssociationResult):
            numeric = value.coefficient
        elif isinstance(value, mv.MaterializedForecastResult):
            numeric = value.prediction
        else:
            assert isinstance(value, mv.MaterializedTimeRunResult)
            numeric = value.count
        operation = numeric.where(numeric.value.is_defined())
        before = run_ids(session)
        if phase == "cold":
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                result = operation.execute()
            assert snapshot(result) == state["fixed"] and run_ids(session) == before
        else:
            result = operation.execute()
            state["fixed"] = snapshot(result)
            state_path.write_bytes(encode(state))
            assert len(run_ids(session) - before) == 1
        with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
            assert snapshot(operation.execute()) == snapshot(result)
        assert snapshot(value) == original
        assert session._runtime.store.resources(session.id) == ()
        return {
            "pid": os.getpid(),
            "phase": phase,
            "original": original,
            "fixed": snapshot(result),
            "new_runs": len(run_ids(session) - before),
            "resources": 0,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
