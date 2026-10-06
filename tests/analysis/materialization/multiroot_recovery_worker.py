"""Exact independent contribution components survive fixed/cold reduction."""

import json
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
from tests.support.json import Json, checked, digest, encode, obj, read


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def components(value: _MaterializedRead) -> dict[str, Json]:
    assert value._dataset is not None
    part = next(part for part in value._dataset.verified().parts if part.role == "original_state")
    contents = obj(
        checked(
            json.loads(
                json.dumps(
                    {"schema": str(part.table.schema), "rows": part.table.to_pylist()},
                    sort_keys=True,
                    default=str,
                )
            )
        )
    )
    return {"sha256": digest(encode(contents)), "contents": contents}


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-multiroot.json")
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

        first, second = 9007199254740992, 9007199254740993
        keys = [("a", first), ("a", second), ("b", first), ("c", first)]
        expected_values: dict[str, tuple[Fraction | None, ...]] = {
            "linear": (Fraction(35), Fraction(110), Fraction(4), Fraction(0)),
            "ratio": (Fraction(6), Fraction(9, 2), Fraction(0), None),
            "weighted": (Fraction(50, 3), Fraction(410, 9), None, None),
            "aggregate": (Fraction(30), Fraction(90), None, None),
            "slice": (Fraction(5), Fraction(20), Fraction(0), Fraction(0)),
        }
        rolled_values = {
            "linear": Fraction(149),
            "ratio": Fraction(120, 29),
            "weighted": Fraction(115, 3),
            "aggregate": Fraction(120),
            "slice": Fraction(25),
        }
        for kind, raw in obj(state["originals"]).items():
            original = obj(raw)
            reference = original["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, (mv.MaterializedNumericRelation, mv.MaterializedRatioRelation))
            assert (
                snapshot(value) == original and components(value) == obj(state["components"])[kind]
            )
            frame = value.to_pandas()
            assert list(zip(frame.member, frame.coord_0, strict=True)) == keys
            for index, expected in enumerate(expected_values[kind]):
                if expected is None:
                    assert frame.cell_tag.iloc[index] == (
                        "undefined" if kind == "ratio" else "null"
                    )
                    assert frame.cell_reason.iloc[index] == (
                        "zero_denominator" if kind == "ratio" else "empty_contribution"
                    )
                else:
                    assert (
                        frame.cell_tag.iloc[index] == "defined"
                        and abs(float(frame.value.iloc[index]) - float(expected)) < 1e-10
                    )
            if isinstance(value, mv.MaterializedRatioRelation):
                assert kind == "ratio"
                for call, ratio_operation, expected in (
                    ("rollup", value.rollup(), rolled_values[kind]),
                    ("count", value.summarize(mv.count()), Fraction(4)),
                    ("count_defined", value.summarize(mv.count_defined()), Fraction(3)),
                ):
                    result = execute(kind + ":" + call, ratio_operation)
                    result_rows = result.to_pandas()
                    assert len(result_rows) == 1 and result_rows.cell_tag.tolist() == ["defined"]
                    assert abs(float(result_rows.value.iloc[0]) - float(expected)) < 1e-10
                assert (
                    snapshot(value) == original
                    and components(value) == obj(state["components"])[kind]
                )
                continue
            defined = execute(kind + ":defined", value.where(value.value.is_defined()))
            assert isinstance(defined, mv.MaterializedSelectedNumericRelation)
            selected = execute(kind + ":where", defined.where(defined.value.gt(0)))
            assert isinstance(selected, mv.MaterializedSelectedNumericRelation)
            selected_keys = keys[:3] if kind == "linear" else keys[:2]
            members = execute(kind + ":members", selected.members())
            member_rows = members.to_pandas()
            assert list(zip(member_rows.member, member_rows.coord_0, strict=True)) == selected_keys
            numbers = [
                number for number in expected_values[kind] if number is not None and number > 0
            ]
            total = sum(numbers, Fraction())
            for call, operation, expected in (
                ("sum", selected.summarize(mv.sum()), total),
                ("mean", selected.summarize(mv.mean()), total / len(numbers)),
                ("rollup", value.rollup(), rolled_values[kind]),
            ):
                result = execute(kind + ":" + call, operation)
                result_rows = result.to_pandas()
                assert len(result_rows) == 1 and result_rows.cell_tag.tolist() == ["defined"]
                assert abs(float(result_rows.value.iloc[0]) - float(expected)) < 1e-10
            if kind == "linear":
                empty = execute("linear:empty", value.where(value.value.gt(1000)))
                assert isinstance(empty, mv.MaterializedSelectedNumericRelation)
                assert len(empty.to_pandas()) == 0
                empty_members = execute("linear:empty_members", empty.members())
                assert len(empty_members.to_pandas()) == 0
            assert (
                snapshot(value) == original and components(value) == obj(state["components"])[kind]
            )
        assert session._runtime.store.resources(session.id) == ()
        assert len(outputs) == 29
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == 29
            state["outputs"] = outputs
            (root / "r94-multiroot.json").write_bytes(encode(state))
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
            "components_preserved": True,
            "source_and_semantic_forbidden": True,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
