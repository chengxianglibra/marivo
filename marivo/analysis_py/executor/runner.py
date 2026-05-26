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
from typing import Any
from zoneinfo import ZoneInfo

import ibis
import pandas as pd

from marivo.analysis_py.errors import BackendError, SliceInvalidError, WindowInvalidError
from marivo.analysis_py.executor.backend import BackendCache
from marivo.analysis_py.windows.resolver import zoneinfo_from_name
from marivo.analysis_py.windows.spec import AbsoluteWindow

_SUPPORTED_FORMATS = {
    ("date", None),
    ("timestamp", None),
    ("string", "yyyy-mm-dd"),
    ("string", "yyyymmdd"),
    ("integer", "yyyymmdd"),
    ("integer", "epoch_seconds"),
}
UTC_ZONE = ZoneInfo("UTC")


def _encode_window_bound(iso_string: str, time_meta: Any) -> Any:
    data_type = time_meta.data_type
    fmt = time_meta.format
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
        if fmt == "yyyy-mm-dd":
            return iso_string
        return iso_string[:10].replace("-", "")
    if data_type == "integer":
        if fmt == "yyyymmdd":
            return int(iso_string[:10].replace("-", ""))
        dt = datetime.fromisoformat(iso_string)
        return int(dt.timestamp())
    raise WindowInvalidError(message=f"unsupported window bound format {pair}")


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
    fmt = time_meta.format
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
    lower = _encode_window_bound(window.start, time_meta)
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
                    'mv.observe(mv.MetricRef("sales.revenue"), '
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
                'mv.observe(mv.MetricRef("sales.revenue"), '
                'window={"start": "2026-07-01", "end": "2026-07-31", '
                f'"time_field": "{first_candidate}"}})'
            ),
        },
    )


def resolve_window_time_field(dataset_ir: Any, *, window: AbsoluteWindow) -> Any:
    time_field = {"time_field": window.time_field} if window.time_field else {}
    return _resolve_time_field(dataset_ir, time_field)


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


def normalize_slice_for_storage(slice: Mapping[str, Any] | None) -> dict[str, Any]:
    if not slice:
        return {}
    normalized: dict[str, Any] = {}
    for field_name, raw_predicate in slice.items():
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
    slice: Mapping[str, Any] | None,
    *,
    dataset_ir: Any,
) -> ibis.Table:
    if not slice:
        return table
    for field_name, raw_predicate in slice.items():
        op, value = _normalize_slice_predicate(raw_predicate)
        field_expr = _resolve_slice_field(dataset_ir, field_name, table)
        table = table.filter(_apply_slice_predicate(field_expr, op=op, value=value))
    return table


@dataclass(frozen=True)
class ExecutionResult:
    df: pd.DataFrame
    duration_ms: int
    row_count: int


def execute(
    expr: ibis.Expr,
    *,
    datasource_name: str,
    cache: BackendCache,
) -> ExecutionResult:
    backend = cache.get_or_create(datasource_name)
    started = monotonic()
    try:
        raw = backend.execute(expr)
    except Exception as exc:
        raise BackendError(
            message=str(exc),
            details=_debug_details(expr, datasource_name),
        ) from exc
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
