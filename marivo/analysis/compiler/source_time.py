"""Pure DuckDB expressions landing governed time in one explicit civil boundary zone."""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.observation.temporal import SourceTimeAuthority, time_zone
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    HourPrefixParse,
    StrptimeParse,
    TargetDimensionContract,
    TimestampParse,
    is_time_bearing_format,
)


def _timezone_signature(zone: str, value: datetime) -> datetime:
    raise NotImplementedError("native DuckDB timezone signature")


_localize_native: Callable[[str, ir.TimestampValue], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _timezone_signature,
    name="timezone",
    signature=((dt.string, dt.timestamp), dt.Timestamp(timezone="UTC")),
)
_render_native: Callable[[str, ir.TimestampValue], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _timezone_signature,
    name="timezone",
    signature=((dt.string, dt.Timestamp(timezone="UTC")), dt.timestamp),
)


def timestamp(value: ir.Value) -> ir.TimestampValue:
    result = value if isinstance(value, ir.TimestampValue) else value.cast("timestamp")
    if not isinstance(result, ir.TimestampValue):
        raise compilation_error("a temporal value", "invalid timestamp expression")
    return result


def localize(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
    resolved = time_zone(zone)
    if isinstance(resolved, timezone):
        offset = resolved.utcoffset(None)
        if offset is None:
            raise compilation_error("an exact fixed offset", "missing timezone offset")
        return _localize_native("UTC", value - ibis.interval(seconds=int(offset.total_seconds())))
    return _localize_native(zone, value)


def render(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
    resolved = time_zone(zone)
    if isinstance(resolved, timezone):
        offset = resolved.utcoffset(None)
        if offset is None:
            raise compilation_error("an exact fixed offset", "missing timezone offset")
        return _render_native("UTC", value) + ibis.interval(seconds=int(offset.total_seconds()))
    return _render_native(zone, value)


def boundary_instant(zone: str, value: ir.Value) -> ir.TimestampValue:
    """Use the first occurrence of a repeated civil boundary, with no machine tick."""
    wall = timestamp(value)
    native = localize(zone, wall)
    if time_zone(zone).utcoffset(None) is not None:
        return render("UTC", native)
    day = ibis.interval(seconds=86400)
    before = localize(zone, wall - day) + day
    after = localize(zone, wall + day) - day
    first = ibis.least(
        native,
        (render(zone, before) == wall).ifelse(before, native),
        (render(zone, after) == wall).ifelse(after, native),
    )
    return render("UTC", timestamp(first))


def source_time(
    value: ir.Value,
    axis: TargetDimensionContract,
    *,
    boundary_timezone: str,
    read_timezone: str | None,
    read_source: Literal["engine", "system_fallback"] = "engine",
    prefix: ir.Value | None = None,
) -> tuple[ir.Value, SourceTimeAuthority]:
    """Parse and localize a column without executing it or consulting ambient state."""
    physical = value.type()
    parse = axis.parse
    declared = (
        parse.timezone
        if isinstance(parse, (DatetimeParse, TimestampParse, StrptimeParse))
        else None
    )
    if isinstance(parse, HourPrefixParse):
        if prefix is None:
            raise compilation_error("a bound civil-date hour prefix", "missing prefix axis")
        hour = value.cast("int64")
        if not isinstance(hour, ir.IntegerValue):
            raise compilation_error("an integer hour", "invalid hour representation")
        value = timestamp(prefix) + hour.as_interval("h")
    elif isinstance(parse, StrptimeParse):
        text = value.cast("string")
        if not isinstance(text, ir.StringValue):
            raise compilation_error("a string parser input", "invalid parser expression")
        value = text.as_timestamp(parse.format)
        if "%z" not in parse.format and "%Z" not in parse.format:
            # Ibis labels StringToTimestamp UTC although DuckDB strptime without
            # an offset returns a naive timestamp. Bind its actual parser type.
            value = value.cast("timestamp")
        if not is_time_bearing_format(parse.format):
            value = value.cast("date")
    elif isinstance(parse, DateParse):
        if not isinstance(value, ir.DateValue):
            raise compilation_error(
                "a native civil date", "unsupported temporal source representation"
            )
    elif parse is not None and not isinstance(parse, (DatetimeParse, TimestampParse)):
        raise compilation_error("a bound supported time parser", "unresolved composite parser")
    if isinstance(value, ir.DateValue):
        if axis.logical_type != "date" or declared is not None:
            raise compilation_error(
                "a civil-date parser without timezone", "unsupported temporal source representation"
            )
        return value, SourceTimeAuthority(
            axis=axis.ref.path,
            physical_type=str(physical),
            kind="civil_date",
            read_timezone=None,
            source="civil_date",
            boundary_timezone=boundary_timezone,
        )
    if not isinstance(value, ir.TimestampValue):
        raise compilation_error("a declared date or timestamp parser", "non-temporal source")
    kind = value.type()
    origin: Literal["physical", "declared", "engine", "system_fallback"]
    if kind.timezone is not None:
        if declared is not None and declared != kind.timezone:
            raise compilation_error("matching declared and physical timezones", "timezone conflict")
        adopted, origin = kind.timezone, "physical"
        instant = value
    else:
        adopted = declared or read_timezone
        origin = "declared" if declared is not None else read_source
        if adopted is None:
            raise compilation_error(
                "execution-resolved source read timezone", "missing wall-clock authority"
            )
        if adopted == boundary_timezone and time_zone(adopted).utcoffset(None) is not None:
            instant = None
        else:
            if kind.scale is not None and kind.scale > 6:
                raise compilation_error(
                    "exact native timezone conversion precision",
                    "submicrosecond conversion unsupported",
                )
            instant = localize(adopted, value)
    if origin not in ("physical", "declared", "engine", "system_fallback"):
        raise compilation_error("a registered read timezone source", "invalid time authority")
    authority = SourceTimeAuthority(
        axis=axis.ref.path,
        physical_type=str(physical),
        kind="instant" if kind.timezone is not None else "localizable",
        read_timezone=adopted,
        source=origin,
        boundary_timezone=boundary_timezone,
    )
    return (value if instant is None else render(boundary_timezone, instant)), authority
