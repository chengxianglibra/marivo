"""Trino engine profile."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
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
    database = request.source.database
    catalog = str(request.datasource_ir.fields["catalog"])
    if isinstance(database, tuple):
        if len(database) >= 2:
            return (str(database[0]), str(database[1]), request.source.table)
        if len(database) == 1:
            return (catalog, str(database[0]), request.source.table)
        return (catalog, request.source.table)
    if database is not None:
        return (catalog, str(database), request.source.table)
    schema_value = request.datasource_ir.fields.get("schema")
    return (
        (catalog, request.source.table)
        if schema_value is None
        else (catalog, str(schema_value), request.source.table)
    )


def _partition_table_parts(request: PartitionProbeRequest) -> tuple[str, str | None, str]:
    database = request.source.database
    catalog = str(request.datasource_ir.fields["catalog"])
    if isinstance(database, tuple):
        if len(database) >= 2:
            return str(database[0]), str(database[1]), request.source.table
        if len(database) == 1:
            return catalog, str(database[0]), request.source.table
        return catalog, None, request.source.table
    if database is not None:
        return catalog, str(database), request.source.table
    schema_value = request.datasource_ir.fields.get("schema")
    return catalog, str(schema_value) if schema_value is not None else None, request.source.table


def inspect_partition_values(request: PartitionProbeRequest) -> PartitionProbeResult:
    catalog, schema_name, table_name = _partition_table_parts(request)
    if schema_name is None:
        raise RuntimeError("trino partition inspection requires database= or datasource schema")
    quoted_columns = ", ".join(
        quote_identifier(column, PROFILE) for column in request.partition_columns
    )
    table_ref = ".".join(
        quote_identifier(part, PROFILE)
        for part in (catalog, schema_name, f"{table_name}$partitions")
    )
    order_by = ", ".join(
        f"{quote_identifier(column, PROFILE)} DESC" for column in request.partition_columns
    )
    sql = f"SELECT {quoted_columns} FROM {table_ref} ORDER BY {order_by} LIMIT {request.limit}"
    frame = decode_cursor_frame(request.backend.raw_sql(sql), include_types=False, max_rows=None)
    return PartitionProbeResult(rows=frame.rows, value_source="metadata")


_TRINO_PARTITION_ARRAY_RE = re.compile(
    r"\b(?:partitioned_by|partitioning)\s*=\s*ARRAY\s*\[(.*?)\]",
    re.IGNORECASE | re.DOTALL,
)
_TRINO_ARRAY_STRING_RE = re.compile(r"'((?:[^']|'')*)'")
_TRINO_PARTITION_TRANSFORM_RE = re.compile(r"^([A-Za-z_]\w*)\((.*)\)$")


def _trino_columns_from_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    include_comment: bool,
) -> dict[str, ColumnMetadata]:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        _bool_from_nullable,
        _empty_to_none,
    )

    columns: dict[str, ColumnMetadata] = {}
    for row in rows:
        name = str(row.get("column_name"))
        ordinal = row.get("ordinal_position")
        columns[name] = ColumnMetadata(
            name=name,
            type=str(row.get("data_type") or ""),
            nullable=_bool_from_nullable(row.get("is_nullable")),
            comment=_empty_to_none(row.get("comment")) if include_comment else None,
            ordinal_position=int(str(ordinal)) if ordinal is not None else None,
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
    backend: Any,
    table: str,
    catalog: str,
    schema_name: str,
    catalog_columns: Mapping[str, ColumnMetadata],
    warnings: list[MetadataWarning],
) -> tuple[PartitionMetadata, ...]:
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
        return ()
    if not rows:
        return ()
    create_sql = ""
    for value in rows[0].values():
        if value is not None:
            create_sql = str(value)
            break
    partitions: list[PartitionMetadata] = []
    for spec in _trino_partition_specs_from_show_create(create_sql):
        partition = _trino_partition_from_spec(spec, catalog_columns)
        if partition is not None:
            partitions.append(partition)
    return tuple(partitions)


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
        _database_label,
        _empty_to_none,
        _is_missing_metadata_column,
        _merge_columns,
        _query_rows,
        _quote_literal,
        _schema_columns,
        _schema_only,
    )

    schema_columns = _schema_columns(table_expr)
    schema_name = _database_label(database) or default_schema
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

    table_predicates = [
        f"table_catalog = {_quote_literal(catalog)}",
        f"table_schema = {_quote_literal(schema_name)}",
        f"table_name = {_quote_literal(table)}",
    ]
    where_clause = " AND ".join(table_predicates)

    try:
        table_rows = _query_rows(
            backend,
            f"SELECT comment FROM information_schema.tables WHERE {where_clause} LIMIT 1",
        )
        if table_rows:
            table_comment = _empty_to_none(table_rows[0].get("comment"))
    except Exception as exc:
        if _is_missing_metadata_column(exc, "comment"):
            warnings.append(
                MetadataWarning(
                    kind="table_comments_unavailable",
                    message=f"trino table comments are unavailable: {exc}",
                )
            )
        else:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"trino table comment query failed: {exc}",
                )
            )

    catalog_columns: dict[str, ColumnMetadata] = {}
    try:
        column_rows = _query_rows(
            backend,
            "SELECT column_name, data_type, is_nullable, comment, ordinal_position "
            "FROM information_schema.columns "
            f"WHERE {where_clause} ORDER BY ordinal_position",
        )
        catalog_columns = _trino_columns_from_rows(column_rows, include_comment=True)
    except Exception as exc:
        try:
            column_rows = _query_rows(
                backend,
                "SELECT column_name, data_type, is_nullable, ordinal_position "
                "FROM information_schema.columns "
                f"WHERE {where_clause} ORDER BY ordinal_position",
            )
            catalog_columns = _trino_columns_from_rows(column_rows, include_comment=False)
            warnings.append(
                MetadataWarning(
                    kind="column_comments_unavailable",
                    message=f"trino column comments are unavailable: {exc}",
                )
            )
        except Exception as exc2:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"trino column metadata query failed: {exc2}",
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
    partitions: tuple[PartitionMetadata, ...] = ()
    if include_partitions:
        partitions = _trino_partitions_from_show_create(
            backend=backend,
            table=table,
            catalog=catalog,
            schema_name=schema_name,
            catalog_columns={column.name: column for column in columns},
            warnings=warnings,
        )
        if not partitions:
            warnings.append(
                MetadataWarning(
                    kind="partitions_unavailable",
                    message=(
                        "trino partition metadata is connector-specific "
                        "and not exposed by this adapter"
                    ),
                )
            )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="trino",
        comment=table_comment,
        columns=columns,
        partitions=partitions,
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
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
    translate_strptime_format=python_to_mysql_strptime,
    postprocess_sql=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=QuantileCapability(mode="approximate", method="qdigest"),
    percentile_uses_approx_quantile=True,
)
