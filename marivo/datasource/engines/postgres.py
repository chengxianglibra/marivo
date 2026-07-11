"""Postgres engine profile."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    AuthoringCapabilities,
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


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host")
    database = require_field(name, kwargs, "database")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = database
    return ibis.postgres.connect(**connect_kwargs)


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    database = request.source.database
    schema_name = (
        str(database) if database is not None and not isinstance(database, tuple) else None
    )
    if schema_name is None:
        schema_value = request.datasource_ir.fields.get("schema")
        schema_name = str(schema_value) if schema_value is not None else None
    return (request.source.table,) if schema_name is None else (schema_name, request.source.table)


def _inspect_postgres(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
    default_schema: str | None,
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
        _partition_columns_from_expression,
        _query_rows,
        _quote_literal,
        _schema_columns,
    )

    schema_columns = _schema_columns(table_expr)
    schema_name = _database_label(database) or default_schema or "public"
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None
    catalog_columns: dict[str, ColumnMetadata] = {}
    physical_profile: TablePhysicalProfile | None = None

    try:
        table_rows = _query_rows(
            backend,
            "SELECT obj_description(to_regclass("
            f"{_quote_literal(f'{schema_name}.{table}')}), 'pg_class') AS comment",
        )
        if table_rows:
            table_comment = _empty_to_none(table_rows[0].get("comment"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"postgres table comment query failed: {exc}",
            )
        )

    try:
        column_rows = _query_rows(
            backend,
            "SELECT column_name, data_type, is_nullable, ordinal_position "
            "FROM information_schema.columns "
            f"WHERE table_schema = {_quote_literal(schema_name)} "
            f"AND table_name = {_quote_literal(table)} "
            "ORDER BY ordinal_position",
        )
        for row in column_rows:
            name = str(row.get("column_name"))
            ordinal = row.get("ordinal_position")
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=str(row.get("data_type") or ""),
                nullable=_bool_from_nullable(row.get("is_nullable")),
                comment=None,
                ordinal_position=int(str(ordinal)) if ordinal is not None else None,
            )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"postgres column metadata query failed: {exc}",
            )
        )

    columns = _merge_columns(schema_columns, catalog_columns)
    column_lookup = {column.name: column for column in columns}
    partitions_by_name: dict[str, PartitionMetadata] = {}
    partition_state: Literal["known", "none", "unknown"] = "unknown"
    if include_partitions:
        try:
            partition_rows = _query_rows(
                backend,
                "SELECT pg_get_partkeydef(c.oid) AS partition_key "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                f"WHERE c.relname = {_quote_literal(table)} "
                f"AND n.nspname = {_quote_literal(schema_name)} "
                "LIMIT 1",
            )
            saw_partition_expression = False
            for row in partition_rows:
                partition_key = str(row.get("partition_key") or "")
                if partition_key:
                    saw_partition_expression = True
                for column_name in _partition_columns_from_expression(partition_key):
                    column = column_lookup.get(column_name or "")
                    if column is not None:
                        partitions_by_name[column.name] = PartitionMetadata(
                            name=column.name,
                            type=column.type,
                            transform=None,
                            comment=None,
                        )
            if partitions_by_name:
                partition_state = "known"
            elif not saw_partition_expression:
                partition_state = "none"
        except Exception as exc:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"postgres partition metadata query failed: {exc}",
                )
            )
        if partition_state == "unknown":
            warnings.append(
                MetadataWarning(
                    kind="partitions_unavailable",
                    message="postgres partition metadata did not expose mappable column partitions",
                )
            )

    try:
        profile_rows = _query_rows(
            backend,
            "SELECT c.reltuples, pg_total_relation_size(c.oid) AS total_relation_size "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE c.relname = {_quote_literal(table)} "
            f"AND n.nspname = {_quote_literal(schema_name)} "
            "LIMIT 1",
        )
        if profile_rows:
            row = profile_rows[0]
            row_count = _int_or_none(row.get("reltuples"))
            if row_count is not None and row_count < 0:
                row_count = None
            size_bytes = _int_or_none(row.get("total_relation_size"))
            if row_count is not None or size_bytes is not None:
                physical_profile = TablePhysicalProfile(
                    row_count=row_count,
                    row_count_kind="estimate" if row_count is not None else "unknown",
                    size_bytes=size_bytes,
                    size_kind="on_disk" if size_bytes is not None else "unknown",
                    source="postgres.pg_class",
                )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=f"postgres physical profile query failed: {exc}",
            )
        )

    return TableMetadata(
        datasource=datasource,
        table=table,
        database=database,
        backend_type="postgres",
        comment=table_comment,
        columns=columns,
        partitions=tuple(partitions_by_name.values()) if include_partitions else (),
        partition_state=partition_state,
        warnings=tuple(warnings),
        physical_profile=physical_profile,
    )


def inspect_table(request: MetadataInspectRequest) -> TableMetadata:
    return _inspect_postgres(
        datasource=request.datasource,
        backend=request.backend,
        table=request.table,
        database=request.database,
        table_expr=request.table_expr,
        include_partitions=request.include_partitions,
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
        raise RuntimeError("postgres backend does not expose raw_sql()")
    raw_sql("BEGIN READ ONLY")
    try:
        raw_sql(f"SET LOCAL statement_timeout = '{timeout_seconds * 1000}ms'")
        yield
    finally:
        raw_sql("ROLLBACK")


PROFILE = EngineProfile(
    name="postgres",
    aliases=("postgresql", "redshift"),
    authoring_func="postgres",
    required_modules=("ibis.backends.postgres",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
    timezone_probe_sql="select current_setting('TimeZone') as timezone",
    identifier_quote='"',
    table_name_parts=table_name_parts,
    inspect_partition_values=None,
    readonly_tx_start="BEGIN READ ONLY",
    metadata=EngineMetadataIntrospection(inspect_table=inspect_table),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=True,
    ),
    translate_strptime_format=identity_str,
    postprocess_sql=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=None,
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
)
