"""Recover actual allocation carriers using retained original partitions only."""

import json
import os
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import duckdb
import ibis
import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.shared_fixtures import run_ids
from tests.support.json import Json, arr, checked, encode, obj, read


def table_rows(table: pa.Table) -> list[Json]:
    for index, field in enumerate(table.schema):
        if pa.types.is_duration(field.type):
            table = table.set_column(index, field.name, table[field.name].cast(pa.int64()))
    return arr(checked(json.loads(json.dumps(table.to_pylist(), default=str))))


def parts(value: _MaterializedRead) -> dict[str, Json]:
    assert value._dataset is not None
    verified = value._dataset.verified()
    return {
        **{
            part.role: {"schema": str(part.table.schema), "rows": table_rows(part.table)}
            for part in verified.parts
        },
        "primary": {"schema": str(verified.primary.schema), "rows": table_rows(verified.primary)},
    }


def check_allocation(result: mv.MaterializedAttributionResult, kind: str) -> None:
    assert result._dataset is not None
    verified = result._dataset.verified()
    allocation = next(part.table for part in verified.parts if part.role == "allocation")
    rows = [obj(row) for row in table_rows(allocation)]
    assert [row["key_1"] for row in rows] == ["Other", "app", "web"]
    assert all(row["key_0"] == 1 and row["key_2"] == 0 for row in rows)
    current = {"web": Fraction(12), "app": Fraction(6), "Other": Fraction()}
    baseline = {"web": Fraction(4), "app": Fraction(), "Other": Fraction(2)}
    if kind == "count":
        current = {key: Fraction(value != 0) for key, value in current.items()}
        baseline = {key: Fraction(value != 0) for key, value in baseline.items()}
    elif kind == "linear":
        current = {key: value * 2 for key, value in current.items()}
        baseline = {key: value * 2 for key, value in baseline.items()}
    elif kind in ("mean", "ratio", "weighted"):
        if kind == "weighted":
            current = {"web": Fraction(36), "app": Fraction(6), "Other": Fraction()}
            baseline = {"web": Fraction(8), "app": Fraction(), "Other": Fraction(4)}
        current_total = 18 if kind == "ratio" else 4 if kind == "weighted" else 2
        baseline_total = 6 if kind == "ratio" else 4 if kind == "weighted" else 2
        current = {key: value / current_total for key, value in current.items()}
        baseline = {key: value / baseline_total for key, value in baseline.items()}
    for row in rows:
        key = row["key_1"]
        assert isinstance(key, str)
        for column, expected in (
            ("allocation__current", current[key]),
            ("allocation__baseline", baseline[key]),
            ("allocation__contribution", current[key] - baseline[key]),
        ):
            actual = row[column]
            assert isinstance(actual, (int, float, str)) and not isinstance(actual, bool)
            if isinstance(actual, str):
                assert abs(Fraction(Decimal(actual)) - expected) <= Fraction(1, 10**6)
            elif isinstance(actual, float):
                assert abs(Fraction(actual) - expected) <= Fraction(1, 10**12)
            else:
                assert Fraction(actual) == expected
    assert dict(result.contract()._facts)["complete_partition"] == "True"
    assert {part.role for part in verified.parts} == {
        "current_endpoint",
        "baseline_endpoint",
        "current_endpoint_coordinates",
        "baseline_endpoint_coordinates",
        "basis",
        "allocation",
        "reconciliation",
        "selection_scope",
    }


def run(root: Path, phase: Literal["fixed", "cold"]) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    path = root / "r94-attribution-carrier.json"
    state = read(path)
    identity = state["session"]
    assert isinstance(identity, str)
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
        originals: dict[str, _MaterializedRead] = {}
        for name, raw in obj(state["originals"]).items():
            saved = obj(raw)
            reference = saved["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, _MaterializedRead)
            assert snapshot(value) == saved and parts(value) == obj(state["parts"])[name]
            originals[name] = value
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        output_parts: dict[str, Json] = {}
        for raw in arr(state["methods"]):
            assert isinstance(raw, str)
            difference = originals[raw + ":difference"]
            assert isinstance(difference, mv.MaterializedDifferenceRelation)
            operation = difference.attribute(axes=(ms.ref.dimension("sales.order.channel"),))
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            check_allocation(result, raw)
            for label in ("allocation", "expanded"):
                source = originals[raw + ":" + label]
                assert isinstance(source, mv.MaterializedAttributionResult)
                check_allocation(source, raw)
            outputs[raw], output_parts[raw] = snapshot(result), parts(result)
            seen = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(operation.execute()) == outputs[raw]
            assert run_ids(session) == seen
        current = run_ids(session)
        assert session._runtime.store.resources(session.id) == ()
        for name, original in originals.items():
            assert snapshot(original) == obj(state["originals"])[name]
            assert parts(original) == obj(state["parts"])[name]
        if phase == "fixed":
            assert len(current - before) == kernels.call_count == len(outputs)
            state["outputs"], state["output_parts"] = outputs, output_parts
            path.write_bytes(encode(state))
        else:
            assert outputs == state["outputs"] and output_parts == state["output_parts"]
            assert current == before and kernels.call_count == 0
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "output_parts": output_parts,
            "new_runs": len(current - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "source_semantic_reconnect_forbidden": True,
            "original_snapshots_and_parts_preserved": True,
        }


if __name__ == "__main__":
    raw = sys.argv[2]
    assert raw in ("fixed", "cold")
    phase: Literal["fixed", "cold"] = "fixed" if raw == "fixed" else "cold"
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), phase)))
