"""Pure dense dwell reduction over committed Lifecycle history rows."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class LifecycleDwellReduction:
    """Dense per-state interval counts and completed-duration statistics."""

    rows: pd.DataFrame
    source_interval_count: int


def reduce_lifecycle_dwell(
    history: pd.DataFrame,
    *,
    state_order: tuple[str, ...],
) -> LifecycleDwellReduction:
    """Aggregate clipped completed durations while partitioning censoring."""

    rows: list[dict[str, object]] = []
    for state in state_order:
        state_rows = history.loc[history["model_state"] == state]
        statuses = state_rows["interval_status"].astype(str).value_counts()
        completed = state_rows.loc[state_rows["interval_status"] == "completed"]
        durations = pd.to_datetime(completed["valid_to"], utc=True) - pd.to_datetime(
            completed["valid_from"], utc=True
        )
        if durations.empty:
            mean_duration = None
            median_duration = None
            p90_duration = None
        else:
            mean_duration = durations.mean()
            median_duration = durations.median()
            p90_duration = durations.quantile(0.9)
        rows.append(
            {
                "model_state": state,
                "interval_count": len(state_rows),
                "completed_count": int(statuses.get("completed", 0)),
                "right_censored_count": int(statuses.get("right_censored", 0)),
                "coverage_censored_count": int(statuses.get("coverage_censored", 0)),
                "mean_duration": mean_duration,
                "median_duration": median_duration,
                "p90_duration": p90_duration,
            }
        )
    return LifecycleDwellReduction(
        rows=pd.DataFrame(
            rows,
            columns=(
                "model_state",
                "interval_count",
                "completed_count",
                "right_censored_count",
                "coverage_censored_count",
                "mean_duration",
                "median_duration",
                "p90_duration",
            ),
        ),
        source_interval_count=len(history),
    )


__all__ = []
