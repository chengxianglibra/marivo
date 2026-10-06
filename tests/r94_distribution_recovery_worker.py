"""Retained nonadditive distributions keep Subject maps and disclosed methods."""

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
from tests.json_support import Json, arr, encode, obj, read
from tests.r94_domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-distribution.json")
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
        refusals: dict[str, Json] = {}

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

        for kind, raw in obj(state["originals"]).items():
            original = obj(raw)
            reference = original["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, mv.MaterializedNumericRelation)
            assert snapshot(value) == original
            assert "relation.rollup()" not in {action.call for action in value.contract().actions}
            seen, calls = run_ids(session), kernels.call_count
            for name in ("rollup", "attribute"):
                try:
                    if name == "rollup":
                        value.rollup()
                    else:
                        value.compare(value).attribute(
                            axes=(ms.ref.dimension("sales.facts.owner"),)
                        )
                except AnalysisError as error:
                    assert error.expected and error.received and error.repair
                    refusals[kind + ":" + name] = {
                        "expected": error.expected,
                        "received": error.received,
                        "repair": str(error.repair),
                    }
                else:
                    raise AssertionError("Distribution acquired original-state authority")
            assert run_ids(session) == seen and kernels.call_count == calls
            selected = execute(
                kind + ":where",
                value.where(value.value.is_defined() if "quantile" in kind else value.value.gt(0)),
            )
            assert isinstance(selected, mv.MaterializedSelectedNumericRelation)
            frame = value.to_pandas().set_index("member")
            selected_frame = selected.to_pandas().set_index("member")
            assert selected_frame.equals(frame.loc[["a", "b"]])
            members = execute(kind + ":members", selected.members())
            assert members.to_pandas().member.tolist() == ["a", "b"]
            numbers = [
                obj(row)["value"]
                for row in arr(obj(original["rows"])["data"])
                if obj(row)["member"] in ("a", "b")
            ]
            assert len(numbers) == 2 and all(isinstance(number, (int, float)) for number in numbers)
            total = sum(float(number) for number in numbers if isinstance(number, (int, float)))
            for name, method, expected in (
                ("sum", mv.sum(), total),
                ("count", mv.count(), 2),
                ("mean", mv.mean(), total / 2),
            ):
                result = execute(kind + ":" + name, selected.summarize(method))
                rows = result.to_pandas()
                assert len(rows) == 1 and abs(float(rows.value.iloc[0]) - expected) < 1e-12
                assert rows.cell_tag.tolist() == ["defined"]
            assert snapshot(value) == original
        assert session._runtime.store.resources(session.id) == ()
        count = 5 * len(obj(state["originals"]))
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == count
            state["outputs"], state["refusals"] = outputs, refusals
            (root / "r94-distribution.json").write_bytes(encode(state))
        else:
            assert outputs == state["outputs"] and refusals == state["refusals"]
            assert run_ids(session) == before and kernels.call_count == 0
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "refusals": refusals,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "original_snapshot_preserved": True,
            "source_and_semantic_forbidden": True,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
