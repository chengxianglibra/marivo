"""Unit tests for the shared DeltaFrame arithmetic helper."""

from __future__ import annotations

import pandas as pd

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
