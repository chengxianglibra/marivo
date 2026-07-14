"""Slice predicates, compile capture, and ibis execution orchestration."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
import logging
import os
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from types import MethodType
from typing import Any

import ibis
import pandas as pd

from marivo.analysis.errors import BackendError, SliceInvalidError
from marivo.analysis.executor.query_record import (
    QueryExecution,
    compute_sql_digest,
    gen_query_ref,
    normalize_sql,
)
from marivo.analysis.executor.windowing import BackendDatetimeDecodePolicy
from marivo.analysis.session._connections import AnalysisConnectionRuntime
from marivo.datasource import secrets as _secrets
from marivo.datasource.engines import profile_for_backend

_MISSING_ATTR = object()

_logger = logging.getLogger("marivo.analysis.executor")


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
        context={
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
                context={"predicate": dict(raw)},
            )
        op = raw["op"]
        value = raw["value"]
        if not isinstance(op, str) or op not in _SUPPORTED_SLICE_OPS:
            _supported = sorted(_SUPPORTED_SLICE_OPS)
            raise SliceInvalidError(
                message=f"unsupported slice predicate op {op!r}; supported ops: {_supported}",
                context={"supported_ops": _supported},
            )
        _validate_slice_value_shape(op, value)
        return str(op), value
    if isinstance(raw, (list, tuple, set)):
        _validate_slice_value_shape("in", raw)
        return "in", raw
    _validate_slice_value_shape("==", raw)
    return "==", raw


def _validate_slice_value_shape(op: str, value: Any) -> None:
    if op in _SCALAR_SLICE_OPS:
        if isinstance(value, (Mapping, list, tuple, set)):
            raise SliceInvalidError(
                message=f"slice op {op!r} requires a scalar value",
                context={"op": op, "value_type": type(value).__name__},
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
            context={"error": str(exc)},
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


def execute(
    expr: ibis.Expr,
    *,
    datasource_name: str,
    cache: AnalysisConnectionRuntime,
    session_id: str | None = None,
) -> ExecutionResult:
    backend = cache.get_or_create(datasource_name)
    profile = profile_for_backend(backend)
    dialect = str(getattr(backend, "name", profile.name))
    decode_policy = profile.datetime_decode_policy
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

                sql = profile.postprocess_sql(sql)

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

                sql = profile.postprocess_sql(sql)

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
            context=_debug_details(expr, datasource_name),
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
