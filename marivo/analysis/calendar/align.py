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

from marivo.analysis.calendar.model import (
    Calendar,
    CalendarEntry,
    CalendarInfo,
    CalendarPolicy,
)
from marivo.analysis.delta_math import compute_delta_columns
from marivo.analysis.errors import AlignmentFailedError, CalendarPolicyError


def align_calendar_frames(
    a: pd.DataFrame,
    b: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
    calendar: Calendar,
    policy: CalendarPolicy,
    report_tz: str,
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

    dates_a = _local_dates(a[time_column], report_tz=report_tz)
    dates_b = _local_dates(b[time_column], report_tz=report_tz)

    period_pairing = _period_pairing(dates_a, dates_b, policy.align_period)

    keys_a = _align_keys(
        dates_a,
        calendar=calendar,
        policy=policy,
        period_ordinals_by_date=period_pairing.current_ordinals_by_date,
    )
    keys_b = _align_keys(
        dates_b,
        calendar=calendar,
        policy=policy,
        period_ordinals_by_date=period_pairing.baseline_ordinals_by_date,
    )

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
                "presence_status": "matched",
                "bucket_start_a": row_a["bucket_start_a"],
                "bucket_start_b": baseline["bucket_start_b"],
                "current": row_a["current"],
                "baseline": baseline["baseline"],
            }
        )

    if policy.fallback == "nearest_prior_workday":
        baseline_workdays_by_ordinal: dict[int, list[tuple[date, int, str, object]]] = {}
        for row_b in frame_b.to_dict("records"):
            baseline_day = cast("date", row_b["date_b"])
            if not _is_workday(
                baseline_day,
                holiday_map=holiday_map,
                adjusted_workdays=adjusted_workdays,
            ):
                continue
            period_ordinal = period_pairing.baseline_ordinals_by_date[baseline_day]
            baseline_workdays_by_ordinal.setdefault(period_ordinal, []).append(
                (
                    baseline_day,
                    int(cast("int", row_b["_row_id_b"])),
                    cast("str", row_b["bucket_start_b"]),
                    row_b["baseline"],
                )
            )
        for baseline_workdays in baseline_workdays_by_ordinal.values():
            baseline_workdays.sort(key=lambda item: (item[0], item[1]))

        for row_a in frame_a.to_dict("records"):
            row_id_a = int(cast("int", row_a["_row_id_a"]))
            if row_id_a in matched_a_rows:
                continue
            current_day = cast("date", row_a["date_a"])
            period_ordinal = period_pairing.current_ordinals_by_date[current_day]
            current_period_start = period_pairing.current_starts_by_ordinal[period_ordinal]
            baseline_period_start = period_pairing.baseline_starts_by_ordinal[period_ordinal]
            anchor = baseline_period_start + (current_day - current_period_start)
            baseline_workdays = baseline_workdays_by_ordinal.get(period_ordinal, [])
            baseline_workday_dates = [item[0] for item in baseline_workdays]
            index = bisect_right(baseline_workday_dates, anchor) - 1
            if index < 0:
                continue
            matched_a_rows.add(row_id_a)
            matched_b_rows.add(baseline_workdays[index][1])
            align_key = _json_key(("fallback_workday", baseline_workdays[index][0].isoformat()))
            rows.append(
                {
                    "align_key": align_key,
                    "align_quality": "fallback",
                    "presence_status": "matched",
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
                "presence_status",
                "bucket_start_a",
                "bucket_start_b",
                "current",
                "baseline",
            ]
        )
    result = compute_delta_columns(result)

    info = CalendarInfo(
        calendar_name=calendar.name,
        session_timezone=report_tz,
        mode=policy.mode,
        align_period=policy.align_period,
        fallback=policy.fallback,
        matched_rows=len(result),
        fallback_rows=fallback_rows,
        dropped_rows_a=int(len(frame_a) - len(matched_a_rows)),
        dropped_rows_b=int(len(frame_b) - len(matched_b_rows)),
    )
    return result.reset_index(drop=True), info


def _local_dates(series: pd.Series, *, report_tz: str) -> pd.Series:
    tz = ZoneInfo(report_tz)
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        parsed = pd.to_datetime(series, errors="coerce")
        _require_no_na_dates(parsed, report_tz=report_tz)
        return parsed.dt.tz_convert(tz).dt.date
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, errors="coerce")
        _require_no_na_dates(parsed, report_tz=report_tz)
        return parsed.dt.date
    return series.map(lambda value: _coerce_local_date(value, report_tz=report_tz))


def _coerce_local_date(value: object, *, report_tz: str) -> date:
    tz = ZoneInfo(report_tz)
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


class _PeriodPairing:
    def __init__(
        self,
        *,
        current_ordinals_by_date: dict[date, int],
        baseline_ordinals_by_date: dict[date, int],
        current_starts_by_ordinal: dict[int, date],
        baseline_starts_by_ordinal: dict[int, date],
    ) -> None:
        self.current_ordinals_by_date = current_ordinals_by_date
        self.baseline_ordinals_by_date = baseline_ordinals_by_date
        self.current_starts_by_ordinal = current_starts_by_ordinal
        self.baseline_starts_by_ordinal = baseline_starts_by_ordinal


