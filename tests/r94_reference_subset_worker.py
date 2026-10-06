"""Nonzero share selection preserves the original denominator and reference."""

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
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, encode, obj, read
from tests.r94_domain_recovery_worker import forbidden, snapshot
from tests.r94_native_domain_k_worker import run_ids
from tests.r94_reference_recovery_worker import reference_parts


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-reference.json")
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
        parts: dict[str, Json] = {}
        refusals: dict[str, Json] = {}
        expected = float(Fraction(4, 6))

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
            assert run_ids(session) == seen
            return result

        for kind, collection in (("source", "originals"), ("fixed", "outputs")):
            original = obj(obj(state[collection])["share"])
            reference = original["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, mv.MaterializedNumericRelation)
            assert snapshot(value) == original
            selected = execute(kind + ":where", value.where(value.value.gt(0.5)))
            assert isinstance(selected, mv.MaterializedSelectedNumericRelation)
            frame = selected.to_pandas().set_index(["member", "coord_0", "coord_1"])
            assert frame.value.to_dict() == {("b", 9007199254740993, 1): expected}
            assert frame.cell_tag.tolist() == ["defined"]
            assert reference_parts(value) == reference_parts(selected)
            parts[kind] = {
                "original": reference_parts(value),
                "selected": reference_parts(selected),
            }
            assert not any("members" in action.call for action in selected.contract().actions)
            seen = run_ids(session)
            calls = kernels.call_count
            try:
                selected.members()
            except DatasetConstructionError as error:
                assert (
                    error.expected
                    and error.received == "current result has no verified Subject map"
                    and error.repair
                )
                refusals[kind] = {
                    "expected": error.expected,
                    "received": error.received,
                    "repair": str(error.repair),
                }
            else:
                raise AssertionError("Share numeric keys acquired Subject authority")
            assert run_ids(session) == seen and kernels.call_count == calls
            for name, method, number in (
                ("sum", mv.sum(), expected),
                ("count", mv.count(), 1),
                ("mean", mv.mean(), expected),
            ):
                statistic = execute(kind + ":" + name, selected.summarize(method))
                assert statistic.to_pandas().value.tolist() == [number]
            assert snapshot(value) == original
        assert len(outputs) == 8 and session._runtime.store.resources(session.id) == ()
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == 8
            state["reference_subset_K"], state["reference_subset_parts"] = outputs, parts
            (root / "r94-reference.json").write_bytes(encode(state))
        else:
            assert (
                outputs == state["reference_subset_K"] and parts == state["reference_subset_parts"]
            )
            assert run_ids(session) == before and kernels.call_count == 0
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "parts": parts,
            "member_refusals": refusals,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "original_snapshot_preserved": True,
            "source_and_semantic_forbidden": True,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
