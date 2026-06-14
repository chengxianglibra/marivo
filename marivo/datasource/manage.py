"""Unified datasource management API (md.*)."""

from __future__ import annotations

import builtins
import time
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from marivo.datasource import backends as _backends
from marivo.datasource import secrets as _secrets
from marivo.datasource import store as _store
from marivo.datasource.authoring import DatasourceSpec
from marivo.datasource.errors import DatasourceMissingError, DatasourcePreviewError
from marivo.datasource.ir import EntitySourceIR, FileSourceIR, TableSourceIR
from marivo.datasource.metadata import TableMetadata
from marivo.datasource.metadata import inspect_source as _inspect_source
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.scan import (
    ColumnInspection,
    ColumnProfile,
    JoinKeyProbe,
    JoinSide,
    ScanReport,
    ScanScope,
)
from marivo.preview import (
    PREVIEW_DEFAULT_LIMIT,
    PreviewFilter,
    PreviewOrder,
    PreviewResult,
    PreviewSamplePolicy,
    preview_ibis_table,
)
from marivo.render import format_bounded_card, result_repr


@dataclass(frozen=True, repr=False)
class DatasourceSummary:
    """Summary row for one configured project datasource."""

    name: str
    backend_type: str
    description: str | None = None

    @property
    def semantic_id(self) -> str:
        """Stable id used by discovery surfaces; equals ``name``."""
        return self.name

    def _repr_identity(self) -> str:
        return f"DatasourceSummary name={self.name} backend={self.backend_type}"

    def render(self) -> str:
        return format_bounded_card(
            identity=self._repr_identity(),
            status=self.description,
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True)
class DatasourceDescription:
    """Literal fields and env refs for one datasource."""

    name: str
    backend_type: str
    literal_fields: dict[str, Any]
    env_refs: dict[str, str]


