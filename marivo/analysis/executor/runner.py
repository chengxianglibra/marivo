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
from itertools import pairwise
from time import monotonic
from types import MethodType
from typing import Any, Literal
from zoneinfo import ZoneInfo

import ibis
import pandas as pd

from marivo.analysis.errors import (
    BackendError,
    DataTypeMismatchError,
    SliceInvalidError,
    TimezoneInvalidError,
    WindowInvalidError,
)
from marivo.analysis.executor.query_record import (
    QueryExecution,
    compute_sql_digest,
    gen_query_ref,
    normalize_sql,
)
from marivo.analysis.session._connections import AnalysisConnectionRuntime
from marivo.analysis.timezone import zoneinfo_from_name
from marivo.analysis.windows.grain import _TRUNCATE_CODE, Grain
from marivo.analysis.windows.spec import AbsoluteWindow, is_date_only
from marivo.datasource import secrets as _secrets

UTC_ZONE = ZoneInfo("UTC")
_MISSING_ATTR = object()
BackendDatetimeDecodePolicy = Literal["local_naive_label", "utc_naive_instant"]
BucketOutputKind = Literal["report_local_timestamp", "date_label", "hour_prefix_label"]

_DATE_DIRECTIVES = frozenset({"%Y", "%y", "%m", "%d", "%j", "%U", "%W"})
_HOUR_DIRECTIVES = frozenset({"%H", "%I", "%k", "%l"})
_MINUTE_DIRECTIVES = frozenset({"%M"})
_SECOND_DIRECTIVES = frozenset({"%S"})
_SUBSECOND_DIRECTIVES = frozenset({"%f"})
_AMPM_DIRECTIVES = frozenset({"%p", "%P"})


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


