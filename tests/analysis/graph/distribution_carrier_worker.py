"""Recover named float/Decimal distributions with complete retained parts."""

import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol
from unittest.mock import patch

import duckdb
import ibis
import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.numeric.attribution_carrier_worker import parts
from tests.shared_fixtures import run_ids
from tests.support.json import Json, encode, obj, read


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def check_original(value: mv.MaterializedNumericRelation, physical: str, kind: str) -> None:
    assert value._dataset is not None
    table = value._dataset.verified().primary
    records = table.to_pylist()
    assert [record["key_0"] for record in records] == ["A", "B", "C", "D"]
    if physical == "decimal":
        assert kind == "quantile"
        assert [record["value"] for record in records[:3]] == [
            Decimal("1.01"),
            Decimal("1.02"),
            Decimal("1.04"),
        ]
        assert all(isinstance(record["value"], Decimal) for record in records[:3])
        assert [record["cell_tag"] for record in records] == ["defined"] * 3 + ["null"]
    elif "distinct" in kind:
        assert [record["value"] for record in records] == [1, 1, 1, 0]
        assert all(record["cell_tag"] == "defined" for record in records)
    else:
        assert [record["value"] for record in records[:3]] == [450, 150, 400]
        assert [record["cell_tag"] for record in records] == ["defined"] * 3 + ["null"]
    assert all(record["value"] is None for record in records if record["cell_tag"] == "null")
    assert "relation.rollup()" not in {action.call for action in value.contract().actions}


