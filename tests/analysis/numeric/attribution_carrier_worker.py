"""Recover actual allocation carriers using retained original partitions only."""

import json
from decimal import Decimal
from fractions import Fraction

import pandas as pd
import pyarrow as pa

import marivo.analysis as mv
from marivo.analysis.materialization.graph_exchange import to_arrow
from marivo.analysis.public_dsl import _MaterializedRead
from tests.support.json import Json, arr, checked, obj


def table_rows(frame: pd.DataFrame, schema: pa.Schema) -> list[Json]:
    table = to_arrow(frame, schema)
    for index, field in enumerate(table.schema):
        if pa.types.is_duration(field.type):
            table = table.set_column(index, field.name, table[field.name].cast(pa.int64()))
    return arr(checked(json.loads(json.dumps(table.to_pylist(), default=str))))


def parts(value: _MaterializedRead) -> dict[str, Json]:
    assert value._dataset is not None
    verified = value._dataset.verified()
    schemas = {part.role: part.schema for part in verified.contract.parts}
    return {
        **{
            part.role: {
                "schema": str(schemas[part.role]),
                "rows": table_rows(part.table, schemas[part.role]),
            }
            for part in verified.parts
        },
        "primary": {
            "schema": str(verified.contract.schema),
            "rows": table_rows(verified.primary, verified.contract.schema),
        },
    }


def check_allocation(result: mv.MaterializedAttributionResult, kind: str) -> None:
    assert result._dataset is not None
    verified = result._dataset.verified()
    allocation = next(part.table for part in verified.parts if part.role == "allocation")
    schema = next(part.schema for part in verified.contract.parts if part.role == "allocation")
    rows = [obj(row) for row in table_rows(allocation, schema)]
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
