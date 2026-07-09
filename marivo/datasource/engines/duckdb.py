"""DuckDB engine profile."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    QuantileCapability,
    default_table_name_parts,
    identity_str,
)

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    path = kwargs.get("path", ":memory:")
    connect_kwargs: dict[str, object] = dict(kwargs)
    connect_kwargs.pop("path", None)
    connect_kwargs["database"] = path
    if "read_only" in connect_kwargs:
        connect_kwargs["read_only"] = bool(connect_kwargs["read_only"])
    return ibis.duckdb.connect(**connect_kwargs)


def apply_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    out = dict(kwargs)
    out["read_only"] = True
    return out


def _duckdb_view_predicate(
    table: str,
    database: str | tuple[str, ...] | None,
    *,
    default_database: str | None = None,
    default_schema: str = "main",
) -> str:
    from marivo.datasource.metadata import _quote_literal

    predicates = [
        f"view_name = {_quote_literal(table)}",
        "internal = false",
    ]
    if isinstance(database, tuple):
        if len(database) == 1:
            predicates.append(f"schema_name = {_quote_literal(database[0])}")
        elif len(database) >= 2:
            predicates.append(f"database_name = {_quote_literal(database[0])}")
            predicates.append(f"schema_name = {_quote_literal(database[1])}")
    elif database is not None:
        predicates.append(f"schema_name = {_quote_literal(database)}")
    else:
        if default_database is not None:
            predicates.append(f"database_name = {_quote_literal(default_database)}")
        predicates.append(f"schema_name = {_quote_literal(default_schema)}")
    return " AND ".join(predicates)


def _inspect_duckdb(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
) -> TableMetadata:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        TableMetadata,
        TablePhysicalProfile,
        UniqueConstraintMetadata,
        _bool_from_nullable,
        _empty_to_none,
        _int_or_none,
        _merge_columns,
        _query_rows,
        _quote_literal,
        _schema_columns,
    )

    schema_columns = _schema_columns(table_expr)
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None
    catalog_columns: dict[str, ColumnMetadata] = {}
    is_view = False
    view_definition: str | None = None
    physical_profile: TablePhysicalProfile | None = None

    try:
        table_rows = _query_rows(
            backend,
            "SELECT comment, estimated_size FROM duckdb_tables() "
            f"WHERE table_name = {_quote_literal(table)} LIMIT 1",
        )
        if table_rows:
            row = table_rows[0]
            table_comment = _empty_to_none(row.get("comment"))
            row_count = _int_or_none(row.get("estimated_size"))
            if row_count is not None:
                physical_profile = TablePhysicalProfile(
                    row_count=row_count,
                    row_count_kind="estimate",
                    size_bytes=None,
                    size_kind="unknown",
                    source="duckdb.duckdb_tables",
                )
    except Exception as exc:
        try:
            table_rows = _query_rows(
                backend,
                "SELECT comment FROM duckdb_tables() "
                f"WHERE table_name = {_quote_literal(table)} LIMIT 1",
            )
            if table_rows:
                table_comment = _empty_to_none(table_rows[0].get("comment"))
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"duckdb physical profile query failed: {exc}",
                )
            )
        except Exception as exc2:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"duckdb table metadata query failed: {exc2}",
                )
            )

    try:
        column_rows = _query_rows(
            backend,
            "SELECT column_name, data_type, is_nullable, comment "
            "FROM duckdb_columns() "
            f"WHERE table_name = {_quote_literal(table)} "
            "ORDER BY column_index",
        )
        for index, row in enumerate(column_rows, start=1):
            name = str(row.get("column_name"))
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=str(row.get("data_type") or ""),
                nullable=_bool_from_nullable(row.get("is_nullable")),
                comment=_empty_to_none(row.get("comment")),
                ordinal_position=index,
            )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"duckdb column metadata query failed: {exc}",
            )
        )

    try:
        default_database: str | None = None
        default_schema = "main"
        if database is None:
            namespace_rows = _query_rows(
                backend,
                "SELECT current_database() AS database_name, current_schema() AS schema_name",
            )
            if namespace_rows:
                default_database = _empty_to_none(namespace_rows[0].get("database_name"))
                default_schema = _empty_to_none(namespace_rows[0].get("schema_name")) or "main"
        view_rows = _query_rows(
            backend,
            "SELECT sql FROM duckdb_views() "
            "WHERE "
            f"{_duckdb_view_predicate(table, database, default_database=default_database, default_schema=default_schema)} "
            "LIMIT 1",
        )
        if view_rows:
            is_view = True
            view_definition = _empty_to_none(view_rows[0].get("sql"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"duckdb view metadata query failed: {exc}",
            )
        )

    primary_keys: tuple[str, ...] = ()
    unique_constraints: tuple[UniqueConstraintMetadata, ...] = ()
    try:
        constraint_rows = _query_rows(
            backend,
            "SELECT constraint_type, constraint_column_names "
            "FROM duckdb_constraints() "
            f"WHERE table_name = {_quote_literal(table)}",
        )
        pk_columns: list[str] = []
        uq_rows: list[UniqueConstraintMetadata] = []
        for row in constraint_rows:
            ctype = str(row.get("constraint_type") or "").upper()
            cols_value = row.get("constraint_column_names")
            cols = (
                tuple(str(col) for col in cols_value)
                if isinstance(cols_value, (list, tuple))
                else ()
            )
            if ctype == "PRIMARY KEY" and cols:
                pk_columns.extend(cols)
            elif ctype == "UNIQUE" and cols:
                uq_rows.append(UniqueConstraintMetadata(name=None, columns=cols, kind="unique"))
        primary_keys = tuple(pk_columns)
        unique_constraints = tuple(uq_rows)
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"duckdb constraint query failed: {exc}",
            )
        )

    if include_partitions:
        warnings.append(
            MetadataWarning(
                kind="partitions_unavailable",
                message="duckdb does not expose table partition metadata through this adapter",
            )
        )

    columns = _merge_columns(schema_columns, catalog_columns)
    if not any(column.comment for column in columns) and table_comment is None:
        warnings.append(
            MetadataWarning(
                kind="comments_unavailable",
                message="duckdb table and column comments are unavailable for this table",
            )
        )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="duckdb",
        comment=table_comment,
        columns=columns,
        partitions=(),
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
        primary_keys=primary_keys,
        unique_constraints=unique_constraints,
        physical_profile=physical_profile,
    )


def inspect_table(request: MetadataInspectRequest) -> TableMetadata:
    return _inspect_duckdb(
        datasource=request.datasource,
        backend=request.backend,
        table=request.table,
        database=request.database,
        table_expr=request.table_expr,
        include_partitions=request.include_partitions,
    )


PROFILE = EngineProfile(
    name="duckdb",
    aliases=(),
    authoring_func="duckdb",
    required_modules=("ibis.backends.duckdb",),
    connect=connect,
    apply_read_only_kwargs=apply_read_only_kwargs,
    timezone_probe_sql="select current_setting('TimeZone') as timezone",
    identifier_quote='"',
    table_name_parts=default_table_name_parts,
    inspect_partition_values=None,
    readonly_tx_start=None,
    metadata=EngineMetadataIntrospection(inspect_table=inspect_table),
    translate_strptime_format=identity_str,
    postprocess_sql=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=QuantileCapability(mode="exact", method="linear_interpolation"),
    percentile_uses_approx_quantile=False,
)
