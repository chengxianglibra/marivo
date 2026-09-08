"""Independent exact source arithmetic and parity with the complete local method."""

from decimal import Decimal

import ibis
import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.compiler.comparison import lower_compare
from marivo.analysis.operators.compare import execute_compare
from marivo.analysis.operators.contracts import CompareSpecV1
from tests.lazy_compare_fixtures import comparison_spec


def _evaluate(current: pa.Table, baseline: pa.Table, spec: CompareSpecV1) -> pd.DataFrame:
    backend = ibis.duckdb.connect()
    try:
        left = backend.create_table("current_values", current)
        right = backend.create_table("baseline_values", baseline)
        expression, assertions = lower_compare(left, right, spec)
        for assertion in assertions:
            violations = backend.to_pyarrow(assertion.expression)["violations"][0].as_py()
            if violations:
                raise ValueError(assertion.name)
        result = backend.to_pyarrow(expression).to_pandas(types_mapper=pd.ArrowDtype)
        assert isinstance(result, pd.DataFrame)
        return result
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "numeric,current,baseline,expected",
    [
        ("float64", -10.0, -20.0, 10.0),
        ("int64", 2**60 + 1, 2**60, 1),
        (
            "decimal",
            Decimal("123456789012345678901234567890.25"),
            Decimal("123456789012345678901234567890.01"),
            Decimal("0.24"),
        ),
    ],
)
def test_source_numeric_exactness_matches_independent_reference(
    numeric: str,
    current: int | float | Decimal,
    baseline: int | float | Decimal,
    expected: int | float | Decimal,
) -> None:
    spec = comparison_spec(shape="scalar", numeric=numeric)
    first, second = pa.table({"revenue": [current]}), pa.table({"revenue": [baseline]})
    source = _evaluate(first, second, spec)
    local = execute_compare(
        first.to_pandas(types_mapper=pd.ArrowDtype),
        second.to_pandas(types_mapper=pd.ArrowDtype),
        spec,
    )
    assert source["delta"].tolist() == local["delta"].tolist() == [expected]
    assert source["relative_delta"].tolist() == local["relative_delta"].tolist()
    if numeric == "float64":
        assert source["relative_delta"].tolist() == [0.5]


@pytest.mark.parametrize(
    "current,baseline",
    [(float("nan"), 1.0), (float("inf"), 1.0), (1.0, -float("inf")), (1e308, -1e308)],
)
def test_source_rejects_nonfinite_input_and_promoted_difference(
    current: float, baseline: float
) -> None:
    with pytest.raises(ValueError, match="finite"):
        _evaluate(
            pa.table({"revenue": [current]}),
            pa.table({"revenue": [baseline]}),
            comparison_spec(shape="scalar"),
        )


def test_source_relative_overflow_retains_the_valid_delta() -> None:
    result = _evaluate(
        pa.table({"revenue": [1e308]}),
        pa.table({"revenue": [1e-308]}),
        comparison_spec(shape="scalar"),
    )
    assert result["delta"].tolist() == [1e308]
    assert result["relative_delta_status"].tolist() == ["delta_unavailable"]
    assert result["relative_delta"].isna().all()


def test_source_integer_overflow_rejects_before_narrow_output() -> None:
    with pytest.raises(ValueError, match="signed_integer_range"):
        _evaluate(
            pa.table({"revenue": [2**63 - 1]}),
            pa.table({"revenue": [-1]}),
            comparison_spec(shape="scalar", numeric="int64"),
        )


def test_source_decimal_overflow_rejects_complete_calculation() -> None:
    import duckdb

    with pytest.raises(duckdb.OutOfRangeException):
        _evaluate(
            pa.table({"revenue": [Decimal("9" * 38)]}),
            pa.table({"revenue": [Decimal(-1)]}),
            comparison_spec(shape="scalar", numeric="decimal"),
        )


def test_source_coordinate_alignment_uses_field_identity_after_display_alias() -> None:
    from dataclasses import replace

    from marivo.analysis.datasets.descriptors import _CORE_TOKEN, DatasetRowContract, _make_schema

    spec = comparison_spec()

    def renamed_row(row: DatasetRowContract) -> DatasetRowContract:
        return replace(
            row,
            _token=_CORE_TOKEN,
            schema=_make_schema(
                tuple(
                    replace(field, _token=_CORE_TOKEN, name="current_value")
                    if field.name == "region"
                    else field
                    for field in row.schema.columns
                )
            ),
        )

    spec = replace(
        spec, current_row=renamed_row(spec.current_row), baseline_row=renamed_row(spec.baseline_row)
    )
    current = pa.table({"current_value": ["a"], "revenue": [4.0]})
    baseline = pa.table({"current_value": ["a"], "revenue": [1.0]})
    result = _evaluate(current, baseline, spec)
    assert result["region"].tolist() == ["a"]
    assert result["delta"].tolist() == [3.0]
