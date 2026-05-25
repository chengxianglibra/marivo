from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo.analysis_py.errors import TimezoneInvalidError, WindowInvalidError
from marivo.analysis_py.windows.relative import RelativeKind, parse_relative_expr
from marivo.analysis_py.windows.spec import AbsoluteWindow, RelativeWindow


def zoneinfo_from_name(name: str) -> ZoneInfo:
    if not isinstance(name, str):
        raise TimezoneInvalidError(
            message=f"timezone name must be a string, got {type(name).__name__}",
            details={"kind": "TimezoneNameInvalid", "tz": repr(name)},
        )
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise TimezoneInvalidError(
            message=f"timezone {name!r} was not found",
            details={"kind": "TimezoneNotFound", "tz": name},
        ) from exc


def coerce_as_of(raw: str | None, *, tz: ZoneInfo) -> datetime:
    if raw is None:
        return datetime.now(tz=tz)
    if not isinstance(raw, str):
        raise WindowInvalidError(
            message=f"window.as_of must be an ISO-8601 string, got {type(raw).__name__}",
            details={"kind": "AsOfInvalid", "as_of": repr(raw)},
        )

    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise WindowInvalidError(
            message=f"window.as_of={raw!r} is not ISO-8601",
            details={"kind": "AsOfInvalid", "as_of": raw},
        ) from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def resolve_to_absolute(window: RelativeWindow, *, as_of: datetime, tz: ZoneInfo) -> AbsoluteWindow:
    local_date = as_of.astimezone(tz).date()
    parsed = parse_relative_expr(window.expr)
    try:
        start_date, end_date = _bounds_for(parsed, as_of=local_date)
    except WindowInvalidError:
        raise
    except (OverflowError, ValueError) as exc:
        raise WindowInvalidError(
            message=f"relative window {window.expr!r} could not be resolved within date bounds",
            details={
                "kind": "WindowResolutionOverflow",
                "expr": window.expr,
                "as_of": local_date.isoformat(),
                "tz": str(tz),
            },
        ) from exc
    return AbsoluteWindow(
        start=start_date.isoformat(),
        end=end_date.isoformat(),
        grain=window.grain,
        tz=window.tz,
        time_field=window.time_field,
    )


def _bounds_for(kind: RelativeKind, *, as_of: date) -> tuple[date, date]:
    if kind.op == "today":
        return as_of, as_of
    if kind.op == "yesterday":
        prior = as_of - timedelta(days=1)
        return prior, prior
    if kind.op in {"to_date", "this"}:
        return _period_start(as_of, unit=kind.unit), as_of
    if kind.op == "last_n" and kind.unit is not None and kind.n is not None:
        return _shift_period_start(as_of, unit=kind.unit, periods_back=kind.n - 1), as_of
    raise WindowInvalidError(message=f"unsupported parsed relative window {kind!r}")


def _period_start(value: date, *, unit: str | None) -> date:
    if unit == "day":
        return value
    if unit == "week":
        # datetime.date.weekday() uses Monday=0.
        return value - timedelta(days=value.weekday())
    if unit == "month":
        return value.replace(day=1)
    if unit == "quarter":
        month = ((value.month - 1) // 3) * 3 + 1
        return value.replace(month=month, day=1)
    if unit == "year":
        return value.replace(month=1, day=1)
    raise WindowInvalidError(message=f"unsupported period unit {unit!r}")


def _shift_period_start(value: date, *, unit: str, periods_back: int) -> date:
    if unit == "day":
        return value - timedelta(days=periods_back)
    if unit == "week":
        return _period_start(value, unit="week") - timedelta(weeks=periods_back)
    if unit == "month":
        return _add_months(_period_start(value, unit="month"), -periods_back)
    if unit == "quarter":
        return _add_months(_period_start(value, unit="quarter"), -3 * periods_back)
    if unit == "year":
        year_start = _period_start(value, unit="year")
        return year_start.replace(year=year_start.year - periods_back)
    raise WindowInvalidError(message=f"unsupported period unit {unit!r}")


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    year = index // 12
    month = index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
