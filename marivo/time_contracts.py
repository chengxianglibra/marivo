from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

TimeGrain = Literal["hour", "day", "week", "month", "quarter", "year"]

SEMANTIC_TIMESTAMP_CONVENTIONS: frozenset[str] = frozenset({"native", "iso8601_t_naive"})
SUPPORTED_STRFTIME_DIRECTIVES: frozenset[str] = frozenset(
    {"%Y", "%y", "%m", "%d", "%H", "%I", "%M", "%S", "%p", "%f", "%%"}
)


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def normalize_timestamp_format(value: Any) -> str | None:
    normalized = optional_str(value)
    if normalized is None:
        return None
    if normalized in SEMANTIC_TIMESTAMP_CONVENTIONS:
        return normalized
    validate_strftime_format(normalized)
    return normalized


def validate_strftime_format(format_string: str) -> None:
    i = 0
    while i < len(format_string):
        if format_string[i] != "%":
            i += 1
            continue
        if i + 1 >= len(format_string):
            raise ValueError("timestamp_format has a trailing '%'")
        token = format_string[i : i + 2]
        if token not in SUPPORTED_STRFTIME_DIRECTIVES:
            raise ValueError(f"Unsupported strftime directive in timestamp_format: {token}")
        i += 2


def normalize_hour_boundary(value: str, *, label: str) -> str:
    normalized = value.strip()
    if "T" not in normalized and " " not in normalized:
        normalized = f"{normalized}T00:00:00"
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{label} must be an ISO-8601 date or datetime string for hour grain "
            "(for example, 2026-04-09 00:00:00)"
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0).isoformat(timespec="seconds")


def bucket_window(bucket_raw: object, grain: TimeGrain) -> dict[str, str]:
    raw_str = str(bucket_raw)
    if grain == "hour":
        bucket_start = datetime.fromisoformat(raw_str).replace(microsecond=0)
        return {
            "start": bucket_start.isoformat(timespec="seconds"),
            "end": (bucket_start + timedelta(hours=1)).isoformat(timespec="seconds"),
        }

    bucket_start_date = date.fromisoformat(raw_str[:10])
    if grain == "day":
        bucket_end = bucket_start_date + timedelta(days=1)
    elif grain == "week":
        bucket_end = bucket_start_date + timedelta(weeks=1)
    elif grain == "month":
        bucket_end = _shift_months(bucket_start_date, 1)
    elif grain == "quarter":
        bucket_end = _shift_months(bucket_start_date, 3)
    else:
        bucket_end = _shift_months(bucket_start_date, 12)
    return {"start": bucket_start_date.isoformat(), "end": bucket_end.isoformat()}


def previous_adjacent_window(start: str, end: str, *, grain: TimeGrain) -> dict[str, str]:
    if grain == "hour":
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        duration = end_dt - start_dt
        if duration.total_seconds() <= 0:
            raise ValueError(f"candidate window duration is non-positive: start={start}, end={end}")
        baseline_start = start_dt - duration
        return {
            "start": baseline_start.isoformat(timespec="seconds"),
            "end": start_dt.isoformat(timespec="seconds"),
        }

    start_date = date.fromisoformat(start[:10])
    end_date = date.fromisoformat(end[:10])
    if grain in {"month", "quarter", "year"}:
        duration_months = _month_delta(start_date, end_date)
        if duration_months <= 0:
            raise ValueError(f"candidate window duration is non-positive: start={start}, end={end}")
        if grain == "quarter" and duration_months % 3 != 0:
            raise ValueError(
                f"candidate window duration is not quarter-aligned: start={start}, end={end}"
            )
        if grain == "year" and duration_months % 12 != 0:
            raise ValueError(
                f"candidate window duration is not year-aligned: start={start}, end={end}"
            )
        baseline_start_date = _shift_months(start_date, -duration_months)
        return {"start": baseline_start_date.isoformat(), "end": start_date.isoformat()}

    duration_days = end_date - start_date
    if duration_days.days <= 0:
        raise ValueError(f"candidate window duration is non-positive: start={start}, end={end}")
    baseline_start_date = start_date - duration_days
    return {"start": baseline_start_date.isoformat(), "end": start_date.isoformat()}


def recommended_minimum_window(end: str, *, grain: TimeGrain, bucket_count: int) -> dict[str, str]:
    if grain == "hour":
        end_dt = datetime.fromisoformat(end)
        start_dt = end_dt - timedelta(hours=bucket_count)
        return {
            "start": start_dt.isoformat(timespec="seconds"),
            "end": end_dt.isoformat(timespec="seconds"),
        }
    end_date = date.fromisoformat(end[:10])
    if grain == "day":
        start_date = end_date - timedelta(days=bucket_count)
    elif grain == "week":
        start_date = end_date - timedelta(weeks=bucket_count)
    elif grain == "month":
        start_date = _shift_months(end_date, -bucket_count)
    elif grain == "quarter":
        start_date = _shift_months(end_date, -(bucket_count * 3))
    else:
        start_date = _shift_months(end_date, -(bucket_count * 12))
    return {"start": start_date.isoformat(), "end": end_date.isoformat()}


def window_length_in_grain(start: str, end: str, *, grain: TimeGrain | str) -> int:
    """Return a normalized window length in the requested time grain."""
    if grain == "hour":
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        return int((end_dt - start_dt).total_seconds() // 3600)

    start_date = date.fromisoformat(start[:10])
    end_date = date.fromisoformat(end[:10])
    if grain == "day":
        return (end_date - start_date).days
    if grain == "week":
        return (end_date - start_date).days // 7

    month_delta = _month_delta(start_date, end_date)
    if grain == "month":
        return month_delta
    if grain == "quarter":
        return month_delta // 3
    if grain == "year":
        return month_delta // 12
    raise ValueError(f"unsupported time grain: {grain}")


def _shift_months(value: date, months: int) -> date:
    zero_based_month = value.month - 1 + months
    year = value.year + zero_based_month // 12
    month = zero_based_month % 12 + 1
    return date(year, month, 1)


def _month_delta(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month
