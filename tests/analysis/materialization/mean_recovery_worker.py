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
from tests.support.json import Json, encode, read


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


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
        assert snapshot(value) == state["original"]
        assert value.to_pandas().set_index("group").value.to_dict() == {"a": 1.0, "b": 100.0}
        assert value._dataset is not None
        rows = next(
            part.table.to_pylist()
            for part in value._dataset.verified().parts
            if part.role == "original_state"
        )
        assert {
            row["key_0"]: (row["original_state__sum"], row["original_state__non_null_count"])
            for row in rows
        } == {"a": (100, 100), "b": (100, 1)}
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        operations: dict[str, Continuation] = {
            "current": value.group_by().summarize(mv.mean()),
            "original": value.rollup(),
        }
        for name, operation in operations.items():
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            frame = result.to_pandas()
            expected = Fraction(101, 2) if name == "current" else Fraction(200, 101)
            assert len(frame) == 1 and abs(float(frame.value.iloc[0]) - float(expected)) < 1e-12
            outputs[name] = snapshot(result)
            current = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(operation.execute()) == outputs[name]
            assert run_ids(session) == current
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
