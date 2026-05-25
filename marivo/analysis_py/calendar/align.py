from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from marivo.analysis_py.calendar.model import (
    Calendar,
    CalendarEntry,
    CalendarInfo,
    CalendarPolicy,
)
from marivo.analysis_py.errors import AlignmentFailedError, CalendarPolicyError


def align_calendar_frames(
    a: pd.DataFrame,
    b: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
    calendar: Calendar,
    policy: CalendarPolicy,
    session_tz: str,
) -> tuple[pd.DataFrame, CalendarInfo]:
    if policy.align_period == "day":
        raise CalendarPolicyError(
            message="align_period='day' is not supported for calendar alignment",
            details={
                "kind": "CalendarPolicyInvalid",
                "align_period": policy.align_period,
                "mode": policy.mode,
            },
        )

    dates_a = _local_dates(a[time_column], session_tz=session_tz)
    dates_b = _local_dates(b[time_column], session_tz=session_tz)

    _require_single_period(dates_a, policy.align_period, side="a")
    _require_single_period(dates_b, policy.align_period, side="b")

    keys_a = _align_keys(dates_a, calendar=calendar, policy=policy)
    keys_b = _align_keys(dates_b, calendar=calendar, policy=policy)

    _require_unique_keys(keys_a, side="a")
    _require_unique_keys(keys_b, side="b")

    holiday_map = _holiday_map(calendar.holidays)
    adjusted_workdays = {date.fromisoformat(entry.date) for entry in calendar.adjusted_workdays}

    frame_a = pd.DataFrame(
        {
            "_row_id_a": np.arange(len(a)),
            "date_a": dates_a,
            "bucket_start_a": dates_a.map(date.isoformat),
            "current": pd.to_numeric(a[value_column], errors="coerce"),
            "_align_key": keys_a,
        }
    )
    frame_b = pd.DataFrame(
        {
            "_row_id_b": np.arange(len(b)),
            "date_b": dates_b,
            "bucket_start_b": dates_b.map(date.isoformat),
            "baseline": pd.to_numeric(b[value_column], errors="coerce"),
            "_align_key": keys_b,
        }
    )
    frame_a["_has_align_key"] = frame_a["_align_key"].notna()
    frame_b["_has_align_key"] = frame_b["_align_key"].notna()

    baseline_by_key: dict[tuple[object, ...], dict[str, object]] = {}
    for row in frame_b[frame_b["_has_align_key"]].to_dict("records"):
        key = cast("tuple[object, ...]", row["_align_key"])
        baseline_by_key[key] = {
            "_row_id_b": row["_row_id_b"],
            "date_b": row["date_b"],
            "bucket_start_b": row["bucket_start_b"],
            "baseline": row["baseline"],
        }

    rows: list[dict[str, object]] = []
    matched_a_rows: set[int] = set()
    matched_b_rows: set[int] = set()
    fallback_rows = 0

    for row_a in frame_a.to_dict("records"):
        if row_a["_align_key"] is None:
            continue
        key = cast("tuple[object, ...]", row_a["_align_key"])
        baseline = baseline_by_key.get(key)
        if baseline is None:
            continue
        matched_a_rows.add(int(cast("int", row_a["_row_id_a"])))
        matched_b_rows.add(int(cast("int", baseline["_row_id_b"])))
        rows.append(
            {
                "align_key": _json_key(key),
                "align_quality": "exact",
                "bucket_start_a": row_a["bucket_start_a"],
                "bucket_start_b": baseline["bucket_start_b"],
                "current": row_a["current"],
                "baseline": baseline["baseline"],
            }
        )

    if policy.fallback == "nearest_prior_workday":
        current_period_start = _period_start(cast("date", dates_a.iloc[0]), policy.align_period)
        baseline_period_start = _period_start(cast("date", dates_b.iloc[0]), policy.align_period)

        baseline_workdays: list[tuple[date, int, str, object]] = []
        for row_b in frame_b.to_dict("records"):
            if not _is_workday(
                cast("date", row_b["date_b"]),
                holiday_map=holiday_map,
                adjusted_workdays=adjusted_workdays,
            ):
                continue
            baseline_workdays.append(
                (
                    cast("date", row_b["date_b"]),
                    int(cast("int", row_b["_row_id_b"])),
                    cast("str", row_b["bucket_start_b"]),
                    row_b["baseline"],
                )
            )
        baseline_workdays.sort(key=lambda item: (item[0], item[1]))
        baseline_workday_dates = [item[0] for item in baseline_workdays]

        for row_a in frame_a.to_dict("records"):
            row_id_a = int(cast("int", row_a["_row_id_a"]))
            if row_id_a in matched_a_rows:
                continue
            anchor = baseline_period_start + (cast("date", row_a["date_a"]) - current_period_start)
            index = bisect_right(baseline_workday_dates, anchor) - 1
            if index < 0:
                continue
            matched_a_rows.add(row_id_a)
            matched_b_rows.add(baseline_workdays[index][1])
            align_key = (
                _json_key(cast("tuple[object, ...]", row_a["_align_key"]))
                if row_a["_align_key"] is not None
                else _json_key(("fallback_workday", baseline_workdays[index][0].isoformat()))
            )
            rows.append(
                {
                    "align_key": align_key,
                    "align_quality": "fallback",
                    "bucket_start_a": row_a["bucket_start_a"],
                    "bucket_start_b": baseline_workdays[index][2],
                    "current": row_a["current"],
                    "baseline": baseline_workdays[index][3],
                }
            )
            fallback_rows += 1

    result = pd.DataFrame(rows)
    if result.empty:
        result = pd.DataFrame(
            columns=[
                "align_key",
                "align_quality",
                "bucket_start_a",
                "bucket_start_b",
                "current",
                "baseline",
            ]
        )
    result["delta"] = result["current"] - result["baseline"]
    result["pct_change"] = np.where(
        result["baseline"] != 0,
        result["delta"] / result["baseline"],
        np.nan,
    )

    info = CalendarInfo(
        calendar_name=calendar.name,
        calendar_timezone=calendar.timezone,
        session_timezone=session_tz,
        mode=policy.mode,
        align_period=policy.align_period,
        fallback=policy.fallback,
        matched_rows=len(result),
        fallback_rows=fallback_rows,
        dropped_rows_a=int(len(frame_a) - len(matched_a_rows)),
        dropped_rows_b=int(len(frame_b) - len(matched_b_rows)),
    )
    return result.reset_index(drop=True), info


