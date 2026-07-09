"""MySQL engine profile."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    TableRefRequest,
    identity_read_only_kwargs,
    identity_str,
    require_field,
)

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata

from marivo.datasource.strptime import python_to_mysql_strptime


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host")
    database = require_field(name, kwargs, "database")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = database
    return ibis.mysql.connect(**connect_kwargs)


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    database = request.source.database
    schema_name = (
        str(database) if database is not None and not isinstance(database, tuple) else None
    )
    if schema_name is None:
        schema_name = str(request.datasource_ir.fields["database"])
    return (schema_name, request.source.table)


def _inspect_mysql(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
    default_database: str | None,
) -> TableMetadata:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        PartitionMetadata,
        TableMetadata,
        TablePhysicalProfile,
        _bool_from_nullable,
        _database_label,
        _empty_to_none,
        _int_or_none,
        _merge_columns,
        _partition_column_from_expression,
        _query_rows,
        _quote_literal,
        _schema_columns,
        _table_ref,
    )

    schema_columns = _schema_columns(table_expr)
    schema_name = _database_label(database) or default_database
    table_comment: str | None = None
    physical_profile: TablePhysicalProfile | None = None
    warnings: list[MetadataWarning] = []

    table_comment_sql = (
        "SELECT TABLE_COMMENT, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH "
        "FROM information_schema.tables "
        f"WHERE table_name = {_quote_literal(table)}"
    )
    if schema_name is not None:
        table_comment_sql += f" AND table_schema = {_quote_literal(schema_name)}"
    try:
        table_rows = _query_rows(backend, table_comment_sql)
        if table_rows:
            row = table_rows[0]
            table_comment = _empty_to_none(row.get("TABLE_COMMENT"))
            row_count = _int_or_none(row.get("TABLE_ROWS"))
            data_length = _int_or_none(row.get("DATA_LENGTH"))
            index_length = _int_or_none(row.get("INDEX_LENGTH"))
            size_bytes = (
                (data_length or 0) + (index_length or 0)
                if data_length is not None or index_length is not None
                else None
            )
            if row_count is not None or size_bytes is not None:
                physical_profile = TablePhysicalProfile(
                    row_count=row_count,
                    row_count_kind="estimate" if row_count is not None else "unknown",
                    size_bytes=size_bytes,
                    size_kind="data_plus_index" if size_bytes is not None else "unknown",
                    source="mysql.information_schema.tables",
                )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"mysql table comment query failed: {exc}",
            )
        )

    catalog_columns: dict[str, ColumnMetadata] = {}
    table_ref = _table_ref(table, database)
    try:
        column_rows = _query_rows(backend, f"SHOW FULL COLUMNS FROM {table_ref}")
        for index, row in enumerate(column_rows, start=1):
            name = str(row.get("Field"))
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=str(row.get("Type") or ""),
                nullable=_bool_from_nullable(row.get("Null")),
                comment=_empty_to_none(row.get("Comment")),
                ordinal_position=index,
            )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"mysql column metadata query failed: {exc}",
            )
        )

    if include_partitions:
        partitions_by_name: dict[str, PartitionMetadata] = {}
        partition_sql = (
            "SELECT DISTINCT PARTITION_EXPRESSION FROM information_schema.PARTITIONS "
            f"WHERE TABLE_NAME = {_quote_literal(table)} "
            "AND PARTITION_NAME IS NOT NULL"
        )
        if schema_name is not None:
            partition_sql += f" AND TABLE_SCHEMA = {_quote_literal(schema_name)}"
        try:
            partition_rows = _query_rows(backend, partition_sql)
            for row in partition_rows:
                column_name = _partition_column_from_expression(row.get("PARTITION_EXPRESSION"))
                column = catalog_columns.get(column_name or "")
                if column is not None:
                    partitions_by_name[column.name] = PartitionMetadata(
                        name=column.name,
                        type=column.type,
                        transform=None,
                        comment=None,
                    )
        except Exception as exc:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"mysql partition metadata query failed: {exc}",
                )
            )
        if not partitions_by_name:
            warnings.append(
                MetadataWarning(
                    kind="partitions_unavailable",
                    message="mysql partition metadata did not expose mappable column partitions",
                )
            )

    is_view = False
    view_definition: str | None = None
    type_sql = (
        "SELECT TABLE_TYPE FROM information_schema.tables "
        f"WHERE table_name = {_quote_literal(table)}"
    )
    if schema_name is not None:
        type_sql += f" AND table_schema = {_quote_literal(schema_name)}"
    try:
        type_rows = _query_rows(backend, type_sql)
        if type_rows and str(type_rows[0].get("TABLE_TYPE") or "").upper() == "VIEW":
            is_view = True
            def_rows = _query_rows(
                backend,
                "SELECT VIEW_DEFINITION FROM information_schema.views "
                f"WHERE table_name = {_quote_literal(table)}"
                + (f" AND table_schema = {_quote_literal(schema_name)}" if schema_name else ""),
            )
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("VIEW_DEFINITION"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"mysql view metadata query failed: {exc}",
            )
        )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="mysql",
        comment=table_comment,
        columns=_merge_columns(schema_columns, catalog_columns),
        partitions=tuple(partitions_by_name.values()) if include_partitions else (),
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
        physical_profile=physical_profile,
    )


def inspect_table(request: MetadataInspectRequest) -> TableMetadata:
    return _inspect_mysql(
        datasource=request.datasource,
        backend=request.backend,
        table=request.table,
        database=request.database,
        table_expr=request.table_expr,
        include_partitions=request.include_partitions,
        default_database=(
            str(request.datasource_ir.fields["database"])
            if request.datasource_ir.fields.get("database") is not None
            else None
        ),
    )


PROFILE = EngineProfile(
    name="mysql",
    aliases=(),
    authoring_func="mysql",
    required_modules=("ibis.backends.mysql",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
    timezone_probe_sql=None,
    identifier_quote="`",
    table_name_parts=table_name_parts,
    inspect_partition_values=None,
    readonly_tx_start="START TRANSACTION READ ONLY",
    metadata=EngineMetadataIntrospection(inspect_table=inspect_table),
    translate_strptime_format=python_to_mysql_strptime,
    postprocess_sql=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=None,
    percentile_uses_approx_quantile=False,
)
