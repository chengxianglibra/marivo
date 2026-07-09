"""Pure pandas/numpy densification and alignment for cumulative observe frames.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``. No
catalog, session, or ibis dependencies; pandas/numpy are imported lazily inside
each function.
"""

from __future__ import annotations

from typing import Any

from marivo.analysis.errors import AnalysisError
from marivo.analysis.windows.grain import _FIXED_UNIT_SECONDS

_GRAIN_PANDAS_FREQ: dict[str, str] = {
    "second": "s",
    "minute": "min",
    "hour": "h",
    "day": "D",
    "week": "W-MON",
    "month": "MS",
    "quarter": "QS",
    "year": "YS",
}

# Fixed-frequency grains that can use Timestamp.floor() for alignment.
_FIXED_GRAINS: frozenset[str] = frozenset({"second", "minute", "hour", "day"})


def _align_to_grain_start(ts: Any, unit: str, count: int = 1) -> Any:
    """Truncate a timestamp to the start of its grain-period.

    For fixed grains (second/minute/hour/day) with ``count == 1`` this uses
    ``Timestamp.floor``.  For fixed sub-day grains with ``count > 1`` the
    alignment replicates the day-anchored offset logic in
    :func:`bucket_start_expr` so that the spine bucket-start values match the
    SQL-level buckets exactly.

    For calendar grains (week/month/quarter/year) the boundary is computed
    explicitly because ``floor`` does not support non-fixed frequencies.
    """
    import pandas as pd

    if unit in _FIXED_GRAINS:
        if count > 1 and unit in ("second", "minute", "hour"):
            width = count * _FIXED_UNIT_SECONDS[unit]
            day_start = ts.floor("D")
            elapsed = int((ts - day_start).total_seconds())
            offset = (elapsed // width) * width
            return day_start + pd.Timedelta(seconds=offset)
        return ts.floor(_GRAIN_PANDAS_FREQ[unit])
    if unit == "week":
        days_since_monday = ts.weekday()  # 0=Monday
        return (ts - pd.Timedelta(days=days_since_monday)).normalize()
    if unit == "month":
        return pd.Timestamp(year=ts.year, month=ts.month, day=1)
    if unit == "quarter":
        quarter_start_month = ((ts.month - 1) // 3) * 3 + 1
        return pd.Timestamp(year=ts.year, month=quarter_start_month, day=1)
    if unit == "year":
        return pd.Timestamp(year=ts.year, month=1, day=1)
    # Should not reach here for valid Grain units.
    raise ValueError(f"unsupported grain unit for alignment: {unit!r}")


def _bucket_date_range(window: Any) -> list[Any]:
    """Generate a list of bucket-start timestamps for a window at the given grain.

    The window is half-open [start, end); we emit one bucket per grain
    interval from start (inclusive, truncated to the grain boundary) to
    end (exclusive).  The bucket-start values align with what
    :func:`bucket_start_expr` produces at the SQL level so that the dense
    spine matches the flow query buckets.
    """
    import pandas as pd

    start = pd.Timestamp(window.start)
    end = pd.Timestamp(window.end)
    grain = window.grain
    if grain is None:
        return [start]
    unit = grain.unit
    count = grain.count
    freq = f"{count}{_GRAIN_PANDAS_FREQ[unit]}" if count > 1 else _GRAIN_PANDAS_FREQ[unit]
    # Truncate start to the grain boundary so the first bucket matches
    # what bucket_start_expr produces for events in the first partial bucket.
    aligned_start = _align_to_grain_start(start, unit, count)
    bucket_index = pd.date_range(aligned_start, end, freq=freq, inclusive="left")
    return list(bucket_index)


def _dense_cumulative_frame(
    *,
    baseline_df: Any,
    flow_df: Any,
    bucket_values: list[Any],
    dimension_columns: list[str],
    value_column: str = "value",
) -> Any:
    """Build a dense cumulative DataFrame from baseline + flow.

    baseline_df: per-slice baseline values (all history before window start).
    flow_df: per-bucket-per-slice flow values (within window).
    bucket_values: dense list of bucket_start timestamps.
    dimension_columns: dimension column names for panel/segmented shapes.

    Returns a DataFrame with columns [bucket_start, *dimension_columns, value]
    where value = baseline + cumsum(flow) within each slice.
    """
    import pandas as pd

    key_columns = list(dimension_columns)
    if key_columns:
        combos = pd.concat(
            [
                baseline_df[key_columns]
                if not baseline_df.empty
                else pd.DataFrame(columns=key_columns),
                flow_df[key_columns] if not flow_df.empty else pd.DataFrame(columns=key_columns),
            ],
            ignore_index=True,
        ).drop_duplicates()
    else:
        combos = pd.DataFrame({"__single__": [0]})
    bucket_df = pd.DataFrame({"bucket_start": bucket_values})
    spine = bucket_df.merge(combos, how="cross") if key_columns else bucket_df.assign(__single__=0)

    baseline = baseline_df.copy()
    flow = flow_df.copy()
    if not key_columns:
        baseline["__single__"] = 0
        flow["__single__"] = 0
    merge_keys = key_columns or ["__single__"]
    seed = (
        baseline.groupby(merge_keys, dropna=False)[value_column].sum().reset_index(name="_baseline")
    )
    out = spine.merge(seed, on=merge_keys, how="left")
    out = out.merge(
        flow[["bucket_start", *merge_keys, value_column]],
        on=["bucket_start", *merge_keys],
        how="left",
    )
    out["_baseline"] = out["_baseline"].fillna(0)
    out[value_column] = out[value_column].fillna(0)
    out = out.sort_values([*merge_keys, "bucket_start"])
    out[value_column] = (
        out.groupby(merge_keys, dropna=False)[value_column].cumsum() + out["_baseline"]
    )
    out = out.drop(columns=["_baseline"])
    if "__single__" in out.columns:
        out = out.drop(columns=["__single__"])
    return out.sort_values(["bucket_start", *key_columns]).reset_index(drop=True)


def _require_grain_to_date_compat(query_grain_token: str, reset_grain: str) -> None:
    """Reject grain_to_date resets whose reset period straddles query buckets.

    Week buckets straddle month/quarter/year boundaries, so a week query grain
    under a month/quarter/year reset is illegal: a single bucket would span two
    reset periods and the period-to-date value is undefined. Day and hour grains
    are always legal; week-under-week is legal.
    """
    if query_grain_token == "week" and reset_grain in ("month", "quarter", "year"):
        raise AnalysisError(
            message=(
                f"grain_to_date(grain={reset_grain!r}) is incompatible with query grain "
                f"{query_grain_token!r}: week buckets straddle {reset_grain} boundaries."
            ),
            hint=("Use day or hour query grain, or grain_to_date(grain='week') for a week reset."),
            details={"reset_grain": reset_grain, "query_grain": query_grain_token},
        )


def _trunc_series_to_grain(values: Any, grain: str) -> Any:
    """Truncate a pandas Series of timestamps to the start of the reset period.

    Mirrors :func:`_align_to_grain_start` but vectorized over a Series, for
    deriving the reset-period key of each bucket_start in a dense spine.
    Always returns a pandas Series so callers can use ``.iloc`` / boolean masks.
    """
    import pandas as pd

    ts = pd.to_datetime(pd.Series(values))
    if grain == "week":
        return ts.dt.to_period("W").dt.start_time
    if grain == "month":
        import numpy as np

        return pd.Series(
            ts.values.astype(np.dtype("datetime64[M]")).astype(np.dtype("datetime64[s]")),
            index=ts.index,
            name=ts.name,
        )
    if grain == "quarter":
        month = ts.dt.month
        quarter_start_month = ((month - 1) // 3) * 3 + 1
        return pd.to_datetime(
            pd.DataFrame({"year": ts.dt.year, "month": quarter_start_month, "day": 1})
        )
    if grain == "year":
        return pd.to_datetime(pd.DataFrame({"year": ts.dt.year, "month": 1, "day": 1}))
    raise ValueError(f"unsupported reset grain for truncation: {grain!r}")


def _grain_to_date_dense_frame(
    *,
    seed_df: Any,
    flow_df: Any,
    bucket_values: list[Any],
    dimension_columns: list[str],
    reset_grain: str,
    value_column: str = "value",
) -> Any:
    """Densify + fill 0 + cumsum partitioned by (dims x reset period) + seed.

    Unlike :func:`_dense_cumulative_frame` (all-history), the cumsum resets at
    each reset-period boundary, and the seed scalar is added only to the first
    (partial) reset period's buckets.

    seed_df: per-slice seed scalars (sum over the first partial period before
        window.start). Empty/None when window.start is on a reset boundary.
    flow_df: per-bucket-per-slice flow values within [window.start, window.end).
    bucket_values: dense list of bucket_start timestamps.
    dimension_columns: dimension column names for panel/segmented shapes.
    reset_grain: the reset grain string (week/month/quarter/year).
    """
    import pandas as pd

    key_columns = list(dimension_columns)
    if key_columns:
        combos = pd.concat(
            [
                seed_df[key_columns] if not seed_df.empty else pd.DataFrame(columns=key_columns),
                flow_df[key_columns] if not flow_df.empty else pd.DataFrame(columns=key_columns),
            ],
            ignore_index=True,
        ).drop_duplicates()
    else:
        combos = pd.DataFrame({"__single__": [0]})
    bucket_df = pd.DataFrame({"bucket_start": bucket_values})
    spine = bucket_df.merge(combos, how="cross") if key_columns else bucket_df.assign(__single__=0)

    flow = flow_df.copy()
    if not key_columns:
        flow["__single__"] = 0
    merge_keys = key_columns or ["__single__"]

    # Reset-period key per bucket.
    spine["_reset_key"] = _trunc_series_to_grain(spine["bucket_start"], reset_grain)
    out = spine.merge(
        flow[["bucket_start", *merge_keys, value_column]],
        on=["bucket_start", *merge_keys],
        how="left",
    )
    out[value_column] = out[value_column].fillna(0)

    # cumsum partitioned by (dims x reset period): resets at each boundary.
    out = out.sort_values([*merge_keys, "bucket_start"])
    out[value_column] = out.groupby([*merge_keys, "_reset_key"], dropna=False)[
        value_column
    ].cumsum()

    # Add the seed scalar to the first (partial) reset period's buckets only.
    if seed_df is not None and not seed_df.empty:
        seed = seed_df.copy()
        if not key_columns:
            seed["__single__"] = 0
        seed_map = (
            seed.groupby(merge_keys, dropna=False)[value_column].sum().reset_index(name="_seed")
        )
        out = out.merge(seed_map, on=merge_keys, how="left")
        out["_seed"] = out["_seed"].fillna(0)
        first_period_key = _trunc_series_to_grain(pd.Series([bucket_values[0]]), reset_grain).iloc[
            0
        ]
        mask = out["_reset_key"] == first_period_key
        out.loc[mask, value_column] = out.loc[mask, value_column] + out.loc[mask, "_seed"]
        out = out.drop(columns=["_seed"])

    out = out.drop(columns=["_reset_key"])
    if "__single__" in out.columns:
        out = out.drop(columns=["__single__"])
    return out.sort_values(["bucket_start", *key_columns]).reset_index(drop=True)


def _trailing_rolling_frame(
    *,
    flow_df: Any,
    bucket_values: list[Any],
    dimension_columns: list[str],
    w_buckets: int,
    display_start: Any,
    display_end: Any,
    value_column: str = "value",
) -> Any:
    """Densify + fill 0 + rolling sum with min_periods=1, clipped to display window.

    Trailing (rolling N) post-process. Each bucket's value is the base
    aggregation over the W_buckets-wide span ending at that bucket's end
    boundary. Empty windows are TRUE ZERO (fill 0 then rolling-sum with
    min_periods=1 yields 0 for a gap). Partial windows (span reaches before
    data start) show the actual partial accumulation because min_periods=1
    sums whatever falls in the span.

    The spine covers the EXTENDED fetch range [display_start - span,
    display_end); the result is clipped back to [display_start, display_end)
    so the displayed frame matches the requested window.
    """
    import pandas as pd

    key_columns = list(dimension_columns)
    if key_columns:
        combos = (
            flow_df[key_columns] if not flow_df.empty else pd.DataFrame(columns=key_columns)
        ).drop_duplicates()
    else:
        combos = pd.DataFrame({"__single__": [0]})
    bucket_df = pd.DataFrame({"bucket_start": bucket_values})
    spine = bucket_df.merge(combos, how="cross") if key_columns else bucket_df.assign(__single__=0)

    flow = flow_df.copy()
    if not key_columns:
        flow["__single__"] = 0
    merge_keys = key_columns or ["__single__"]

    out = spine.merge(
        flow[["bucket_start", *merge_keys, value_column]],
        on=["bucket_start", *merge_keys],
        how="left",
    )
    # Empty windows are true zero: fill missing flow with 0 (NOT carry-forward).
    out[value_column] = out[value_column].fillna(0)
    out = out.sort_values([*merge_keys, "bucket_start"])
    # Rolling sum with min_periods=1: partial windows produce actual values,
    # not NaN; empty windows produce 0.
    out[value_column] = (
        out.groupby(merge_keys, dropna=False)[value_column]
        .rolling(window=w_buckets, min_periods=1)
        .sum()
        .reset_index(level=merge_keys, drop=True)
    )
    if "__single__" in out.columns:
        out = out.drop(columns=["__single__"])
    # Clip the extended fetch range back to the display window.
    mask = (out["bucket_start"] >= display_start) & (out["bucket_start"] < display_end)
    return out.loc[mask].sort_values(["bucket_start", *key_columns]).reset_index(drop=True)


def _trailing_coverage_df(
    *,
    dense_df: Any,
    bucket_values: list[Any],
    data_start: Any,
    span_seconds: int,
) -> Any:
    """Build a trailing ``window_coverage`` sidecar with precise span ratios.

    Each display bucket ``b`` aggregates the span ending at ``b``'s end
    boundary, i.e. the window ``[bucket_end - span_seconds, bucket_end)`` where
    ``bucket_end = bucket_start + grain``. The span is fully covered by data
    when ``bucket_end - data_start >= span_seconds``; otherwise the covered
    portion is the tail ``bucket_end - data_start`` (clipped to ``[0, span]``).

    Rows carry ``(bucket_start, expected_span, covered_span, coverage_ratio,
    coverage_status)`` where ``expected_span = span_seconds``,
    ``covered_span = min(span_seconds, max(0, bucket_end - data_start))``,
    ``coverage_ratio = covered_span / expected_span``, and
    ``coverage_status = "partial" if covered_span < expected_span else "complete"``.

    Buckets with no data in their span are still ``complete`` coverage-wise (the
    empty window is a true zero, not a coverage gap) — partiality is strictly
    about the window reaching before the data start.
    """
    import pandas as pd

    grain_seconds: int | None = None
    if len(bucket_values) >= 2:
        grain_seconds = int(
            (pd.Timestamp(bucket_values[1]) - pd.Timestamp(bucket_values[0])).total_seconds()
        )
    out = dense_df.copy()
    out["expected_span"] = span_seconds
    if grain_seconds is None or data_start is None:
        out["covered_span"] = span_seconds
        out["coverage_ratio"] = 1.0
        out["coverage_status"] = "complete"
        return out
    bucket_start_ts = pd.to_datetime(out["bucket_start"])
    bucket_end_ts = bucket_start_ts + pd.Timedelta(seconds=grain_seconds)
    data_start_ts = pd.Timestamp(data_start)
    covered = (bucket_end_ts - data_start_ts).dt.total_seconds()
    # Clip to [0, span_seconds]: buckets ending at/before data start have no
    # covered span; buckets whose full span is inside data have the full span.
    covered = covered.clip(lower=0, upper=span_seconds).astype("int64")
    out["covered_span"] = covered
    out["coverage_ratio"] = covered / span_seconds
    out["coverage_status"] = "partial"
    out.loc[covered >= span_seconds, "coverage_status"] = "complete"
    return out


def _fixed_grain_seconds_for_coverage(count: int, unit: str) -> int:
    """Convert a grain (count, unit) to total seconds for coverage expected_samples calculation."""
    return count * _FIXED_UNIT_SECONDS.get(unit, 0)
