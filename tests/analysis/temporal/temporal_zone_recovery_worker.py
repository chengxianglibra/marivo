"""Recover native DATE/timestamp grid reductions without source or Semantic."""

import os
import sys
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import duckdb
import ibis
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.time_grid import BoundTimeGrid
from marivo.analysis.materialization import graph_local_execution
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import Json, checked, encode, read


def run(root: Path, phase: Literal["fixed", "cold"]) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-temporal-zone.json")
    identity, reference, civil_date = state["session"], state["source"], state["civil_date"]
    assert isinstance(identity, str) and isinstance(reference, str) and isinstance(civil_date, bool)
    assert not (root / "models").exists() and not (root / "warehouse.duckdb").exists()
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(identity, by="id")
        value = session.artifact(reference)
        assert isinstance(value, mv.MaterializedNumericRelation)
        assert snapshot(value) == state["original"]
        retained_grid = value._node.root.signature.domain.time_grid
        assert retained_grid is not None
        assert retained_grid.report_timezone == state["report_zone"]
        assert retained_grid.boundary_timezone == (
            "America/New_York" if civil_date else "Asia/Tokyo"
        )
        assert (
            checked(TypeAdapter(BoundTimeGrid).dump_python(retained_grid, mode="json"))
            == state["grid"]
        )
        before = run_ids(session)
        operation = value.group_by(mv.grain("day")).rollup()
        if phase == "cold":
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                result = operation.execute()
        else:
            result = operation.execute()
        assert result.to_pandas().value.tolist() == [10, 20]
        output = snapshot(result)
        current = run_ids(session)
        with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
            assert snapshot(operation.execute()) == output
        assert run_ids(session) == current
        assert snapshot(value) == state["original"]
        assert session._runtime.store.resources(session.id) == ()
        if phase == "fixed":
            assert len(current - before) == kernels.call_count == 1
            state["output"] = output
            (root / "r94-temporal-zone.json").write_bytes(encode(state))
        else:
            assert output == state["output"] and current == before and kernels.call_count == 0
        return {
            "phase": phase,
            "pid": os.getpid(),
            "output": output,
            "new_runs": len(current - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "source_and_semantic_forbidden": True,
            "original_snapshot_preserved": True,
            "grid": checked(TypeAdapter(BoundTimeGrid).dump_python(retained_grid, mode="json")),
        }


if __name__ == "__main__":
    raw_phase = sys.argv[2]
    assert raw_phase in ("fixed", "cold")
    phase: Literal["fixed", "cold"] = "fixed" if raw_phase == "fixed" else "cold"
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), phase)))
