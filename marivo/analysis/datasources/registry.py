"""Public ``mv.datasources`` API surface."""

from __future__ import annotations

import builtins
import time
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from marivo.analysis.datasources import backends as _backends
from marivo.analysis.datasources import secrets as _secrets
from marivo.analysis.datasources import store as _store
from marivo.analysis.datasources.metadata import TableMetadata
from marivo.analysis.datasources.metadata import inspect_source as _inspect_source
from marivo.analysis.datasources.metadata import inspect_table as _inspect_table
from marivo.analysis.errors import DatasourceMissingError, DatasourcePreviewError
from marivo.datasource.authoring import DatasourceSpec
from marivo.preview import (
    PREVIEW_DEFAULT_LIMIT,
    PreviewFilter,
    PreviewOrder,
    PreviewResult,
    PreviewSamplePolicy,
    preview_ibis_table,
)
from marivo.semantic.ir import EntitySourceIR


@dataclass(frozen=True)
class DatasourceSummary:
    name: str
    backend_type: str


@dataclass(frozen=True)
class DatasourceDescription:
    name: str
    backend_type: str
    literal_fields: dict[str, Any]
    env_refs: dict[str, str]


@dataclass(frozen=True)
class DatasourceTestResult:
    name: str
    ok: bool
    error: str | None
    latency_ms: int | None


_ENV_SOURCED_SECRETS_ATTR = "_marivo_env_sourced_secrets"


def _remember_env_sourced_secrets(
    backend: Any,
    resolved: tuple[_secrets.ResolvedSecret, ...],
) -> None:
    with suppress(Exception):
        setattr(backend, _ENV_SOURCED_SECRETS_ATTR, resolved)


def _persist_backend_env_sourced_secrets(backend: Any) -> None:
    resolved = getattr(backend, _ENV_SOURCED_SECRETS_ATTR, ())
    if isinstance(resolved, tuple):
        _secrets.persist_env_sourced(resolved)


def register(spec: DatasourceSpec) -> DatasourceSummary:
    """Create or replace a project-level datasource file."""
    stored = _store.save_one(spec)
    return DatasourceSummary(name=stored.name, backend_type=stored.backend_type)


def remove(name: str) -> bool:
    """Delete the named project datasource file."""
    return _store.delete_one(name)


def all() -> builtins.list[DatasourceSummary]:
    """Return every project datasource, sorted by name."""
    return [
        DatasourceSummary(name=p.name, backend_type=p.backend_type)
        for p in sorted(_store.load_all().values(), key=lambda item: item.name)
    ]


def describe(name: str) -> DatasourceDescription:
    """Return the shape of the named datasource."""
    datasource = _store.load_one(name)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            details={"datasource": name, "available": _store.list_names()},
        )
    return DatasourceDescription(
        name=datasource.name,
        backend_type=datasource.backend_type,
        literal_fields=dict(datasource.fields),
        env_refs=dict(datasource.env_refs),
    )


def build_backend(name: str) -> Any:
    """Return a live ibis backend for the named project datasource."""
    datasource = _store.load_one(name)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            details={"datasource": name, "available": _store.list_names()},
        )
    built = _backends.build_backend_with_secrets(datasource)
    _remember_env_sourced_secrets(built.backend, built.env_sourced_secrets)
    return built.backend


def _preview_ref(datasource: str, table: str, database: str | tuple[str, ...] | None) -> str:
    if database is None:
        return f"{datasource}.{table}"
    namespace = ".".join(database) if isinstance(database, tuple) else database
    return f"{datasource}.{namespace}.{table}"


def _validate_filter(raw_filter: object) -> PreviewFilter:
    if not isinstance(raw_filter, Mapping):
        raise DatasourcePreviewError(
            message="preview where entries must be structured preview filter mappings",
            details={"field": "where", "value": repr(raw_filter)},
        )
    column = raw_filter.get("column")
    op = raw_filter.get("op")
    if not isinstance(column, str) or not column:
        raise DatasourcePreviewError(
            message="preview filter column must be a non-empty string",
            details={"field": "where.column", "value": repr(column)},
        )
    allowed_ops = {"=", "!=", "<", "<=", ">", ">=", "in", "is_null", "is_not_null"}
    if op not in allowed_ops:
        raise DatasourcePreviewError(
            message="preview filter op is not supported",
            details={"field": "where.op", "value": repr(op), "allowed": sorted(allowed_ops)},
        )
    if op not in {"is_null", "is_not_null"} and "value" not in raw_filter:
        raise DatasourcePreviewError(
            message="preview filter value is required for this op",
            details={"field": "where.value", "op": op},
        )
    out: PreviewFilter = {"column": column, "op": op}
    if "value" in raw_filter:
        out["value"] = raw_filter["value"]
    return out


def _validate_order(raw_order: object) -> PreviewOrder:
    if not isinstance(raw_order, Mapping):
        raise DatasourcePreviewError(
            message="preview order_by entries must be structured preview order mappings",
            details={"field": "order_by", "value": repr(raw_order)},
        )
    column = raw_order.get("column")
    direction = raw_order.get("direction", "asc")
    if not isinstance(column, str) or not column:
        raise DatasourcePreviewError(
            message="preview order column must be a non-empty string",
            details={"field": "order_by.column", "value": repr(column)},
        )
    if direction not in {"asc", "desc"}:
        raise DatasourcePreviewError(
            message="preview order direction must be 'asc' or 'desc'",
            details={"field": "order_by.direction", "value": repr(direction)},
        )
    return {"column": column, "direction": direction}


