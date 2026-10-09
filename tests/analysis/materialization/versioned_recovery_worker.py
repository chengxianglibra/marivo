"""Versioned attribute selections retain exact Subject identity offline."""

import os
import sys
from datetime import date
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
from tests.support.json import Json, encode, obj, read


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-versions.json")
    identity = state["session"]
    assert isinstance(identity, str) and not (root / "models").exists()
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
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        restored: dict[str, _MaterializedRead] = {}
        for name, raw in obj(state["originals"]).items():
            original = obj(raw)
            reference = original["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, _MaterializedRead)
            assert snapshot(value) == original
            restored[name] = value

        def execute(name: str, operation: Continuation) -> _MaterializedRead:
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            outputs[name] = snapshot(result)
            seen = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(operation.execute()) == outputs[name]
            assert seen == run_ids(session)
            return result

        all_keys = [(9007199254740992, "a"), (9007199254740993, "a"), (9007199254740993, "b")]
        for kind in ("snapshot", "validity"):
            numeric = restored[kind + ":numeric"]
            category = restored[kind + ":category"]
            boolean = restored[kind + ":boolean"]
            temporal = restored[kind + ":temporal"]
            assert isinstance(numeric, mv.MaterializedNumericRelation)
            assert isinstance(category, mv.MaterializedCategoryRelation)
            assert isinstance(boolean, mv.MaterializedBooleanRelation)
            assert isinstance(temporal, mv.MaterializedTemporalRelation)
            operations: tuple[
                tuple[
                    str,
                    mv.LogicalSelectedNumericRelation
                    | mv.LogicalSelectedBooleanRelation
                    | mv.LogicalCategoryRelation
                    | mv.LogicalSelectedTemporalRelation,
                    list[tuple[int, str]],
                ],
                ...,
            ] = (
                ("numeric", numeric.where(numeric.value.gt(15)), all_keys[1:]),
                ("boolean", boolean.where(boolean.value.eq(True)), all_keys[1:]),
                ("category", category.where(category.value.eq("b")), all_keys[2:]),
                ("temporal", temporal.where(temporal.value.eq(date(2026, 8, 1))), all_keys),
                ("empty", category.where(category.value.eq("absent")), []),
            )
            for name, operation, expected in operations:
                selected = execute(kind + ":" + name + ":where", operation)
                assert isinstance(
                    selected,
                    (
                        mv.MaterializedSelectedNumericRelation,
                        mv.MaterializedSelectedBooleanRelation,
                        mv.MaterializedCategoryRelation,
                        mv.MaterializedSelectedTemporalRelation,
                    ),
                )
                members = execute(kind + ":" + name + ":members", selected.members())
                frame = members.to_pandas()
                assert list(zip(frame.member, frame.coord_0, strict=True)) == expected
                if isinstance(selected, mv.MaterializedSelectedNumericRelation):
                    assert selected.to_pandas().value.tolist() == [20, 30]
                    total = execute(kind + ":numeric:sum", selected.summarize(mv.sum()))
                    assert total.to_pandas().value.tolist() == [50]
        for name, expected_total in obj(state.get("extra_oracles", {})).items():
            extra = restored[name]
            assert isinstance(extra, mv.MaterializedNumericRelation)
            total = execute(name + ":sum", extra.rollup())
            assert total.to_pandas().value.tolist() == [expected_total]
        for name, restored_value in restored.items():
            assert snapshot(restored_value) == obj(state["originals"])[name]
        assert session._runtime.store.resources(session.id) == ()
        expected_count = 22 + len(obj(state.get("extra_oracles", {})))
        assert len(outputs) == expected_count
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == expected_count
            state["outputs"] = outputs
            (root / "r94-versions.json").write_bytes(encode(state))
        else:
            assert outputs == state["outputs"]
            assert run_ids(session) == before and kernels.call_count == 0
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
