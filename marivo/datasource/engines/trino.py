"""Trino engine profile."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    PartitionProbeRequest,
    PartitionProbeResult,
    QuantileCapability,
    TableRefRequest,
    decode_cursor_frame,
    identity_read_only_kwargs,
    identity_str,
    quote_identifier,
    require_field,
)

if TYPE_CHECKING:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        PartitionMetadata,
        TableMetadata,
        TablePhysicalProfile,
    )

from marivo.datasource.strptime import python_to_mysql_strptime


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host")
    catalog = require_field(name, kwargs, "catalog")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs.pop("catalog", None)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = catalog
    if "client_tags" in kwargs:
        tags: Any = kwargs["client_tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        connect_kwargs["client_tags"] = list(tags)
    if "session_properties" in kwargs and isinstance(kwargs["session_properties"], dict):
        connect_kwargs["session_properties"] = dict(kwargs["session_properties"])
    return ibis.trino.connect(**connect_kwargs)


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    catalog = str(request.datasource_ir.fields["catalog"])
    schema_value = request.datasource_ir.fields.get("schema")
    catalog_name, schema_name = _trino_namespace(
        request.source.database,
        catalog=catalog,
        default_schema=str(schema_value) if schema_value is not None else None,
    )
    return (
        (catalog_name, request.source.table)
        if schema_name is None
        else (catalog_name, schema_name, request.source.table)
    )


def _trino_namespace(
    database: str | tuple[str, ...] | None,
    *,
    catalog: str,
    default_schema: str | None,
) -> tuple[str, str | None]:
    if isinstance(database, tuple):
        if len(database) >= 2:
            return str(database[0]), str(database[1])
        if len(database) == 1:
            return catalog, str(database[0])
        return catalog, None
    if database is not None:
        parts = str(database).split(".")
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]
        return catalog, str(database)
    return catalog, default_schema


def _partition_table_parts(request: PartitionProbeRequest) -> tuple[str, str | None, str]:
    catalog = str(request.datasource_ir.fields["catalog"])
    schema_value = request.datasource_ir.fields.get("schema")
    catalog_name, schema_name = _trino_namespace(
        request.source.database,
        catalog=catalog,
        default_schema=str(schema_value) if schema_value is not None else None,
    )
    return catalog_name, schema_name, request.source.table


def inspect_partition_values(request: PartitionProbeRequest) -> PartitionProbeResult:
    catalog, schema_name, table_name = _partition_table_parts(request)
    if schema_name is None:
        raise RuntimeError("trino partition inspection requires database= or datasource schema")
    table_ref = ".".join(
        quote_identifier(part, PROFILE)
        for part in (catalog, schema_name, f"{table_name}$partitions")
    )
    # Hive-connector ``$partitions`` exposes partition columns as top-level
    # columns, but Iceberg ``$partitions`` nests partition values under a
    # ``partition`` row column — so ``SELECT <col>`` raises COLUMN_NOT_FOUND on
    # Iceberg even though the column exists in ``SHOW COLUMNS``. Probe the
    # ``$partitions`` schema once and route through the ``partition`` row when
    # the partition columns are not top-level. See issue #21.
    iceberg = _partitions_table_is_iceberg(request.backend, table_ref, request.partition_columns)
    select_columns = ", ".join(
        _partition_column_select(column, iceberg) for column in request.partition_columns
    )
    order_by = ", ".join(
        f"{_partition_column_ref(column, iceberg)} DESC" for column in request.partition_columns
    )
    sql = f"SELECT {select_columns} FROM {table_ref} ORDER BY {order_by} LIMIT {request.limit}"
    frame = decode_cursor_frame(request.backend.raw_sql(sql), include_types=False, max_rows=None)
    return PartitionProbeResult(rows=frame.rows, value_source="metadata")


def _partitions_table_is_iceberg(
    backend: BaseBackend,
    table_ref: str,
    partition_columns: tuple[str, ...],
) -> bool:
    """Return True when ``$partitions`` nests partition values under ``partition``.

    Iceberg's ``$partitions`` table has a ``partition`` row column and does not
    expose the partition columns at the top level. Hive's ``$partitions`` exposes
    the partition columns directly. A ``LIMIT 0`` probe reads only the column
    metadata, so it scans no partition data.
    """
    probe = decode_cursor_frame(
        backend.raw_sql(f"SELECT * FROM {table_ref} LIMIT 0"),
        include_types=False,
        max_rows=None,
    )
    columns = set(probe.columns)
    if not columns:
        return False
    return "partition" in columns and not all(column in columns for column in partition_columns)


def _partition_column_ref(column: str, iceberg: bool) -> str:
    quoted = quote_identifier(column, PROFILE)
    return f"{quote_identifier('partition', PROFILE)}.{quoted}" if iceberg else quoted


def _partition_column_select(column: str, iceberg: bool) -> str:
    quoted = quote_identifier(column, PROFILE)
    if not iceberg:
        return quoted
    # Route through the ``partition`` row and alias back to the column name so
    # downstream value extraction (row.get(field.name)) resolves the column.
    return f"{quote_identifier('partition', PROFILE)}.{quoted} AS {quoted}"


_TRINO_PARTITION_ARRAY_RE = re.compile(
    r"\b(?:partitioned_by|partitioning)\s*=\s*ARRAY\s*\[(.*?)\]",
    re.IGNORECASE | re.DOTALL,
)
_TRINO_ARRAY_STRING_RE = re.compile(r"'((?:[^']|'')*)'")
_TRINO_PARTITION_TRANSFORM_RE = re.compile(r"^([A-Za-z_]\w*)\((.*)\)$")
_TRINO_TABLE_COMMENT_RE = re.compile(
    r"\)\s*COMMENT\s+'((?:[^']|'')*)'",
    re.IGNORECASE | re.DOTALL,
)


def _trino_columns_from_rows(
    rows: Iterable[Mapping[str, object]],
) -> dict[str, ColumnMetadata]:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        _bool_from_nullable,
    )

    columns: dict[str, ColumnMetadata] = {}
    for row in rows:
        name = str(row.get("column_name"))
        ordinal = row.get("ordinal_position")
        columns[name] = ColumnMetadata(
            name=name,
            type=str(row.get("data_type") or ""),
            nullable=_bool_from_nullable(row.get("is_nullable")),
            comment=None,
            ordinal_position=int(str(ordinal)) if ordinal is not None else None,
        )
    return columns


def _row_value(row: Mapping[str, object], *names: str) -> object:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name in row:
            return row[name]
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return None


def _trino_columns_with_show_comments(
    catalog_columns: Mapping[str, ColumnMetadata],
    rows: Iterable[Mapping[str, object]],
) -> dict[str, ColumnMetadata]:
    from marivo.datasource.metadata import _empty_to_none

    columns = dict(catalog_columns)
    for row in rows:
        name_value = _row_value(row, "Column", "column_name", "column")
        if name_value is None:
            continue
        name = str(name_value)
        column = columns.get(name)
        if column is None:
            continue
        columns[name] = replace(
            column,
            comment=_empty_to_none(_row_value(row, "Comment", "comment")),
        )
    return columns


def _trino_partition_specs_from_show_create(create_sql: str) -> tuple[str, ...]:
    match = _TRINO_PARTITION_ARRAY_RE.search(create_sql)
    if not match:
        return ()
    return tuple(
        value.replace("''", "'").strip() for value in _TRINO_ARRAY_STRING_RE.findall(match.group(1))
    )


def _trino_partition_from_spec(
    spec: str,
    catalog_columns: Mapping[str, ColumnMetadata],
) -> PartitionMetadata | None:
    from marivo.datasource.metadata import PartitionMetadata

    transform: str | None = None
    column_name = spec.strip()
    transform_match = _TRINO_PARTITION_TRANSFORM_RE.match(column_name)
    if transform_match:
        transform = transform_match.group(1)
        first_arg = transform_match.group(2).split(",", 1)[0].strip()
        column_name = first_arg.strip('"')
    column = catalog_columns.get(column_name)
    if column is None:
        return None
    return PartitionMetadata(
        name=column_name,
        type=column.type,
        transform=transform,
        comment=None,
    )


def _trino_partitions_from_show_create(
    *,
    create_sql: str,
    catalog_columns: Mapping[str, ColumnMetadata],
) -> tuple[PartitionMetadata, ...]:
    partitions: list[PartitionMetadata] = []
    for spec in _trino_partition_specs_from_show_create(create_sql):
        partition = _trino_partition_from_spec(spec, catalog_columns)
        if partition is not None:
            partitions.append(partition)
    return tuple(partitions)


def _trino_show_create_table(
    *,
    backend: Any,
    table: str,
    catalog: str,
    schema_name: str,
    warnings: list[MetadataWarning],
) -> str | None:
    from marivo.datasource.metadata import (
        MetadataWarning,
        _query_rows,
        _table_ref,
    )

    try:
        table_ref = _table_ref(table, (catalog, schema_name))
        rows = _query_rows(backend, f"SHOW CREATE TABLE {table_ref}")
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"trino show create table query failed: {exc}",
            )
        )
        return None
    if not rows:
        return None
    for value in rows[0].values():
        if value is not None:
            return str(value)
    return None


def _trino_table_comment_from_show_create(create_sql: str) -> str | None:
    from marivo.datasource.metadata import _empty_to_none

    match = _TRINO_TABLE_COMMENT_RE.search(create_sql)
    if match is None:
        return None
    return _empty_to_none(match.group(1).replace("''", "'"))


def _trino_physical_profile(
    *,
    backend: Any,
    table: str,
    catalog: str,
    schema_name: str,
    warnings: list[MetadataWarning],
) -> TablePhysicalProfile | None:
    from marivo.datasource.metadata import (
        MetadataWarning,
        TablePhysicalProfile,
        _int_or_none,
        _query_rows,
        _table_ref,
    )

    try:
        rows = _query_rows(backend, f"SHOW STATS FOR {_table_ref(table, (catalog, schema_name))}")
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"trino physical profile query failed: {exc}",
            )
        )
        return None

    row_count: int | None = None
    size_bytes = 0
    saw_size = False
    for row in rows:
        column_name = row.get("column_name")
        candidate_row_count = _int_or_none(row.get("row_count"))
        if (
            column_name is None or str(column_name).strip() == ""
        ) and candidate_row_count is not None:
            row_count = candidate_row_count
        data_size = _int_or_none(row.get("data_size"))
        if data_size is not None:
            saw_size = True
            size_bytes += data_size
    if row_count is None and not saw_size:
        return None
    return TablePhysicalProfile(
        row_count=row_count,
        row_count_kind="estimate" if row_count is not None else "unknown",
        size_bytes=size_bytes if saw_size else None,
        size_kind="table_stats" if saw_size else "unknown",
        source="trino.show_stats",
    )


def _inspect_trino(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
    catalog: str,
    default_schema: str | None,
) -> TableMetadata:
    from marivo.datasource.metadata import (
        MetadataWarning,
        TableMetadata,
        _empty_to_none,
        _merge_columns,
        _query_rows,
        _quote_literal,
        _schema_columns,
        _schema_only,
        _table_ref,
    )

    schema_columns = _schema_columns(table_expr)
    catalog_name, schema_name = _trino_namespace(
        database,
        catalog=catalog,
        default_schema=default_schema,
    )
    if schema_name is None:
        return _schema_only(
            datasource=datasource,
            table=table,
            database=database,
            backend_type="trino",
            table_expr=table_expr,
            warnings=(
                MetadataWarning(
                    kind="comments_unavailable",
                    message="trino metadata inspection requires database= or datasource schema",
                ),
                MetadataWarning(
                    kind="nullable_unavailable",
                    message="trino metadata inspection requires database= or datasource schema",
                ),
            ),
        )
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None
    physical_profile: TablePhysicalProfile | None = None

    table_predicates = [
        f"table_catalog = {_quote_literal(catalog_name)}",
        f"table_schema = {_quote_literal(schema_name)}",
        f"table_name = {_quote_literal(table)}",
    ]
    where_clause = " AND ".join(table_predicates)

    catalog_columns: dict[str, ColumnMetadata] = {}
    try:
        column_rows = _query_rows(
            backend,
            "SELECT column_name, data_type, is_nullable, ordinal_position "
            "FROM information_schema.columns "
            f"WHERE {where_clause} ORDER BY ordinal_position",
        )
        catalog_columns = _trino_columns_from_rows(column_rows)
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"trino column metadata query failed: {exc}",
            )
        )

    if catalog_columns:
        try:
            show_column_rows = _query_rows(
                backend,
                f"SHOW COLUMNS FROM {_table_ref(table, (catalog_name, schema_name))}",
            )
            catalog_columns = _trino_columns_with_show_comments(
                catalog_columns,
                show_column_rows,
            )
        except Exception as exc:
            warnings.append(
                MetadataWarning(
                    kind="column_comments_unavailable",
                    message=f"trino column comments are unavailable: {exc}",
                )
            )

    is_view = False
    view_definition: str | None = None
    try:
        type_rows = _query_rows(
            backend,
            f"SELECT table_type FROM information_schema.tables WHERE {where_clause} LIMIT 1",
        )
        if type_rows and str(type_rows[0].get("table_type") or "").upper() == "VIEW":
            is_view = True
            def_rows = _query_rows(
                backend,
                f"SELECT view_definition FROM information_schema.views WHERE {where_clause} LIMIT 1",
            )
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("view_definition"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"trino view metadata query failed: {exc}",
            )
        )

    columns = _merge_columns(schema_columns, catalog_columns)
    create_sql: str | None = None
    if not is_view:
        create_sql = _trino_show_create_table(
            backend=backend,
            table=table,
            catalog=catalog_name,
            schema_name=schema_name,
            warnings=warnings,
        )
        if create_sql is not None:
            table_comment = _trino_table_comment_from_show_create(create_sql)
    if table_comment is None:
        warnings.append(
            MetadataWarning(
                kind="table_comments_unavailable",
                message="trino table comments are unavailable from SHOW CREATE TABLE",
            )
        )

    partitions: tuple[PartitionMetadata, ...] = ()
    if include_partitions and create_sql is not None:
        partitions = _trino_partitions_from_show_create(
            create_sql=create_sql,
            catalog_columns={column.name: column for column in columns},
        )
    if include_partitions and not partitions:
        warnings.append(
            MetadataWarning(
                kind="partitions_unavailable",
                message=(
                    "trino partition metadata is connector-specific and not exposed by this adapter"
                ),
            )
        )

    if not is_view:
        physical_profile = _trino_physical_profile(
            backend=backend,
            table=table,
            catalog=catalog_name,
            schema_name=schema_name,
            warnings=warnings,
        )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="trino",
        comment=table_comment,
        columns=columns,
        partitions=partitions,
        partition_state="known" if partitions else "unknown",
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
        physical_profile=physical_profile,
    )


def inspect_table(request: MetadataInspectRequest) -> TableMetadata:
    return _inspect_trino(
        datasource=request.datasource,
        backend=request.backend,
        table=request.table,
        database=request.database,
        table_expr=request.table_expr,
        include_partitions=request.include_partitions,
        catalog=str(request.datasource_ir.fields["catalog"]),
        default_schema=(
            str(request.datasource_ir.fields["schema"])
            if request.datasource_ir.fields.get("schema") is not None
            else None
        ),
    )


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    raw_sql = getattr(backend, "raw_sql", None)
    if not callable(raw_sql):
        raise RuntimeError("trino backend does not expose raw_sql()")
    cursor = raw_sql("SHOW SESSION LIKE 'query_max_run_time'")
    fetchone = getattr(cursor, "fetchone", None)
    row = fetchone() if callable(fetchone) else None
    if not row or len(row) < 2:
        raise RuntimeError("trino did not expose the current query_max_run_time setting")
    previous = str(row[1]).replace("'", "''")
    try:
        raw_sql(f"SET SESSION query_max_run_time = '{timeout_seconds}s'")
    except BaseException:
        raw_sql(f"SET SESSION query_max_run_time = '{previous}'")
        raise
    try:
        yield
    finally:
        raw_sql(f"SET SESSION query_max_run_time = '{previous}'")


PROFILE = EngineProfile(
    name="trino",
    aliases=("presto",),
    authoring_func="trino",
    required_modules=("ibis.backends.trino",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
    timezone_probe_sql="select current_timezone() as timezone",
    identifier_quote='"',
    table_name_parts=table_name_parts,
    inspect_partition_values=inspect_partition_values,
    readonly_tx_start=None,
    metadata=EngineMetadataIntrospection(inspect_table=inspect_table),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=True,
    ),
    translate_strptime_format=python_to_mysql_strptime,
    postprocess_sql=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=QuantileCapability(mode="approximate", method="qdigest"),
    percentile_uses_approx_quantile=True,
    authoring_timeout=authoring_timeout,
)