def _require_column(available: Iterable[str], column: str, *, field: str) -> None:
    available_columns = tuple(available)
    if column not in available_columns:
        raise DatasourcePreviewError(
            message=f"preview references unknown column {column!r}",
            details={"field": field, "column": column, "available": available_columns},
        )


def _apply_preview_filter(expr: Any, preview_filter: PreviewFilter) -> Any:
    column = preview_filter["column"]
    _require_column(expr.columns, column, field="where.column")
    value = expr[column]
    op = preview_filter["op"]
    if op == "=":
        return expr.filter(value == preview_filter["value"])
    if op == "!=":
        return expr.filter(value != preview_filter["value"])
    if op == "<":
        return expr.filter(value < preview_filter["value"])
    if op == "<=":
        return expr.filter(value <= preview_filter["value"])
    if op == ">":
        return expr.filter(value > preview_filter["value"])
    if op == ">=":
        return expr.filter(value >= preview_filter["value"])
    if op == "in":
        raw_value = preview_filter["value"]
        if isinstance(raw_value, str) or not isinstance(raw_value, Iterable):
            raise DatasourcePreviewError(
                message="preview 'in' filter value must be a non-string iterable",
                details={"field": "where.value", "op": "in", "value": repr(raw_value)},
            )
        return expr.filter(value.isin(list(raw_value)))
    if op == "is_null":
        return expr.filter(value.isnull())
    if op == "is_not_null":
        return expr.filter(value.notnull())
    raise DatasourcePreviewError(
        message="preview filter op is not supported",
        details={"field": "where.op", "value": op},
    )


def _apply_preview_order(expr: Any, preview_order: PreviewOrder) -> tuple[Any, str]:
    column = preview_order["column"]
    _require_column(expr.columns, column, field="order_by.column")
    direction = preview_order.get("direction", "asc")
    column_expr = expr[column]
    if direction == "desc":
        return expr.order_by(column_expr.desc()), f"{column} desc"
    return expr.order_by(column_expr), f"{column} asc"


def preview(
    datasource: str,
    *,
    table: str,
    database: str | tuple[str, ...] | None = None,
    columns: Iterable[str] | None = None,
    limit: int = PREVIEW_DEFAULT_LIMIT,
    where: Iterable[PreviewFilter] | None = None,
    order_by: Iterable[PreviewOrder] | None = None,
    include_types: bool = True,
) -> PreviewResult:
    """Return a bounded preview of a datasource table."""
    backend = build_backend(datasource)
    try:
        expr = backend.table(table) if database is None else backend.table(table, database=database)

        selected_columns = tuple(columns or ())
        for column in selected_columns:
            _require_column(expr.columns, column, field="columns")
        if selected_columns:
            expr = expr.select(*selected_columns)

        filters = tuple(_validate_filter(item) for item in (where or ()))
        for preview_filter in filters:
            expr = _apply_preview_filter(expr, preview_filter)

        order_labels: list[str] = []
        orders = tuple(_validate_order(item) for item in (order_by or ()))
        for preview_order in orders:
            expr, label = _apply_preview_order(expr, preview_order)
            order_labels.append(label)

        sample_policy = PreviewSamplePolicy(
            method="ordered_limit" if order_labels else "bounded_limit",
            limit=limit,
            order_by=tuple(order_labels),
            filters=filters,
        )
        return preview_ibis_table(
            expr,
            kind="datasource_table",
            ref=_preview_ref(datasource, table, database),
            limit=limit,
            sample_policy=sample_policy,
            include_types=include_types,
        )
    except DatasourcePreviewError:
        raise
    except Exception as exc:
        raise DatasourcePreviewError(
            message=f"failed to preview datasource table {datasource!r}.{table!r}: {exc}",
            details={"datasource": datasource, "table": table, "database": database},
        ) from exc


def inspect_table(
    datasource: str,
    *,
    table: str,
    database: str | tuple[str, ...] | None = None,
    include_partitions: bool = True,
) -> TableMetadata:
    """Return schema, comments, nullable flags, and partition hints for a table."""
    return _inspect_table(
        datasource,
        table=table,
        database=database,
        include_partitions=include_partitions,
    )


def inspect_source(
    datasource: str,
    *,
    source: EntitySourceIR,
    include_partitions: bool = True,
) -> TableMetadata:
    """Return schema, comments, nullable flags, and partition hints for a source."""
    return _inspect_source(
        datasource,
        source=source,
        include_partitions=include_partitions,
    )


def test(name: str) -> DatasourceTestResult:
    """Open the backend and run a trivial round-trip to verify reachability."""
    start = time.perf_counter()
    backend: Any | None = None
    try:
        backend = build_backend(name)
        backend.raw_sql("SELECT 1")
        _persist_backend_env_sourced_secrets(backend)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(name=name, ok=True, error=None, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(
            name=name,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            latency_ms=latency_ms,
        )
    finally:
        if backend is not None:
            disconnect = getattr(backend, "disconnect", None)
            if callable(disconnect):
                with suppress(Exception):
                    disconnect()
