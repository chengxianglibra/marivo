"""Per-objective candidate scorers for discover.

Each scorer returns a list of row dicts compatible with build_union_columns.
Scorers do not persist; the caller drives validation and persistence.
"""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.types import is_datetime64_any_dtype


def _detect_time_columns(df: pd.DataFrame) -> list[str]:
    return [str(col) for col in df.columns if is_datetime64_any_dtype(df[col])]


def _row_window(df: pd.DataFrame, row_index: int, time_columns: list[str]) -> dict[str, str] | None:
    """Build a point-anomaly window from the row's time column.

    Returns None when there is no usable time column or the row's timestamp is
    missing/unparseable, so the candidate carries no window rather than a
    fabricated ``now()`` value.
    """
    if not time_columns:
        return None
    time_col = time_columns[0]
    raw = df.iloc[row_index][time_col]
    if pd.isna(raw):
        return None
    try:
        iso = pd.Timestamp(raw).isoformat()
    except (ValueError, TypeError):
        return None
    return {"start": iso, "end": iso}


def _scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def score_point_anomalies(
    source_df: pd.DataFrame,
    *,
    source_ref: str,
    value_column: str,
    threshold: float,
    time_column: str | None = None,
) -> list[dict[str, Any]]:
    series = source_df[value_column]
    non_null = series.dropna()
    mean: float | None = None
    if len(non_null) < 2:
        scores = np.zeros(len(source_df))
    else:
        std = float(non_null.std(ddof=0))
        if std == 0:
            scores = np.zeros(len(source_df))
        else:
            mean = float(non_null.mean())
            scores = ((series - mean) / std).fillna(0).to_numpy()

    time_columns = [time_column] if time_column else _detect_time_columns(source_df)
    key_columns = [
        col for col in source_df.columns if col != value_column and col not in time_columns
    ]
    baseline_window: dict[str, str] | None = None
    if time_columns:
        ts_col = source_df[time_columns[0]].dropna()
        if not ts_col.empty:
            baseline_window = {
                "start": pd.Timestamp(ts_col.min()).isoformat(),
                "end": pd.Timestamp(ts_col.max()).isoformat(),
            }
    rows: list[dict[str, Any]] = []
    for row_index, is_candidate in enumerate(np.abs(scores) >= threshold):
        if not bool(is_candidate):
            continue
        row = source_df.iloc[row_index]
        score = float(scores[row_index])
        keys = {str(col): _scalar(row[col]) for col in key_columns if pd.notna(row[col])}
        window = _row_window(source_df, row_index, time_columns)
        observed_value = float(row[value_column]) if pd.notna(row[value_column]) else None
        rows.append(
            {
                "item_id": f"cand_{row_index}",
                "score": score,
                "observed_value": observed_value,
                "baseline_value": mean,
                "delta": (observed_value - mean)
                if observed_value is not None and mean is not None
                else None,
                "direction": "high" if score > 0 else "low",
                "reason_codes": [f"abs_z={abs(score):.2f}"],
                "source_refs": [f"{source_ref}#row={row_index}"],
                "keys": keys if keys else {},
                "window": window,
                "baseline_window": baseline_window,
            }
        )
    return rows


def score_period_shifts(
    source_df: pd.DataFrame,
    *,
    source_ref: str,
    bucket_column: str,
    value_column: str,
    threshold: float,
    group_columns: list[str],
) -> list[dict[str, Any]]:
    if group_columns:
        rows: list[dict[str, Any]] = []
        sort_cols = [*group_columns, bucket_column]
        for group_keys, group_df in source_df.sort_values(sort_cols).groupby(
            group_columns, dropna=False
        ):
            keys = _group_keys(group_columns, group_keys)
            rows.extend(
                _segments_for_series(
                    group_df.reset_index(drop=True),
                    source_ref=source_ref,
                    bucket_column=bucket_column,
                    value_column=value_column,
                    threshold=threshold,
                    keys=keys,
                )
            )
        return rows
    return _segments_for_series(
        source_df.sort_values(bucket_column).reset_index(drop=True),
        source_ref=source_ref,
        bucket_column=bucket_column,
        value_column=value_column,
        threshold=threshold,
        keys={},
    )