@dataclass(frozen=True, repr=False)
class DatasourceTestResult:
    """Result of a datasource connectivity round-trip."""

    name: str
    ok: bool
    error: str | None
    latency_ms: int | None

    def _repr_identity(self) -> str:
        latency = "n/a" if self.latency_ms is None else f"{self.latency_ms}ms"
        return f"DatasourceTestResult name={self.name} ok={self.ok} latency={latency}"

    def render(self) -> str:
        return format_bounded_card(
            identity=self._repr_identity(),
            status=self.error,
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


def register(
    spec: DatasourceSpec,
    *,
    project_root: Path | None = None,
) -> DatasourceSummary:
    """Create or replace a project datasource file from a DatasourceSpec.

    Args:
        spec: Validated datasource specification with name, backend_type,
            and connection fields.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``DatasourceSummary`` for the newly stored datasource.

    Example:
        >>> import marivo.datasource as md
        >>> md.register(md.DatasourceSpec(name="wh", backend_type="duckdb", path=":memory:"))

    Constraints:
        The name must be a flat identifier (no dots).  Sensitive fields
        (password, token, key) must use ``*_env`` references, not literals.
    """
    stored = _store.save_one(spec, project_root=project_root)
    return DatasourceSummary(
        name=stored.name, backend_type=stored.backend_type, description=stored.description
    )


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
        Only the project-local ``marivo/datasources/<name>.py`` file is removed.
    """
    return _store.delete_one(name)


def list() -> builtins.list[DatasourceSummary]:
    """List configured project datasources as DatasourceSummary rows.

    Returns:
        Sorted list of ``DatasourceSummary`` objects (by name).

    Example:
        >>> import marivo.datasource as md
        >>> md.list()
        [DatasourceSummary(name='wh', ...)]

    Constraints:
        Only datasources with a persisted project file are included.
    """
    return [
        DatasourceSummary(name=p.name, backend_type=p.backend_type, description=p.description)
        for p in sorted(_store.load_all().values(), key=lambda item: item.name)
    ]


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


def connect(name: str) -> Any:
    """Open a live ibis backend for a datasource; caller disconnects.

    Args:
        name: The datasource name to connect to.

    Returns:
        A live ibis backend. The caller must call ``.disconnect()`` when done.

    Example:
        >>> import marivo.datasource as md
        >>> backend = md.connect("wh")
        >>> try:
        ...     backend.raw_sql("SELECT 1")
        ... finally:
        ...     backend.disconnect()

    Constraints:
        The caller owns the backend lifetime and must call ``disconnect()``.
        Env-sourced secrets used to open this backend are remembered on the
        backend object so that a subsequent round-trip validation can persist
        them via ``secrets.persist_backend_env_sourced``.
    """
    datasource = _store.load_one(name)
    if datasource is None:
        raise DatasourceMissingError(
            message=f"datasource {name!r} is not configured",
            details={"datasource": name, "available": _store.list_names()},
        )
    built = _backends.build_backend_with_secrets(datasource)
    _secrets.remember_env_sourced(built.backend, built.env_sourced_secrets)
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
    finally:
        if backend is not None:
            disconnect = getattr(backend, "disconnect", None)
            if callable(disconnect):
                with suppress(Exception):
                    disconnect()


def inspect_table(
    datasource: str,
    source: EntitySourceIR | None = None,
    *,
    table: str | None = None,
    database: str | tuple[str, ...] | None = None,
    include_partitions: bool = True,
    project_root: Path | None = None,
) -> TableMetadata:
    """Schema, comments, nullability, and partition metadata for a table.

    Args:
        datasource: Name of the project datasource.
        source: An ``EntitySourceIR`` (from ``md.table()`` or ``md.file()``).
            Pass either ``source`` or ``table``, not both.
        table: Table name within the datasource (alternative to ``source``).
        database: Optional database/catalog path.
        include_partitions: Whether to include partition hints.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``TableMetadata`` with columns, warnings, and optional partitions.

    Example:
        >>> import marivo.datasource as md
        >>> md.inspect_table("wh", md.table("orders"))

    Constraints:
        Opens and closes a backend connection internally.
    """
    if source is not None and table is not None:
        raise TypeError("Pass either source or table, not both.")
    if source is None:
        if table is None:
            raise TypeError("inspect_table requires a structured source or table name.")
        source = TableSourceIR(table=table, database=database)
    return _inspect_source(
        datasource,
        source=source,
        include_partitions=include_partitions,
        project_root=project_root,
    )


def inspect_source(
    datasource: str,
    *,
    source: EntitySourceIR,
    include_partitions: bool = True,
    project_root: Path | None = None,
) -> TableMetadata:
    """Table metadata for a semantic entity source (table or file).

    Args:
        datasource: Name of the project datasource.
        source: An ``EntitySourceIR`` describing the table or file.
        include_partitions: Whether to include partition hints.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``TableMetadata`` with columns, warnings, and optional partitions.

    Example:
        >>> import marivo.datasource as md
        >>> md.inspect_source("wh", source=source_ir)

    Constraints:
        Opens and closes a backend connection internally.
    """
    return _inspect_source(
        datasource,
        source=source,
        include_partitions=include_partitions,
        project_root=project_root,
    )


def inspect_columns(
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
        source: An ``EntitySourceIR`` (from ``md.table()`` or ``md.file()``).
        columns: Column names to profile; ``None`` profiles all columns
            (capped by ``scope.max_columns``).
        scope: Bounded scan configuration; defaults to ``ScanScope()``.
        project_root: Optional project root directory; defaults to cwd.

    Returns:
        A ``ColumnInspection`` with per-column profiles and a ``ScanReport``.

    Example:
        >>> import marivo.datasource as md
        >>> md.inspect_columns(
        ...     "wh",
        ...     md.table("orders"),
        ...     columns=("status", "amount"),
        ...     scope=md.ScanScope(partition=None, max_rows=100),
        ... )

    Constraints:
        The backend is always disconnected before returning, even on error.
        Scan scope limits (max_rows, max_columns) are always enforced.
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

    # Build column spec lookup from metadata.
    column_specs: dict[str, tuple[str, bool | None, str | None]] = {
        column.name: (column.type, column.nullable, column.comment) for column in metadata.columns
    }

    # Resolve partition for the scan report.
    partition_resolution: str
    partition_used: Mapping[str, str] | None = None
    if scope.partition is None:
        partition_resolution = "unpruned"
    elif scope.partition == "latest":
        partition_resolution = "latest"
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
    warnings: builtins.list[str] = []

    # Profile each column.
    profiles: builtins.list[ColumnProfile] = []
    for column_name in selected_columns:
        spec = column_specs.get(column_name)
        if spec is None:
            warnings.append(f"column {column_name!r} absent from source schema")
            profiles.append(
                ColumnProfile(
                    column=column_name,
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
                )
            )
            continue

        data_type, nullable, comment = spec
        if column_name not in frame:
            warnings.append(f"column {column_name!r} absent from bounded sample")
            profiles.append(
                ColumnProfile(
                    column=column_name,
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
        if isinstance(source, (TableSourceIR, FileSourceIR))
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
        elif isinstance(source, FileSourceIR):
            reader_name = {
                "parquet": "read_parquet",
                "csv": "read_csv",
                "json": "read_json",
            }[source.format]
            reader = getattr(backend, reader_name)
            expr = reader(source.path, **source.options)
        else:
            raise TypeError(f"unsupported source type: {type(source).__name__}")

        # Apply partition filter if scope has an explicit partition.
        if (
            scope.partition is not None
            and scope.partition != "latest"
            and isinstance(scope.partition, Mapping)
        ):
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

    empty_count = 0
    if series.dtype == object:
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

    return ColumnProfile(
        column=column_name,
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
    )


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
        side.datasource,
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
    elif scope.partition == "latest":
        partition_resolution = "latest"
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
        side.datasource,
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
    elif scope.partition == "latest":
        partition_resolution = "latest"
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


def probe_join_keys(
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

    Example:
        >>> import marivo.datasource as md
        >>> md.probe_join_keys(
        ...     from_side=md.JoinSide("wh", md.table("orders"), columns=("customer_id",)),
        ...     to_side=md.JoinSide("wh", md.table("customers"), columns=("customer_id",)),
        ...     scope=md.ScanScope(partition=None, max_rows=100),
        ...     project_root=project_root,
        ... )

    Constraints:
        Both from-side and to-side may reference the same or different
        datasources. Key comparison uses tuple equality. Matching is
        performed client-side after a bounded sample.
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


def test(name: str) -> DatasourceTestResult:
    """Round-trip the backend and persist validated env secrets.

    Args:
        name: The datasource name to test.

    Returns:
        A ``DatasourceTestResult`` with ok/error status and latency.

    Example:
        >>> import marivo.datasource as md
        >>> md.test("wh")

    Constraints:
        On success, env-sourced secrets that resolved correctly are
        persisted to the user-global plaintext cache. The backend is
        always disconnected.
    """
    start = time.perf_counter()
    backend: Any | None = None
    try:
        backend = connect(name)
        backend.raw_sql("SELECT 1")
        _secrets.persist_backend_env_sourced(backend)
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
