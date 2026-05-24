"""Window/slice filtering and ibis execution."""
# mypy: disable-error-code=import-untyped

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

import ibis
import pandas as pd

from marivo.analysis_py.errors import BackendError, SliceInvalidError, WindowInvalidError
from marivo.analysis_py.executor.backend import BackendCache

_SUPPORTED_FORMATS = {
    ("date", None),
    ("timestamp", None),
    ("string", "yyyy-mm-dd"),
    ("string", "yyyymmdd"),
    ("integer", "yyyymmdd"),
    ("integer", "epoch_seconds"),
}


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
        raise WindowInvalidError(
            message=f"window.time_field={requested!r} is not on dataset '{dataset_ir.name}'",
            details={"candidates": [field.name for field in time_fields]},
        )
    raise WindowInvalidError(
        message=f"dataset '{dataset_ir.name}' has multiple time_fields",
        details={"candidates": [field.name for field in time_fields]},
    )


def apply_window_to_dataset(
    table: ibis.Table,
    window: Mapping[str, Any] | None,
    *,
    dataset_ir: Any,
) -> ibis.Table:
    if window is None:
        return table
    time_field_ir = _resolve_time_field(dataset_ir, window)
    if time_field_ir.time_meta is None:
        raise WindowInvalidError(message=f"field '{time_field_ir.name}' has no time metadata")
    field_expr = time_field_ir.fn(table)
    lower = _encode_window_bound(window["start"], time_field_ir.time_meta)
    upper = _encode_window_bound(window["end"], time_field_ir.time_meta)
    return table.filter(field_expr >= lower, field_expr <= upper)


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


def apply_slice_to_dataset(
    table: ibis.Table,
    slice: Mapping[str, Any] | None,
    *,
    dataset_ir: Any,
) -> ibis.Table:
    if not slice:
        return table
    for field_name, value in slice.items():
        table = table.filter(_resolve_slice_field(dataset_ir, field_name, table) == value)
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