_ISO_LEXICOGRAPHIC_DAY_FORMATS = frozenset(
    {
        "%Y%m%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
    }
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


def _parse_string_column(field_expr: Any, time_meta: Any) -> Any:
    """Parse a string/integer column into a temporal type using as_date/as_timestamp."""
    data_type = time_meta.data_type
    fmt = time_meta.format
    if fmt is None or not fmt.startswith("%"):
        raise WindowInvalidError(
            message=f"cannot parse string column without a strptime format "
            f"(data_type={data_type!r}, format={fmt!r})",
        )
    if data_type == "integer":
        string_expr = field_expr.cast("string")
    elif data_type == "string":
        string_expr = field_expr
    else:
        raise WindowInvalidError(
            message=f"_parse_string_column only supports string/integer, got {data_type!r}",
        )
    classification = _classify_strptime_format(fmt)
    if classification == "day":
        return string_expr.as_date(fmt)
    return string_expr.as_timestamp(fmt)


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


def backend_datetime_decode_policy(dialect: str) -> BackendDatetimeDecodePolicy:
    return "utc_naive_instant" if dialect.lower() == "clickhouse" else "local_naive_label"


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
        parsed_expr = _parse_string_column(field_expr, time_meta)
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
                    'timescope={"start": "2026-07-01", "end": "2026-08-01"}, '
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
                'timescope={"start": "2026-07-01", "end": "2026-08-01"}, '
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


def apply_window_to_dataset(
    table: ibis.Table,
    window: AbsoluteWindow | Mapping[str, Any] | None,
    *,
    dataset_ir: Any,
    report_tz: ZoneInfo = UTC_ZONE,
    datasource_read_tz: ZoneInfo = UTC_ZONE,
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


def _timestamp_expr_in_report_timezone(
    ts_expr: Any,
    *,
    column_tz: ZoneInfo,
    report_tz: ZoneInfo,
    window: AbsoluteWindow,
) -> Any:
    start_utc = _coerce_bound_datetime(window.start, tz=report_tz, bound_name="start")
    end_utc = _coerce_bound_datetime(window.end, tz=report_tz, bound_name="end")
    boundaries = _timezone_offset_boundaries(
        start_utc,
        end_utc,
        column_tz=column_tz,
        report_tz=report_tz,
    )
    if len(boundaries) <= 2:
        return _shift_timestamp_expr(
            ts_expr,
            shift_seconds=_report_local_shift_seconds(
                start_utc,
                column_tz=column_tz,
                report_tz=report_tz,
            ),
        )

    branches: list[tuple[Any, Any]] = []
    for segment_start, segment_end in pairwise(boundaries):
        lower = _instant_as_naive_in_zone(segment_start, column_tz)
        upper = _instant_as_naive_in_zone(segment_end, column_tz)
        shifted = _shift_timestamp_expr(
            ts_expr,
            shift_seconds=_report_local_shift_seconds(
                segment_start,
                column_tz=column_tz,
                report_tz=report_tz,
            ),
        )
        branches.append(
            (
                (ts_expr >= ibis.timestamp(lower.isoformat()))
                & (ts_expr < ibis.timestamp(upper.isoformat())),
                shifted,
            )
        )

    fallback = _shift_timestamp_expr(
        ts_expr,
        shift_seconds=_report_local_shift_seconds(
            start_utc,
            column_tz=column_tz,
            report_tz=report_tz,
        ),
    )
    first, *rest = branches
    return ibis.cases(first, *rest, else_=fallback)


def _report_local_shift_seconds(
    instant_utc: datetime,
    *,
    column_tz: ZoneInfo,
    report_tz: ZoneInfo,
) -> int:
    column_offset = instant_utc.astimezone(column_tz).utcoffset()
    report_offset = instant_utc.astimezone(report_tz).utcoffset()
    column_seconds = int(column_offset.total_seconds()) if column_offset is not None else 0
    report_seconds = int(report_offset.total_seconds()) if report_offset is not None else 0
    return report_seconds - column_seconds


def _shift_timestamp_expr(ts_expr: Any, *, shift_seconds: int) -> Any:
    if shift_seconds == 0:
        return ts_expr
    return ts_expr + ibis.interval(seconds=shift_seconds)


def _instant_as_naive_in_zone(instant_utc: datetime, tz: ZoneInfo) -> datetime:
    return instant_utc.astimezone(tz).replace(tzinfo=None)


def _timezone_offset_boundaries(
    start_utc: datetime,
    end_utc: datetime,
    *,
    column_tz: ZoneInfo,
    report_tz: ZoneInfo,
) -> list[datetime]:
    if end_utc <= start_utc:
        return [start_utc, end_utc]

    boundaries = {start_utc, end_utc}
    for tz in {column_tz, report_tz}:
        boundaries.update(_timezone_offset_transitions(start_utc, end_utc, tz))
    return sorted(boundaries)


def _timezone_offset_transitions(
    start_utc: datetime,
    end_utc: datetime,
    tz: ZoneInfo,
) -> list[datetime]:
    transitions: list[datetime] = []
    step = timedelta(hours=1)
    left = start_utc
    left_offset = left.astimezone(tz).utcoffset()
    cursor = min(left + step, end_utc)
    while cursor <= end_utc:
        cursor_offset = cursor.astimezone(tz).utcoffset()
        if cursor_offset != left_offset:
            transition = _bisect_timezone_transition(left, cursor, tz, left_offset)
            if start_utc < transition < end_utc:
                transitions.append(transition)
            left_offset = cursor_offset
        left = cursor
        if cursor == end_utc:
            break
        cursor = min(cursor + step, end_utc)
    return transitions


def _bisect_timezone_transition(
    left: datetime,
    right: datetime,
    tz: ZoneInfo,
    left_offset: timedelta | None,
) -> datetime:
    while right - left > timedelta(seconds=1):
        midpoint = left + (right - left) / 2
        if midpoint.astimezone(tz).utcoffset() == left_offset:
            left = midpoint
        else:
            right = midpoint
    return right.replace(microsecond=0)


def bucket_time_expression(
    raw: Any,
    *,
    time_meta: Any,
    grain: Grain,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    window: AbsoluteWindow | None,
) -> Any:
    """Return a report-local bucket expression for a timestamp-like time field."""
    if time_meta.data_type in {"datetime", "timestamp"}:
        if window is None:
            raise WindowInvalidError(
                message="bucket_time_expression requires a window for timestamp bucketing.",
                details={"data_type": time_meta.data_type},
            )
        return _local_bucket_expr(
            raw,
            time_meta=time_meta,
            grain=grain,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            window=window,
        )
    if (
        time_meta.data_type in {"string", "integer"}
        and time_meta.format is not None
        and time_meta.format.startswith("%")
        and getattr(time_meta, "parse_kind", None) == "strptime"
    ):
        if window is None:
            raise WindowInvalidError(
                message="bucket_time_expression requires a window for strptime bucketing.",
                details={"data_type": time_meta.data_type, "format": time_meta.format},
            )
        parsed = _parse_string_column(raw, time_meta)
        classification = _classify_strptime_format(time_meta.format)
        grain_matches_classification = (
            (grain.is_day and classification == "day")
            or (grain.unit == "hour" and grain.count == 1 and classification == "hour")
            or (grain.unit == "minute" and grain.count == 1 and classification == "minute")
        )
        context = effective_time_context(
            time_meta,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            field_expr=raw,
        )
        if grain_matches_classification and (
            classification == "day"
            or context.effective_column_tz is None
            or context.effective_column_tz == report_tz
        ):
            return parsed
        column_tz = context.effective_column_tz or datasource_read_tz
        local_parsed = _timestamp_expr_in_report_timezone(
            parsed,
            column_tz=column_tz,
            report_tz=report_tz,
            window=window,
        )
        return bucket_start_expr(local_parsed, grain)
    raise WindowInvalidError(
        message="bucket_time_expression requires a datetime, timestamp, or strptime time field.",
        details={"data_type": time_meta.data_type},
    )


def _local_bucket_expr(
    raw: Any,
    *,
    time_meta: Any,
    grain: Grain,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    window: AbsoluteWindow,
) -> Any:
    """Compute a bucket-start expression that aligns instant or declared-naive
    timestamp fields to report-local calendar boundaries.

    For naive timestamp columns with a declared timezone, the shift converts
    from declared-local to report-local.  For naive timestamp columns with no
    declaration, the column is already in datasource-read-local space so the
    shift converts from read-local to report-local.
    """
    data_type = time_meta.data_type
    if data_type in {"datetime", "timestamp"}:
        ts_expr = raw
        context = effective_time_context(
            time_meta,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            field_expr=raw,
        )
        column_tz = context.effective_column_tz or datasource_read_tz
        if context.declared_tz is None and context.actual_field_tz is None:
            _logger.warning(
                "Time dimension %r has no declared timezone for naive %r column; "
                "assuming datasource read timezone %s. Add timezone= to @ms.time_dimension "
                "to avoid silent misalignment.",
                getattr(time_meta, "semantic_id", "?"),
                data_type,
                getattr(datasource_read_tz, "key", str(datasource_read_tz)),
            )
    else:
        raise WindowInvalidError(
            message=f"_local_bucket_expr only supports datetime/timestamp, "
            f"got data_type={data_type!r}",
        )

    local_expr = _timestamp_expr_in_report_timezone(
        ts_expr,
        column_tz=column_tz,
        report_tz=report_tz,
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


def combine_prefix_hour_to_timestamp(
    table: ibis.Table,
    *,
    hour_field_ir: Any,
    dataset_ir: Any,
) -> Any:
    """Combine a day-level prefix field and an hour-only column into a timestamp.

    Returns an ibis timestamp expression representing the start of each hour
    sample point.
    """
    prefix_field_ir = _resolve_required_prefix_time_field(dataset_ir, hour_field_ir)
    if prefix_field_ir is None or prefix_field_ir.time_meta is None:
        raise WindowInvalidError(
            message=f"hour_prefix time field '{hour_field_ir.name}' requires a "
            f"day-level prefix for timestamp construction",
        )
    prefix_date = _prefix_date_expr(table, prefix_field_ir)
    raw = hour_field_ir.fn(table)
    time_meta = hour_field_ir.time_meta
    hour_int = raw.cast("int") if time_meta.data_type == "string" else raw
    return prefix_date.cast("timestamp") + (hour_int * 3600).as_interval("s")


def _apply_hour_only_bucket(
    table: ibis.Table,
    *,
    raw: Any,
    field_ir: Any,
    window: AbsoluteWindow,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
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

    # Grain matches field: concatenate prefix + hour into sortable string.
    # lpad(2, "0") unconditionally handles both "1" and "01" source columns.
    hour_str = raw if time_meta.data_type == "string" else raw.cast("string")
    hour_str = hour_str.lpad(2, "0")
    prefix_fmt = prefix_field_ir.time_meta.format
    if prefix_fmt == "%Y-%m-%d":
        bucket = (prefix_raw + ibis.literal("-") + hour_str).name("bucket_start")
    elif prefix_fmt == "%Y%m%d":
        bucket = (prefix_raw + hour_str).name("bucket_start")
    else:
        # Fallback for unusual prefix formats
        prefix_date = _prefix_date_expr(table, prefix_field_ir)
        hour_int = raw.cast("int") if time_meta.data_type == "string" else raw
        date_ts = prefix_date.cast("timestamp")
        bucket = (date_ts + (hour_int * 3600).as_interval("s")).name("bucket_start")
    return table.mutate(bucket_start=bucket)


def ensure_bucket_start_timestamp(
    series: pd.Series,
    *,
    time_meta: Any,
    dataset_ir: Any,
    grain: Grain | None,
    report_tz: ZoneInfo | None = None,
    time_context: EffectiveTimeContext | None = None,
    backend_datetime_decode_policy: BackendDatetimeDecodePolicy = "local_naive_label",
) -> pd.Series:
    """Normalize bucket_start values after SQL execution.

    Handles two post-execution normalizations:

    1. String-to-timestamp conversion for hour-only partition fields
       (produced by ``_apply_hour_only_bucket``).
    2. Timezone normalization: tz-aware bucket_start values (e.g. from
       ClickHouse ``Nullable(DateTime)`` columns) are converted to
       report-local naive timestamps so that downstream consumers see
       business-timezone dates, not UTC.
    """
    if grain is None:
        return series

    if time_context is not None:
        report_tz = report_tz or time_context.report_tz
        backend_datetime_decode_policy = time_context.backend_datetime_decode_policy
        bucket_output_kind = time_context.bucket_output_kind
    else:
        bucket_output_kind = _bucket_output_kind(time_meta)

    # Timezone normalization: convert tz-aware timestamps to report-local naive.
    if report_tz is not None and isinstance(series.dtype, pd.DatetimeTZDtype):
        converted: pd.Series = series.dt.tz_convert(report_tz).dt.tz_localize(None)
        return converted

    if (
        report_tz is not None
        and backend_datetime_decode_policy == "utc_naive_instant"
        and bucket_output_kind == "report_local_timestamp"
        and pd.api.types.is_datetime64_any_dtype(series)
    ):
        converted = series.dt.tz_localize(UTC_ZONE).dt.tz_convert(report_tz).dt.tz_localize(None)
        return converted

    if not pd.api.types.is_string_dtype(series):
        return series

    if time_meta is None or not _is_hour_only_partition_meta(time_meta):
        return series

    required_prefix = getattr(time_meta, "required_prefix", None)
    if not required_prefix:
        return series

    # Look up the prefix field directly by name/semantic_id.
    prefix_field_ir = None
    for field in dataset_ir.fields.values():
        if getattr(field, "is_time", False) and (
            field.name == required_prefix or field.semantic_id == required_prefix
        ):
            prefix_field_ir = field
            break
    if prefix_field_ir is None or prefix_field_ir.time_meta is None:
        return series

    prefix_fmt = prefix_field_ir.time_meta.format

    # Map (prefix_fmt, grain) → strptime format for the concatenated bucket_start.
    # _apply_hour_only_bucket only produces string bucket_start for count=1
    # hour grain or day grain; multi-hour grains skip string concatenation
    # and use the timestamp fallback path instead.
    if grain.unit == "hour" and grain.count == 1:
        fmt_map = {
            "%Y-%m-%d": "%Y-%m-%d-%H",
            "%Y%m%d": "%Y%m%d%H",
        }
    elif grain.is_day:
        fmt_map = {
            "%Y-%m-%d": "%Y-%m-%d",
            "%Y%m%d": "%Y%m%d",
        }
    else:
        return series

    if prefix_fmt is None:
        return series

    strptime_fmt = fmt_map.get(prefix_fmt)
    if strptime_fmt is None:
        return series

    return pd.to_datetime(series, format=strptime_fmt, errors="coerce")


def apply_time_series_bucket(
    table: ibis.Table,
    *,
    field_ir: Any,
    window: AbsoluteWindow,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
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
    if window.grain is None:
        return table

    # Strptime format: parse the column into a temporal type
    # (excludes hour-only fields that rely on required_prefix)
    if (
        data_type in {"string", "integer"}
        and fmt is not None
        and fmt.startswith("%")
        and not _is_hour_only_partition_meta(time_meta)
    ):
        parsed = _parse_string_column(raw, time_meta)
        classification = _classify_strptime_format(fmt)
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
            context = effective_time_context(
                time_meta,
                report_tz=report_tz,
                datasource_read_tz=datasource_read_tz,
                field_expr=raw,
            )
            if (
                classification == "day"
                or context.effective_column_tz is None
                or context.effective_column_tz == report_tz
            ):
                bucket = parsed.name("bucket_start")
            else:
                column_tz = context.effective_column_tz or datasource_read_tz
                local_parsed = _timestamp_expr_in_report_timezone(
                    parsed,
                    column_tz=column_tz,
                    report_tz=report_tz,
                    window=window,
                )
                bucket = bucket_start_expr(local_parsed, window.grain)
        else:
            context = effective_time_context(
                time_meta,
                report_tz=report_tz,
                datasource_read_tz=datasource_read_tz,
                field_expr=raw,
            )
            column_tz = context.effective_column_tz or datasource_read_tz
            local_parsed = _timestamp_expr_in_report_timezone(
                parsed,
                column_tz=column_tz,
                report_tz=report_tz,
                window=window,
            )
            bucket = bucket_start_expr(local_parsed, window.grain)
        return table.mutate(bucket_start=bucket)

    # Timestamp: bucket in report-local calendar
    if data_type in {"datetime", "timestamp"}:
        bucket = _local_bucket_expr(
            raw,
            time_meta=time_meta,
            grain=window.grain,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
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
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
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
            _supported = sorted(_SUPPORTED_SLICE_OPS)
            raise SliceInvalidError(
                message=f"unsupported slice predicate op {op!r}; supported ops: {_supported}",
                details={"supported_ops": _supported},
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
    backend_dialect: str = "unknown"
    backend_datetime_decode_policy: BackendDatetimeDecodePolicy = "local_naive_label"


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
    cache: AnalysisConnectionRuntime,
    session_id: str | None = None,
) -> ExecutionResult:
    backend = cache.get_or_create(datasource_name)
    dialect = _backend_dialect(backend)
    decode_policy = backend_datetime_decode_policy(dialect)
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
        _secrets.persist_backend_env_sourced(backend)
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
        backend_dialect=dialect,
        backend_datetime_decode_policy=decode_policy,
    )


def _debug_details(expr: Any, datasource_name: str) -> dict[str, Any]:
    details: dict[str, Any] = {"datasource": datasource_name}
    if os.environ.get("MARIVO_ANALYSIS_DEBUG") == "1":
        with suppress(Exception):
            details["expr_sql"] = ibis.to_sql(expr)
    return details