def _period_pairing(dates_a: pd.Series, dates_b: pd.Series, align_period: str) -> _PeriodPairing:
    current_periods = _ordered_periods(dates_a, align_period)
    baseline_periods = _ordered_periods(dates_b, align_period)
    if len(current_periods) != len(baseline_periods):
        raise AlignmentFailedError(
            message=(
                "calendar alignment requires current and baseline to span the same "
                f"number of periods for align_period={align_period!r}"
            ),
            details={
                "kind": "CalendarAlignPeriodPairMismatch",
                "align_period": align_period,
                "current_period_ids": [period_id for period_id, _start in current_periods],
                "baseline_period_ids": [period_id for period_id, _start in baseline_periods],
            },
        )
    current_ordinal_by_id = {
        period_id: index for index, (period_id, _start) in enumerate(current_periods)
    }
    baseline_ordinal_by_id = {
        period_id: index for index, (period_id, _start) in enumerate(baseline_periods)
    }
    return _PeriodPairing(
        current_ordinals_by_date=_period_ordinals_by_date(
            dates_a, align_period, current_ordinal_by_id
        ),
        baseline_ordinals_by_date=_period_ordinals_by_date(
            dates_b, align_period, baseline_ordinal_by_id
        ),
        current_starts_by_ordinal={
            index: start for index, (_period_id_value, start) in enumerate(current_periods)
        },
        baseline_starts_by_ordinal={
            index: start for index, (_period_id_value, start) in enumerate(baseline_periods)
        },
    )


def _ordered_periods(dates: pd.Series, align_period: str) -> list[tuple[str, date]]:
    periods = {
        _period_id(cast("date", day), align_period): _period_start(cast("date", day), align_period)
        for day in dates
    }
    return sorted(periods.items(), key=lambda item: item[1])


def _period_ordinals_by_date(
    dates: pd.Series, align_period: str, ordinal_by_period_id: dict[str, int]
) -> dict[date, int]:
    return {
        cast("date", day): ordinal_by_period_id[_period_id(cast("date", day), align_period)]
        for day in dates
    }


def _align_keys(
    dates: pd.Series,
    *,
    calendar: Calendar,
    policy: CalendarPolicy,
    period_ordinals_by_date: dict[date, int],
) -> pd.Series:
    holiday_map = _holiday_map(calendar.holidays)
    adjusted_workdays = {date.fromisoformat(entry.date) for entry in calendar.adjusted_workdays}

    needs_ordinals = policy.mode in ("holiday_aligned", "holiday_and_dow_aligned")
    ordinals: dict[date, int] = {}
    if needs_ordinals:
        ordinals = _holiday_ordinals(calendar.holidays, align_period=policy.align_period)

    def _key_for(
        day: date,
    ) -> tuple[object, ...] | None:
        period_ordinal = period_ordinals_by_date[day]
        holiday = holiday_map.get(day)
        if policy.mode == "dow_aligned":
            return (
                "period",
                period_ordinal,
                "dow",
                day.isoweekday(),
                _week_offset(day, policy.align_period),
            )
        if policy.mode == "workday_aligned":
            nth = _nth_workday(
                day,
                align_period=policy.align_period,
                holiday_map=holiday_map,
                adjusted_workdays=adjusted_workdays,
            )
            if nth is None:
                return None
            return ("period", period_ordinal, "workday", nth)
        if policy.mode == "holiday_aligned":
            if holiday is not None:
                return ("period", period_ordinal, "holiday", holiday, ordinals[day])
            return None
        if policy.mode == "holiday_and_dow_aligned":
            if holiday is not None:
                return ("period", period_ordinal, "holiday", holiday, ordinals[day])
            return (
                "period",
                period_ordinal,
                "dow",
                day.isoweekday(),
                _week_offset(day, policy.align_period),
            )
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
        out[entry_date] = entry.holiday_id or entry_date.isoformat()
    return out


def _holiday_ordinals(entries: Sequence[CalendarEntry], *, align_period: str) -> dict[date, int]:
    by_period_and_id: dict[tuple[str, str], list[date]] = {}
    for entry in entries:
        entry_date = date.fromisoformat(entry.date)
        resolved = entry.holiday_id or entry_date.isoformat()
        period_id = _period_id(entry_date, align_period)
        by_period_and_id.setdefault((period_id, resolved), []).append(entry_date)
    ordinals: dict[date, int] = {}
    for dates_for_id in by_period_and_id.values():
        for index, entry_date in enumerate(sorted(dates_for_id), start=1):
            ordinals[entry_date] = index
    return ordinals


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
    key_kind = value[0] if value else None
    if key_kind == "period":
        return _json_key(value[2:])
    if key_kind == "dow":
        public_key = {
            "kind": "dow",
            "iso_weekday": value[1],
            "period_week_offset": value[2],
        }
    elif key_kind == "holiday":
        public_key = {
            "kind": "holiday",
            "holiday_id": value[1],
            "holiday_ordinal": value[2],
        }
    elif key_kind == "workday":
        public_key = {"kind": "workday", "workday_ordinal": value[1]}
    elif key_kind == "fallback_workday":
        public_key = {"kind": "fallback_workday", "baseline_date": value[1]}
    else:
        raise CalendarPolicyError(
            message=f"unsupported calendar align key kind {key_kind!r}",
            details={"kind": "CalendarAlignKeyInvalid", "align_key_kind": key_kind},
        )
    return json.dumps(public_key, ensure_ascii=False, separators=(",", ":"))


def _require_no_na_dates(series: pd.Series, *, report_tz: str) -> None:
    if not series.isna().any():
        return
    raise AlignmentFailedError(
        message=f"failed to parse date values with session timezone {report_tz!r}",
        details={"kind": "CalendarAlignDateParseFailed", "session_timezone": report_tz},
    )


def _timestamp_to_local_date(ts: pd.Timestamp, tz: ZoneInfo) -> date:
    if ts.tzinfo is None:
        return ts.date()
    return ts.tz_convert(tz).date()
