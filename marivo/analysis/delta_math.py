"""Shared DeltaFrame arithmetic helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

PCT_CHANGE_STATUS_COLUMN = "pct_change_status"


def compute_delta_columns(
    df: pd.DataFrame,
    *,
    current_column: str = "current",
    baseline_column: str = "baseline",
    delta_column: str = "delta",
    pct_change_column: str = "pct_change",
    status_column: str = PCT_CHANGE_STATUS_COLUMN,
) -> pd.DataFrame:
    """Compute delta, pct_change, and pct_change_status in-place."""
    current = pd.to_numeric(df[current_column], errors="coerce")
    baseline = pd.to_numeric(df[baseline_column], errors="coerce")
    delta = current - baseline

    df[current_column] = current
    df[baseline_column] = baseline
    df[delta_column] = delta
    df[pct_change_column] = np.nan
    df[status_column] = "not_computable"

    finite_inputs = np.isfinite(current) & np.isfinite(baseline) & np.isfinite(delta)
    nonzero_baseline = finite_inputs & (baseline != 0)
    zero_baseline = finite_inputs & (baseline == 0)

    df.loc[nonzero_baseline, pct_change_column] = (
        delta.loc[nonzero_baseline] / baseline.loc[nonzero_baseline].abs()
    )
    df.loc[nonzero_baseline, status_column] = "computed"

    from_zero_growth = zero_baseline & (delta > 0)
    # pct_change is undefined for a zero baseline; leave it null (not +inf) so
    # the column stays finite and does not poison downstream sorts/aggregates.
    # pct_change_status still marks the from-zero-growth case. See issue #30.
    df.loc[from_zero_growth, status_column] = "from_zero_growth"

    from_zero_decline = zero_baseline & (delta < 0)
    df.loc[from_zero_decline, status_column] = "from_zero_decline"

    zero_no_change = zero_baseline & (delta == 0)
    df.loc[zero_no_change, status_column] = "zero_baseline_no_change"

    return df
