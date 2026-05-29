"""Pure pandas quality checks for analysis frames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from typing import Any

import pandas as pd

from marivo.analysis.frames.metric import MetricFrame

_FREQ = {"day": "D", "week": "W-MON", "month": "MS", "quarter": "QS"}


def run_metric_checks(frame: MetricFrame) -> list[dict[str, str]]:
    df = frame.to_pandas()
    rows = [_row_count_check(df)]
    rows.extend(_null_ratio_checks(df, frame))
    if frame.meta.semantic_kind in {"time_series", "panel"}:
        rows.append(_time_coverage_check(df, frame))
    if frame.meta.semantic_kind in {"segmented", "panel"}:
        rows.append(_duplicate_keys_check(df, frame))
    return rows


def _result(
    check_id: str,
    check_kind: str,
    status: str,
    severity: str,
    message: str,
    details: dict[str, Any],
) -> dict[str, str]:
    return {
        "check_id": check_id,
        "check_kind": check_kind,
        "status": status,
        "severity": severity,
        "message": message,
        "details_json": json.dumps(details, sort_keys=True, default=str),
    }


def _row_count_check(df: pd.DataFrame) -> dict[str, str]:
    count = len(df)
    severity = "blocking" if count == 0 else "warning" if count < 5 else "ok"
    return _result(
        "row_count",
        "row_count",
        severity,
        severity,
        f"row count is {count}",
        {"row_count": count, "threshold_warning": 5, "threshold_blocking": 0},
    )


def _measure_columns(frame: MetricFrame) -> list[str]:
    measure = frame.meta.measure
    if isinstance(measure.get("field"), str):
        return [str(measure["field"])]
    if isinstance(measure.get("fields"), list):
        return [str(column) for column in measure["fields"]]
    return []


def _null_ratio_checks(df: pd.DataFrame, frame: MetricFrame) -> list[dict[str, str]]:
    rows = []
    denominator = len(df)
    for column in _measure_columns(frame):
        null_count = int(df[column].isna().sum()) if column in df else denominator
        ratio = 0.0 if denominator == 0 else null_count / denominator
        severity = "blocking" if ratio > 0.5 else "warning" if ratio > 0.1 else "ok"
        rows.append(
            _result(
                f"null_ratio:{column}",
                "null_ratio",
                severity,
                severity,
                f"null ratio for {column} is {ratio:.3f}",
                {
                    "column": column,
                    "null_count": null_count,
                    "null_ratio": ratio,
                    "threshold_warning": 0.1,
                    "threshold_blocking": 0.5,
                },
            )
        )
    return rows


def _time_axis(frame: MetricFrame) -> tuple[str, str]:
    axis = frame.meta.axes.get("time", {})
    if isinstance(axis, dict):
        return str(axis.get("field") or axis.get("column") or "time"), str(axis.get("grain", "day"))
    return "time", "day"


def _time_coverage_check(df: pd.DataFrame, frame: MetricFrame) -> dict[str, str]:
    time_col, grain = _time_axis(frame)
    window = frame.meta.window or {}
    start = window.get("start")
    end = window.get("end")
    if start is None or end is None or grain not in _FREQ:
        return _result(
            "time_coverage",
            "time_coverage",
            "warning",
            "warning",
            "time coverage cannot be computed from frame metadata",
            {
                "expected_buckets": 0,
                "observed_buckets": int(df[time_col].nunique()) if time_col in df else 0,
                "coverage_ratio": 0.0,
                "missing_examples": [],
            },
        )
    expected = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq=_FREQ[grain])
    observed = (
        list(pd.to_datetime(df[time_col]).dropna().dt.normalize().unique())
        if time_col in df and len(df)
        else []
    )
    observed_set = {pd.Timestamp(value).normalize() for value in observed}
    missing = [value for value in expected if value.normalize() not in observed_set]
    ratio = 1.0 if len(expected) == 0 else (len(expected) - len(missing)) / len(expected)
    severity = "blocking" if ratio < 0.8 else "warning" if ratio < 0.95 else "ok"
    return _result(
        "time_coverage",
        "time_coverage",
        severity,
        severity,
        f"time coverage ratio is {ratio:.3f}",
        {
            "expected_buckets": len(expected),
            "observed_buckets": len(observed_set),
            "coverage_ratio": ratio,
            "missing_examples": [value.isoformat() for value in missing[:5]],
        },
    )


def _segment_dimensions(frame: MetricFrame) -> list[str]:
    dims = frame.meta.axes.get("dimensions", [])
    return [str(dim["field"]) for dim in dims if isinstance(dim, dict) and "field" in dim]


def _duplicate_keys_check(df: pd.DataFrame, frame: MetricFrame) -> dict[str, str]:
    keys = _segment_dimensions(frame)
    if frame.meta.semantic_kind == "panel":
        time_col, _ = _time_axis(frame)
        keys.append(time_col)
    duplicates = df.duplicated(subset=keys, keep=False) if keys else pd.Series([False] * len(df))
    duplicate_count = int(duplicates.sum())
    severity = "blocking" if duplicate_count else "ok"
    examples = df.loc[duplicates, keys].head(5).to_dict("records") if duplicate_count else []
    return _result(
        "duplicate_keys",
        "duplicate_keys",
        severity,
        severity,
        f"duplicate key row count is {duplicate_count}",
        {"duplicate_count": duplicate_count, "examples": examples},
    )