def _local_dates(series: pd.Series, *, session_tz: str) -> pd.Series:
    tz = ZoneInfo(session_tz)
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        parsed = pd.to_datetime(series, errors="coerce")
        _require_no_na_dates(parsed, session_tz=session_tz)
        return parsed.dt.tz_convert(tz).dt.date
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, utc=True, errors="coerce")
        _require_no_na_dates(parsed, session_tz=session_tz)
        return parsed.dt.tz_convert(tz).dt.date
    return series.map(lambda value: _coerce_local_date(value, session_tz=session_tz))


def _coerce_local_date(value: object, *, session_tz: str) -> date:
    tz = ZoneInfo(session_tz)
    if isinstance(value, pd.Timestamp):
        return _timestamp_to_local_date(value, tz)
    if isinstance(value, datetime):
        return _timestamp_to_local_date(pd.Timestamp(value), tz)
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        try:
            parsed_date = date.fromisoformat(stripped)
        except ValueError:
            parsed_date = None
        if parsed_date is not None and parsed_date.isoformat() == stripped:
            return parsed_date
        try:
            parsed_dt = pd.to_datetime(stripped, utc=True, errors="raise")
        except (TypeError, ValueError) as exc:
            raise AlignmentFailedError(
                message=f"failed to parse date value {value!r}",
                details={
                    "kind": "CalendarAlignDateParseFailed",
                    "value": value,
                },
            ) from exc
        return _timestamp_to_local_date(pd.Timestamp(parsed_dt), tz)
    raise AlignmentFailedError(
        message=f"unsupported date value {value!r}",
        details={
            "kind": "CalendarAlignDateParseFailed",
            "value": str(value),
        },
    )


