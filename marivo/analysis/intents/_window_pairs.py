"""Window-bucket enumeration and ordinal pairing primitives.

Shared by intents that need to align two time-indexed frames by ordinal
bucket position (compare, hypothesis_test). The functions here enumerate the
expected bucket timestamps for a frame from its window metadata, build
grain-keyed string keys for matching observed rows to ordinal slots, and
project observed values into key-indexed maps.

Names keep a leading underscore to signal that the API is internal to
``marivo.analysis.intents`` even though it crosses module boundaries.
"""

from __future__ import annotations

import calendar as calendar_lib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, cast

import pandas as pd

from marivo.analysis.errors import AlignmentFailedError
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.windows.grain import Grain as _Grain
from marivo.analysis.windows.grain import normalize_grain as _normalize_grain
from marivo.analysis.windows.spec import is_date_only


def _not_nan(value: object) -> bool:
    """Return True if *value* is neither None nor NaN."""
    if value is None:
        return False
    return not pd.isna(cast("Any", value))


@dataclass
class _OrdinalPair:
    """One ordinal slot in a window-bucket alignment walk."""

    ordinal: int
    a_bucket: object
    b_bucket: object
    a_value: object
    b_value: object
    a_present: bool
    b_present: bool


_WINDOW_BUCKET_CAP = 100_000


def _panel_grain(frame: MetricFrame) -> str | None:
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        if axis.get("role") != "time":
            continue
        grain = axis.get("grain")
        if isinstance(grain, str) and grain:
            return grain
    return None


def _panel_grains(a: MetricFrame, b: MetricFrame) -> tuple[str | None, str | None]:
    return _panel_grain(a), _panel_grain(b)


def _parse_window_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise AlignmentFailedError(
            message=f"window_bucket alignment requires window.{field}",
            details={"kind": "WindowBucketWindowMissing", "field": field},
        )
    if is_date_only(value):
        return datetime.combine(date.fromisoformat(value), time.min)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise AlignmentFailedError(
            message=f"window_bucket alignment requires valid ISO window.{field}",
            details={"kind": "WindowBucketWindowInvalid", "field": field, "value": value},
        ) from exc


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    year = index // 12
    month = index % 12 + 1
    day = min(value.day, calendar_lib.monthrange(year, month)[1])
    return date(year, month, day)


