"""Window/slice filtering and ibis execution."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
import os
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

from marivo.analysis.errors import BackendError, SliceInvalidError, WindowInvalidError
from marivo.analysis.executor.backend import BackendCache
from marivo.analysis.windows.resolver import zoneinfo_from_name
from marivo.analysis.windows.spec import AbsoluteWindow

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


def _normalize_time_format(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower().strip()
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
    pair = (data_type, fmt)
    if pair not in _SUPPORTED_FORMATS:
        raise WindowInvalidError(
            message=f"v1 window encoder does not support (data_type, format)={pair}",
            hint="hh/h and custom formats are deferred.",
            details={"data_type": data_type, "format": fmt},
        )
    if data_type == "date":
        return ibis.date(iso_string)
    if data_type == "timestamp":
        return ibis.timestamp(iso_string)
    if data_type == "string":
        if fmt in _HOUR_PRECISION_FORMATS:
            parsed_dt, _is_date_bound = _parse_partition_datetime(
                iso_string, fmt=fmt, tz=UTC_ZONE, bound_name="bound"
            )
            return _format_hour_precision_partition_literal(parsed_dt, fmt)
        if fmt == "yyyy-mm-dd":
            return iso_string
        return iso_string[:10].replace("-", "")
    if data_type == "integer":
        if fmt == "yyyymmddhh":
            parsed_dt, _is_date_bound = _parse_partition_datetime(
                iso_string, fmt=fmt, tz=UTC_ZONE, bound_name="bound"
            )
            return int(_format_hour_precision_partition_literal(parsed_dt, fmt))
        if fmt == "yyyymmdd":
            return int(iso_string[:10].replace("-", ""))
        dt = datetime.fromisoformat(iso_string)
        return int(dt.timestamp())
    raise WindowInvalidError(message=f"unsupported window bound format {pair}")


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
    data_type = time_meta.data_type
    fmt = _normalize_time_format(time_meta.format)
    return data_type in {"string", "integer"} and fmt in _HOUR_ONLY_FORMATS


def _next_day_bound(value: str, *, bound_name: str) -> str:
    try:
        base_day = date.fromisoformat(value[:10])
    except (TypeError, ValueError) as exc:
        _raise_window_bound_invalid(
            bound_name=bound_name,
            value=value,
            tz=UTC_ZONE,
            error=exc,
        )
    return (base_day + timedelta(days=1)).isoformat()


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
    parsed, is_date_bound = _parse_partition_datetime(value, fmt=fmt, tz=tz, bound_name=bound_name)
    if is_date_bound:
        next_day = parsed.date() + timedelta(days=1)
        return datetime.combine(next_day, time.min, tzinfo=tz)
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


def _is_date_only(value: str) -> bool:
    if len(value) != 10 or "T" in value:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


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
    if _is_date_only(value):
        local_dt = datetime.combine(date.fromisoformat(value), time.min, tzinfo=tz)
        return local_dt.astimezone(UTC)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        _raise_window_bound_invalid(bound_name=bound_name, value=value, tz=tz, error=exc)
    dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
    return dt.astimezone(UTC)


def _next_local_midnight(value: str, *, tz: ZoneInfo, bound_name: str) -> datetime:
    if _is_date_only(value):
        try:
            base_day = date.fromisoformat(value)
        except ValueError as exc:
            _raise_window_bound_invalid(bound_name=bound_name, value=value, tz=tz, error=exc)
    else:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(normalized)
        except (TypeError, ValueError) as exc:
            _raise_window_bound_invalid(bound_name=bound_name, value=value, tz=tz, error=exc)
        dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
        base_day = dt.date()
    next_midnight = datetime.combine(base_day + timedelta(days=1), time.min, tzinfo=tz)
    return next_midnight.astimezone(UTC)


def _window_bound_predicates(
    field_expr: Any,
    window: AbsoluteWindow,
    time_meta: Any,
    *,
    session_tz: ZoneInfo,
) -> tuple[Any, Any]:
    data_type = time_meta.data_type
    fmt = _normalize_time_format(time_meta.format)
    if data_type == "timestamp":
        lower_dt = _coerce_bound_datetime(window.start, tz=session_tz, bound_name="start")
        if _is_date_only(window.end):
            upper_dt = _next_local_midnight(window.end, tz=session_tz, bound_name="end")
            return (
                field_expr >= ibis.timestamp(lower_dt.isoformat()),
                field_expr < ibis.timestamp(upper_dt.isoformat()),
            )
        upper_dt = _coerce_bound_datetime(window.end, tz=session_tz, bound_name="end")
        return (
            field_expr >= ibis.timestamp(lower_dt.isoformat()),
            field_expr <= ibis.timestamp(upper_dt.isoformat()),
        )
    if data_type == "integer" and fmt == "epoch_seconds":
        lower_epoch = int(
            _coerce_bound_datetime(window.start, tz=session_tz, bound_name="start").timestamp()
        )
        if _is_date_only(window.end):
            upper_epoch = int(
                _next_local_midnight(window.end, tz=session_tz, bound_name="end").timestamp()
            )
            return (field_expr >= lower_epoch, field_expr < upper_epoch)
        upper_epoch = int(
            _coerce_bound_datetime(window.end, tz=session_tz, bound_name="end").timestamp()
        )
        return (field_expr >= lower_epoch, field_expr <= upper_epoch)
    if _is_hour_precision_partition_meta(time_meta):
        lower_dt = _partition_start_datetime(
            window.start, fmt=fmt, tz=session_tz, bound_name="start"
        )
        upper_dt = _partition_exclusive_end_datetime(
            window.end, fmt=fmt, tz=session_tz, bound_name="end"
        )
        lower = _encode_hour_precision_bound(lower_dt, time_meta)
        upper = _encode_hour_precision_bound(upper_dt, time_meta)
        return (field_expr >= lower, field_expr < upper)
    lower = _encode_window_bound(window.start, time_meta)
    if _is_day_partition_meta(time_meta):
        upper = _encode_window_bound(_next_day_bound(window.end, bound_name="end"), time_meta)
        return (field_expr >= lower, field_expr < upper)
    upper = _encode_window_bound(window.end, time_meta)
    return (field_expr >= lower, field_expr <= upper)


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
                f"window.time_field={requested!r} is not on dataset '{dataset_ir.name}'; "
                f"candidates: {candidates}"
            ),
            hint="Pass one of the dataset time fields in window['time_field'].",
            details={
                "candidates": candidates,
                "fix_snippet": (
                    'session.observe(mv.MetricRef("sales.revenue"), '
                    'window={"start": "2026-07-01", "end": "2026-07-31", '
                    f'"time_field": "{first_candidate}"}})'
                ),
            },
        )
    candidates = [field.name for field in time_fields]
    first_candidate = candidates[0]
    raise WindowInvalidError(
        message=f"dataset '{dataset_ir.name}' has multiple time_fields: {candidates}",
        hint="Pass window['time_field'] to choose the time axis for this observe call.",
        details={
            "candidates": candidates,
            "fix_snippet": (
                'session.observe(mv.MetricRef("sales.revenue"), '
                'window={"start": "2026-07-01", "end": "2026-07-31", '
                f'"time_field": "{first_candidate}"}})'
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
            tz=window.get("tz"),
            time_field=window.get("time_field"),
        )
    time_field_ir = resolve_window_time_field(dataset_ir, window=normalized_window)
    if time_field_ir.time_meta is None:
        raise WindowInvalidError(message=f"field '{time_field_ir.name}' has no time metadata")
    effective_tz = session_tz
    if normalized_window.tz is not None:
        effective_tz = zoneinfo_from_name(normalized_window.tz)
    composite_predicate = _composite_hour_partition_predicate(
        table,
        normalized_window,
        dataset_ir=dataset_ir,
        hour_field_ir=time_field_ir,
        session_tz=effective_tz,
    )
    if composite_predicate is not None:
        return table.filter(composite_predicate)
    field_expr = time_field_ir.fn(table)
    lower_predicate, upper_predicate = _window_bound_predicates(
        field_expr,
        normalized_window,
        time_field_ir.time_meta,
        session_tz=effective_tz,
    )
    return table.filter(lower_predicate, upper_predicate)


def apply_time_series_bucket(
    table: ibis.Table,
    *,
    field_ir: Any,
    window: AbsoluteWindow,
    session_tz: ZoneInfo,
) -> ibis.Table:
    if field_ir.time_meta is None:
        raise WindowInvalidError(message=f"field '{field_ir.name}' has no time metadata")
    effective_tz = session_tz
    if window.tz is not None:
        effective_tz = zoneinfo_from_name(window.tz)
    raw = field_ir.fn(table)
    time_meta = field_ir.time_meta
    data_type = time_meta.data_type
    fmt = time_meta.format
    if window.grain is None:
        return table
    if window.grain == "day":
        if data_type in {"timestamp", "integer"} and (
            data_type != "integer" or fmt == "epoch_seconds"
        ):
            # Shift by the effective timezone's UTC offset before day bucketing.
            # This fixes local-day boundary assignment for timestamp/epoch fields.
            # Limitation: this uses a single offset anchored at window.start, so
            # windows spanning DST transitions may still need backend-specific TZ binning.
            anchor_utc = _coerce_bound_datetime(window.start, tz=effective_tz, bound_name="start")
            offset = anchor_utc.astimezone(effective_tz).utcoffset()
            offset_seconds = int(offset.total_seconds()) if offset is not None else 0
            ts_expr = raw.cast("timestamp") if data_type == "integer" else raw
            bucket = (
                (ts_expr + ibis.interval(seconds=offset_seconds)).cast("date").name("bucket_start")
            )
        else:
            bucket = raw.cast("date").name("bucket_start")
    else:
        if data_type == "integer" and fmt == "epoch_seconds":
            bucket = raw.cast("timestamp").truncate(window.grain).name("bucket_start")
        else:
            bucket = raw.truncate(window.grain).name("bucket_start")
    return table.mutate(bucket_start=bucket)


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


def _sql_execution_comment(session_id: str) -> str:
    safe_session_id = session_id.replace("*/", "* /").replace("\r", " ").replace("\n", " ")
    return f"/* from=marivo,session={safe_session_id} */"


def _prefix_sql_for_session(sql: Any, *, session_id: str) -> str:
    return f"{_sql_execution_comment(session_id)}\n{sql}"


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
    prefix_session_id: str | None = None
    compile_fn: Any = None
    if session_id is not None and callable(original_compile):
        prefix_session_id = session_id
        compile_fn = original_compile
    started = monotonic()
    try:
        if prefix_session_id is not None:

            def compile_with_prefix(self: Any, expr: ibis.Expr, /, *args: Any, **kwargs: Any) -> str:
                return _prefix_sql_for_session(
                    compile_fn(expr, *args, **kwargs),
                    session_id=prefix_session_id,
                )

            backend.compile = MethodType(compile_with_prefix, backend)
        raw = backend.execute(expr)
    except Exception as exc:
        raise BackendError(
            message=str(exc),
            details=_debug_details(expr, datasource_name),
        ) from exc
    finally:
        if prefix_session_id is not None:
            if original_compile_attr is _MISSING_ATTR:
                with suppress(AttributeError):
                    delattr(backend, "compile")
            else:
                backend.compile = original_compile_attr
    if isinstance(raw, pd.DataFrame):
        df = raw
    elif isinstance(raw, pd.Series):
        df = raw.to_frame()
    else:
        df = pd.DataFrame({"value": [raw]})
    return ExecutionResult(
        df=df,
        duration_ms=int((monotonic() - started) * 1000),
        row_count=len(df),
    )


def _debug_details(expr: Any, datasource_name: str) -> dict[str, Any]:
    details: dict[str, Any] = {"datasource": datasource_name}
    if os.environ.get("MARIVO_ANALYSIS_DEBUG") == "1":
        with suppress(Exception):
            details["expr_sql"] = ibis.to_sql(expr)
    return details
