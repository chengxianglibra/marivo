from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

TimeGrain = Literal["hour", "day", "week", "month"]

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
        raise ValueError(
            f"{label} must be a naive datetime string for hour grain "
            "(for example, 2026-04-09 00:00:00)"
        )
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"{label} must be a naive datetime string for hour grain "
            "(for example, 2026-04-09 00:00:00)"
        ) from exc
    if parsed.tzinfo is not None:
        raise ValueError(
            f"{label} must be a naive datetime string without timezone "
            "(for example, 2026-04-09 00:00:00)"
        )
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
    else:
        bucket_end = _shift_months(bucket_start_date, 1)
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
    else:
        start_date = _shift_months(end_date, -bucket_count)
    return {"start": start_date.isoformat(), "end": end_date.isoformat()}


def _shift_months(value: date, months: int) -> date:
    zero_based_month = value.month - 1 + months
    year = value.year + zero_based_month // 12
    month = zero_based_month % 12 + 1
    return date(year, month, 1)
