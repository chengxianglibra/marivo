"""Independent numeric and key references for the complete pandas comparison."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.operators.compare import execute_compare, promoted_numeric_type
from marivo.refs import ref
from tests.lazy_compare_fixtures import comparison_spec as _spec

REVENUE = ref.metric("sales.revenue")
REGION = ref.dimension("sales.customers.region")
DAY = ref.time_dimension("sales.orders.order_time")


def test_presence_null_zero_and_negative_baseline_have_independent_reference() -> None:
    current = pd.DataFrame(
        {"region": ["negative", "null", "zero", "new"], "revenue": [-10.0, None, 4.0, 3.0]}
    ).convert_dtypes(dtype_backend="pyarrow")
    baseline = pd.DataFrame(
        {"region": ["negative", "null", "zero", "old"], "revenue": [-20.0, 1.0, 0.0, 2.0]}
    ).convert_dtypes(dtype_backend="pyarrow")
    result = execute_compare(current, baseline, _spec()).set_index("region")
    assert result.loc["negative", "delta"] == 10
    assert result.loc["negative", "relative_delta"] == 0.5
    assert result.loc["null", "calculation_status"] == "null_input"
    assert result.loc["null", "relative_delta_status"] == "delta_unavailable"
    assert result.loc["zero", "relative_delta_status"] == "baseline_zero"
    assert (
        result.loc["new", "coordinate_presence"] == "current_only"
        and result.loc["new", "delta"] == 3
    )
    assert (
        result.loc["old", "coordinate_presence"] == "baseline_only"
        and result.loc["old", "delta"] == -2
    )
    missing = execute_compare(current, baseline, _spec(zero=False)).set_index("region")
    assert missing.loc["new", "calculation_status"] == "missing_side"
    assert missing.loc["old", "calculation_status"] == "missing_side"
    assert current.columns.tolist() == ["region", "revenue"]


def test_time_ordinals_are_per_series_and_preserve_both_exact_bucket_values() -> None:
    current = pd.DataFrame(
        {
            "region": ["a", "b", "a"],
            "order_time": [date(2026, 2, 2), date(2026, 2, 1), date(2026, 2, 1)],
            "revenue": [5.0, 4.0, 3.0],
        }
    )
    baseline = pd.DataFrame(
        {
            "region": ["a", "a", "b"],
            "order_time": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 1)],
            "revenue": [1.0, 2.0, 3.0],
        }
    )
    result = execute_compare(current, baseline, _spec(shape="dimension-time"))
    assert result["comparison_ordinal"].tolist() == [0, 1, 0]
    assert result["delta"].tolist() == [2, 3, 1]
    assert result["current_time"].tolist() == [date(2026, 2, 1), date(2026, 2, 2), date(2026, 2, 1)]
    assert result["baseline_time"].tolist() == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 1),
    ]
    with pytest.raises(DatasetConstructionError, match="bucket counts"):
        execute_compare(current, baseline.iloc[:2], _spec(shape="dimension-time"))


def test_scalar_null_and_nonfinite_are_distinct_and_duplicate_keys_fail() -> None:
    for invalid in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(DatasetConstructionError, match="non-finite"):
            execute_compare(
                pd.DataFrame({"revenue": [invalid]}),
                pd.DataFrame({"revenue": [1.0]}),
                _spec(shape="scalar"),
            )
    result = execute_compare(
        pd.DataFrame({"revenue": [None]}), pd.DataFrame({"revenue": [1.0]}), _spec(shape="scalar")
    )
    assert result["calculation_status"].tolist() == ["null_input"]
    duplicate = pd.DataFrame({"region": ["x", "x"], "revenue": [1.0, 2.0]})
    with pytest.raises(DatasetConstructionError, match="duplicate"):
        execute_compare(duplicate, duplicate, _spec())


def test_integer_and_decimal_promotions_reject_whole_action_overflow() -> None:
    assert promoted_numeric_type("int32") == "int64"
    assert promoted_numeric_type("float32") == "float64"
    with pytest.raises(DatasetConstructionError):
        promoted_numeric_type("uint64")
    integer = _spec(shape="scalar", numeric="int64")
    result = execute_compare(
        pd.DataFrame({"revenue": [2**60 + 1]}), pd.DataFrame({"revenue": [2**60]}), integer
    )
    assert result["delta"].tolist() == [1]
    with pytest.raises(DatasetConstructionError, match="overflow"):
        execute_compare(
            pd.DataFrame({"revenue": [2**63 - 1]}), pd.DataFrame({"revenue": [-1]}), integer
        )
    decimal = _spec(shape="scalar", numeric="decimal")
    result = execute_compare(
        pd.DataFrame({"revenue": [Decimal("12345678901234567890.25")]}),
        pd.DataFrame({"revenue": [Decimal("12345678901234567890.01")]}),
        decimal,
    )
    assert result["delta"].tolist() == [Decimal("0.24")]
    with pytest.raises(DatasetConstructionError, match="overflow"):
        execute_compare(
            pd.DataFrame({"revenue": [Decimal("9" * 38)]}),
            pd.DataFrame({"revenue": [Decimal(-1)]}),
            decimal,
        )


def test_empty_inputs_and_null_dimension_preserve_exact_key_meaning() -> None:
    empty = pd.DataFrame(
        {
            "region": pd.Series([], dtype="string[pyarrow]"),
            "revenue": pd.Series([], dtype="float64[pyarrow]"),
        }
    )
    assert execute_compare(empty, empty, _spec()).empty
    keyed = pd.DataFrame({"region": pd.Series([None], dtype="string[pyarrow]"), "revenue": [2.0]})
    result = execute_compare(keyed, keyed, _spec())
    assert len(result) == 1 and result["coordinate_presence"].tolist() == ["matched"]
    assert result["region"].isna().all()
