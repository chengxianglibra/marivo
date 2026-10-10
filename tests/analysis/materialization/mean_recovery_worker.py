"""Source-free current-row mean and original weighted mean recovery."""

import os
import sys
from fractions import Fraction
from pathlib import Path
from typing import Protocol
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
from tests.support.json import Json, arr, encode, obj, read


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def snapshot_rows(saved: dict[str, Json]) -> list[Json]:
    return arr(obj(saved["rows"])["data"])


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-mean.json")
    identity, reference = state["session"], state["source"]
    assert isinstance(identity, str) and isinstance(reference, str)
    assert not (root / "models").exists()
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
        assert isinstance(value, mv.MaterializedGroupedNumericRelation)
        original = snapshot(value)
        assert original == state["original"]
        assert {
            str(obj(row)["group"]): float(str(obj(row)["value"])) for row in snapshot_rows(original)
        } == {"a": 1.0, "b": 100.0}
        assert value._dataset is not None
        rows = next(
            part.table.to_pylist()
            for part in value._dataset.verified().parts
            if part.role == "original_state"
        )
        assert {
            row["key_0"]: (row["original_state__sum"], row["original_state__non_null_count"])
            for row in rows
        } == {"a": (2, 2), "b": (100, 1)}
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        operations: dict[str, Continuation] = {
            "current": value.group_by().aggregate(mv.mean()),
            "original": value.rollup(),
        }
        for name, operation in operations.items():
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            saved = snapshot(result)
            rows = snapshot_rows(saved)
            expected = Fraction(101, 2) if name == "current" else Fraction(34)
            assert len(rows) == 1
            actual = float(str(obj(rows[0])["value"]))
            assert abs(actual - float(expected)) < 1e-12
            outputs[name] = saved
        assert snapshot(value) == state["original"]
        assert session._runtime.store.resources(session.id) == ()
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == 2
            state["outputs"] = outputs
            (root / "r94-mean.json").write_bytes(encode(state))
        else:
            assert (
                outputs == state["outputs"]
                and run_ids(session) == before
                and kernels.call_count == 0
            )
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "original_snapshot_preserved": True,
            "source_and_semantic_forbidden": True,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