def score_point_anomalies_seasonal_robust(
    source_df: pd.DataFrame,
    *,
    source_ref: str,
    value_column: str,
    threshold: float,
    time_column: str | None = None,
) -> list[dict[str, Any]]:
    """Score point anomalies with a robust, day-of-week-stratified baseline.

    Median/MAD (mean-absolute-deviation fallback) within each day-of-week
    group resists an anomaly contaminating the baseline (masking), and
    stratifying by day-of-week avoids flagging weekly seasonality (e.g.
    weekend dips) that a global z-score reports as anomalies. Falls back to a
    global robust baseline when no datetime time column is available.
    """
    series = source_df[value_column].astype(float)
    has_time = (
        bool(time_column)
        and time_column in source_df.columns
        and is_datetime64_any_dtype(source_df[time_column])
    )
    if has_time:
        dow = source_df[time_column].dt.dayofweek
        median = series.groupby(dow, dropna=False).transform("median")
        dev = (series - median).abs()
        mad = dev.groupby(dow, dropna=False).transform("median")
        mean_ad = dev.groupby(dow, dropna=False).transform("mean")
    else:
        median_val = float(series.median())
        median = pd.Series(np.full(len(series), median_val), index=series.index)
        dev = (series - median).abs()
        mad_val = float(dev.median())
        mean_ad_val = float(dev.mean())
        mad = pd.Series(np.full(len(dev), mad_val), index=dev.index)
        mean_ad = pd.Series(np.full(len(dev), mean_ad_val), index=dev.index)

    median_arr = median.to_numpy()
    mad_arr = mad.to_numpy()
    scale = np.where(mad_arr > 0, mad_arr, mean_ad.to_numpy())
    scale_label = np.where(mad_arr > 0, "mad", "mean_ad")
    with np.errstate(divide="ignore", invalid="ignore"):
        z_values = np.where(
            scale > 0,
            (series.to_numpy() - median_arr) / (1.4826 * scale),
            0.0,
        )

    time_columns = [time_column] if time_column else _detect_time_columns(source_df)
    key_columns = [
        col for col in source_df.columns if col != value_column and col not in time_columns
    ]
    baseline_window: dict[str, str] | None = None
    if time_columns:
        ts_col = source_df[time_columns[0]].dropna()
        if not ts_col.empty:
            baseline_window = {
                "start": pd.Timestamp(ts_col.min()).isoformat(),
                "end": pd.Timestamp(ts_col.max()).isoformat(),
            }
    rows: list[dict[str, Any]] = []
    for row_index, is_candidate in enumerate(np.abs(z_values) >= threshold):
        if not bool(is_candidate):
            continue
        row = source_df.iloc[row_index]
        z = float(z_values[row_index])
        keys = {str(col): _scalar(row[col]) for col in key_columns if pd.notna(row[col])}
        window = _row_window(source_df, row_index, time_columns)
        observed = float(row[value_column]) if pd.notna(row[value_column]) else None
        baseline = float(median_arr[row_index])
        rows.append(
            {
                "item_id": "",  # assigned globally below
                "score": z,
                "observed_value": observed,
                "baseline_value": baseline,
                "delta": (observed - baseline) if observed is not None else None,
                "direction": "high" if z > 0 else "low",
                "reason_codes": [
                    f"robust_z={z:.2f}",
                    f"{scale_label[row_index]}={float(scale[row_index]):.2f}",
                ],
                "source_refs": [f"{source_ref}#row={row_index}"],
                "keys": keys if keys else {},
                "window": window,
                "baseline_window": baseline_window,
            }
        )
    for index, entry in enumerate(rows):
        entry["item_id"] = f"cand_{index}"
    return rows