def _truncate_bucket_date(value: date, *, grain: str) -> date:
    if grain == "day":
        return value
    if grain == "week":
        return value - timedelta(days=value.weekday())
    if grain == "month":
        return value.replace(day=1)
    if grain == "quarter":
        month = ((value.month - 1) // 3) * 3 + 1
        return value.replace(month=month, day=1)
    if grain == "year":
        return value.replace(month=1, day=1)
    raise AlignmentFailedError(
        message=f"window_bucket alignment does not support grain {grain!r}",
        details={"kind": "WindowBucketUnsupportedGrain", "grain": grain},
    )


def _advance_bucket_date(value: date, *, grain: str) -> date:
    if grain == "day":
        return value + timedelta(days=1)
    if grain == "week":
        return value + timedelta(weeks=1)
    if grain == "month":
        return _add_months(value, 1)
    if grain == "quarter":
        return _add_months(value, 3)
    if grain == "year":
        return value.replace(year=value.year + 1)
    raise AlignmentFailedError(
        message=f"window_bucket alignment does not support grain {grain!r}",
        details={"kind": "WindowBucketUnsupportedGrain", "grain": grain},
    )


def _grain_is_subday_token(grain: str) -> bool:
    try:
        return _normalize_grain(grain).is_subday  # type: ignore[union-attr]
    except (ValueError, TypeError):
        return False


def _truncate_bucket_datetime(value: datetime, *, grain: _Grain) -> datetime:
    width = grain.width_seconds()
    day_start = value.replace(hour=0, minute=0, second=0, microsecond=0)
    offset = int((value - day_start).total_seconds()) // width * width
    return day_start + timedelta(seconds=offset)


def _advance_bucket_datetime(value: datetime, *, grain: _Grain) -> datetime:
    return value + timedelta(seconds=grain.width_seconds())


def _bucket_key(value: object, *, grain: str) -> str:
    if value is None or pd.isna(cast("Any", value)):
        return ""
    timestamp = pd.Timestamp(cast("Any", value))
    if _grain_is_subday_token(grain):
        normalized = cast("_Grain", _normalize_grain(grain))
        bucketed = _truncate_bucket_datetime(timestamp.to_pydatetime(), grain=normalized)
        return bucketed.strftime("%Y-%m-%dT%H:%M:%S")
    bucket_date = _truncate_bucket_date(timestamp.date(), grain=grain)
    return bucket_date.isoformat()


def _window_bucket_values(frame: MetricFrame) -> list[object]:
    grain = _panel_grain(frame)
    window = frame.meta.window
    if not isinstance(window, dict) or not isinstance(window.get("start"), str):
        raise AlignmentFailedError(
            message=(
                "window_bucket ordinal alignment requires metric frame window metadata "
                "when bucket_start values do not overlap"
            ),
            details={"kind": "WindowBucketWindowMissing", "frame_ref": frame.ref},
        )
    if not isinstance(window.get("end"), str):
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires window.end metadata",
            details={"kind": "WindowBucketWindowMissing", "frame_ref": frame.ref},
        )
    grain_ok = isinstance(grain, str) and (
        grain in {"hour", "day", "week", "month", "quarter", "year"}
        or _grain_is_subday_token(grain)
    )
    if not grain_ok:
        raise AlignmentFailedError(
            message=("window_bucket ordinal alignment requires a calendar or sub-day grain"),
            details={"kind": "WindowBucketGrainMissing", "frame_ref": frame.ref, "grain": grain},
        )
    assert isinstance(grain, str)  # narrowed by grain_ok guard

    start_raw = window["start"]
    end_raw = window["end"]
    if _grain_is_subday_token(grain):
        normalized = cast("_Grain", _normalize_grain(grain))
        current_dt = _truncate_bucket_datetime(
            _parse_window_datetime(start_raw, field="start"), grain=normalized
        )
        if is_date_only(end_raw):
            stop = datetime.combine(date.fromisoformat(end_raw), time.min)
        else:
            stop = _parse_window_datetime(end_raw, field="end")
        buckets: list[object] = []
        while current_dt < stop:
            if len(buckets) >= _WINDOW_BUCKET_CAP:
                raise AlignmentFailedError(
                    message=(
                        "window_bucket ordinal alignment would exceed "
                        f"{_WINDOW_BUCKET_CAP} buckets; coarsen the grain or shrink the window"
                    ),
                    details={
                        "kind": "WindowBucketCapExceeded",
                        "frame_ref": frame.ref,
                        "grain": grain,
                    },
                )
            buckets.append(pd.Timestamp(current_dt))
            current_dt = _advance_bucket_datetime(current_dt, grain=normalized)
        return buckets
    if grain == "hour":
        current = _parse_window_datetime(start_raw, field="start").replace(
            minute=0, second=0, microsecond=0
        )
        if is_date_only(end_raw):
            stop_exclusive = datetime.combine(date.fromisoformat(end_raw), time.min)
            values: list[object] = []
            while current < stop_exclusive:
                values.append(pd.Timestamp(current))
                current += timedelta(hours=1)
            return values
        stop = _parse_window_datetime(end_raw, field="end").replace(
            minute=0, second=0, microsecond=0
        )
        values = []
        while current < stop:
            values.append(pd.Timestamp(current))
            current += timedelta(hours=1)
        return values

    current_date = _truncate_bucket_date(
        _parse_window_datetime(start_raw, field="start").date(), grain=grain
    )
    stop_date = _truncate_bucket_date(
        _parse_window_datetime(end_raw, field="end").date(), grain=grain
    )
    values = []
    while current_date < stop_date:
        values.append(current_date)
        current_date = _advance_bucket_date(current_date, grain=grain)
    return values


def _prepared_value_map(
    df: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
    grain: str,
) -> dict[str, tuple[object, object]]:
    if df.empty:
        return {}
    keys = df[time_column].map(lambda value: _bucket_key(value, grain=grain))
    if keys.duplicated().any():
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires unique bucket_start values",
            details={"kind": "WindowBucketDuplicateBuckets"},
        )
    return {
        str(key): (row[time_column], row[value_column])
        for key, (_, row) in zip(keys, df.iterrows(), strict=True)
        if key
    }


def _walk_ordinal_pairs(
    a_values: dict[str, tuple[object, object]],
    b_values: dict[str, tuple[object, object]],
    *,
    grain: str,
    frame_a: MetricFrame,
    frame_b: MetricFrame,
) -> Iterator[_OrdinalPair]:
    """Walk two window-bucket sequences in lockstep by ordinal position.

    Yields one :class:`_OrdinalPair` per ordinal index up to the longer
    bucket sequence.  Callers project the fields they need (compare emits
    every ordinal with NaN gaps; test filters to only present-on-both-sides
    rows).
    """
    a_buckets = _window_bucket_values(frame_a)
    b_buckets = _window_bucket_values(frame_b)
    for ordinal in range(max(len(a_buckets), len(b_buckets))):
        a_bucket = a_buckets[ordinal] if ordinal < len(a_buckets) else None
        b_bucket = b_buckets[ordinal] if ordinal < len(b_buckets) else None
        a_key = _bucket_key(a_bucket, grain=grain) if _not_nan(a_bucket) else ""
        b_key = _bucket_key(b_bucket, grain=grain) if _not_nan(b_bucket) else ""
        a_entry = a_values.get(a_key) if a_key else None
        b_entry = b_values.get(b_key) if b_key else None
        a_value = a_entry[1] if a_entry is not None else None
        b_value = b_entry[1] if b_entry is not None else None
        yield _OrdinalPair(
            ordinal=ordinal,
            a_bucket=a_bucket if _not_nan(a_bucket) else pd.NaT,
            b_bucket=b_bucket if _not_nan(b_bucket) else pd.NaT,
            a_value=a_value,
            b_value=b_value,
            a_present=a_entry is not None,
            b_present=b_entry is not None,
        )
