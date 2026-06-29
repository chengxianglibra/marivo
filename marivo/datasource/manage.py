"""Unified datasource management API (md.*)."""

from __future__ import annotations

import builtins
import re
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from marivo.datasource import backends as _backends
from marivo.datasource import secrets as _secrets
from marivo.datasource import store as _store
from marivo.datasource.authoring import (
    DatasourceRef,
    DatasourceSpec,
    _storage_name,
)
from marivo.datasource.discovery import DatasourceResult, PartitionInspectionResult, RawSqlResult
from marivo.datasource.errors import (
    DatasourceError,
    DatasourceMissingError,
    DatasourcePreviewError,
    DatasourceRawSqlError,
)
from marivo.datasource.ir import CsvSourceIR, EntitySourceIR, ParquetSourceIR, TableSourceIR
from marivo.datasource.metadata import _inspect_source
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.scan import (
    ColumnInspection,
    ColumnProfile,
    JoinKeyProbe,
    JoinSide,
    ScanReport,
    ScanScope,
    _coarse_type_family,
)
from marivo.preview import (
    PREVIEW_DEFAULT_LIMIT,
    PreviewFilter,
    PreviewOrder,
    PreviewResult,
    PreviewSamplePolicy,
    preview_ibis_table,
)
from marivo.render import Card, RenderableResult, result_repr


@dataclass(frozen=True, repr=False)
class DatasourceSummary(RenderableResult):
    """Summary row for one configured project datasource."""

    name: str
    backend_type: str

    @property
    def semantic_id(self) -> str:
        """Stable id used by discovery surfaces; equals ``name``."""
        return self.name

    def _repr_identity(self) -> str:
        return f"DatasourceSummary name={self.name} backend={self.backend_type}"

    def _card(self) -> Card:
        return Card(identity=self._repr_identity(), available=(".render()", ".show()"))


@dataclass(frozen=True, repr=False)
class DatasourceList(RenderableResult):
    """Displayable collection of configured project datasource summaries."""

    _items: tuple[DatasourceSummary, ...]

    @property
    def items(self) -> tuple[DatasourceSummary, ...]:
        """Return all datasource summary rows."""
        return self._items

    def ids(self) -> builtins.list[str]:
        """Return datasource names in display order."""
        return [item.name for item in self._items]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[DatasourceSummary]:
        return iter(self._items)

    def __getitem__(self, index: int) -> DatasourceSummary:
        return self._items[index]

    def _repr_identity(self) -> str:
        return f"DatasourceList count={len(self._items)}"

    def _card(self) -> Card:
        rows = [[item.name, item.backend_type] for item in self._items]
        return Card(
            identity=self._repr_identity(),
            available=(".items", ".ids()", ".render()", ".show()"),
        ).table(columns=["name", "backend"], rows=rows, row_count=len(self._items))


@dataclass(frozen=True, repr=False)
class DatasourceDescription(RenderableResult):
    """Literal fields and env refs for one datasource."""

    name: str
    backend_type: str
    literal_fields: dict[str, Any]
    env_refs: dict[str, str]

    def _repr_identity(self) -> str:
        return (
            f"DatasourceDescription name={self.name} backend={self.backend_type} "
            f"fields={len(self.literal_fields)} env_refs={len(self.env_refs)}"
        )

    def _card(self) -> Card:
        field_names = sorted(self.literal_fields)
        env_ref_names = sorted(self.env_refs)
        return Card(identity=self._repr_identity(), available=(".render()", ".show()")).field(
            label="columns",
            value=" | ".join(field_names + [f"{name}_env" for name in env_ref_names]),
        )


@dataclass(frozen=True, repr=False)
class DatasourceTestResult(RenderableResult):
    """Result of a datasource connectivity round-trip."""

    name: str
    ok: bool
    error: str | None
    latency_ms: int | None

    def _repr_identity(self) -> str:
        latency = "n/a" if self.latency_ms is None else f"{self.latency_ms}ms"
        return f"DatasourceTestResult name={self.name} ok={self.ok} latency={latency}"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()"))
        if self.error is not None:
            card.status(self.error)
        return card


