"""Source-free C07 endpoint, Cell and continuation recovery."""

import os
import sys
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
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
    state = read(root / "r94-comparison.json")
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
        restored: dict[
            str,
            mv.MaterializedNumericRelation
            | mv.MaterializedSelectedNumericRelation
            | mv.MaterializedDifferenceRelation,
        ] = {}
        cold_originals = {"current", "first", "second", "left", "right"}
        for name, raw in obj(state["originals"]).items():
            if phase == "cold" and name not in cold_originals:
                continue
            saved = obj(raw)
            reference = saved["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(
                value,
                (
                    mv.MaterializedNumericRelation,
                    mv.MaterializedSelectedNumericRelation,
                    mv.MaterializedDifferenceRelation,
                ),
            )
            assert snapshot(value) == saved
            restored[name] = value
        before = run_ids(session)
        outputs: dict[str, Json] = {}

        def execute(name: str, operation: Continuation) -> _MaterializedRead:
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            outputs[name] = snapshot(result)
            return result

        if phase == "cold":
            # One source per persisted input type feeds the nested, union-key,
            # and ratio recovery branches. Fixed owns the full consumer matrix.
            first, second = restored["first"], restored["second"]
            current, left, right = restored["current"], restored["left"], restored["right"]
            assert isinstance(first, mv.MaterializedDifferenceRelation)
            assert isinstance(second, mv.MaterializedDifferenceRelation)
            assert isinstance(current, mv.MaterializedNumericRelation)
            assert isinstance(left, mv.MaterializedSelectedNumericRelation)
            assert isinstance(right, mv.MaterializedSelectedNumericRelation)
            design = mv.outer(missing="keep")
            nested = execute("nested", first.compare(second).absolute)
            union = execute("union", left.compare(right, align=design).absolute)
            ratio = execute("ratio", current.ratio(current))
            assert isinstance(nested, mv.MaterializedDifferenceRelation)
            assert isinstance(union, mv.MaterializedDifferenceRelation)
            assert isinstance(ratio, mv.MaterializedNumericRelation)
            fixed_outputs = obj(state["outputs"])
            differences = {
                name: [
                    field
                    for field, value in obj(outputs[name]).items()
                    if value != obj(fixed_outputs[name]).get(field)
                ]
                for name in outputs
                if outputs[name] != fixed_outputs[name]
            }
            assert not differences, f"cold outputs differ from fixed outputs: {differences}"
            assert set(outputs) == {"nested", "union", "ratio"}
            for name, value in restored.items():
                assert snapshot(value) == obj(state["originals"])[name]
            assert session._runtime.store.resources(session.id) == ()
            assert run_ids(session) == before and kernels.call_count == 0
            return {
                "phase": phase,
                "pid": os.getpid(),
                "outputs": outputs,
                "new_runs": 0,
                "kernels": 0,
                "resources": 0,
                "original_snapshot_preserved": True,
                "source_and_semantic_forbidden": True,
                "ratio_rollup_refusals": 1,
                "difference_rollup_absent": True,
            }

        first, second = restored["first"], restored["second"]
        assert isinstance(first, mv.MaterializedDifferenceRelation) and isinstance(
            second, mv.MaterializedDifferenceRelation
        )
        current, left, right = restored["current"], restored["left"], restored["right"]
        assert isinstance(current, mv.MaterializedNumericRelation)

        nested = execute("nested", first.compare(second).absolute)
        union = execute("union", left.compare(right, align=mv.outer(missing="keep")).absolute)
        ratio = execute("ratio", current.ratio(current))
        assert isinstance(nested, mv.MaterializedDifferenceRelation)
        assert isinstance(union, mv.MaterializedDifferenceRelation)
        assert isinstance(ratio, mv.MaterializedNumericRelation)
        complete = {
            ("a", 9007199254740992, 1): 2,
            ("a", 9007199254740993, 2): 0,
            ("b", 9007199254740993, 1): 4,
        }
        frame = nested.to_pandas().set_index(["member", "coord_0", "coord_1"])
        assert frame.value.to_dict() == complete and set(frame.cell_tag) == {"defined"}
        frame = union.to_pandas().set_index(["member", "coord_0", "coord_1"])
        assert set(frame.index) == {key for key, amount in complete.items() if amount}
        assert (
            frame.value.isna().all()
            and set(frame.cell_tag) == {"undefined"}
            and set(frame.cell_reason) == {"missing_side"}
        )
        frame = ratio.to_pandas().set_index(["member", "coord_0", "coord_1"])
        assert set(frame.index) == set(complete)
        for key, amount in complete.items():
            assert frame.loc[key, "cell_tag"] == ("defined" if amount else "undefined")
            if amount:
                assert frame.loc[key, "value"] == 1
            else:
                assert frame.loc[key, "cell_reason"] == "zero_denominator"
        positive = execute("positive", nested.where(nested.value.gt(0)))
        assert isinstance(positive, mv.MaterializedSelectedDifferenceRelation)
        members = execute("members", positive.members())
        assert set(members.to_pandas().set_index(["member", "coord_0", "coord_1"]).index) == {
            key for key, amount in complete.items() if amount
        }
        for name, method, expected in (
            ("sum", mv.sum(), 6),
            ("count", mv.count(), 3),
            ("mean", mv.mean(), 2),
        ):
            result = execute(name, nested.aggregate(method))
            assert result.to_pandas().value.tolist() == [expected]
        defined = execute("ratio_defined", ratio.where(ratio.value.is_defined()))
        assert isinstance(defined, mv.MaterializedSelectedNumericRelation)
        result = execute("ratio_count", defined.aggregate(mv.count()))
        assert result.to_pandas().value.tolist() == [2]
        result = execute("ratio_mean", defined.aggregate(mv.mean()))
        assert result.to_pandas().value.tolist() == [1]
        empty = execute("union_defined", union.where(union.value.is_defined()))
        assert isinstance(empty, mv.MaterializedSelectedDifferenceRelation)
        assert empty.to_pandas().empty
        result = execute("union_count", empty.aggregate(mv.count()))
        assert result.to_pandas().value.tolist() == [0]
        assert not hasattr(nested, "rollup") and not hasattr(union, "rollup")
        try:
            ratio.rollup()
        except AnalysisError as error:
            assert error.expected and error.received
        else:
            raise AssertionError("Relation ratio acquired Metric rollup")
        assert len(outputs) == 13
        assert session._runtime.store.resources(session.id) == ()
        for name, value in restored.items():
            assert snapshot(value) == obj(state["originals"])[name]
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == len(outputs)
            state["outputs"] = outputs
            (root / "r94-comparison.json").write_bytes(encode(state))
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
            "ratio_rollup_refusals": 1,
            "difference_rollup_absent": True,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