def run(root: Path, phase: Literal["fixed", "cold"]) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    path = root / "r94-distribution-carrier.json"
    state = read(path)
    identity, physical = state["session"], state["physical"]
    assert isinstance(identity, str) and isinstance(physical, str)
    assert not any(
        (root / name).exists() for name in ("models", "warehouse.duckdb", "source_files")
    )
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
        output_parts: dict[str, Json] = {}
        refusals: dict[str, Json] = {}

        def execute(name: str, operation: Continuation) -> _MaterializedRead:
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            outputs[name], output_parts[name] = snapshot(result), parts(result)
            seen = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                repeated = operation.execute()
                assert snapshot(repeated) == outputs[name] and parts(repeated) == output_parts[name]
            assert run_ids(session) == seen
            return result

        for kind, raw in obj(state["originals"]).items():
            original = obj(raw)
            reference = original["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, mv.MaterializedNumericRelation)
            assert snapshot(value) == original and parts(value) == obj(state["parts"])[kind]
            check_original(value, physical, kind)
            seen, calls = run_ids(session), kernels.call_count
            for name in ("rollup", "attribute"):
                try:
                    if name == "rollup":
                        value.rollup()
                    else:
                        value.compare(value).attribute(
                            axes=(ms.ref.dimension("sales.order.channel"),)
                        )
                except AnalysisError as error:
                    assert error.expected and error.received and error.repair
                    refusals[kind + ":" + name] = {
                        "expected": error.expected,
                        "received": error.received,
                        "repair": str(error.repair),
                    }
                else:
                    raise AssertionError("Distribution gained original-quantity authority")
            assert run_ids(session) == seen and kernels.call_count == calls
            selected = execute(
                kind + ":where",
                value.where(value.value.gt(0) if "distinct" in kind else value.value.is_defined()),
            )
            assert isinstance(selected, mv.MaterializedSelectedNumericRelation)
            expected_members = ["A", "B", "C"]
            assert selected.to_pandas().member.tolist() == expected_members
            selected_facts = dict(selected.contract()._facts)
            original_facts = dict(value.contract()._facts)
            for fact in ("method", "algorithm"):
                assert fact in original_facts and selected_facts[fact] == original_facts[fact]
            members = execute(kind + ":members", selected.members())
            assert members.to_pandas().member.tolist() == expected_members
            total = Decimal("3.07") if physical == "decimal" else 3 if "distinct" in kind else 1000
            size = len(expected_members)
            reductions: tuple[tuple[str, mv.RowMethod, int | float | Decimal], ...] = (
                ("sum", mv.sum(), total),
                ("count", mv.count(), size),
                ("mean", mv.mean(), total / size),
            )
            if physical == "decimal":
                reductions = (
                    ("min", mv.min(), Decimal("1.01")),
                    ("count", mv.count(), size),
                    ("max", mv.max(), Decimal("1.04")),
                    ("sum", mv.sum(), total),
                    ("mean", mv.mean(), Decimal("1.023333")),
                )
            for name, method, expected in reductions:
                result = execute(kind + ":" + name, selected.summarize(method))
                assert result._dataset is not None
                rows = result._dataset.verified().primary.to_pylist()
                assert len(rows) == 1 and rows[0]["cell_tag"] == "defined"
                actual = rows[0]["value"]
                if isinstance(expected, Decimal):
                    assert isinstance(actual, Decimal) and actual == expected
                else:
                    assert abs(float(actual) - expected) <= 1e-12
                if physical == "decimal" and name in ("sum", "mean"):
                    assert isinstance(result, mv.MaterializedStatisticRelation)
                    verified = result._dataset.verified()
                    assert verified.primary.schema.field("value").type == pa.decimal128(
                        38, 2 if name == "sum" else 6
                    )
                    row_state = next(p.table for p in verified.parts if p.role == "row_state")
                    assert row_state.schema.field("row_state__sum").type == pa.decimal128(38, 2)
                    assert row_state.schema.field("row_state__count").type == pa.int64()
                    assert row_state.to_pylist() == [
                        {"row_state__sum": Decimal("3.07"), "row_state__count": 3}
                    ]
                    rolled = execute(kind + ":" + name + ":rollup", result.rollup())
                    assert parts(rolled) == parts(result)
            if physical == "decimal":
                empty = execute(
                    kind + ":empty", selected.where(mv.not_(selected.value.is_defined()))
                )
                assert isinstance(empty, mv.MaterializedSelectedNumericRelation)
                assert empty.to_pandas().empty
                for name, method in (("sum", mv.sum()), ("mean", mv.mean())):
                    result = execute(kind + ":empty:" + name, empty.summarize(method))
                    assert isinstance(result, mv.MaterializedStatisticRelation)
                    assert result._dataset is not None
                    verified = result._dataset.verified()
                    assert verified.primary.to_pylist() == [
                        {
                            "value": Decimal("0.00") if name == "sum" else None,
                            "cell_tag": "defined" if name == "sum" else "undefined",
                            "cell_reason": None if name == "sum" else "empty_mean",
                        }
                    ]
                    row_state = next(p.table for p in verified.parts if p.role == "row_state")
                    assert row_state.schema.field("row_state__sum").type == pa.decimal128(38, 2)
                    assert row_state.to_pylist() == [
                        {"row_state__sum": Decimal("0.00"), "row_state__count": 0}
                    ]
                    rolled = execute(kind + ":empty:" + name + ":rollup", result.rollup())
                    assert parts(rolled) == parts(result)
            assert snapshot(value) == original and parts(value) == obj(state["parts"])[kind]
        assert session._runtime.store.resources(session.id) == ()
        count = len(outputs)
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == count
            state["outputs"], state["output_parts"], state["refusals"] = (
                outputs,
                output_parts,
                refusals,
            )
            path.write_bytes(encode(state))
        else:
            assert outputs == state["outputs"] and output_parts == state["output_parts"]
            assert (
                refusals == state["refusals"]
                and run_ids(session) == before
                and kernels.call_count == 0
            )
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "parts": output_parts,
            "refusals": refusals,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "original_snapshot_and_parts_preserved": True,
            "source_and_semantic_forbidden": True,
        }


if __name__ == "__main__":
    assert sys.argv[2] in ("fixed", "cold")
    phase: Literal["fixed", "cold"] = "fixed" if sys.argv[2] == "fixed" else "cold"
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), phase)))
