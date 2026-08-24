"""Unit tests for the shared DeltaFrame arithmetic helper."""

from __future__ import annotations

import pandas as pd
import pytest

from marivo.analysis.delta_math import compute_delta_columns


def _row(current: float, baseline: float) -> pd.DataFrame:
    return pd.DataFrame({"current": [current], "baseline": [baseline]})


def test_from_zero_growth_uses_null_pct_change_not_inf() -> None:
    """A zero baseline with positive growth has an undefined pct change; it must
    be null (not +inf) so it does not poison downstream sorts/aggregates, while
    the status still marks the from-zero-growth case. See issue #30.
    """
    df = compute_delta_columns(_row(10.0, 0.0))

    assert df.loc[0, "delta"] == 10.0
    assert pd.isna(df.loc[0, "pct_change"])
    assert df.loc[0, "pct_change_status"] == "from_zero_growth"


def test_from_zero_decline_uses_null_pct_change_not_neg_inf() -> None:
    df = compute_delta_columns(_row(-5.0, 0.0))

    assert df.loc[0, "delta"] == -5.0
    assert pd.isna(df.loc[0, "pct_change"])
    assert df.loc[0, "pct_change_status"] == "from_zero_decline"


def test_computed_pct_change_unchanged_for_nonzero_baseline() -> None:
    df = compute_delta_columns(_row(10.0, 5.0))

    assert df.loc[0, "delta"] == 5.0
    assert df.loc[0, "pct_change"] == 1.0
    assert df.loc[0, "pct_change_status"] == "computed"


@pytest.mark.parametrize("dtype", ["uint64", "UInt64", "uint64[pyarrow]"])
def test_unsigned_operands_are_normalized_before_signed_delta_math(dtype: str) -> None:
    df = pd.DataFrame(
        {
            "current": pd.Series([7, 3, 0], dtype=dtype),
            "baseline": pd.Series([5, 0, 4], dtype=dtype),
        }
    )

    result = compute_delta_columns(df)

    assert result["current"].dtype == "float64"
    assert result["baseline"].dtype == "float64"
    assert result["delta"].dtype == "float64"
    assert result["delta"].tolist() == [2.0, 3.0, -4.0]
    assert result["pct_change_status"].tolist() == [
        "computed",
        "from_zero_growth",
        "computed",
    ]


@pytest.mark.parametrize("dtype", ["UInt64", "uint64[pyarrow]"])
def test_nullable_unsigned_operands_preserve_null_delta(dtype: str) -> None:
    df = pd.DataFrame(
        {
            "current": pd.Series([3, pd.NA], dtype=dtype),
            "baseline": pd.Series([0, pd.NA], dtype=dtype),
        }
    )

    result = compute_delta_columns(df)

    assert result.loc[0, "delta"] == 3.0
    assert pd.isna(result.loc[1, "delta"])
    assert result["pct_change_status"].tolist() == ["from_zero_growth", "not_computable"]
