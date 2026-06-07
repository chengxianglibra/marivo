"""Window/slice filtering and ibis execution."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
import logging
import os
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from types import MethodType
from typing import Any
from zoneinfo import ZoneInfo

import ibis
import pandas as pd

from marivo.analysis.datasources import registry as _datasource_registry
from marivo.analysis.errors import (
    BackendError,
    DataTypeMismatchError,
    SliceInvalidError,
    TimezoneInvalidError,
    WindowInvalidError,
)
from marivo.analysis.executor.backend import BackendCache
from marivo.analysis.executor.query_record import (
    QueryExecution,
    compute_sql_digest,
    gen_query_ref,
    normalize_sql,
)
from marivo.analysis.timezone import zoneinfo_from_name
from marivo.analysis.windows.grain import _TRUNCATE_CODE, Grain
from marivo.analysis.windows.spec import AbsoluteWindow, is_date_only

_SUPPORTED_FORMATS = {
    ("date", None),
    ("timestamp", None),
    ("string", "yyyy-mm-dd"),
    ("string", "yyyymmdd"),
    ("string", "yyyymmddhh"),
    ("string", "yyyymmdd-hh"),
    ("string", "yyyy-mm-dd-hh"),
    ("string", "yyyymmddthh"),
    ("integer", "yyyymmdd"),
    ("integer", "yyyymmddhh"),
    ("integer", "epoch_seconds"),
}
_HOUR_PRECISION_FORMATS = frozenset({"yyyymmddhh", "yyyymmdd-hh", "yyyy-mm-dd-hh", "yyyymmddthh"})
_HOUR_ONLY_FORMATS = frozenset({"hh", "h", "int"})
UTC_ZONE = ZoneInfo("UTC")
_MISSING_ATTR = object()

_SHORTHAND_TO_STRPTIME: dict[str, str] = {
    "yyyy-mm-dd": "%Y-%m-%d",
    "yyyymmdd": "%Y%m%d",
    "yyyymmddhh": "%Y%m%d%H",
    "yyyymmdd-hh": "%Y%m%d-%H",
    "yyyy-mm-dd-hh": "%Y-%m-%d-%H",
    "yyyymmddthh": "%Y%m%dT%H",
}

_DATE_DIRECTIVES = frozenset({"%Y", "%y", "%m", "%d", "%j", "%U", "%W"})
_HOUR_DIRECTIVES = frozenset({"%H", "%I", "%k", "%l"})
_MINUTE_DIRECTIVES = frozenset({"%M"})
_SECOND_DIRECTIVES = frozenset({"%S"})
_SUBSECOND_DIRECTIVES = frozenset({"%f"})
_AMPM_DIRECTIVES = frozenset({"%p", "%P"})


def _classify_strptime_format(fmt: str) -> str:
    """Classify a strptime format string by its temporal granularity."""
    tokens = re.findall(r"%[a-zA-Z]", fmt)
    has_date = bool(_DATE_DIRECTIVES & set(tokens))
    has_hour = bool((_HOUR_DIRECTIVES | _AMPM_DIRECTIVES) & set(tokens))
    has_minute = bool(_MINUTE_DIRECTIVES & set(tokens))
    has_second = bool(_SECOND_DIRECTIVES & set(tokens))
    has_subsecond = bool(_SUBSECOND_DIRECTIVES & set(tokens))

    if has_subsecond or has_second:
        return "sub_hour"
    if has_minute:
        if not has_date:
            return "hour_only_minute"
        return "minute"
    if has_hour:
        if not has_date:
            return "hour_only"
        return "hour"
    if has_date:
        return "day"
    return "hour_only"


def _resolve_strptime_format(fmt: str | None) -> str | None:
    """Resolve a format string to its strptime equivalent.

    Shorthand aliases (e.g. ``"yyyymmdd"``) map to their strptime forms.
    Strptime-style strings (starting with ``%``) pass through.
    Hour-only shorthands (``"hh"``, ``"h"``, ``"int"``) pass through as-is.
    """
    if fmt is None:
        return None
    normalized = _normalize_time_format(fmt)
    if normalized is None:
        return None
    if normalized.startswith("%"):
        return normalized
    if normalized in _HOUR_ONLY_FORMATS:
        return normalized
    if normalized in _SHORTHAND_TO_STRPTIME:
        return _SHORTHAND_TO_STRPTIME[normalized]
    return None


def _normalize_time_format(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.startswith("%"):
        return stripped
    lowered = stripped.lower()
    compact = lowered.replace("_", "").replace(" ", "")
    if compact in {"yyyy-mm-dd-hh", "yyyy/mm/dd/hh"}:
        return "yyyy-mm-dd-hh"
    if compact == "yyyymmdd-hh":
        return "yyyymmdd-hh"
    if compact == "yyyymmddthh":
        return "yyyymmddthh"
    if compact in {"%y-%m-%d", "%y/%m/%d", "yyyy-mm-dd", "yyyy/mm/dd"}:
        return "yyyy-mm-dd"
    normalized = compact.replace("-", "").replace("/", "")
    if normalized in {"epochseconds"}:
        return "epoch_seconds"
    if normalized in {"yyyymmddhh"}:
        return "yyyymmddhh"
    if normalized in {"%y%m%d", "yyyymmdd"}:
        return "yyyymmdd"
    if normalized in _HOUR_ONLY_FORMATS:
        return normalized
    return normalized


def _encode_window_bound(iso_string: str, time_meta: Any) -> Any:
    data_type = time_meta.data_type
    fmt = _normalize_time_format(time_meta.format)
    strptime_fmt = _resolve_strptime_format(time_meta.format)

    if data_type == "date":
        return ibis.date(iso_string)
    if data_type == "timestamp":
        return ibis.timestamp(iso_string)

    if data_type in {"string", "integer"}:
        if data_type == "integer" and fmt == "epoch_seconds":
            dt = datetime.fromisoformat(iso_string)
            return int(dt.timestamp())

        # Shorthand hour-precision formats
        if fmt in _HOUR_PRECISION_FORMATS:
            parsed_dt, _is_date_bound = _parse_partition_datetime(
                iso_string, fmt=fmt, tz=UTC_ZONE, bound_name="bound"
            )
            result = _format_hour_precision_partition_literal(parsed_dt, fmt)
            return int(result) if data_type == "integer" else result

        # Shorthand day-precision formats
        if data_type == "string" and fmt == "yyyy-mm-dd":
            return iso_string
        if fmt == "yyyymmdd":
            result = iso_string[:10].replace("-", "")
            return int(result) if data_type == "integer" else result

        # Arbitrary strptime formats
        if strptime_fmt is not None and strptime_fmt not in _HOUR_ONLY_FORMATS:
            parsed, _ = _parse_partition_datetime(
                iso_string, fmt=None, tz=UTC_ZONE, bound_name="bound"
            )
            result = parsed.strftime(strptime_fmt)
            return int(result) if data_type == "integer" else result

    raise WindowInvalidError(
        message=f"unsupported window bound format (data_type={data_type}, format={fmt!r})",
        details={"data_type": data_type, "format": fmt},
    )


def _is_day_partition_meta(time_meta: Any) -> bool:
    data_type = time_meta.data_type
    fmt = _normalize_time_format(time_meta.format)
    return (data_type, fmt) in {
        ("string", "yyyy-mm-dd"),
        ("string", "yyyymmdd"),
        ("integer", "yyyymmdd"),
    }


def _is_hour_precision_partition_meta(time_meta: Any) -> bool:
    data_type = time_meta.data_type
    fmt = _normalize_time_format(time_meta.format)
    return (data_type == "string" and fmt in _HOUR_PRECISION_FORMATS) or (
        data_type == "integer" and fmt == "yyyymmddhh"
    )


def _is_hour_only_partition_meta(time_meta: Any) -> bool:
    """True for string/integer time fields whose format encodes only the hour (no date)."""
    data_type = time_meta.data_type
    if data_type not in {"string", "integer"}:
        return False
    fmt = _normalize_time_format(time_meta.format)
    if fmt in _HOUR_ONLY_FORMATS:
        return True
    strptime_fmt = _resolve_strptime_format(time_meta.format)
    if strptime_fmt is not None and strptime_fmt.startswith("%"):
        classification = _classify_strptime_format(strptime_fmt)
        return classification in {"hour_only", "hour_only_minute"}
    return False


def _parse_hour_precision_literal(value: str, fmt: str | None) -> datetime | None:
    if fmt == "yyyymmddhh" and len(value) == 10 and value.isdigit():
        return datetime(
            int(value[0:4]),
            int(value[4:6]),
            int(value[6:8]),
            int(value[8:10]),
        )
    if fmt == "yyyymmdd-hh" and len(value) == 11 and value[8] == "-":
        return datetime(
            int(value[0:4]),
            int(value[4:6]),
            int(value[6:8]),
            int(value[9:11]),
        )
    if fmt == "yyyy-mm-dd-hh" and len(value) == 13 and value[10] == "-":
        return datetime(
            int(value[0:4]),
            int(value[5:7]),
            int(value[8:10]),
            int(value[11:13]),
        )
    if fmt == "yyyymmddthh" and len(value) == 11 and value[8].lower() == "t":
        return datetime(
            int(value[0:4]),
            int(value[4:6]),
            int(value[6:8]),
            int(value[9:11]),
        )
    return None


def _parse_partition_datetime(
    value: str,
    *,
    fmt: str | None,
    tz: ZoneInfo,
    bound_name: str,
) -> tuple[datetime, bool]:
    raw = str(value).strip()
    try:
        parsed_hour = _parse_hour_precision_literal(raw, fmt)
        if parsed_hour is None and fmt not in _HOUR_PRECISION_FORMATS:
            parsed_hour = _parse_hour_precision_literal(raw, "yyyymmddhh")
    except (TypeError, ValueError) as exc:
        _raise_window_bound_invalid(bound_name=bound_name, value=value, tz=tz, error=exc)
    if parsed_hour is not None:
        return parsed_hour.replace(tzinfo=tz), False
    if "T" not in raw and "t" not in raw and ":" not in raw:
        try:
            parsed_date = date.fromisoformat(raw)
        except ValueError:
            pass
        else:
            return datetime.combine(parsed_date, time.min, tzinfo=tz), True

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        _raise_window_bound_invalid(bound_name=bound_name, value=value, tz=tz, error=exc)
    local = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    return local, False


def _partition_start_datetime(
    value: str, *, fmt: str | None, tz: ZoneInfo, bound_name: str
) -> datetime:
    parsed, _is_date_bound = _parse_partition_datetime(value, fmt=fmt, tz=tz, bound_name=bound_name)
    return parsed.replace(minute=0, second=0, microsecond=0)


def _partition_exclusive_end_datetime(
    value: str, *, fmt: str | None, tz: ZoneInfo, bound_name: str
) -> datetime:
    """Partition-level exclusive end bound for hour-precision partitions.

    For date-only ends the exclusive bound is midnight of the stated date
    (the date itself is excluded under [start, end)).  For non-date-only
    ends the bound advances to the next whole hour so the hour partition
    containing the end timestamp is included in the scan; row-level
    filtering then applies the precise ``< end_instant`` cutoff.
    """
    parsed, is_date_bound = _parse_partition_datetime(value, fmt=fmt, tz=tz, bound_name=bound_name)
    if is_date_bound:
        return datetime.combine(parsed.date(), time.min, tzinfo=tz)
    return parsed.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _format_hour_precision_partition_literal(value: datetime, fmt: str | None) -> str:
    if fmt == "yyyymmddhh":
        return value.strftime("%Y%m%d%H")
    if fmt == "yyyymmdd-hh":
        return value.strftime("%Y%m%d-%H")
    if fmt == "yyyy-mm-dd-hh":
        return value.strftime("%Y-%m-%d-%H")
    if fmt == "yyyymmddthh":
        return value.strftime("%Y%m%dT%H")
    strptime_fmt = _resolve_strptime_format(fmt)
    if strptime_fmt is not None and strptime_fmt not in _HOUR_ONLY_FORMATS:
        return value.strftime(strptime_fmt)
    raise WindowInvalidError(message=f"unsupported hour partition format {fmt!r}")


def _encode_hour_precision_bound(value: datetime, time_meta: Any) -> Any:
    fmt = _normalize_time_format(time_meta.format)
    literal = _format_hour_precision_partition_literal(value, fmt)
    return int(literal) if time_meta.data_type == "integer" else literal


def _encode_hour_only_bound(hour: int, time_meta: Any) -> Any:
    fmt = _normalize_time_format(time_meta.format)
    if fmt == "hh":
        literal = f"{hour:02d}"
    elif fmt in {"h", "int"}:
        literal = str(hour)
    else:
        raise WindowInvalidError(message=f"unsupported hour partition format {fmt!r}")
    return int(literal) if time_meta.data_type == "integer" else literal


def _encode_partition_date_bound(value: date, time_meta: Any) -> Any:
    return _encode_window_bound(value.isoformat(), time_meta)


def _parse_string_column(field_expr: Any, time_meta: Any) -> Any:
    """Parse a string/integer column into a temporal type using as_date/as_timestamp."""
    data_type = time_meta.data_type
    strptime_fmt = _resolve_strptime_format(time_meta.format)
    if strptime_fmt is None or strptime_fmt in _HOUR_ONLY_FORMATS:
        raise WindowInvalidError(
            message=f"cannot parse string column without a resolvable strptime format "
            f"(data_type={data_type!r}, format={time_meta.format!r})",
        )
    if data_type == "integer":
        string_expr = field_expr.cast("string")
    elif data_type == "string":
        string_expr = field_expr
    else:
        raise WindowInvalidError(
            message=f"_parse_string_column only supports string/integer, got {data_type!r}",
        )
    classification = _classify_strptime_format(strptime_fmt)
    if classification == "day":
        return string_expr.as_date(strptime_fmt)
    return string_expr.as_timestamp(strptime_fmt)


def _raise_window_bound_invalid(
    *, bound_name: str, value: str, tz: ZoneInfo, error: Exception
) -> None:
    raise WindowInvalidError(
        message=f"window.{bound_name}={value!r} is not a valid ISO-8601 date/datetime",
        details={
            "kind": "WindowBoundInvalid",
            "bound": bound_name,
            "value": value,
            "tz": str(tz),
        },
    ) from error


def _coerce_bound_datetime(value: str, *, tz: ZoneInfo, bound_name: str) -> datetime:
    if is_date_only(value):
        local_dt = datetime.combine(date.fromisoformat(value), time.min, tzinfo=tz)
        return local_dt.astimezone(UTC)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        _raise_window_bound_invalid(bound_name=bound_name, value=value, tz=tz, error=exc)
    dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
    return dt.astimezone(UTC)


def _local_midnight_of(value: str, *, tz: ZoneInfo, bound_name: str) -> datetime:
    """Resolve a date-only end bound to midnight of that date in session_tz.

    For [start, end) semantics the exclusive upper bound is midnight of the
    stated end date itself (no +1 day).  Delegates to ``_coerce_bound_datetime``
    which already resolves date-only strings to midnight of the stated date.
    """
    return _coerce_bound_datetime(value, tz=tz, bound_name=bound_name)


def _declared_timezone(time_meta: Any) -> str | None:
    value = getattr(time_meta, "timezone", None)
    return value if isinstance(value, str) and value else None


def _is_time_bearing_string_integer_meta(time_meta: Any) -> bool:
    data_type = time_meta.data_type
    if data_type not in {"string", "integer"}:
        return False
    strptime_fmt = _resolve_strptime_format(time_meta.format)
    if strptime_fmt is None or strptime_fmt in _HOUR_ONLY_FORMATS:
        return False
    return _classify_strptime_format(strptime_fmt) != "day"


def _field_timezone(field_expr: Any) -> str | None:
    with suppress(Exception):
        dtype = field_expr.type()
        timezone = getattr(dtype, "timezone", None)
        return str(timezone) if timezone else None
    return None


_TEMPORAL_DECLARED_DATA_TYPES = {"date", "datetime", "timestamp"}

_IBIS_DTYPE_TO_DECLARED: dict[str, set[str]] = {
    "date": {"date"},
    "timestamp": {"datetime", "timestamp"},
    "string": {"string"},
    "varchar": {"string"},
    "int8": {"integer"},
    "int16": {"integer"},
    "int32": {"integer"},
    "int64": {"integer"},
    "uint8": {"integer"},
    "uint16": {"integer"},
    "uint32": {"integer"},
    "uint64": {"integer"},
}


def _normalize_ibis_dtype(dtype_name: str) -> str:
    """Normalize ibis dtype string for lookup.

    Ibis prefixes non-nullable dtypes with "!"; DuckDB reports timestamp
    precision as "timestamp(6)" etc. Normalize both forms for compatibility
    mapping.
    """
    bare = dtype_name.lstrip("!")
    if bare.startswith("timestamp"):
        return "timestamp"
    return bare


def _validate_time_field_dtype(field_expr: Any, time_meta: Any) -> None:
    declared = time_meta.data_type
    if declared is None:
        return
    try:
        dtype_name = str(field_expr.type())
    except Exception:
        return
    normalized = _normalize_ibis_dtype(dtype_name)
    compatible = _IBIS_DTYPE_TO_DECLARED.get(normalized)
    if compatible is not None and declared not in compatible:
        raise DataTypeMismatchError(
            message=f"time_field declared data_type={declared!r} but the expression "
            f"produces ibis dtype {dtype_name!r}; this mismatch causes "
            f"TypeError at execution.",
            hint=f"Change data_type to {sorted(compatible)[0]!r} or adjust "
            f"the body to produce a {declared!r}-compatible expression.",
            details={
                "kind": "DataTypeDeclarationMismatch",
                "declared": declared,
                "actual_ibis_dtype": dtype_name,
                "compatible_declarations": sorted(compatible),
            },
        )
    if compatible is None and declared in _TEMPORAL_DECLARED_DATA_TYPES:
        raise DataTypeMismatchError(
            message=f"time_field declared data_type={declared!r} but the expression "
            f"produces unexpected ibis dtype {dtype_name!r}; "
            f"temporal time fields require date or timestamp dtype.",
            hint="Adjust the body to produce a date or timestamp expression, "
            "or change data_type to match the column's actual dtype.",
            details={
                "kind": "DataTypeUnexpectedForTemporal",
                "declared": declared,
                "actual_ibis_dtype": dtype_name,
            },
        )


def _is_naive_temporal_expr(field_expr: Any) -> bool:
    """Return True if the field expression has no actual timezone attached."""
    return _field_timezone(field_expr) is None


def _timestamp_bounds_for_column(
    window: AbsoluteWindow,
    *,
    session_tz: ZoneInfo,
    column_tz: ZoneInfo,
    bound_name: str,
    value: str,
) -> datetime:
    """Convert a window bound from session-local to column-local naive datetime.

    The bound is first resolved as a session-local instant, then projected
    into column_tz space and stripped of tzinfo so it can be compared
    against naive timestamp column values.
    """
    instant_utc = _coerce_bound_datetime(value, tz=session_tz, bound_name=bound_name)
    return instant_utc.astimezone(column_tz).replace(tzinfo=None)


def _exclusive_end_for_column(
    window: AbsoluteWindow,
    *,
    session_tz: ZoneInfo,
    column_tz: ZoneInfo,
) -> datetime:
    upper_utc = _coerce_bound_datetime(window.end, tz=session_tz, bound_name="end")
    return upper_utc.astimezone(column_tz).replace(tzinfo=None)


def _column_timezone(time_meta: Any, session_tz: ZoneInfo) -> ZoneInfo:
    declared = _declared_timezone(time_meta)
    return zoneinfo_from_name(declared) if declared is not None else session_tz


def _validate_time_field_timezone(field_expr: Any, time_meta: Any) -> None:
    declared = _declared_timezone(time_meta)
    if declared is None:
        return
    zoneinfo_from_name(declared)
    data_type = time_meta.data_type
    if data_type not in {"datetime", "timestamp"} and not _is_time_bearing_string_integer_meta(
        time_meta
    ):
        raise TimezoneInvalidError(
            message="timezone declarations are only supported on datetime or timestamp time fields",
            hint="date and partition time fields do not support timezone declarations; use system timezone or a tz-aware timestamp field.",
            details={
                "kind": "TimezoneDeclarationUnsupported",
                "data_type": data_type,
                "format": getattr(time_meta, "format", None),
                "declared": declared,
            },
        )
    if data_type in {"string", "integer"}:
        return
    actual = _field_timezone(field_expr)
    if actual is not None and actual != declared:
        raise TimezoneInvalidError(
            message="timezone declaration conflicts with the time field expression timezone",
            details={
                "kind": "TimezoneDeclarationConflict",
                "declared": declared,
                "actual": actual,
            },
        )


def _window_bound_predicates(
    field_expr: Any,
    window: AbsoluteWindow,
    time_meta: Any,
    *,
    session_tz: ZoneInfo,
) -> tuple[Any, Any]:
    _validate_time_field_timezone(field_expr, time_meta)
    _validate_time_field_dtype(field_expr, time_meta)
    data_type = time_meta.data_type
    fmt = _normalize_time_format(time_meta.format)
    strptime_fmt = _resolve_strptime_format(time_meta.format)

    # timestamp / epoch_seconds: compare as proper temporal values
    if data_type in {"datetime", "timestamp"}:
        declared = _declared_timezone(time_meta)
        actual = _field_timezone(field_expr)
        if actual is None:
            # Naive timestamp column: determine effective column timezone
            column_tz = zoneinfo_from_name(declared) if declared is not None else session_tz
            lower_dt = _timestamp_bounds_for_column(
                window,
                session_tz=session_tz,
                column_tz=column_tz,
                bound_name="start",
                value=window.start,
            )
            if is_date_only(window.end):
                upper_utc = _local_midnight_of(window.end, tz=session_tz, bound_name="end")
                upper_dt = upper_utc.astimezone(column_tz).replace(tzinfo=None)
            else:
                upper_dt = _timestamp_bounds_for_column(
                    window,
                    session_tz=session_tz,
                    column_tz=column_tz,
                    bound_name="end",
                    value=window.end,
                )
            return (
                field_expr >= ibis.timestamp(lower_dt.isoformat()),
                field_expr < ibis.timestamp(upper_dt.isoformat()),
            )
        # Tz-aware timestamp column: compare as UTC instants
        lower_dt = _coerce_bound_datetime(window.start, tz=session_tz, bound_name="start")
        if is_date_only(window.end):
            upper_dt = _local_midnight_of(window.end, tz=session_tz, bound_name="end")
        else:
            upper_dt = _coerce_bound_datetime(window.end, tz=session_tz, bound_name="end")
        return (
            field_expr >= ibis.timestamp(lower_dt.isoformat()),
            field_expr < ibis.timestamp(upper_dt.isoformat()),
        )
    if data_type == "integer" and fmt == "epoch_seconds":
        lower_epoch = int(
            _coerce_bound_datetime(window.start, tz=session_tz, bound_name="start").timestamp()
        )
        if is_date_only(window.end):
            upper_epoch = int(
                _local_midnight_of(window.end, tz=session_tz, bound_name="end").timestamp()
            )
            return (field_expr >= lower_epoch, field_expr < upper_epoch)
        upper_epoch = int(
            _coerce_bound_datetime(window.end, tz=session_tz, bound_name="end").timestamp()
        )
        return (field_expr >= lower_epoch, field_expr < upper_epoch)

    # Shorthand hour-precision formats: raw string/integer comparison
    if _is_hour_precision_partition_meta(time_meta):
        declared = _declared_timezone(time_meta)
        if declared is None:
            lower_dt = _partition_start_datetime(
                window.start, fmt=fmt, tz=session_tz, bound_name="start"
            )
            upper_dt = _partition_exclusive_end_datetime(
                window.end, fmt=fmt, tz=session_tz, bound_name="end"
            )
        else:
            bound_tz = zoneinfo_from_name(declared)
            lower_bound = _timestamp_bounds_for_column(
                window,
                session_tz=session_tz,
                column_tz=bound_tz,
                bound_name="start",
                value=window.start,
            )
            lower_dt = _partition_start_datetime(
                lower_bound.isoformat(), fmt=fmt, tz=bound_tz, bound_name="start"
            )
            if is_date_only(window.end):
                upper_dt = _exclusive_end_for_column(
                    window, session_tz=session_tz, column_tz=bound_tz
                ).replace(minute=0, second=0, microsecond=0)
            else:
                upper_bound = _timestamp_bounds_for_column(
                    window,
                    session_tz=session_tz,
                    column_tz=bound_tz,
                    bound_name="end",
                    value=window.end,
                )
                upper_dt = _partition_exclusive_end_datetime(
                    upper_bound.isoformat(), fmt=fmt, tz=bound_tz, bound_name="end"
                )
        lower = _encode_hour_precision_bound(lower_dt, time_meta)
        upper = _encode_hour_precision_bound(upper_dt, time_meta)
        return (field_expr >= lower, field_expr < upper)

    # Shorthand day-precision formats: raw string/integer comparison
    if _is_day_partition_meta(time_meta):
        lower = _encode_window_bound(window.start, time_meta)
        upper = _encode_window_bound(window.end, time_meta)
        return (field_expr >= lower, field_expr < upper)

    # Strptime formats: parse column into temporal type and compare
    if (
        data_type in {"string", "integer"}
        and strptime_fmt is not None
        and strptime_fmt not in _HOUR_ONLY_FORMATS
    ):
        if session_tz is None:
            raise WindowInvalidError(
                message="strptime format time fields require an explicit session timezone",
                hint="Pass timezone= when attaching the session.",
                details={"format": strptime_fmt},
            )
        parsed_expr = _parse_string_column(field_expr, time_meta)
        classification = _classify_strptime_format(strptime_fmt)
        declared = _declared_timezone(time_meta)
        if declared is None:
            lower_dt = _coerce_bound_datetime(window.start, tz=session_tz, bound_name="start")
        else:
            column_tz = zoneinfo_from_name(declared)
            lower_dt = _timestamp_bounds_for_column(
                window,
                session_tz=session_tz,
                column_tz=column_tz,
                bound_name="start",
                value=window.start,
            )

        if classification == "day":
            if is_date_only(window.end):
                upper_dt = _local_midnight_of(window.end, tz=session_tz, bound_name="end")
                return (
                    parsed_expr >= ibis.date(lower_dt.date().isoformat()),
                    parsed_expr < ibis.date(upper_dt.date().isoformat()),
                )
            if declared is None:
                upper_dt = _coerce_bound_datetime(window.end, tz=session_tz, bound_name="end")
            else:
                upper_dt = _timestamp_bounds_for_column(
                    window,
                    session_tz=session_tz,
                    column_tz=column_tz,
                    bound_name="end",
                    value=window.end,
                )
            return (
                parsed_expr >= ibis.date(lower_dt.date().isoformat()),
                parsed_expr < ibis.date(upper_dt.date().isoformat()),
            )
        else:
            if is_date_only(window.end):
                if declared is None:
                    upper_dt = _local_midnight_of(window.end, tz=session_tz, bound_name="end")
                else:
                    upper_dt = _exclusive_end_for_column(
                        window, session_tz=session_tz, column_tz=column_tz
                    )
                return (
                    parsed_expr >= ibis.timestamp(lower_dt.isoformat()),
                    parsed_expr < ibis.timestamp(upper_dt.isoformat()),
                )
            upper_dt = (
                _timestamp_bounds_for_column(
                    window,
                    session_tz=session_tz,
                    column_tz=column_tz,
                    bound_name="end",
                    value=window.end,
                )
                if declared is not None
                else _coerce_bound_datetime(window.end, tz=session_tz, bound_name="end")
            )
            return (
                parsed_expr >= ibis.timestamp(lower_dt.isoformat()),
                parsed_expr < ibis.timestamp(upper_dt.isoformat()),
            )

    # Fallback: raw value comparison
    lower = _encode_window_bound(window.start, time_meta)
    upper = _encode_window_bound(window.end, time_meta)
    return (field_expr >= lower, field_expr < upper)


def _resolve_time_field(dataset_ir: Any, window: Mapping[str, Any]) -> Any:
    time_fields = [
        field for field in dataset_ir.fields.values() if getattr(field, "is_time", False)
    ]
    if not time_fields:
        raise WindowInvalidError(
            message=f"dataset '{dataset_ir.name}' has no @ms.time_field",
        )
    if len(time_fields) == 1:
        return time_fields[0]
    requested = window.get("time_field")
    if requested:
        for field in time_fields:
            if field.name == requested:
                return field
        candidates = [field.name for field in time_fields]
        first_candidate = candidates[0] if candidates else "<time_field>"
        raise WindowInvalidError(
            message=(
                f"time_field={requested!r} is not on dataset '{dataset_ir.name}'; "
                f"candidates: {candidates}"
            ),
            hint="Pass one of the dataset time fields as observe(..., time_field=...).",
            details={
                "candidates": candidates,
                "fix_snippet": (
                    'session.observe(mv.MetricRef("sales.revenue"), '
                    'timescope={"start": "2026-07-01", "end": "2026-08-01"}, '
                    f'time_field=mv.DimensionRef("{first_candidate}"))'
                ),
            },
        )
    # No explicit request — check for a declared default
    defaults = [f for f in time_fields if getattr(f, "is_default", False)]
    if len(defaults) == 1:
        return defaults[0]
    candidates = [field.name for field in time_fields]
    first_candidate = candidates[0]
    raise WindowInvalidError(
        message=f"dataset '{dataset_ir.name}' has multiple time_fields: {candidates}",
        hint=(
            "Pass observe(..., time_field=...) to choose the time axis, "
            "or mark one time field as @ms.time_field(..., is_default=True) "
            "in the semantic definition."
        ),
        details={
            "candidates": candidates,
            "fix_snippet": (
                'session.observe(mv.MetricRef("sales.revenue"), '
                'timescope={"start": "2026-07-01", "end": "2026-08-01"}, '
                f'time_field=mv.DimensionRef("{first_candidate}"))'
            ),
        },
    )


def resolve_window_time_field(dataset_ir: Any, *, window: AbsoluteWindow) -> Any:
    time_field = {"time_field": window.time_field} if window.time_field else {}
    return _resolve_time_field(dataset_ir, time_field)


def _resolve_required_prefix_time_field(dataset_ir: Any, hour_field_ir: Any) -> Any | None:
    if hour_field_ir.time_meta is None:
        return None
    prefix = hour_field_ir.time_meta.required_prefix
    if not prefix:
        return None
    for field in dataset_ir.fields.values():
        if not getattr(field, "is_time", False):
            continue
        if field.name == prefix or field.semantic_id == prefix:
            return field
    return None


def _combine_or(clauses: list[Any]) -> Any:
    if not clauses:
        raise WindowInvalidError(message="composite partition filter has no clauses")
    combined = clauses[0]
    for clause in clauses[1:]:
        combined = combined | clause
    return combined


def _composite_hour_partition_predicate(
    table: ibis.Table,
    window: AbsoluteWindow,
    *,
    dataset_ir: Any,
    hour_field_ir: Any,
    session_tz: ZoneInfo,
) -> Any | None:
    if hour_field_ir.time_meta is None or not _is_hour_only_partition_meta(hour_field_ir.time_meta):
        return None
    date_field_ir = _resolve_required_prefix_time_field(dataset_ir, hour_field_ir)
    if date_field_ir is None or date_field_ir.time_meta is None:
        return None
    if not _is_day_partition_meta(date_field_ir.time_meta):
        return None

    hour_fmt = _normalize_time_format(hour_field_ir.time_meta.format)
    lower_dt = _partition_start_datetime(
        window.start, fmt=hour_fmt, tz=session_tz, bound_name="start"
    )
    upper_dt = _partition_exclusive_end_datetime(
        window.end, fmt=hour_fmt, tz=session_tz, bound_name="end"
    )
    last_day = (upper_dt - timedelta(microseconds=1)).date()
    start_day = lower_dt.date()
    date_expr = date_field_ir.fn(table)
    hour_expr = hour_field_ir.fn(table)

    def date_eq(value: date) -> Any:
        return date_expr == _encode_partition_date_bound(value, date_field_ir.time_meta)

    def date_gt(value: date) -> Any:
        return date_expr > _encode_partition_date_bound(value, date_field_ir.time_meta)

    def date_lt(value: date) -> Any:
        return date_expr < _encode_partition_date_bound(value, date_field_ir.time_meta)

    def hour_gte(value: int) -> Any:
        return hour_expr >= _encode_hour_only_bound(value, hour_field_ir.time_meta)

    def hour_lt(value: int) -> Any:
        return hour_expr < _encode_hour_only_bound(value, hour_field_ir.time_meta)

    if start_day == last_day:
        clause = date_eq(start_day) & hour_gte(lower_dt.hour)
        if upper_dt.date() == start_day:
            clause = clause & hour_lt(upper_dt.hour)
        return clause

    clauses = [date_eq(start_day) & hour_gte(lower_dt.hour)]
    first_middle_day = start_day + timedelta(days=1)
    last_middle_day = last_day - timedelta(days=1)
    if first_middle_day <= last_middle_day:
        clauses.append(date_gt(start_day) & date_lt(last_day))
    if upper_dt.time() == time.min:
        clauses.append(date_eq(last_day))
    else:
        clauses.append(date_eq(last_day) & hour_lt(upper_dt.hour))
    return _combine_or(clauses)


def apply_window_to_dataset(
    table: ibis.Table,
    window: AbsoluteWindow | Mapping[str, Any] | None,
    *,
    dataset_ir: Any,
    session_tz: ZoneInfo = UTC_ZONE,
) -> ibis.Table:
    if window is None:
        return table
    if isinstance(window, AbsoluteWindow):
        normalized_window = window
    else:
        normalized_window = AbsoluteWindow(
            start=str(window["start"]),
            end=str(window["end"]),
            grain=window.get("grain"),
            time_field=window.get("time_field"),
        )
    time_field_ir = resolve_window_time_field(dataset_ir, window=normalized_window)
    if time_field_ir.time_meta is None:
        raise WindowInvalidError(message=f"field '{time_field_ir.name}' has no time metadata")
    composite_predicate = _composite_hour_partition_predicate(
        table,
        normalized_window,
        dataset_ir=dataset_ir,
        hour_field_ir=time_field_ir,
        session_tz=session_tz,
    )
    if composite_predicate is not None:
        return table.filter(composite_predicate)
    field_expr = time_field_ir.fn(table)
    lower_predicate, upper_predicate = _window_bound_predicates(
        field_expr,
        normalized_window,
        time_field_ir.time_meta,
        session_tz=session_tz,
    )
    return table.filter(lower_predicate, upper_predicate)


def bucket_start_expr(local_ts: Any, grain: Grain) -> Any:
    """Bucket-start expression for a session-local timestamp/date expression."""
    if grain.count == 1:
        if grain.is_day:
            return local_ts.cast("date").name("bucket_start")
        return local_ts.truncate(_TRUNCATE_CODE[grain.unit]).name("bucket_start")
    width = grain.width_seconds()
    day_start = local_ts.truncate("D")
    offset = ((local_ts.epoch_seconds() - day_start.epoch_seconds()) // width) * width
    return (day_start + offset.as_interval("s")).name("bucket_start")


def _timestamp_expr_in_session_timezone(
    ts_expr: Any,
    *,
    column_tz: ZoneInfo,
    session_tz: ZoneInfo,
    window: AbsoluteWindow,
) -> Any:
    anchor_utc = _coerce_bound_datetime(window.start, tz=session_tz, bound_name="start")
    column_offset = anchor_utc.astimezone(column_tz).utcoffset()
    session_offset = anchor_utc.astimezone(session_tz).utcoffset()
    column_seconds = int(column_offset.total_seconds()) if column_offset is not None else 0
    session_seconds = int(session_offset.total_seconds()) if session_offset is not None else 0
    shift_seconds = session_seconds - column_seconds
    if shift_seconds == 0:
        return ts_expr
    return ts_expr + ibis.interval(seconds=shift_seconds)


def _local_bucket_expr(
    raw: Any,
    *,
    time_meta: Any,
    grain: Grain,
    session_tz: ZoneInfo,
    window: AbsoluteWindow,
) -> Any:
    """Compute a bucket-start expression that aligns instant or declared-naive
    timestamp fields to session-local calendar boundaries.

    For epoch_seconds columns, values are first cast to timestamp (UTC instant).
    For naive timestamp columns with a declared timezone, the shift converts
    from declared-local to session-local.  For naive timestamp columns with no
    declaration, the column is already in session-local space so the shift is
    zero.
    """
    data_type = time_meta.data_type
    fmt = _normalize_time_format(time_meta.format)
    declared = _declared_timezone(time_meta)
    if data_type == "integer" and fmt == "epoch_seconds":
        ts_expr = raw.cast("timestamp")
        column_tz = UTC_ZONE
    elif data_type in {"datetime", "timestamp"}:
        ts_expr = raw
        column_tz = zoneinfo_from_name(declared) if declared is not None else session_tz
    else:
        raise WindowInvalidError(
            message=f"_local_bucket_expr only supports datetime/timestamp/epoch_seconds, "
            f"got data_type={data_type!r}",
        )

    local_expr = _timestamp_expr_in_session_timezone(
        ts_expr,
        column_tz=column_tz,
        session_tz=session_tz,
        window=window,
    )
    return bucket_start_expr(local_expr, grain)


def _prefix_date_expr(table: ibis.Table, prefix_field_ir: Any) -> Any:
    """Compute a date ibis expression from a day-level required_prefix field."""
    time_meta = prefix_field_ir.time_meta
    raw = prefix_field_ir.fn(table)
    if time_meta.data_type == "date":
        return raw
    if time_meta.data_type in {"string", "integer"}:
        return _parse_string_column(raw, time_meta)
    if time_meta.data_type in {"datetime", "timestamp"}:
        return raw.cast("date")
    raise WindowInvalidError(
        message=f"required_prefix field '{prefix_field_ir.name}' has unsupported "
        f"data_type {time_meta.data_type!r} for day-level bucketing",
    )


def _apply_hour_only_bucket(
    table: ibis.Table,
    *,
    raw: Any,
    field_ir: Any,
    window: AbsoluteWindow,
    session_tz: ZoneInfo,
    dataset_ir: Any,
) -> ibis.Table:
    """Bucket for hour-only string/integer time fields that use required_prefix."""
    time_meta = field_ir.time_meta
    grain = window.grain
    assert grain is not None
    field_grain = Grain(count=1, unit=time_meta.granularity)

    prefix_field_ir = _resolve_required_prefix_time_field(dataset_ir, field_ir)
    if prefix_field_ir is None or prefix_field_ir.time_meta is None:
        raise WindowInvalidError(
            message=f"hour-only time field '{field_ir.name}' requires a day-level "
            f"required_prefix for bucket computation",
        )

    if grain < field_grain:
        raise WindowInvalidError(
            message=f"requested grain {grain.to_token()!r} is finer than time field "
            f"'{field_ir.name}' granularity '{time_meta.granularity}'",
        )

    prefix_raw = prefix_field_ir.fn(table)

    # Grain coarser than field: use prefix value directly (no parse, no truncate)
    if grain > field_grain:
        if _is_day_partition_meta(prefix_field_ir.time_meta) and grain.is_day:
            return table.mutate(bucket_start=prefix_raw.name("bucket_start"))
        prefix_date = _prefix_date_expr(table, prefix_field_ir)
        return table.mutate(bucket_start=bucket_start_expr(prefix_date, grain))

    # Grain matches field: concatenate prefix + hour into sortable string
    # No parse, no truncate — raw values already represent the bucket.
    hour_str = raw if time_meta.data_type == "string" else raw.cast("string")
    if _normalize_time_format(time_meta.format) in {"h", "int"}:
        hour_str = hour_str.lpad(2, "0")
    prefix_fmt = _normalize_time_format(prefix_field_ir.time_meta.format)
    if prefix_fmt == "yyyy-mm-dd":
        bucket = (prefix_raw + ibis.literal("-") + hour_str).name("bucket_start")
    elif prefix_fmt == "yyyymmdd":
        bucket = (prefix_raw + hour_str).name("bucket_start")
    else:
        # Fallback for unusual prefix formats
        prefix_date = _prefix_date_expr(table, prefix_field_ir)
        hour_int = raw.cast("int") if time_meta.data_type == "string" else raw
        date_ts = prefix_date.cast("timestamp")
        bucket = (date_ts + (hour_int * 3600).as_interval("s")).name("bucket_start")
    return table.mutate(bucket_start=bucket)


def apply_time_series_bucket(
    table: ibis.Table,
    *,
    field_ir: Any,
    window: AbsoluteWindow,
    session_tz: ZoneInfo,
    dataset_ir: Any | None = None,
) -> ibis.Table:
    if field_ir.time_meta is None:
        raise WindowInvalidError(message=f"field '{field_ir.name}' has no time metadata")
    raw = field_ir.fn(table)
    _validate_time_field_timezone(raw, field_ir.time_meta)
    _validate_time_field_dtype(raw, field_ir.time_meta)
    time_meta = field_ir.time_meta
    data_type = time_meta.data_type
    fmt = time_meta.format
    strptime_fmt = _resolve_strptime_format(fmt)
    if window.grain is None:
        return table

    # Strptime format: parse the column into a temporal type
    # (excludes hour-only formats like "hh", "%H" — those need required_prefix)
    if (
        data_type in {"string", "integer"}
        and strptime_fmt is not None
        and not _is_hour_only_partition_meta(time_meta)
    ):
        parsed = _parse_string_column(raw, time_meta)
        classification = _classify_strptime_format(strptime_fmt)
        grain_matches_classification = (
            (window.grain.is_day and classification == "day")
            or (
                window.grain.unit == "hour" and window.grain.count == 1 and classification == "hour"
            )
            or (
                window.grain.unit == "minute"
                and window.grain.count == 1
                and classification == "minute"
            )
        )
        if grain_matches_classification:
            bucket = parsed.name("bucket_start")
        else:
            column_tz = _column_timezone(time_meta, session_tz)
            local_parsed = _timestamp_expr_in_session_timezone(
                parsed,
                column_tz=column_tz,
                session_tz=session_tz,
                window=window,
            )
            bucket = bucket_start_expr(local_parsed, window.grain)
        return table.mutate(bucket_start=bucket)

    # Timestamp / epoch_seconds: bucket in session-local calendar
    if data_type in {"datetime", "timestamp"} or (
        data_type == "integer" and fmt == "epoch_seconds"
    ):
        bucket = _local_bucket_expr(
            raw,
            time_meta=time_meta,
            grain=window.grain,
            session_tz=session_tz,
            window=window,
        )
        return table.mutate(bucket_start=bucket)

    # Hour-only string/integer with required_prefix
    if data_type in {"string", "integer"} and _is_hour_only_partition_meta(time_meta):
        return _apply_hour_only_bucket(
            table,
            raw=raw,
            field_ir=field_ir,
            window=window,
            session_tz=session_tz,
            dataset_ir=dataset_ir,
        )

    # Date type: simple truncate or cast
    if data_type == "date":
        bucket = bucket_start_expr(raw, window.grain)
        return table.mutate(bucket_start=bucket)

    # Unhandled type/format combination
    raise WindowInvalidError(
        message=f"cannot compute bucket for time field '{field_ir.name}' with "
        f"data_type={data_type!r}, format={fmt!r}",
    )


def _resolve_slice_field(dataset_ir: Any, field_name: str, table: ibis.Table) -> Any:
    field_ir = dataset_ir.fields.get(field_name)
    if field_ir is not None:
        return field_ir.fn(table)
    if field_name in table.columns:
        return table[field_name]
    raise SliceInvalidError(
        message=(
            f"slice key '{field_name}' is neither a declared field on dataset "
            f"'{dataset_ir.name}' nor a physical column"
        ),
        details={
            "dataset": dataset_ir.name,
            "declared_fields": sorted(dataset_ir.fields),
            "physical_columns": sorted(table.columns),
        },
    )


_SUPPORTED_SLICE_OPS = {"==", "!=", "in", ">", ">=", "<", "<=", "between"}
_SCALAR_SLICE_OPS = {"==", "!=", ">", ">=", "<", "<="}


def _normalize_slice_predicate(raw: Any) -> tuple[str, Any]:
    if isinstance(raw, Mapping):
        if "op" not in raw or "value" not in raw:
            raise SliceInvalidError(
                message="structured slice predicate must include 'op' and 'value'",
                details={"predicate": dict(raw)},
            )
        op = raw["op"]
        value = raw["value"]
        if not isinstance(op, str) or op not in _SUPPORTED_SLICE_OPS:
            raise SliceInvalidError(
                message=f"unsupported slice predicate op {op!r}",
                details={"supported_ops": sorted(_SUPPORTED_SLICE_OPS)},
            )
        _validate_slice_value_shape(op, value)
        return str(op), value
    _validate_slice_value_shape("==", raw)
    return "==", raw


def _validate_slice_value_shape(op: str, value: Any) -> None:
    if op in _SCALAR_SLICE_OPS:
        if isinstance(value, (Mapping, list, tuple, set)):
            raise SliceInvalidError(
                message=f"slice op {op!r} requires a scalar value",
                details={"op": op, "value_type": type(value).__name__},
            )
        return
    if op == "in":
        if not isinstance(value, (list, tuple, set)) or len(value) == 0:
            raise SliceInvalidError(message="slice op 'in' requires a non-empty list/tuple/set")
        return
    if op == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise SliceInvalidError(message="slice op 'between' requires exactly two values")
        return


def normalize_slice_for_storage(where: Mapping[str, Any] | None) -> dict[str, Any]:
    if not where:
        return {}
    normalized: dict[str, Any] = {}
    for field_name, raw_predicate in where.items():
        op, value = _normalize_slice_predicate(raw_predicate)
        if op == "==" and not isinstance(raw_predicate, Mapping):
            normalized[field_name] = _json_safe_value(value)
        else:
            normalized[field_name] = {"op": op, "value": _json_safe_value(value)}
    _ensure_json_safe(normalized)
    return normalized


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, set):
        return [_json_safe_value(item) for item in sorted(value, key=repr)]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _json_safe_value(item) for key, item in value.items()}
    return value


def _ensure_json_safe(value: Any) -> None:
    try:
        json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SliceInvalidError(
            message="slice predicate value must be JSON serializable",
            details={"error": str(exc)},
        ) from exc


def _apply_slice_predicate(field_expr: Any, *, op: str, value: Any) -> Any:
    if op == "==":
        return field_expr == value
    if op == "!=":
        return field_expr != value
    if op == "in":
        return field_expr.isin(list(value))
    if op == ">":
        return field_expr > value
    if op == ">=":
        return field_expr >= value
    if op == "<":
        return field_expr < value
    if op == "<=":
        return field_expr <= value
    if op == "between":
        lower, upper = value
        return (field_expr >= lower) & (field_expr <= upper)
    raise SliceInvalidError(message=f"unsupported slice predicate op {op!r}")


def apply_slice_to_dataset(
    table: ibis.Table,
    where: Mapping[str, Any] | None,
    *,
    dataset_ir: Any,
) -> ibis.Table:
    if not where:
        return table
    for field_name, raw_predicate in where.items():
        op, value = _normalize_slice_predicate(raw_predicate)
        field_expr = _resolve_slice_field(dataset_ir, field_name, table)
        table = table.filter(_apply_slice_predicate(field_expr, op=op, value=value))
    return table


@dataclass(frozen=True)
class ExecutionResult:
    df: pd.DataFrame
    duration_ms: int
    row_count: int
    query: QueryExecution | None = None


def _sql_execution_comment(session_id: str) -> str:
    safe_session_id = session_id.replace("*/", "* /").replace("\r", " ").replace("\n", " ")
    return f"/* from=marivo,session={safe_session_id} */"


def _prefix_sql_for_session(sql: Any, *, session_id: str) -> str:
    return f"{_sql_execution_comment(session_id)}\n{sql}"


_DATETRUNC_TO_NATIVE: dict[str, str] = {
    "second": "toStartOfSecond",
    "minute": "toStartOfMinute",
    "hour": "toStartOfHour",
    "day": "toStartOfDay",
    "week": "toMonday",
    "month": "toStartOfMonth",
    "quarter": "toStartOfQuarter",
    "year": "toStartOfYear",
}


def _fix_clickhouse_datetrunc(sql: str) -> str:
    """Replace dateTrunc with native ClickHouse toStartOf* functions.

    Ibis 12.0.0 generates dateTrunc('DAY', col) etc. for ClickHouse, but
    dateTrunc is unsupported or unreliable in ClickHouse 22.3. Native
    ClickHouse functions (toStartOfDay, toStartOfHour, toMonday, etc.)
    work in all versions.

    This transforms:
        dateTrunc('DAY', col)   → toStartOfDay(col)
        dateTrunc('HOUR', col)  → toStartOfHour(col)
        dateTrunc('WEEK', col)  → toMonday(col)
        etc.
    Any surrounding CAST wrapper is preserved.
    """

    def _replace_unit(match: re.Match[str]) -> str:
        unit = match.group(1).lower()
        native = _DATETRUNC_TO_NATIVE.get(unit)
        if native is None:
            return match.group(0)
        return f"{native}("

    return re.sub(r"dateTrunc\('([A-Za-z]+)',\s*", _replace_unit, sql)


def _backend_dialect(backend: Any) -> str:
    return getattr(backend, "name", "unknown")


_logger = logging.getLogger("marivo.analysis.executor")


def execute(
    expr: ibis.Expr,
    *,
    datasource_name: str,
    cache: BackendCache,
    session_id: str | None = None,
) -> ExecutionResult:
    backend = cache.get_or_create(datasource_name)
    original_compile_attr = getattr(backend, "__dict__", {}).get("compile", _MISSING_ATTR)
    original_compile = getattr(backend, "compile", None)
    captured_sql: str | None = None
    if callable(original_compile):
        if session_id is not None:

            def compile_with_prefix(
                self: Any, expr: ibis.Expr, /, *args: Any, **kwargs: Any
            ) -> str:
                nonlocal captured_sql
                sql = original_compile(expr, *args, **kwargs)

                dialect = _backend_dialect(backend)
                if dialect == "clickhouse":
                    sql = _fix_clickhouse_datetrunc(sql)

                prefixed = _prefix_sql_for_session(sql, session_id=session_id)
                captured_sql = prefixed
                return prefixed

            compile_fn = compile_with_prefix
        else:

            def compile_and_capture(
                self: Any, expr: ibis.Expr, /, *args: Any, **kwargs: Any
            ) -> str:
                nonlocal captured_sql
                sql: str = original_compile(expr, *args, **kwargs)

                dialect = _backend_dialect(backend)
                if dialect == "clickhouse":
                    sql = _fix_clickhouse_datetrunc(sql)

                captured_sql = sql
                return sql

            compile_fn = compile_and_capture
    else:
        compile_fn = None

    query_started_at = datetime.now(UTC)
    started = monotonic()
    try:
        if compile_fn is not None:
            backend.compile = MethodType(compile_fn, backend)
        raw = backend.execute(expr)
    except Exception as exc:
        query_finished_at = datetime.now(UTC)
        failed_duration = int((monotonic() - started) * 1000)
        if captured_sql is not None:
            dialect = _backend_dialect(backend)
            norm_sql, bind_params = normalize_sql(captured_sql, dialect=dialect)
            failed_qe = QueryExecution(
                query_id=gen_query_ref(),
                datasource=datasource_name,
                dialect=dialect,
                sql=captured_sql,
                normalized_sql=norm_sql,
                sql_digest=compute_sql_digest(norm_sql),
                bind_params=bind_params,
                row_count=0,
                duration_ms=failed_duration,
                started_at=query_started_at.isoformat(),
                finished_at=query_finished_at.isoformat(),
                status="failed",
                output_ref=None,
            )
            _logger.warning(
                "Query failed: datasource=%s dialect=%s digest=%s sql=%s",
                datasource_name,
                dialect,
                failed_qe.sql_digest,
                captured_sql[:200],
            )
        raise BackendError(
            message=str(exc),
            details=_debug_details(expr, datasource_name),
        ) from exc
    finally:
        if compile_fn is not None:
            if original_compile_attr is _MISSING_ATTR:
                with suppress(AttributeError):
                    delattr(backend, "compile")
            else:
                backend.compile = original_compile_attr

    if cache.should_mark_validated(datasource_name):
        _datasource_registry._persist_backend_env_sourced_secrets(backend)
        cache.mark_validated(datasource_name)
    if isinstance(raw, pd.DataFrame):
        df = raw
    elif isinstance(raw, pd.Series):
        df = raw.to_frame()
    else:
        df = pd.DataFrame({"value": [raw]})

    query_finished_at = datetime.now(UTC)
    duration_ms = int((monotonic() - started) * 1000)

    qe: QueryExecution | None = None
    if captured_sql is not None:
        dialect = _backend_dialect(backend)
        norm_sql, bind_params = normalize_sql(captured_sql, dialect=dialect)
        qe = QueryExecution(
            query_id=gen_query_ref(),
            datasource=datasource_name,
            dialect=dialect,
            sql=captured_sql,
            normalized_sql=norm_sql,
            sql_digest=compute_sql_digest(norm_sql),
            bind_params=bind_params,
            row_count=len(df),
            duration_ms=duration_ms,
            started_at=query_started_at.isoformat(),
            finished_at=query_finished_at.isoformat(),
            status="succeeded",
            output_ref=None,
        )
        cache.record_query(qe)

    return ExecutionResult(
        df=df,
        duration_ms=duration_ms,
        row_count=len(df),
        query=qe,
    )


def _debug_details(expr: Any, datasource_name: str) -> dict[str, Any]:
    details: dict[str, Any] = {"datasource": datasource_name}
    if os.environ.get("MARIVO_ANALYSIS_DEBUG") == "1":
        with suppress(Exception):
            details["expr_sql"] = ibis.to_sql(expr)
    return details