def _period_id(day: date, align_period: str) -> str:
    if align_period == "week":
        iso = day.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"
    if align_period == "month":
        return f"{day.year:04d}-{day.month:02d}"
    if align_period == "quarter":
        quarter = ((day.month - 1) // 3) + 1
        return f"{day.year:04d}-Q{quarter}"
    if align_period == "year":
        return f"{day.year:04d}"
    raise CalendarPolicyError(
        message=f"unsupported align_period {align_period!r}",
        details={"kind": "CalendarPolicyInvalid", "align_period": align_period},
    )


def _period_start(day: date, align_period: str) -> date:
    if align_period == "week":
        return day - timedelta(days=day.isoweekday() - 1)
    if align_period == "month":
        return day.replace(day=1)
    if align_period == "quarter":
        month = ((day.month - 1) // 3) * 3 + 1
        return date(day.year, month, 1)
    if align_period == "year":
        return date(day.year, 1, 1)
    raise CalendarPolicyError(
        message=f"unsupported align_period {align_period!r}",
        details={"kind": "CalendarPolicyInvalid", "align_period": align_period},
    )


def _week_offset(day: date, align_period: str) -> int:
    return (day - _period_start(day, align_period)).days // 7


def _require_single_period(dates: pd.Series, align_period: str, *, side: str) -> None:
    period_ids = {str(_period_id(value, align_period)) for value in dates}
    if len(period_ids) == 1:
        return
    raise AlignmentFailedError(
        message=f"frame '{side}' spans multiple periods for align_period={align_period!r}",
        details={
            "kind": "CalendarAlignFrameSpansMultiplePeriods",
            "side": side,
            "align_period": align_period,
            "period_ids": sorted(period_ids),
        },
    )


def _align_keys(dates: pd.Series, *, calendar: Calendar, policy: CalendarPolicy) -> pd.Series:
    holiday_map = _holiday_map(calendar.holidays)
    adjusted_workdays = {date.fromisoformat(entry.date) for entry in calendar.adjusted_workdays}

    def _key_for(day: date) -> tuple[str, object] | tuple[str, int, int] | None:
        holiday = holiday_map.get(day)
        if policy.mode == "dow_aligned":
            return ("dow", day.isoweekday(), _week_offset(day, policy.align_period))
        if policy.mode == "workday_aligned":
            nth = _nth_workday(
                day,
                align_period=policy.align_period,
                holiday_map=holiday_map,
                adjusted_workdays=adjusted_workdays,
            )
            if nth is None:
                return None
            return ("workday", nth)
        if policy.mode == "holiday_aligned":
            if holiday is not None:
                return ("holiday", holiday)
            return None
        if policy.mode == "holiday_and_dow_aligned":
            if holiday is not None:
                return ("holiday", holiday)
            return ("dow", day.isoweekday(), _week_offset(day, policy.align_period))
        raise CalendarPolicyError(
            message=f"unsupported calendar mode {policy.mode!r}",
            details={"kind": "CalendarPolicyInvalid", "mode": policy.mode},
        )

    return dates.map(_key_for)


def _nth_workday(
    day: date,
    *,
    align_period: str,
    holiday_map: dict[date, str],
    adjusted_workdays: set[date],
) -> int | None:
    if not _is_workday(day, holiday_map=holiday_map, adjusted_workdays=adjusted_workdays):
        return None

    period_start = _period_start(day, align_period)
    cursor = period_start
    nth = 0
    while cursor <= day:
        if _is_workday(cursor, holiday_map=holiday_map, adjusted_workdays=adjusted_workdays):
            nth += 1
        cursor += timedelta(days=1)
    return nth


def _is_workday(day: date, *, holiday_map: dict[date, str], adjusted_workdays: set[date]) -> bool:
    if day in adjusted_workdays:
        return True
    if day.isoweekday() >= 6:
        return False
    return day not in holiday_map


def _holiday_map(entries: Sequence[CalendarEntry]) -> dict[date, str]:
    out: dict[date, str] = {}
    for entry in entries:
        entry_date = date.fromisoformat(entry.date)
        group_id = entry.group_id
        name = entry.name
        out[entry_date] = group_id or name or entry_date.isoformat()
    return out


def _require_unique_keys(keys: pd.Series, *, side: str) -> None:
    non_null = keys.dropna()
    duplicated = non_null[non_null.duplicated(keep=False)]
    if duplicated.empty:
        return
    duplicates = sorted({_json_key(tuple(value)) for value in duplicated.tolist()})
    raise AlignmentFailedError(
        message=f"frame '{side}' has duplicate calendar align keys",
        details={
            "kind": "CalendarAlignKeyNotUnique",
            "side": side,
            "duplicate_keys": duplicates,
        },
    )


def _json_key(value: tuple[object, ...]) -> str:
    return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))


def _require_no_na_dates(series: pd.Series, *, session_tz: str) -> None:
    if not series.isna().any():
        return
    raise AlignmentFailedError(
        message=f"failed to parse date values with session timezone {session_tz!r}",
        details={"kind": "CalendarAlignDateParseFailed", "session_timezone": session_tz},
    )


def _timestamp_to_local_date(ts: pd.Timestamp, tz: ZoneInfo) -> date:
    localized = ts.tz_localize("UTC") if ts.tzinfo is None else ts
    return cast("date", localized.tz_convert(tz).date())
