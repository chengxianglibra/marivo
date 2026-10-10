"""Check fixed and cold continuations for sibling source statistics."""

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
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode, obj, read


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("fixed", "cold")
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state_path = root / "statistic-producer.json"
    state = read(state_path)
    raw_artifacts = state["artifacts"]
    assert isinstance(raw_artifacts, list) and raw_artifacts
    summaries: list[Json] = []
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
    ):
        for raw in raw_artifacts:
            artifact = obj(raw)
            method = artifact["method"]
            identity = artifact["session"]
            original = obj(artifact["original"])
            reference = original["artifact"]
            assert isinstance(method, str)
            assert isinstance(identity, str) and isinstance(reference, str)
            session = mv.session.resume(identity, by="id")
            value = session.artifact(reference)
            assert isinstance(value, _MaterializedRead) and snapshot(value) == original
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
                fixed = obj(artifact["fixed"])
                assert snapshot(result) == fixed and run_ids(session) == before
            else:
                result = operation.execute()
                artifact["fixed"] = snapshot(result)
                assert len(run_ids(session) - before) == 1
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(operation.execute()) == snapshot(result)
            assert snapshot(value) == original
            assert session._runtime.store.resources(session.id) == ()
            summaries.append(
                {
                    "method": method,
                    "original": original,
                    "fixed": snapshot(result),
                    "new_runs": len(run_ids(session) - before),
                    "resources": 0,
                }
            )
    if phase == "fixed":
        state_path.write_bytes(encode(state))
    return {"pid": os.getpid(), "phase": phase, "artifacts": summaries}


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