def _segments_for_series(
    df: pd.DataFrame,
    *,
    source_ref: str,
    bucket_column: str,
    value_column: str,
    threshold: float,
    keys: dict[str, Any],
) -> list[dict[str, Any]]:
    n = len(df)
    if n < 4:
        return []
    window_size = max(7, n // 10)
    series = df[value_column].astype(float)
    window_means = series.rolling(window_size, min_periods=window_size).mean()
    valid = window_means.dropna()
    if valid.empty:
        return []
    overall_mean = float(valid.mean())
    overall_std = float(valid.std(ddof=0))
    if overall_std == 0 or not np.isfinite(overall_std):
        return []
    z = (window_means - overall_mean) / overall_std
    hits = (z.abs() >= threshold).fillna(False)

    segments: list[tuple[int, int]] = []
    in_segment = False
    seg_start = 0
    for idx in range(n):
        is_hit = bool(hits.iloc[idx])
        if is_hit and not in_segment:
            seg_start = idx
            in_segment = True
        elif not is_hit and in_segment:
            in_segment = False
            segments.append((seg_start, idx - 1))
    if in_segment:
        segments.append((seg_start, n - 1))

    rows: list[dict[str, Any]] = []
    for seg_idx, (start, end) in enumerate(segments):
        seg_len = end - start + 1
        baseline_end = start - 1
        baseline_start = max(0, baseline_end - seg_len + 1)
        if baseline_end < 0:
            continue
        # Score on the segment's peak |z| (not the segment-end z), so a
        # strong-then-decaying shift is not underestimated. Direction follows
        # the sign at that peak. Mirrors interesting_windows' max_abs_z.
        seg_z = z.iloc[start : end + 1].to_numpy()
        peak_rel = int(np.argmax(np.abs(seg_z)))
        peak_z = float(seg_z[peak_rel])
        window = {
            "start": pd.Timestamp(df.iloc[start][bucket_column]).isoformat(),
            "end": pd.Timestamp(df.iloc[end][bucket_column]).isoformat(),
        }
        baseline_window = {
            "start": pd.Timestamp(df.iloc[baseline_start][bucket_column]).isoformat(),
            "end": pd.Timestamp(df.iloc[baseline_end][bucket_column]).isoformat(),
        }
        rows.append(
            {
                "item_id": f"shift_{seg_idx}",
                "score": peak_z,
                "direction": "high" if peak_z >= 0 else "low",
                "reason_codes": [
                    f"window_size={window_size}",
                    f"max_abs_z={abs(peak_z):.2f}",
                ],
                "source_refs": [source_ref],
                "keys": keys,
                "window": window,
                "baseline_window": baseline_window,
            }
        )
    return rows


def _group_keys(group_columns: list[str], group_keys: Any) -> dict[str, Any]:
    if not isinstance(group_keys, tuple):
        group_keys = (group_keys,)
    return {
        col: _scalar(value)
        for col, value in zip(group_columns, group_keys, strict=True)
        if pd.notna(value)
    }


def score_driver_axes(
    source_df: pd.DataFrame,
    *,
    source_ref: str,
    value_column: str,
    axes: list[str],
    bucket_column: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    if bucket_column and bucket_column in source_df.columns:
        # Sum across time buckets so contributions reflect axis totals.
        df = source_df.drop(columns=[bucket_column])
    else:
        df = source_df

    scored: list[tuple[str, float, list[str]]] = []
    for axis in axes:
        if axis not in df.columns:
            continue
        grouped = df.groupby(axis, dropna=False)[value_column].sum()
        contributions = grouped.abs().sort_values(ascending=False)
        total = float(contributions.sum())
        if total == 0 or not np.isfinite(total):
            continue
        cumulative = 0.0
        k = 0
        for value in contributions:
            cumulative += float(value)
            k += 1
            if cumulative / total >= 0.5:
                break
        cardinality = int(grouped.size)
        score = 1.0 / (k + cardinality / 1000.0)
        top_share = cumulative / total
        codes = [
            f"top_k_share={top_share:.3f}",
            f"axis_cardinality={cardinality}",
            f"k={k}",
        ]
        scored.append((axis, score, codes))

    scored.sort(key=lambda entry: entry[1], reverse=True)
    if limit is not None:
        scored = scored[:limit]

    rows: list[dict[str, Any]] = []
    for index, (axis, score, codes) in enumerate(scored):
        rows.append(
            {
                "item_id": f"axis_{index}",
                "score": score,
                "axis": axis,
                "reason_codes": codes,
                "source_refs": [source_ref],
            }
        )
    return rows


_SLICE_MAX_GROUPS = 50_000


def score_interesting_slices(
    source_df: pd.DataFrame,
    *,
    source_ref: str,
    value_column: str,
    axes: list[str],
    threshold: float,
    limit: int | None,
    max_groups: int = _SLICE_MAX_GROUPS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Score dimension slices by how far their value means deviate from the mean.

    Slice means (per subset) are z-scored against the row-level value
    distribution, so group statistics and baseline share one caliber and one
    threshold meaning (|z| >= threshold) for both MetricFrame and DeltaFrame
    inputs. Using the group mean (not the group sum) keeps the score
    size-invariant, so a small shifted slice is not buried by large average
    ones. Axis-pair subsets whose cardinality product exceeds ``max_groups``
    are skipped and recorded in the returned skip log rather than materializing
    an explosive groupby.

    Returns ``(rows, skipped)`` where ``skipped`` lists the subsets that hit
    the guard with their estimated cardinality and reason.
    """
    available_axes = [axis for axis in axes if axis in source_df.columns]
    if not available_axes:
        return [], []

    series = source_df[value_column].astype(float)
    std = float(series.std(ddof=0))
    if std == 0 or not np.isfinite(std):
        return [], []
    mean = float(series.mean())

    candidates: list[tuple[float, dict[str, Any]]] = []
    axis_subsets: list[list[str]] = [[axis] for axis in available_axes]
    if len(available_axes) >= 2:
        for i, a in enumerate(available_axes):
            for b in available_axes[i + 1 :]:
                axis_subsets.append([a, b])

    skipped: list[dict[str, Any]] = []
    for subset in axis_subsets:
        cardinality = 1
        for axis in subset:
            cardinality *= int(source_df[axis].nunique(dropna=False))
        if cardinality > max_groups:
            skipped.append(
                {
                    "axes": list(subset),
                    "cardinality": cardinality,
                    "reason": f"cardinality {cardinality} exceeds max_groups {max_groups}",
                }
            )
            continue
        grouped = source_df.groupby(subset, dropna=False)[value_column].mean().reset_index()
        values = grouped[value_column].to_numpy(dtype=float)
        scores = np.abs((values - mean) / std)
        for pos in np.nonzero(scores >= threshold)[0]:
            row = grouped.iloc[pos]
            selector = {axis: _scalar(row[axis]) for axis in subset if pd.notna(row[axis])}
            candidates.append((float(scores[pos]), selector))

    candidates.sort(key=lambda entry: entry[0], reverse=True)
    if limit is not None:
        candidates = candidates[:limit]

    rows: list[dict[str, Any]] = []
    for index, (score, selector) in enumerate(candidates):
        rows.append(
            {
                "item_id": f"slice_{index}",
                "score": score,
                "selector": selector,
                "keys": dict(selector),
                "reason_codes": [
                    f"abs_z={score:.2f}",
                    f"axes={','.join(selector.keys())}",
                ],
                "source_refs": [source_ref],
            }
        )
    return rows, skipped


def score_interesting_windows(
    source_df: pd.DataFrame,
    *,
    source_ref: str,
    bucket_column: str,
    value_column: str,
    threshold: float,
    group_columns: list[str],
) -> list[dict[str, Any]]:
    if group_columns:
        rows: list[dict[str, Any]] = []
        sort_cols = [*group_columns, bucket_column]
        for group_keys, group_df in source_df.sort_values(sort_cols).groupby(
            group_columns, dropna=False
        ):
            keys = _group_keys(group_columns, group_keys)
            rows.extend(
                _windows_for_series(
                    group_df.reset_index(drop=True),
                    source_ref=source_ref,
                    bucket_column=bucket_column,
                    value_column=value_column,
                    threshold=threshold,
                    keys=keys,
                )
            )
        return rows
    return _windows_for_series(
        source_df.sort_values(bucket_column).reset_index(drop=True),
        source_ref=source_ref,
        bucket_column=bucket_column,
        value_column=value_column,
        threshold=threshold,
        keys={},
    )


def _windows_for_series(
    df: pd.DataFrame,
    *,
    source_ref: str,
    bucket_column: str,
    value_column: str,
    threshold: float,
    keys: dict[str, Any],
) -> list[dict[str, Any]]:
    n = len(df)
    if n < 4:
        return []
    series = df[value_column].astype(float)
    overall_mean = float(series.mean())
    overall_std = float(series.std(ddof=0))
    if overall_std == 0 or not np.isfinite(overall_std):
        return []
    z = (series - overall_mean) / overall_std
    hits = (z.abs() >= threshold).fillna(False)

    rows: list[dict[str, Any]] = []
    in_segment = False
    seg_start = 0
    seg_max_z = 0.0
    seg_index = 0
    for idx in range(n):
        if hits.iloc[idx]:
            if not in_segment:
                seg_start = idx
                seg_max_z = float(abs(z.iloc[idx]))
                in_segment = True
            else:
                seg_max_z = max(seg_max_z, float(abs(z.iloc[idx])))
        elif in_segment:
            rows.append(
                _window_row(
                    df,
                    bucket_column,
                    seg_start,
                    idx - 1,
                    seg_max_z,
                    seg_index,
                    source_ref,
                    keys,
                )
            )
            seg_index += 1
            in_segment = False
    if in_segment:
        rows.append(
            _window_row(
                df,
                bucket_column,
                seg_start,
                n - 1,
                seg_max_z,
                seg_index,
                source_ref,
                keys,
            )
        )
    return rows


def _window_row(
    df: pd.DataFrame,
    bucket_column: str,
    start: int,
    end: int,
    max_z: float,
    seg_index: int,
    source_ref: str,
    keys: dict[str, Any],
) -> dict[str, Any]:
    return {
        "item_id": f"window_{seg_index}",
        "score": max_z,
        "reason_codes": [f"max_abs_z={max_z:.2f}"],
        "source_refs": [source_ref],
        "keys": keys,
        "window": {
            "start": pd.Timestamp(df.iloc[start][bucket_column]).isoformat(),
            "end": pd.Timestamp(df.iloc[end][bucket_column]).isoformat(),
        },
    }


def score_cross_sectional_outliers(
    source_df: pd.DataFrame,
    *,
    source_ref: str,
    value_column: str,
    segment_columns: list[str],
    bucket_column: str | None,
    threshold: float,
    peer_scope: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if bucket_column and bucket_column in source_df.columns:
        for bucket_value, bucket_df in source_df.groupby(bucket_column, dropna=False):
            rows.extend(
                _outliers_in_slice(
                    bucket_df.reset_index(drop=True),
                    source_ref=source_ref,
                    value_column=value_column,
                    segment_columns=segment_columns,
                    threshold=threshold,
                    peer_scope=peer_scope,
                    bucket_value=bucket_value,
                )
            )
    else:
        rows.extend(
            _outliers_in_slice(
                source_df.reset_index(drop=True),
                source_ref=source_ref,
                value_column=value_column,
                segment_columns=segment_columns,
                threshold=threshold,
                peer_scope=peer_scope,
                bucket_value=None,
            )
        )
    # item_id must be unique across the CandidateSet; the per-slice
    # outlier_{len(rows)} resets each bucket/peer group, so reassign a global
    # ordinal.
    for index, row in enumerate(rows):
        row["item_id"] = f"outlier_{index}"
    return rows


def _outliers_in_slice(
    df: pd.DataFrame,
    *,
    source_ref: str,
    value_column: str,
    segment_columns: list[str],
    threshold: float,
    peer_scope: list[str],
    bucket_value: Any,
) -> list[dict[str, Any]]:
    if df.empty:
        return []
    if peer_scope:
        # Compare segments against their peers: compute median/MAD within each
        # peer_scope group (overlaid on the time bucket) instead of across all
        # segments, so cross-peer magnitude differences do not dominate. The
        # dispatch layer validates that peer_scope axes are materialized, so a
        # missing column here is a loud KeyError rather than a silent fallback.
        rows: list[dict[str, Any]] = []
        for _, peer_df in df.groupby(peer_scope, dropna=False):
            rows.extend(
                _outliers_in_peer_group(
                    peer_df.reset_index(drop=True),
                    source_ref=source_ref,
                    value_column=value_column,
                    segment_columns=segment_columns,
                    threshold=threshold,
                    peer_scope=peer_scope,
                    bucket_value=bucket_value,
                )
            )
        return rows
    return _outliers_in_peer_group(
        df,
        source_ref=source_ref,
        value_column=value_column,
        segment_columns=segment_columns,
        threshold=threshold,
        peer_scope=peer_scope,
        bucket_value=bucket_value,
    )


def _outliers_in_peer_group(
    df: pd.DataFrame,
    *,
    source_ref: str,
    value_column: str,
    segment_columns: list[str],
    threshold: float,
    peer_scope: list[str],
    bucket_value: Any,
) -> list[dict[str, Any]]:
    if df.empty:
        return []
    series = df[value_column].astype(float)
    median = float(np.median(series.dropna()))
    deviations = (series - median).abs()
    mad = float(np.median(deviations.dropna()))
    if mad == 0 or not np.isfinite(mad):
        # MAD collapses to zero when more than half of the slice shares the
        # median exactly. Fall back to mean absolute deviation so a lone
        # extreme value still surfaces.
        mean_ad = float(deviations.dropna().mean()) if not deviations.dropna().empty else 0.0
        if mean_ad == 0 or not np.isfinite(mean_ad):
            return []
        scale = mean_ad
        scale_label = "mean_ad"
    else:
        scale = mad
        scale_label = "mad"
    robust_z = (series - median) / (1.4826 * scale)
    hits = robust_z.abs() >= threshold
    rows: list[dict[str, Any]] = []
    for index, hit in enumerate(hits.fillna(False)):
        if not hit:
            continue
        segment_keys = {
            col: _scalar(df.iloc[index][col])
            for col in segment_columns
            if pd.notna(df.iloc[index][col])
        }
        if bucket_value is not None and pd.notna(bucket_value):
            segment_keys["bucket"] = _scalar(bucket_value)
        z = float(robust_z.iloc[index])
        rows.append(
            {
                "item_id": "",  # assigned globally by score_cross_sectional_outliers
                "score": z,
                "direction": "high" if z > 0 else "low",
                "reason_codes": [f"robust_z={z:.2f}", f"{scale_label}={scale:.2f}"],
                "source_refs": [source_ref],
                "keys": segment_keys,
                "peer_scope": list(peer_scope),
            }
        )
    return rows
