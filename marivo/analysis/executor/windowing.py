"""Window bound coercion, timezone resolution, and partition predicate helpers."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import ibis

from marivo.analysis.errors import (
    DataTypeMismatchError,
    TimezoneInvalidError,
    WindowInvalidError,
)
from marivo.analysis.executor.string_time import _classify_strptime_format, _parse_string_column
from marivo.analysis.session._connections import AnalysisConnectionRuntime
from marivo.analysis.timezone import zoneinfo_from_name
from marivo.analysis.windows.spec import AbsoluteWindow, is_date_only
from marivo.datasource.engines import profile_for_backend
from marivo.datasource.engines.base import GENERIC_PROFILE, EngineProfile

UTC_ZONE = ZoneInfo("UTC")
BackendDatetimeDecodePolicy = Literal["local_naive_label", "utc_naive_instant"]
BucketOutputKind = Literal["report_local_timestamp", "date_label", "hour_prefix_label"]

_ISO_LEXICOGRAPHIC_DAY_FORMATS = frozenset(
    {
        "%Y%m%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
    }
)

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


@dataclass(frozen=True)
class EffectiveTimeContext:
    report_tz: ZoneInfo
    datasource_read_tz: ZoneInfo
    declared_tz: ZoneInfo | None
    actual_field_tz: ZoneInfo | None
    effective_column_tz: ZoneInfo | None
    parse_kind: str | None
    time_data_type: str | None
    bucket_output_kind: BucketOutputKind
    backend_datetime_decode_policy: BackendDatetimeDecodePolicy


def _encode_window_bound(iso_string: str, time_meta: Any) -> Any:
    data_type = time_meta.data_type
    fmt = time_meta.format

    if data_type == "date":
        return ibis.date(iso_string)
    if data_type == "timestamp":
        return ibis.timestamp(iso_string)

    if data_type in {"string", "integer"}:
        if fmt is None or not fmt.startswith("%"):
            raise WindowInvalidError(
                message=f"unsupported window bound format (data_type={data_type}, format={fmt!r})",
                details={"data_type": data_type, "format": fmt},
            )
        classification = _classify_strptime_format(fmt)

        # Hour-only formats cannot encode a window bound on their own; they
        # require a composite required_prefix field for date context.
        if classification in {"hour_only", "hour_only_minute"}:
            raise WindowInvalidError(
                message=f"unsupported window bound format (data_type={data_type}, format={fmt!r})",
                details={"data_type": data_type, "format": fmt},
            )

        # Hour-precision formats: parse ISO bound and reformat
        if classification == "hour":
            parsed_dt, _is_date_bound = _parse_partition_datetime(
                iso_string, fmt=fmt, tz=UTC_ZONE, bound_name="bound"
            )
            result = _format_hour_precision_partition_literal(parsed_dt, fmt)
            return int(result) if data_type == "integer" else result

        # Day-precision and other time-bearing formats: parse ISO and reformat
        parsed, _ = _parse_partition_datetime(iso_string, fmt=None, tz=UTC_ZONE, bound_name="bound")
        result = parsed.strftime(fmt)
        return int(result) if data_type == "integer" else result

    raise WindowInvalidError(
        message=f"unsupported window bound format (data_type={data_type}, format={fmt!r})",
        details={"data_type": data_type, "format": fmt},
    )


def _is_lexicographic_day_format(fmt: str | None) -> bool:
    """True when a day format is ISO-ordered so lexicographic order matches dates.

    Only year-first, fixed-width formats have the property that string ordering
    matches chronological ordering. Month-first or day-first formats (e.g.
    ``%m/%d/%Y``, ``%d/%m/%Y``) are fixed-width but NOT lexicographically
    sortable, so they must be parsed via STRPTIME before comparison.
    """
    return fmt in _ISO_LEXICOGRAPHIC_DAY_FORMATS


def _is_day_partition_meta(time_meta: Any) -> bool:
    parse_kind = getattr(time_meta, "parse_kind", None)
    data_type = time_meta.data_type
    # strptime with string/integer data_type (or deferred data_type) is a
    # partition-style dimension; check the format classification.
    if parse_kind == "strptime" or data_type in {"string", "integer"}:
        fmt = time_meta.format
        if fmt is None or not fmt.startswith("%"):
            return False
        return _classify_strptime_format(fmt) == "day"
    return False


def _is_hour_precision_partition_meta(time_meta: Any) -> bool:
    parse_kind = getattr(time_meta, "parse_kind", None)
    data_type = time_meta.data_type
    if parse_kind != "strptime" and data_type not in {"string", "integer"}:
        return False
    fmt = time_meta.format
    if fmt is None or not fmt.startswith("%"):
        return False
    return _classify_strptime_format(fmt) == "hour"


def _is_hour_only_partition_meta(time_meta: Any) -> bool:
    """True for string/integer hour-only time fields that use required_prefix.

    Hour-only fields carry no date component in their own value; they rely on a
    separate day-level required_prefix field to supply the date context.
    """
    parse_kind = getattr(time_meta, "parse_kind", None)
    if parse_kind == "hour_prefix":
        return True
    data_type = time_meta.data_type
    if data_type not in {"string", "integer"}:
        return False
    return getattr(time_meta, "required_prefix", None) is not None


def _parse_hour_precision_literal(value: str, fmt: str | None) -> datetime | None:
    """Parse a compact hour-precision partition bound string using its strptime format.

    Returns None if fmt is not an hour-precision strptime or the value does not
    match the format exactly.
    """
    if fmt is None or not fmt.startswith("%"):
        return None
    if _classify_strptime_format(fmt) != "hour":
        return None
    try:
        return datetime.strptime(value, fmt)
    except ValueError:
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
    if fmt is None or not fmt.startswith("%"):
        raise WindowInvalidError(message=f"unsupported hour partition format {fmt!r}")
    return value.strftime(fmt)


def _encode_hour_precision_bound(value: datetime, time_meta: Any) -> Any:
    literal = _format_hour_precision_partition_literal(value, time_meta.format)
    return int(literal) if time_meta.data_type == "integer" else literal


def _encode_hour_only_bound(hour: int, time_meta: Any) -> Any:
    if time_meta.data_type == "integer":
        return hour
    return f"{hour:02d}"


def _encode_partition_date_bound(value: date, time_meta: Any) -> Any:
    return _encode_window_bound(value.isoformat(), time_meta)


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
    """Resolve a date-only end bound to midnight of that date in report_tz.

    For [start, end) semantics the exclusive upper bound is midnight of the
    stated end date itself (no +1 day).  Delegates to ``_coerce_bound_datetime``
    which already resolves date-only strings to midnight of the stated date.
    """
    return _coerce_bound_datetime(value, tz=tz, bound_name=bound_name)


def _declared_timezone(time_meta: Any) -> str | None:
    value = getattr(time_meta, "timezone", None)
    return value if isinstance(value, str) and value else None


def _is_time_bearing_string_integer_meta(time_meta: Any) -> bool:
    from marivo.semantic.ir import is_time_bearing_format

    data_type = getattr(time_meta, "data_type", None)
    if data_type not in {"string", "integer"}:
        return False
    return is_time_bearing_format(time_meta.format)


def _field_timezone(field_expr: Any) -> str | None:
    with suppress(Exception):
        dtype = field_expr.type()
        timezone = getattr(dtype, "timezone", None)
        return str(timezone) if timezone else None
    return None


def _bucket_output_kind(time_meta: Any) -> BucketOutputKind:
    data_type = getattr(time_meta, "data_type", None)
    if data_type == "date":
        return "date_label"
    if data_type is None:
        return "report_local_timestamp"
    if _is_day_partition_meta(time_meta):
        return "date_label"
    if _is_hour_only_partition_meta(time_meta):
        return "hour_prefix_label"
    if data_type in {"datetime", "timestamp"}:
        return "report_local_timestamp"
    if _is_time_bearing_string_integer_meta(time_meta):
        return "report_local_timestamp"
    return "date_label"


def effective_time_context(
    time_meta: Any,
    *,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    field_expr: Any | None = None,
    backend_policy: BackendDatetimeDecodePolicy = "local_naive_label",
) -> EffectiveTimeContext:
    declared_name = _declared_timezone(time_meta)
    declared_tz = zoneinfo_from_name(declared_name) if declared_name is not None else None
    data_type = getattr(time_meta, "data_type", None)
    parse_kind = getattr(time_meta, "parse_kind", None)
    actual_name = _field_timezone(field_expr) if field_expr is not None else None
    actual_tz = zoneinfo_from_name(actual_name) if actual_name is not None else None

    if actual_name is not None and declared_name is not None and actual_name != declared_name:
        raise TimezoneInvalidError(
            message="timezone declaration conflicts with the time field expression timezone",
            details={
                "kind": "TimezoneDeclarationConflict",
                "declared": declared_name,
                "actual": actual_name,
            },
        )

    effective_column_tz: ZoneInfo | None
    if data_type is None:
        effective_column_tz = declared_tz or datasource_read_tz
    elif data_type in {"datetime", "timestamp"}:
        effective_column_tz = actual_tz or declared_tz or datasource_read_tz
    elif _is_time_bearing_string_integer_meta(time_meta):
        effective_column_tz = declared_tz or datasource_read_tz
    else:
        effective_column_tz = None

    return EffectiveTimeContext(
        report_tz=report_tz,
        datasource_read_tz=datasource_read_tz,
        declared_tz=declared_tz,
        actual_field_tz=actual_tz,
        effective_column_tz=effective_column_tz,
        parse_kind=parse_kind if isinstance(parse_kind, str) else None,
        time_data_type=data_type if isinstance(data_type, str) else None,
        bucket_output_kind=_bucket_output_kind(time_meta),
        backend_datetime_decode_policy=backend_policy,
    )


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
    # Ensure data_type is resolved before validation.
    _ensure_resolved_data_type(field_expr, time_meta)
    parse_kind = getattr(time_meta, "parse_kind", None)
    declared = time_meta.data_type
    # When parse was deferred, data_type was just inferred from ibis dtype.
    # Check if the inferred type is non-temporal (string/integer without parse).
    if parse_kind is None:
        if declared in {"string", "integer"}:
            try:
                dtype_name = str(field_expr.type())
            except Exception:
                return
            raise DataTypeMismatchError(
                message=f"time_dimension column has ibis dtype {dtype_name!r} which is "
                f"not a native temporal type; provide parse=ms.strptime(...) "
                f"for string/integer time columns.",
                hint="Add parse=ms.strptime(format) to the time_dimension declaration.",
                details={
                    "kind": "InferredNonTemporal",
                    "inferred_data_type": declared,
                    "actual_ibis_dtype": dtype_name,
                },
            )
        # A date column cannot support sub-day granularity — ibis would raise
        # SignatureValidationError when trying to bucket a DateColumn at hour/
        # minute/second grain.  Fail closed with a Marivo error instead.
        if declared == "date":
            granularity = getattr(time_meta, "granularity", None)
            if granularity in {"hour", "minute", "second"}:
                raise DataTypeMismatchError(
                    message=(
                        f"time_dimension inferred data_type='date' but "
                        f"granularity={granularity!r} requires sub-day resolution. "
                        "Date columns do not carry time-of-day information."
                    ),
                    hint=(
                        "Use a datetime/timestamp column for sub-day granularity, "
                        "or change granularity to 'day' or coarser."
                    ),
                    details={
                        "kind": "InferredDateWithSubDayGranularity",
                        "inferred_data_type": declared,
                        "granularity": granularity,
                    },
                )
        return
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
            message=f"time_dimension declared data_type={declared!r} but the expression "
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
            message=f"time_dimension declared data_type={declared!r} but the expression "
            f"produces unexpected ibis dtype {dtype_name!r}; "
            f"temporal time dimensions require date or timestamp dtype.",
            hint="Adjust the body to produce a date or timestamp expression, "
            "or change data_type to match the column's actual dtype.",
            details={
                "kind": "DataTypeUnexpectedForTemporal",
                "declared": declared,
                "actual_ibis_dtype": dtype_name,
            },
        )


def _infer_data_type_from_ibis(field_expr: Any) -> str | None:
    """Infer declared data_type from an ibis expression's dtype.

    Returns one of ``"date"``, ``"datetime"``, ``"timestamp"``,
    ``"string"``, ``"integer"``, or ``None`` when the dtype is
    not recognized.
    """
    try:
        dtype_name = str(field_expr.type())
    except Exception:
        return None
    normalized = _normalize_ibis_dtype(dtype_name)
    compatible = _IBIS_DTYPE_TO_DECLARED.get(normalized)
    if compatible is None:
        return None
    return sorted(compatible)[0]


def resolve_time_parse(dimension_ir: Any, field_expr: Any) -> Any:
    """Resolve the full SemanticParse from a DimensionIR and materialized ibis expression.

    When ``dimension_ir.parse`` is not None, returns it directly (filling in
    ``data_type`` on StrptimeParse/HourPrefixParse if still absent).
    When ``dimension_ir.parse`` is None, infers the parse variant from the
    column's ibis dtype.
    """
    from marivo.semantic.ir import DateParse, DatetimeParse

    parse = dimension_ir.parse
    if parse is not None:
        return parse
    # No explicit parse — infer from column type.
    inferred = _infer_data_type_from_ibis(field_expr)
    if inferred == "date":
        return DateParse()
    if inferred in ("datetime", "timestamp"):
        return DatetimeParse()
    if inferred in ("string", "integer"):
        raise DataTypeMismatchError(
            message=f"time_dimension column has ibis dtype mapping to data_type={inferred!r} "
            f"which is not a native temporal type; provide parse=ms.strptime(...) "
            f"for string/integer time columns.",
            hint="Add parse=ms.strptime(format) to the time_dimension declaration.",
            details={
                "kind": "InferredNonTemporalNoParse",
                "inferred_data_type": inferred,
            },
        )
    raise DataTypeMismatchError(
        message="time_dimension column has unrecognized ibis dtype; "
        "cannot infer parse variant. Provide an explicit parse parameter.",
        hint="Use parse=ms.datetime(...), ms.timestamp(...), or ms.strptime(...).",
        details={
            "kind": "InferredUnknown",
            "inferred_data_type": inferred,
        },
    )


def _is_naive_temporal_expr(field_expr: Any) -> bool:
    """Return True if the field expression has no actual timezone attached."""
    return _field_timezone(field_expr) is None


def _timestamp_bounds_for_column(
    window: AbsoluteWindow,
    *,
    report_tz: ZoneInfo,
    column_tz: ZoneInfo,
    bound_name: str,
    value: str,
) -> datetime:
    """Convert a window bound from report-local to column-local naive datetime.

    The bound is first resolved as a report-local instant, then projected
    into column_tz space and stripped of tzinfo so it can be compared
    against naive timestamp column values.
    """
    instant_utc = _coerce_bound_datetime(value, tz=report_tz, bound_name=bound_name)
    return instant_utc.astimezone(column_tz).replace(tzinfo=None)


def _exclusive_end_for_column(
    window: AbsoluteWindow,
    *,
    report_tz: ZoneInfo,
    column_tz: ZoneInfo,
) -> datetime:
    upper_utc = _coerce_bound_datetime(window.end, tz=report_tz, bound_name="end")
    return upper_utc.astimezone(column_tz).replace(tzinfo=None)


def _column_timezone(time_meta: Any, *, datasource_read_tz: ZoneInfo) -> ZoneInfo:
    context = effective_time_context(
        time_meta,
        report_tz=datasource_read_tz,
        datasource_read_tz=datasource_read_tz,
    )
    return context.effective_column_tz or datasource_read_tz


def _ensure_resolved_data_type(field_expr: Any, time_meta: Any) -> None:
    """Resolve data_type from the ibis expression when it was not declared.

    When ``parse_kind is None`` (deferred parse) or when ``data_type`` is
    still a placeholder (strptime/hour_prefix no longer carry data_type),
    this function infers the actual type from the ibis expression and patches
    the adapter in place.
    """
    parse_kind = getattr(time_meta, "parse_kind", None)
    # Deferred parse — always resolve
    if parse_kind is None:
        inferred = _infer_data_type_from_ibis(field_expr)
        if inferred is not None:
            try:
                time_meta.data_type = inferred
            except AttributeError:
                object.__setattr__(time_meta, "data_type", inferred)
        return
    # strptime/hour_prefix — data_type was removed from the IR; resolve from column.
    # The adapter may have set a default ("string") but the actual column could be
    # integer, so always infer from the ibis expression.
    if parse_kind in {"strptime", "hour_prefix"}:
        inferred = _infer_data_type_from_ibis(field_expr)
        if inferred in {"string", "integer"}:
            try:
                time_meta.data_type = inferred
            except AttributeError:
                object.__setattr__(time_meta, "data_type", inferred)


def _validate_time_field_timezone(field_expr: Any, time_meta: Any) -> None:
    _ensure_resolved_data_type(field_expr, time_meta)
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
            hint="date and partition time fields do not support timezone declarations; remove timezone= or use a time-bearing datetime/timestamp parse.",
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
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    profile: EngineProfile = GENERIC_PROFILE,
) -> tuple[Any, Any]:
    _validate_time_field_timezone(field_expr, time_meta)
    _validate_time_field_dtype(field_expr, time_meta)
    data_type = time_meta.data_type
    fmt = time_meta.format
    context = effective_time_context(
        time_meta,
        report_tz=report_tz,
        datasource_read_tz=datasource_read_tz,
        field_expr=field_expr,
    )

    # timestamp: compare as proper temporal values
    if data_type in {"datetime", "timestamp"}:
        if context.actual_field_tz is None:
            # Naive timestamp column: determine effective column timezone
            column_tz = context.effective_column_tz or datasource_read_tz
            lower_dt = _timestamp_bounds_for_column(
                window,
                report_tz=report_tz,
                column_tz=column_tz,
                bound_name="start",
                value=window.start,
            )
            if is_date_only(window.end):
                upper_utc = _local_midnight_of(window.end, tz=report_tz, bound_name="end")
                upper_dt = upper_utc.astimezone(column_tz).replace(tzinfo=None)
            else:
                upper_dt = _timestamp_bounds_for_column(
                    window,
                    report_tz=report_tz,
                    column_tz=column_tz,
                    bound_name="end",
                    value=window.end,
                )
            return (
                field_expr >= ibis.timestamp(lower_dt.isoformat()),
                field_expr < ibis.timestamp(upper_dt.isoformat()),
            )
        # Tz-aware timestamp column: compare as UTC instants
        lower_dt = _coerce_bound_datetime(window.start, tz=report_tz, bound_name="start")
        if is_date_only(window.end):
            upper_dt = _local_midnight_of(window.end, tz=report_tz, bound_name="end")
        else:
            upper_dt = _coerce_bound_datetime(window.end, tz=report_tz, bound_name="end")
        return (
            field_expr >= ibis.timestamp(lower_dt.isoformat()),
            field_expr < ibis.timestamp(upper_dt.isoformat()),
        )

    # Hour-precision partition formats: raw string/integer comparison
    if _is_hour_precision_partition_meta(time_meta):
        column_tz = context.effective_column_tz or report_tz
        if _parse_hour_precision_literal(str(window.start), fmt) is not None:
            lower_dt = _partition_start_datetime(
                window.start, fmt=fmt, tz=column_tz, bound_name="start"
            )
        else:
            lower_bound = _timestamp_bounds_for_column(
                window,
                report_tz=report_tz,
                column_tz=column_tz,
                bound_name="start",
                value=window.start,
            )
            lower_dt = _partition_start_datetime(
                lower_bound.isoformat(), fmt=fmt, tz=column_tz, bound_name="start"
            )
        if _parse_hour_precision_literal(str(window.end), fmt) is not None:
            upper_dt = _partition_exclusive_end_datetime(
                window.end, fmt=fmt, tz=column_tz, bound_name="end"
            )
        elif is_date_only(window.end):
            upper_dt = _exclusive_end_for_column(
                window, report_tz=report_tz, column_tz=column_tz
            ).replace(minute=0, second=0, microsecond=0)
        else:
            upper_bound = _timestamp_bounds_for_column(
                window,
                report_tz=report_tz,
                column_tz=column_tz,
                bound_name="end",
                value=window.end,
            )
            upper_dt = _partition_exclusive_end_datetime(
                upper_bound.isoformat(), fmt=fmt, tz=column_tz, bound_name="end"
            )
        lower = _encode_hour_precision_bound(lower_dt, time_meta)
        upper = _encode_hour_precision_bound(upper_dt, time_meta)
        return (field_expr >= lower, field_expr < upper)

    # ISO-ordered day-precision partition formats: raw string/integer
    # comparison is pushdown-friendly AND correct, because lexicographic order
    # matches chronological order for these formats.
    # Non-ISO day formats (e.g. %m/%d/%Y) fall through to the STRPTIME path
    # below, since their lexicographic order does not match dates.
    if _is_day_partition_meta(time_meta) and _is_lexicographic_day_format(fmt):
        lower = _encode_window_bound(window.start, time_meta)
        upper = _encode_window_bound(window.end, time_meta)
        return (field_expr >= lower, field_expr < upper)

    # Strptime formats: parse column into temporal type and compare
    if (
        data_type in {"string", "integer"}
        and fmt is not None
        and fmt.startswith("%")
        and not _is_hour_only_partition_meta(time_meta)
    ):
        if report_tz is None:
            raise WindowInvalidError(
                message="strptime format time fields require an explicit report timezone",
                hint="Pass timezone= when attaching the session.",
                details={"format": fmt},
            )
        parsed_expr = _parse_string_column(field_expr, time_meta, profile=profile)
        classification = _classify_strptime_format(fmt)
        column_tz = context.effective_column_tz or report_tz
        if classification == "day":
            lower_dt = _coerce_bound_datetime(window.start, tz=report_tz, bound_name="start")
        else:
            lower_dt = _timestamp_bounds_for_column(
                window,
                report_tz=report_tz,
                column_tz=column_tz,
                bound_name="start",
                value=window.start,
            )

        if classification == "day":
            if is_date_only(window.end):
                upper_dt = _local_midnight_of(window.end, tz=report_tz, bound_name="end")
                return (
                    parsed_expr >= ibis.date(lower_dt.date().isoformat()),
                    parsed_expr < ibis.date(upper_dt.date().isoformat()),
                )
            upper_dt = _coerce_bound_datetime(window.end, tz=report_tz, bound_name="end")
            return (
                parsed_expr >= ibis.date(lower_dt.date().isoformat()),
                parsed_expr < ibis.date(upper_dt.date().isoformat()),
            )
        else:
            if is_date_only(window.end):
                upper_dt = _exclusive_end_for_column(
                    window, report_tz=report_tz, column_tz=column_tz
                )
                return (
                    parsed_expr >= ibis.timestamp(lower_dt.isoformat()),
                    parsed_expr < ibis.timestamp(upper_dt.isoformat()),
                )
            upper_dt = _timestamp_bounds_for_column(
                window,
                report_tz=report_tz,
                column_tz=column_tz,
                bound_name="end",
                value=window.end,
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
            message=f"dataset '{dataset_ir.name}' has no @ms.time_dimension",
        )
    if len(time_fields) == 1:
        return time_fields[0]
    requested = window.get("time_dimension")
    if requested:
        for field in time_fields:
            if field.name == requested or field.semantic_id == requested:
                return field
        candidates = [field.name for field in time_fields]
        raise WindowInvalidError(
            message=(
                f"time_dimension={requested!r} is not on dataset '{dataset_ir.name}'; "
                f"candidates: {candidates}"
            ),
            hint="Pass one of the dataset time dimensions as observe(..., time_dimension=...).",
            details={
                "candidates": candidates,
                "fix_snippet": (
                    'session.observe(session.catalog.get("metric.sales.revenue"), '
                    'time_scope={"start": "2026-07-01", "end": "2026-08-01"}, '
                    'time_dimension=session.catalog.get("time_dimension.<domain.entity.time_dimension>").ref)'
                ),
            },
        )
    # No explicit request — check for a declared default
    defaults = [f for f in time_fields if getattr(f, "is_default", False)]
    if len(defaults) == 1:
        return defaults[0]
    candidates = [field.name for field in time_fields]
    raise WindowInvalidError(
        message=f"dataset '{dataset_ir.name}' has multiple time_dimensions: {candidates}",
        hint=(
            "Pass observe(..., time_dimension=...) to choose the time axis, "
            "or mark one time dimension as @ms.time_dimension(..., is_default=True) "
            "in the semantic definition."
        ),
        details={
            "candidates": candidates,
            "fix_snippet": (
                'session.observe(session.catalog.get("metric.sales.revenue"), '
                'time_scope={"start": "2026-07-01", "end": "2026-08-01"}, '
                'time_dimension=session.catalog.get("time_dimension.<domain.entity.time_dimension>").ref)'
            ),
        },
    )


def resolve_window_time_field(dataset_ir: Any, *, window: AbsoluteWindow) -> Any:
    time_dimension = {"time_dimension": window.time_dimension} if window.time_dimension else {}
    return _resolve_time_field(dataset_ir, time_dimension)


def _resolve_required_prefix_time_field(dataset_ir: Any, hour_field_ir: Any) -> Any | None:
    if hour_field_ir.time_meta is None:
        return None
    prefix = getattr(hour_field_ir.time_meta, "required_prefix", None)
    if prefix:
        for field in dataset_ir.fields.values():
            if not getattr(field, "is_time", False):
                continue
            if field.name == prefix or field.semantic_id == prefix:
                return field
    # Fallback for catalog-backed fields where required_prefix is not set
    # but parse_kind indicates hour_prefix: find the default date-level time field.
    parse_kind = getattr(hour_field_ir.time_meta, "parse_kind", None)
    if parse_kind == "hour_prefix":
        for field in dataset_ir.fields.values():
            if not getattr(field, "is_time", False):
                continue
            field_meta = getattr(field, "time_meta", None)
            if field_meta is None:
                continue
            field_data_type = getattr(field_meta, "data_type", None)
            if field_data_type in {"date", "datetime", "timestamp"} and getattr(
                field, "is_default", False
            ):
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
    report_tz: ZoneInfo,
) -> Any | None:
    if hour_field_ir.time_meta is None or not _is_hour_only_partition_meta(hour_field_ir.time_meta):
        return None
    date_field_ir = _resolve_required_prefix_time_field(dataset_ir, hour_field_ir)
    if date_field_ir is None or date_field_ir.time_meta is None:
        return None
    if not _is_day_partition_meta(date_field_ir.time_meta):
        return None

    hour_fmt = hour_field_ir.time_meta.format
    lower_dt = _partition_start_datetime(
        window.start, fmt=hour_fmt, tz=report_tz, bound_name="start"
    )
    upper_dt = _partition_exclusive_end_datetime(
        window.end, fmt=hour_fmt, tz=report_tz, bound_name="end"
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


def datasource_read_timezone(
    cache: AnalysisConnectionRuntime,
    datasource_name: str,
) -> ZoneInfo:
    resolved = cache.engine_timezone(datasource_name)
    tz = getattr(resolved, "engine_timezone_tz", None)
    if isinstance(tz, ZoneInfo):
        return tz
    name = getattr(resolved, "engine_timezone_name", None)
    if isinstance(name, str):
        return zoneinfo_from_name(name)
    from marivo.analysis.timezone import resolve_system_timezone

    fallback = resolve_system_timezone()
    if isinstance(fallback.tz, ZoneInfo):
        return fallback.tz
    return ZoneInfo("UTC")


def datasource_engine_profile(
    cache: AnalysisConnectionRuntime,
    datasource_name: str,
) -> EngineProfile:
    """Resolve the engine profile for a datasource.

    Mirrors :func:`datasource_read_timezone` so the intent layer can resolve the
    profile once and thread it into the expression builders, which gate
    strptime translation and SQL postprocessing on it.
    """
    backend = cache.get_or_create(datasource_name)
    return profile_for_backend(backend)


def apply_window_to_dataset(
    table: ibis.Table,
    window: AbsoluteWindow | Mapping[str, Any] | None,
    *,
    dataset_ir: Any,
    report_tz: ZoneInfo = UTC_ZONE,
    datasource_read_tz: ZoneInfo = UTC_ZONE,
    profile: EngineProfile = GENERIC_PROFILE,
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
            time_dimension=window.get("time_dimension"),
        )
    time_field_ir = resolve_window_time_field(dataset_ir, window=normalized_window)
    if time_field_ir.time_meta is None:
        raise WindowInvalidError(message=f"field '{time_field_ir.name}' has no time metadata")
    composite_predicate = _composite_hour_partition_predicate(
        table,
        normalized_window,
        dataset_ir=dataset_ir,
        hour_field_ir=time_field_ir,
        report_tz=report_tz,
    )
    if composite_predicate is not None:
        return table.filter(composite_predicate)
    field_expr = time_field_ir.fn(table)
    lower_predicate, upper_predicate = _window_bound_predicates(
        field_expr,
        normalized_window,
        time_field_ir.time_meta,
        report_tz=report_tz,
        datasource_read_tz=datasource_read_tz,
        profile=profile,
    )
    return table.filter(lower_predicate, upper_predicate)
