"""Source-free temporal fold recovery preserves spatial-before-time order."""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.methods.temporal_fold import decode_samples
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, arr, encode, read
from tests.r94_domain_recovery_worker import forbidden, snapshot
from tests.r94_native_domain_k_worker import run_ids


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-temporal.json")
    identity, reference = state["session"], state["source"]
    assert isinstance(identity, str) and isinstance(reference, str)
    subject_keys = arr(state["subject_keys"])
    assert len(subject_keys) == 2
    key_a, key_b = subject_keys
    assert isinstance(key_a, (int, str)) and isinstance(key_b, (int, str))
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
        assert isinstance(value, mv.MaterializedNumericRelation)
        assert snapshot(value) == state["original"]
        assert value.to_pandas().set_index("member").value.to_dict() == {key_a: 18.0, key_b: 150.0}
        assert value._dataset is not None
        rows = next(
            part.table.to_pylist()
            for part in value._dataset.verified().parts
            if part.role == "original_state"
        )
        assert {row["key_0"]: decode_samples(row["original_state__samples"]) for row in rows} == {
            key_a: ((datetime(2026, 8, 1), 15, 2), (datetime(2026, 8, 2), 21, 2)),
            key_b: ((datetime(2026, 8, 1), 100, 1), (datetime(2026, 8, 2), 200, 1)),
        }
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
            expected = 84 if name == "current" else 168
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
            (root / "r94-temporal.json").write_bytes(encode(state))
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