class DatasourceConnection:
    """Context-manageable datasource backend connection.

    Args:
        backend: The live ibis backend opened for a project datasource.

    Returns:
        A connection proxy that delegates backend methods and owns cleanup.

    Example:
        >>> import marivo.datasource as md
        >>> with md.connect("wh") as con:
        ...     con.raw_sql("SELECT 1")

    Constraints:
        ``with`` blocks yield the raw ibis backend and disconnect on exit.
        Scripts that cannot use ``with`` may call ``.disconnect()`` manually.
        The ``.backend`` property exposes the raw backend for explicit handoff.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._closed = False

    @property
    def backend(self) -> Any:
        """Return the wrapped raw ibis backend."""
        return self._backend

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    def __enter__(self) -> Any:
        return self._backend

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> Literal[False]:
        self._disconnect(suppress_errors=exc_type is not None)
        return False

    def _disconnect(self, *, suppress_errors: bool) -> None:
        if self._closed:
            return
        disconnect = getattr(self._backend, "disconnect", None)
        if not callable(disconnect):
            self._closed = True
            return
        try:
            disconnect()
        except Exception:
            if not suppress_errors:
                raise
        finally:
            self._closed = True

    def disconnect(self) -> None:
        """Disconnect the backend once; repeated calls are no-ops."""
        self._disconnect(suppress_errors=False)

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return result_repr(f"DatasourceConnection backend={type(self._backend).__name__} {state}")


def register(
    spec: DatasourceSpec,
    *,
    project_root: Path | None = None,
) -> DatasourceSummary:
    """Create or replace a project datasource file from a DatasourceSpec.

    Args:
        spec: A public backend datasource spec returned by helpers such as
            ``md.duckdb(...)`` or ``md.trino(...)``.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``DatasourceSummary`` for the newly stored datasource.

    Example:
        >>> import marivo.datasource as md
        >>> spec = md.duckdb(name="wh", path=":memory:")
        >>> md.register(spec)

    Constraints:
        Use one of the public typed specs. Sensitive fields use named
        ``*_env`` references, not plaintext literals or generic keyword bags.
    """
    stored = _store.save_one(spec, project_root=project_root)
    return DatasourceSummary(name=stored.name, backend_type=stored.backend_type)


def remove(name: str) -> bool:
    """Delete the named project datasource file.

    Args:
        name: The datasource name to remove.

    Returns:
        True if the file existed and was deleted; False if it was not found.

    Example:
        >>> import marivo.datasource as md
        >>> md.remove("wh")
        True

    Constraints:
        Only the project-local ``models/datasources/<name>.py`` file is removed.
    """
    return _store.delete_one(name)


def list() -> DatasourceList:
    """List configured project datasources as a displayable DatasourceList.

    Returns:
        ``DatasourceList`` containing sorted ``DatasourceSummary`` rows.

    Example:
        >>> import marivo.datasource as md
        >>> md.list().show()
        >>> md.list().items

    Constraints:
        Only datasources with a persisted project file are included.
    """
    return DatasourceList(
        tuple(
            DatasourceSummary(name=p.name, backend_type=p.backend_type)
            for p in sorted(_store.load_all().values(), key=lambda item: item.name)
        )
    )


def describe(name: str) -> DatasourceDescription:
    """Show literal fields and env refs for one datasource.

    Args:
        name: The datasource name to describe.

    Returns:
        A ``DatasourceDescription`` with literal_fields and env_refs.

    Example:
        >>> import marivo.datasource as md
        >>> md.describe("wh")

    Constraints:
        Raises ``DatasourceMissingError`` when the name has no project file.
    """
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


def connect(name: str) -> DatasourceConnection:
    """Open a context-manageable live ibis backend for a datasource.

    Args:
        name: The datasource name to connect to.

    Returns:
        A ``DatasourceConnection`` proxy that delegates backend methods and
        disconnects automatically when used as a context manager.

    Example:
        >>> import marivo.datasource as md
        >>> with md.connect("wh") as con:
        ...     con.raw_sql("SELECT 1")

    Constraints:
        Prefer ``with md.connect(...) as con`` so cleanup is automatic. For
        manual lifetime management, call ``connection.disconnect()`` when done.
        Env-sourced secrets used to open this backend are remembered on the
        connection object so that a subsequent round-trip validation can persist
        them via ``secrets.persist_backend_env_sourced``.
    """
    datasource = _store.load_one(name)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            details={"datasource": name, "available": _store.list_names()},
        )
    built = _backends.build_backend_with_secrets(datasource)
    connection = DatasourceConnection(built.backend)
    _secrets.remember_env_sourced(built.backend, built.env_sourced_secrets)
    _secrets.remember_env_sourced(connection, built.env_sourced_secrets)
    return connection


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
        return expr.filter(value.isin(builtins.list(raw_value)))
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
    """Bounded, filtered preview of one datasource table.

    Args:
        datasource: Name of the project datasource.
        table: Table name within the datasource.
        database: Optional database/catalog path.
        columns: Optional column subset to select.
        limit: Maximum rows to return (default 100).
        where: Structured filter mappings (column, op, value).
        order_by: Structured order mappings (column, direction).
        include_types: Whether to include column type information.

    Returns:
        A ``PreviewResult`` with rows, columns, types, and sample metadata.

    Example:
        >>> import marivo.datasource as md
        >>> md.preview("wh", table="orders", limit=5)

    Constraints:
        The backend is always disconnected before returning, even on error.
        Raw SQL filters are rejected; use structured ``where`` mappings.
    """
    backend: Any | None = None
    try:
        backend = connect(datasource)
        expr = backend.table(table) if database is None else backend.table(table, database=database)

        selected_columns = tuple(columns or ())
        for column in selected_columns:
            _require_column(expr.columns, column, field="columns")
        if selected_columns:
            expr = expr.select(*selected_columns)

        filters = tuple(_validate_filter(item) for item in (where or ()))
        for preview_filter in filters:
            expr = _apply_preview_filter(expr, preview_filter)

        order_labels: builtins.list[str] = []
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
        from marivo.datasource.timezone import system_timezone_name

        report_tz = system_timezone_name()
        return preview_ibis_table(
            expr,
            kind="datasource_table",
            ref=_preview_ref(datasource, table, database),
            limit=limit,
            sample_policy=sample_policy,
            include_types=include_types,
            report_tz=report_tz,
        )
    except DatasourcePreviewError:
        raise
    except Exception as exc:
        raise DatasourcePreviewError(
            message=f"failed to preview datasource table {datasource!r}.{table!r}: {exc}",
            details={"datasource": datasource, "table": table, "database": database},
        ) from exc
    finally:
        if backend is not None:
            disconnect = getattr(backend, "disconnect", None)
            if callable(disconnect):
                with suppress(Exception):
                    disconnect()


def inspect_table(
    datasource: DatasourceRef,
    source: EntitySourceIR | None = None,
    *,
    table: str | None = None,
    database: str | tuple[str, ...] | None = None,
    include_partitions: bool = True,
    project_root: Path | None = None,
) -> DatasourceResult:
    """Schema, comments, nullability, and partition metadata for a table.

    Args:
        datasource: Datasource reference returned by ``md.ref(...)``.
        source: An ``EntitySourceIR`` (from ``md.table()``, ``md.parquet()``, or ``md.csv()``).
            Pass either ``source`` or ``table``, not both.
        table: Table name within the datasource (alternative to ``source``).
        database: Optional database/catalog path.
        include_partitions: Whether to include partition hints.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``TableMetadata`` with table comments, column types, nullability,
        column comments, warnings, and optional partition columns.

    Constraints:
        Metadata-only inspection. Does not execute sampled data scans. Opens
        and closes a backend connection internally.
    """
    if not isinstance(datasource, DatasourceRef):
        raise TypeError(
            f"datasource must be md.DatasourceRef from md.ref(...), got {type(datasource).__name__}."
        )
    if source is not None and table is not None:
        raise TypeError("Pass either source or table, not both.")
    if source is None:
        if table is None:
            raise TypeError("inspect_table requires a structured source or table name.")
        source = TableSourceIR(table=table, database=database)
    return _inspect_source(
        _storage_name(datasource),
        source=source,
        include_partitions=include_partitions,
        project_root=project_root,
    )


def _quote_metadata_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _partition_values_unavailable(
    *,
    datasource: DatasourceRef,
    source: EntitySourceIR,
    partition_columns: tuple[str, ...],
    limit: int,
    reason: str,
) -> PartitionInspectionResult:
    return PartitionInspectionResult(
        datasource=datasource,
        source=source,
        partition_columns=partition_columns,
        rows=(),
        requested_limit=limit,
        is_truncated=False,
        warnings=(
            "Partition values unavailable for this backend/table without scanning data. "
            "Discovery still requires an explicit md.partition({...}) for partitioned tables. "
            "Use backend metadata SQL through md.raw_sql(...) or provide the partition values "
            f"from source knowledge. reason={reason}",
        ),
    )


def _no_partition_values_result(
    *,
    datasource: DatasourceRef,
    source: EntitySourceIR,
    limit: int,
) -> PartitionInspectionResult:
    return PartitionInspectionResult(
        datasource=datasource,
        source=source,
        partition_columns=(),
        rows=(),
        requested_limit=limit,
        is_truncated=False,
        warnings=(
            "No partition columns were exposed by metadata. Discovery does not "
            "require md.partition({...}) for this table.",
        ),
    )


def inspect_partitions(
    datasource: DatasourceRef,
    source: EntitySourceIR,
    *,
    limit: int = 50,
    project_root: Path | None = None,
) -> DatasourceResult:
    """Inspect bounded available partition values without scanning table data."""
    if limit < 1:
        raise ValueError("limit must be positive.")
    if not isinstance(datasource, DatasourceRef):
        raise TypeError(
            f"datasource must be md.DatasourceRef from md.ref(...), got {type(datasource).__name__}."
        )
    datasource_id = _storage_name(datasource)
    metadata = _inspect_source(
        datasource_id,
        source=source,
        include_partitions=True,
        project_root=project_root,
    )
    partition_columns = tuple(partition.name for partition in metadata.partitions)
    if not partition_columns:
        return _no_partition_values_result(
            datasource=datasource,
            source=source,
            limit=limit,
        )
    if any(partition.transform for partition in metadata.partitions):
        return _partition_values_unavailable(
            datasource=datasource,
            source=source,
            partition_columns=partition_columns,
            limit=limit,
            reason="transformed partition values cannot be mapped to md.partition({...}) safely",
        )
    if not isinstance(source, TableSourceIR):
        return _partition_values_unavailable(
            datasource=datasource,
            source=source,
            partition_columns=partition_columns,
            limit=limit,
            reason="file source partition values are not exposed as backend metadata",
        )

    datasource_ir = _store.load_one(datasource_id, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMissingError(
            message=f"datasource {datasource_id!r} is not configured",
            details={"datasource": datasource_id, "available": _store.list_names(project_root)},
        )
    if datasource_ir.backend_type != "trino":
        return _partition_values_unavailable(
            datasource=datasource,
            source=source,
            partition_columns=partition_columns,
            limit=limit,
            reason=f"{datasource_ir.backend_type} partition value metadata is not supported",
        )

    database = source.database
    catalog = str(datasource_ir.fields["catalog"])
    schema_name: str | None
    if isinstance(database, tuple):
        if len(database) >= 2:
            catalog = str(database[0])
            schema_name = str(database[1])
        elif len(database) == 1:
            schema_name = str(database[0])
        else:
            schema_name = None
    elif database is not None:
        schema_name = str(database)
    else:
        schema_value = datasource_ir.fields.get("schema")
        schema_name = str(schema_value) if schema_value is not None else None
    if schema_name is None:
        return _partition_values_unavailable(
            datasource=datasource,
            source=source,
            partition_columns=partition_columns,
            limit=limit,
            reason="trino partition inspection requires database= or datasource schema",
        )

    backend: Any = None
    try:
        backend = _backends.build_backend(datasource_ir)
        quoted_columns = ", ".join(
            _quote_metadata_identifier(column) for column in partition_columns
        )
        table_ref = ".".join(
            _quote_metadata_identifier(part)
            for part in (catalog, schema_name, f"{source.table}$partitions")
        )
        order_by = ", ".join(
            f"{_quote_metadata_identifier(column)} DESC" for column in partition_columns
        )
        sql = f"SELECT {quoted_columns} FROM {table_ref} ORDER BY {order_by} LIMIT {limit + 1}"
        cursor = backend.raw_sql(sql)
        _, raw_rows, _ = _extract_raw_sql_frame(cursor, include_types=False, limit=limit + 1)
    except Exception as exc:
        return _partition_values_unavailable(
            datasource=datasource,
            source=source,
            partition_columns=partition_columns,
            limit=limit,
            reason=str(exc),
        )
    finally:
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
            with suppress(Exception):
                disconnect()

    complete_rows: builtins.list[dict[str, str]] = []
    omitted_incomplete = 0
    for row in raw_rows[:limit]:
        if any(row.get(column) is None for column in partition_columns):
            omitted_incomplete += 1
            continue
        complete_rows.append({column: str(row[column]) for column in partition_columns})
    warnings: builtins.list[str] = []
    if omitted_incomplete:
        warnings.append(f"incomplete partition rows omitted={omitted_incomplete}")
    return PartitionInspectionResult(
        datasource=datasource,
        source=source,
        partition_columns=partition_columns,
        rows=tuple(complete_rows),
        requested_limit=limit,
        is_truncated=len(raw_rows) > limit,
        warnings=tuple(warnings),
    )


def _inspect_columns(
    datasource: str,
    source: EntitySourceIR,
    *,
    columns: tuple[str, ...] | None = None,
    scope: ScanScope | None = None,
    project_root: Path | None = None,
) -> ColumnInspection:
    """Profile selected columns from a datasource source with bounded scan.

    Args:
        datasource: Name of the project datasource.
        source: An ``EntitySourceIR`` (from ``md.table()``, ``md.parquet()``, or ``md.csv()``).
        columns: Column names to profile; ``None`` profiles all columns
            (capped by ``scope.max_columns``).
        scope: Bounded scan configuration; defaults to ``ScanScope()``.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``ColumnInspection`` with per-column profiles and a ``ScanReport``.

    Constraints:
        Internal helper backing ``md.discover_*``. The backend is always
        disconnected before returning, even on error. Scan scope limits
        (max_rows, max_columns) are always enforced. Not an agent-facing API.
    """
    if scope is None:
        scope = ScanScope()

    metadata = _inspect_source(
        datasource,
        source=source,
        include_partitions=False,
        project_root=project_root,
    )

    # Determine which columns to profile.
    all_column_names = tuple(column.name for column in metadata.columns)
    requested = columns if columns is not None else all_column_names
    selected_columns = requested[: scope.max_columns]

    warnings: builtins.list[str] = []

    # Warn when columns are truncated by max_columns.
    if len(requested) > scope.max_columns:
        truncated_count = len(requested) - len(selected_columns)
        truncated_names = requested[scope.max_columns :]
        warnings.append(
            f"column list truncated by max_columns={scope.max_columns}: "
            f"{truncated_count} columns not profiled "
            f"(first omitted: {', '.join(str(c) for c in truncated_names[:3])}); "
            f"pass scope=ScanScope(max_columns={len(requested)}) to profile all columns"
        )

    # Build column spec lookup from metadata.
    column_specs: dict[str, tuple[str, bool | None, str | None]] = {
        column.name: (column.type, column.nullable, column.comment) for column in metadata.columns
    }

    # Resolve partition for the scan report.
    partition_resolution: str
    partition_used: Mapping[str, str] | None = None
    if scope.partition is None:
        partition_resolution = "unpruned"
    else:
        partition_resolution = "explicit"
        partition_used = dict(scope.partition)

    # Execute the bounded sample.
    start = time.perf_counter()
    frame = _execute_scoped_sample(
        datasource,
        source,
        selected_columns=selected_columns,
        scope=scope,
        project_root=project_root,
    )
    elapsed = time.perf_counter() - start

    rows_scanned = len(frame)
    truncated = rows_scanned >= scope.max_rows

    # Profile each column.
    profiles: builtins.list[ColumnProfile] = []
    for column_name in selected_columns:
        spec = column_specs.get(column_name)
        if spec is None:
            warnings.append(f"column {column_name!r} absent from source schema")
            profiles.append(
                ColumnProfile(
                    name=column_name,
                    data_type="UNKNOWN",
                    nullable=None,
                    comment=None,
                    null_count=0,
                    empty_count=0,
                    distinct_count=0,
                    top_values=(),
                    sample_values=(),
                    min_value=None,
                    max_value=None,
                    type_family=_coarse_type_family("UNKNOWN"),
                    read_status="not_found",
                )
            )
            continue

        data_type, nullable, comment = spec
        if column_name not in frame:
            warnings.append(f"column {column_name!r} absent from bounded sample")
            profiles.append(
                ColumnProfile(
                    name=column_name,
                    data_type=data_type,
                    nullable=nullable,
                    comment=comment,
                    null_count=0,
                    empty_count=0,
                    distinct_count=0,
                    top_values=(),
                    sample_values=(),
                    min_value=None,
                    max_value=None,
                    type_family=_coarse_type_family(data_type),
                    read_status="unreadable",
                )
            )
            continue

        profiles.append(_profile_column(frame, column_name, data_type, nullable, comment))

    scan_report = ScanReport(
        partition_used=partition_used,
        partition_resolution=partition_resolution,  # type: ignore[arg-type]
        rows_scanned=rows_scanned,
        columns_scanned=tuple(selected_columns),
        truncated=truncated,
        elapsed_seconds=elapsed,
        warnings=tuple(warnings),
    )

    return ColumnInspection(
        datasource=datasource,
        source=source
        if isinstance(source, (TableSourceIR, ParquetSourceIR, CsvSourceIR))
        else TableSourceIR(table=str(source)),
        profiles=tuple(profiles),
        scan=scan_report,
    )


def _execute_scoped_sample(
    datasource: str,
    source: EntitySourceIR,
    *,
    selected_columns: tuple[str, ...],
    scope: ScanScope,
    project_root: Path | None,
) -> Any:
    """Execute a bounded sample against a datasource source and return a DataFrame."""
    service = DatasourceConnectionService(project_root)
    with service.use_backend(datasource) as backend:
        expr: Any
        if isinstance(source, TableSourceIR):
            if source.database is None:
                expr = backend.table(source.table)
            else:
                expr = backend.table(source.table, database=source.database)
        elif isinstance(source, ParquetSourceIR):
            pq_kwargs: dict[str, object] = {}
            if source.hive_partitioning:
                pq_kwargs["hive_partitioning"] = source.hive_partitioning
            if source.columns is not None:
                pq_kwargs["columns"] = builtins.list(source.columns)
            expr = backend.read_parquet(source.path, **pq_kwargs)
        elif isinstance(source, CsvSourceIR):
            csv_kwargs: dict[str, object] = {}
            if not source.header:
                csv_kwargs["header"] = source.header
            if source.delimiter != ",":
                csv_kwargs["delimiter"] = source.delimiter
            if source.columns is not None:
                csv_kwargs["columns"] = builtins.list(source.columns)
            expr = backend.read_csv(source.path, **csv_kwargs)
        else:
            raise TypeError(f"unsupported source type: {type(source).__name__}")

        # Apply partition filter if scope has an explicit partition.
        if scope.partition is not None and isinstance(scope.partition, Mapping):
            for column, value in scope.partition.items():
                if column in expr.columns:
                    expr = expr.filter(expr[column] == value)

        # Select requested columns and limit rows.
        if selected_columns:
            available = set(expr.columns)
            present = [col for col in selected_columns if col in available]
            if present:
                expr = expr.select(*present)

        expr = expr.limit(scope.max_rows)
        return expr.execute()


def _profile_column(
    frame: Any,
    column_name: str,
    data_type: str,
    nullable: bool | None,
    comment: str | None,
) -> ColumnProfile:
    """Profile a single column from a pandas DataFrame."""
    from collections import Counter

    from marivo.preview import normalize_preview_cell

    series = frame[column_name]
    non_null = series.dropna()
    null_count = int(series.isna().sum())
    non_null_count = len(non_null)

    is_string_series = series.dtype.kind == "O"
    empty_count = 0
    if is_string_series:
        empty_count = int((series.dropna() == "").sum())

    # Distinct count from non-null values.
    distinct_count = int(non_null.nunique())

    # Top values from non-null values.
    counter = Counter(non_null)
    top_values = tuple(
        (normalize_preview_cell(value), count) for value, count in counter.most_common(10)
    )

    # Sample values (first 10 non-null).
    sample_values = tuple(normalize_preview_cell(value) for value in non_null.head(10))

    # Min/max for orderable types.
    min_value: object | None = None
    max_value: object | None = None
    if not non_null.empty:
        try:
            min_value = normalize_preview_cell(non_null.min())
            max_value = normalize_preview_cell(non_null.max())
        except TypeError:
            min_value = None
            max_value = None

    distinct_ratio: float | None = None
    if non_null_count > 0:
        distinct_ratio = distinct_count / non_null_count

    top_value_concentration: float | None = None
    if non_null_count > 0 and top_values:
        top_value_concentration = top_values[0][1] / non_null_count

    negative_count = 0
    zero_count = 0
    if not non_null.empty and _is_numeric_series(non_null):
        numeric = non_null.astype("float64")
        negative_count = int((numeric < 0).sum())
        zero_count = int((numeric == 0).sum())

    min_length: int | None = None
    max_length: int | None = None
    avg_length: float | None = None
    if is_string_series and not non_null.empty:
        lengths = non_null.astype(str).str.len()
        if not lengths.empty:
            min_length = int(lengths.min())
            max_length = int(lengths.max())
            avg_length = float(lengths.mean())

    return ColumnProfile(
        name=column_name,
        data_type=data_type,
        nullable=nullable,
        comment=comment,
        null_count=null_count,
        empty_count=empty_count,
        distinct_count=distinct_count,
        top_values=top_values,
        sample_values=sample_values,
        min_value=min_value,
        max_value=max_value,
        non_null_count=non_null_count,
        distinct_ratio=distinct_ratio,
        top_value_concentration=top_value_concentration,
        negative_count=negative_count,
        zero_count=zero_count,
        min_length=min_length,
        max_length=max_length,
        avg_length=avg_length,
        type_family=_coarse_type_family(data_type),
    )


def _is_numeric_series(series: Any) -> bool:
    """Return True when a pandas Series is numeric or numeric-coercible."""
    dtype = str(series.dtype)
    if any(token in dtype.upper() for token in ("INT", "FLOAT", "UINT", "COMPLEX")):
        return True
    try:
        coerced = series.astype("float64")
    except (ValueError, TypeError):
        return False
    return bool(coerced.notna().all())


def _join_side_datasource_name(side: JoinSide) -> str:
    return _storage_name(side.datasource)


def _datasource_name(value: str | DatasourceRef) -> str:
    return _storage_name(value)


def _sample_distinct_keys(
    side: JoinSide,
    scope: ScanScope,
    key_sample_size: int,
    project_root: Path | None,
) -> tuple[builtins.list[tuple[object, ...]], ScanReport]:
    """Sample distinct key tuples from one join side.

    Returns:
        A pair of (distinct key tuples, scan report).
    """
    start = time.perf_counter()
    frame = _execute_scoped_sample(
        _join_side_datasource_name(side),
        side.source,
        selected_columns=tuple(side.columns),
        scope=scope,
        project_root=project_root,
    )
    elapsed = time.perf_counter() - start

    rows_scanned = len(frame)
    truncated = rows_scanned >= scope.max_rows
    warnings: builtins.list[str] = []

    # Extract distinct key tuples.
    key_columns = builtins.list(side.columns)
    seen: set[tuple[object, ...]] = set()
    distinct_keys: builtins.list[tuple[object, ...]] = []
    for row_values in frame[key_columns].itertuples(index=False, name=None):
        key_tuple = tuple(row_values)
        if key_tuple not in seen:
            seen.add(key_tuple)
            distinct_keys.append(key_tuple)
        if len(distinct_keys) >= key_sample_size:
            break

    partition_resolution: str
    partition_used: Mapping[str, str] | None = None
    if scope.partition is None:
        partition_resolution = "unpruned"
    else:
        partition_resolution = "explicit"
        partition_used = dict(scope.partition)

    scan_report = ScanReport(
        partition_used=partition_used,
        partition_resolution=partition_resolution,  # type: ignore[arg-type]
        rows_scanned=rows_scanned,
        columns_scanned=tuple(side.columns),
        truncated=truncated,
        elapsed_seconds=elapsed,
        warnings=tuple(warnings),
    )
    return distinct_keys, scan_report


def _count_matching_keys(
    side: JoinSide,
    key_tuples: builtins.list[tuple[object, ...]],
    scope: ScanScope,
    project_root: Path | None,
) -> tuple[dict[tuple[object, ...], int], ScanReport]:
    """Count how many rows on the to-side match each from-side key.

    Returns:
        A pair of (key -> count mapping, scan report).
    """
    start = time.perf_counter()
    frame = _execute_scoped_sample(
        _join_side_datasource_name(side),
        side.source,
        selected_columns=tuple(side.columns),
        scope=scope,
        project_root=project_root,
    )
    elapsed = time.perf_counter() - start

    rows_scanned = len(frame)
    truncated = rows_scanned >= scope.max_rows
    warnings: builtins.list[str] = []

    # Build a lookup of key -> count from the to-side sample.
    key_columns = builtins.list(side.columns)
    from_key_set = set(key_tuples)
    counts: dict[tuple[object, ...], int] = {}
    for row_values in frame[key_columns].itertuples(index=False, name=None):
        key_tuple = tuple(row_values)
        if key_tuple in from_key_set:
            counts[key_tuple] = counts.get(key_tuple, 0) + 1

    partition_resolution: str
    partition_used: Mapping[str, str] | None = None
    if scope.partition is None:
        partition_resolution = "unpruned"
    else:
        partition_resolution = "explicit"
        partition_used = dict(scope.partition)

    scan_report = ScanReport(
        partition_used=partition_used,
        partition_resolution=partition_resolution,  # type: ignore[arg-type]
        rows_scanned=rows_scanned,
        columns_scanned=tuple(side.columns),
        truncated=truncated,
        elapsed_seconds=elapsed,
        warnings=tuple(warnings),
    )
    return counts, scan_report


def _probe_join_keys(
    *,
    from_side: JoinSide,
    to_side: JoinSide,
    scope: ScanScope | None = None,
    key_sample_size: int = 500,
    project_root: Path | None = None,
) -> JoinKeyProbe:
    """Probe join compatibility between two sources on specified key columns.

    Samples distinct keys from the from-side, then counts matching rows
    on the to-side to estimate match rate and join cardinality.

    Args:
        from_side: The left side of the join, defining keys to probe.
        to_side: The right side of the join, checked for key matches.
        scope: Bounded scan configuration; defaults to ``ScanScope()``.
        key_sample_size: Maximum distinct keys to sample from the from-side.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``JoinKeyProbe`` with match statistics and cardinality estimate.

    Constraints:
        Internal helper backing ``md.discover_relationship``. Both from-side
        and to-side may reference the same or different datasources. Key
        comparison uses tuple equality. Matching is performed client-side
        after a bounded sample. Not an agent-facing API.
    """
    if scope is None:
        scope = ScanScope()

    # Step 1: Sample distinct keys from the from-side.
    distinct_keys, from_scan = _sample_distinct_keys(
        from_side, scope, key_sample_size, project_root
    )

    # Step 2: Count matching keys from the to-side.
    counts_by_key, to_scan = _count_matching_keys(to_side, distinct_keys, scope, project_root)

    # Step 3: Compute metrics.
    sampled_key_count = len(distinct_keys)
    matched_key_count = sum(1 for key_tuple in distinct_keys if key_tuple in counts_by_key)
    match_rate = matched_key_count / sampled_key_count if sampled_key_count > 0 else 0.0

    max_rows_per_key = 0
    total_rows = 0
    for key_tuple in distinct_keys:
        count = counts_by_key.get(key_tuple, 0)
        total_rows += count
        if count > max_rows_per_key:
            max_rows_per_key = count

    avg_rows_per_key = total_rows / sampled_key_count if sampled_key_count > 0 else 0.0

    # Cardinality estimate.
    if matched_key_count == 0:
        cardinality_estimate: Literal["one_to_one", "many_to_one", "indeterminate"] = (
            "indeterminate"
        )
    elif max_rows_per_key > 1:
        cardinality_estimate = "many_to_one"
    else:
        cardinality_estimate = "one_to_one"

    return JoinKeyProbe(
        type_compatible=True,
        sampled_key_count=sampled_key_count,
        matched_key_count=matched_key_count,
        match_rate=match_rate,
        max_rows_per_key=max_rows_per_key,
        avg_rows_per_key=avg_rows_per_key,
        cardinality_estimate=cardinality_estimate,
        from_scan=from_scan,
        to_scan=to_scan,
    )


def test(name: str | DatasourceRef) -> DatasourceTestResult:
    """Round-trip the backend and persist validated env secrets.

    Args:
        name: The datasource name or ``DatasourceRef`` to test.

    Returns:
        A ``DatasourceTestResult`` with ok/error status and latency.

    Example:
        >>> import marivo.datasource as md
        >>> md.test(md.ref("datasource.wh"))

    Constraints:
        On success, env-sourced secrets that resolved correctly are
        persisted to the user-global plaintext cache. The backend is
        always disconnected.
    """
    datasource_name = _datasource_name(name)
    start = time.perf_counter()
    backend: Any | None = None
    try:
        backend = connect(datasource_name)
        backend.raw_sql("SELECT 1")
        _secrets.persist_backend_env_sourced(backend)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
            ok=True,
            error=None,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DatasourceTestResult(
            name=datasource_name,
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


def _require_raw_sql_reason(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be non-empty.")
    return reason.strip()


def _require_single_statement(sql: str) -> str:
    """Reject empty SQL and ``;``-separated multi-statement input.

    Read-only is enforced at the connection level (and via a read-only transaction
    for transaction-based backends), not by parsing the statement shape, so this
    check only guards statement count.
    """
    text = sql.strip()
    if not text:
        raise ValueError("sql must be non-empty.")
    stripped = text.rstrip(";")
    if ";" in stripped:
        raise ValueError("raw_sql accepts a single read-only statement.")
    return stripped


_RAW_SQL_METADATA_KEYWORDS = {"SHOW", "DESCRIBE", "DESC", "EXPLAIN"}


def _raw_sql_keyword(sql: str) -> str:
    match = re.match(r"([A-Za-z_]+)", sql.lstrip())
    return match.group(1).upper() if match else ""


def _is_metadata_diagnostic_sql(sql: str) -> bool:
    return _raw_sql_keyword(sql) in _RAW_SQL_METADATA_KEYWORDS


# Transaction-based backends have no connect-level read-only mode; raw_sql wraps
# their query in a read-only transaction. DuckDB, ClickHouse, and Trino enforce
# read-only without a transaction (connection-level for DuckDB/ClickHouse,
# subquery wrapper for Trino) and need no entry here.
_READONLY_TX_START: dict[str, str] = {
    "postgres": "BEGIN READ ONLY",
    "mysql": "START TRANSACTION READ ONLY",
}


def _execute_readonly(
    backend: Any,
    backend_type: str,
    sql: str,
    *,
    use_transaction: bool = True,
) -> Any:
    """Run ``sql`` against ``backend`` under read-only enforcement.

    DuckDB, ClickHouse, and Trino run the query directly (read-only is enforced
    elsewhere — connection-level for DuckDB/ClickHouse, subquery wrapper for
    Trino). Postgres and MySQL run the query inside a ``BEGIN/START TRANSACTION
    READ ONLY`` transaction that is committed on success or rolled back on
    failure.
    """
    start = _READONLY_TX_START.get(backend_type) if use_transaction else None
    if start is None:
        return backend.raw_sql(sql)
    backend.raw_sql(start)
    try:
        cursor = backend.raw_sql(sql)
    except BaseException:
        with suppress(Exception):
            backend.raw_sql("ROLLBACK")
        raise
    backend.raw_sql("COMMIT")
    return cursor


def _extract_raw_sql_frame(
    cursor: Any,
    include_types: bool,
    *,
    limit: int | None = None,
) -> tuple[tuple[str, ...], tuple[dict[str, object], ...], dict[str, str]]:
    """Extract columns, rows, and best-effort types from a backend cursor.

    Mirrors the portable cursor-row pattern in ``marivo.datasource.metadata``: the
    DB-API ``description``+``fetchall`` path (DuckDB/Postgres/Trino/MySQL) and the
    ``column_names``+``result_rows`` path (ClickHouse).
    """
    description = getattr(cursor, "description", None)
    row_limit = limit + 1 if limit is not None else None
    fetchall = getattr(cursor, "fetchall", None)
    if description is not None and callable(fetchall):
        columns = tuple(str(item[0]) for item in description)
        types = {str(item[0]): str(item[1]) for item in description} if include_types else {}
        fetchmany = getattr(cursor, "fetchmany", None)
        raw_rows = (
            fetchmany(row_limit) if row_limit is not None and callable(fetchmany) else fetchall()
        )
        if row_limit is not None:
            raw_rows = raw_rows[:row_limit]
        rows = tuple(dict(zip(columns, row, strict=True)) for row in raw_rows)
        return columns, rows, types
    column_names = getattr(cursor, "column_names", None)
    result_rows = getattr(cursor, "result_rows", None)
    if column_names and result_rows is not None:
        columns = tuple(str(name) for name in column_names)
        raw_rows = result_rows[:row_limit] if row_limit is not None else result_rows
        rows = tuple(dict(zip(columns, row, strict=True)) for row in raw_rows)
        return columns, rows, {}
    return (), (), {}


def raw_sql(
    datasource: DatasourceRef,
    sql: str,
    *,
    limit: int = 100,
    reason: str,
    include_types: bool = True,
    project_root: Path | None = None,
) -> DatasourceResult:
    """Run a bounded read-only SQL diagnostic against a datasource.

    Args:
        datasource: Datasource reference returned by ``md.ref("datasource.warehouse")``.
        sql: Single read-only SQL statement. ``SELECT`` and ``WITH`` diagnostics
            are bounded with a wrapper query; metadata diagnostics such as
            ``SHOW``, ``DESCRIBE``, ``DESC``, and ``EXPLAIN`` execute directly
            so backend metadata syntax remains valid.
        limit: Maximum rows to return.
        reason: Required diagnostic reason; shown in the result.
        include_types: Whether to include returned column type labels when available.
        project_root: Optional project root for tests and embedded callers.

    Returns:
        ``DatasourceResult`` labeled as ``escape_hatch`` evidence.

    Example:
        >>> import marivo.datasource as md
        >>> md.raw_sql(md.ref("datasource.warehouse"), "SELECT 1 AS ok", reason="check query path")

    Constraints:
        Rejects empty reasons, empty SQL, and multi-statement SQL before execution.
        Read-only is enforced at the connection level: DuckDB and ClickHouse open in
        read-only mode, Postgres/MySQL run inside a ``READ ONLY`` transaction, and
        Trino runs ordinary SELECT/WITH queries through a read-only subquery
        wrapper. Unsupported backends are refused with a typed datasource error. Any
        execution failure (including a write attempt) surfaces as a
        ``DatasourceRawSqlError``; the backend is always disconnected.
    """
    if limit < 1:
        raise ValueError("limit must be positive.")
    reason_text = _require_raw_sql_reason(reason)
    statement = _require_single_statement(sql)
    datasource_id = _storage_name(datasource)
    datasource_ir = _store.load_one(datasource_id, project_root=project_root)
    if datasource_ir is None:
        raise DatasourceMissingError(
            message=f"datasource {datasource_id!r} is not configured",
            details={
                "datasource": datasource_id,
                "available": _store.list_names(project_root),
            },
        )
    backend_type = datasource_ir.backend_type
    service = DatasourceConnectionService(project_root)
    with service.use_backend(datasource_id, read_only=True) as backend:
        is_metadata_diagnostic = _is_metadata_diagnostic_sql(statement)
        execution_sql = (
            statement
            if is_metadata_diagnostic
            else f"SELECT * FROM ({statement}) AS marivo_raw_sql LIMIT {limit}"
        )
        use_transaction = backend_type in ("postgres", "mysql")
        try:
            cursor = _execute_readonly(
                backend,
                backend_type,
                execution_sql,
                use_transaction=use_transaction,
            )
            columns, extracted_rows, types = _extract_raw_sql_frame(
                cursor,
                include_types,
                limit=limit if is_metadata_diagnostic else None,
            )
        except DatasourceError:
            raise
        except Exception as exc:
            raise DatasourceRawSqlError(
                message="raw_sql execution failed; no side effects were applied.",
                details={
                    "datasource": datasource_id,
                    "backend_type": backend_type,
                    "reason": reason_text,
                    "cause": str(exc),
                },
            ) from exc
        rows = extracted_rows[:limit]
        is_truncated = len(extracted_rows) > limit if is_metadata_diagnostic else len(rows) >= limit
        return RawSqlResult(
            datasource=datasource,
            backend_type=backend_type,
            sql=statement,
            reason=reason_text,
            columns=columns,
            types=types,
            rows=rows,
            requested_limit=limit,
            returned_row_count=len(rows),
            is_truncated=is_truncated,
            warnings=(),
        )
