"""Postgres engine profile."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal

from ibis.backends import BaseBackend

from marivo.datasource.capabilities import (
    ProviderStatement,
    execute_provider_statement,
    register_provider_statements,
)
from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    TableRefRequest,
    identity_read_only_kwargs,
    require_field,
    structured_exception_chain,
)
from marivo.datasource.strptime import python_to_postgres_strptime

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host", help_target="postgres")
    database = require_field(name, kwargs, "database", help_target="postgres")
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


register_provider_statements(
    "postgres",
    {
        "comment.table": ProviderStatement(
            statement_id="postgres.comment.table",
            template=(
                "SELECT obj_description(pg_catalog.to_regclass({qualified}), 'pg_class') AS comment"
            ),
            literal_slots=frozenset({"qualified"}),
        ),
        "columns": ProviderStatement(
            statement_id="postgres.columns",
            template=(
                "SELECT column_name, data_type, is_nullable, ordinal_position, datetime_precision "
                "FROM information_schema.columns "
                "WHERE table_schema = {schema} "
                "AND table_name = {table} "
                "ORDER BY ordinal_position"
            ),
            literal_slots=frozenset({"schema", "table"}),
        ),
        "comment.columns": ProviderStatement(
            statement_id="postgres.comment.columns",
            template=(
                "SELECT a.attname AS column_name, "
                "pg_catalog.col_description(a.attrelid, a.attnum) AS comment "
                "FROM pg_catalog.pg_attribute a "
                "WHERE a.attrelid = pg_catalog.to_regclass({qualified}) "
                "AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY a.attnum"
            ),
            literal_slots=frozenset({"qualified"}),
        ),
        "constraints": ProviderStatement(
            statement_id="postgres.constraints",
            template=(
                "SELECT c.oid AS constraint_id, c.contype AS constraint_kind, c.conkey AS key_attnums, "
                "a.attname AS column_name "
                "FROM pg_catalog.pg_constraint c "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.conrelid "
                "AND a.attnum = ANY(c.conkey) "
                "WHERE c.conrelid = pg_catalog.to_regclass({qualified}) "
                "AND c.contype IN ('p', 'u') "
                "ORDER BY c.oid, array_position(c.conkey, a.attnum)"
            ),
            literal_slots=frozenset({"qualified"}),
        ),
        "tables.kind": ProviderStatement(
            statement_id="postgres.tables.kind",
            template=(
                "SELECT c.relkind AS relation_kind, pg_get_viewdef(c.oid) AS view_definition "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = {table} "
                "AND n.nspname = {schema} "
                "LIMIT 1"
            ),
            literal_slots=frozenset({"schema", "table"}),
        ),
        "partition.key": ProviderStatement(
            statement_id="postgres.partition.key",
            template=(
                "SELECT pg_get_partkeydef(c.oid) AS partition_key "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = {table} "
                "AND n.nspname = {schema} "
                "LIMIT 1"
            ),
            literal_slots=frozenset({"schema", "table"}),
        ),
        "profile.physical": ProviderStatement(
            statement_id="postgres.profile.physical",
            template=(
                "SELECT c.reltuples, pg_total_relation_size(c.oid) AS total_relation_size "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = {table} "
                "AND n.nspname = {schema} "
                "LIMIT 1"
            ),
            literal_slots=frozenset({"schema", "table"}),
        ),
    },
)


def _postgres_rows(
    backend: Any, statement_id: str, values: Mapping[str, object] = {}
) -> tuple[dict[str, object], ...]:
    return execute_provider_statement(
        backend, PROFILE, statement_id, values=values, purpose="datasource.metadata.postgres"
    )


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
    from dataclasses import replace

    from marivo.datasource.errors import _backend_failure_summary
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        PartitionMetadata,
        TableMetadata,
        TablePhysicalProfile,
        UniqueConstraintMetadata,
        _bool_from_nullable,
        _database_label,
        _empty_to_none,
        _int_or_none,
        _merge_columns,
        _partition_columns_from_expression,
        _quote_identifier,
        _schema_columns,
    )

    schema_columns = _schema_columns(table_expr)
    schema_name = _database_label(database) or default_schema or "public"
    warnings: list[MetadataWarning] = []
    comment_schema = _database_label(database) or default_schema
    qualified = ".".join(
        _quote_identifier(part)
        for part in ((comment_schema, table) if comment_schema else (table,))
    )
    table_comment: str | None = None
    catalog_columns: dict[str, ColumnMetadata] = {}
    physical_profile: TablePhysicalProfile | None = None

    try:
        table_rows = _postgres_rows(backend, "postgres.comment.table", {"qualified": qualified})
        if table_rows:
            table_comment = _empty_to_none(table_rows[0].get("comment"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    f"postgres table comment query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    try:
        column_rows = _postgres_rows(
            backend, "postgres.columns", {"schema": schema_name, "table": table}
        )
        for row in column_rows:
            name = str(row.get("column_name"))
            ordinal = row.get("ordinal_position")
            physical_type = str(row.get("data_type") or "")
            precision = row.get("datetime_precision")
            if physical_type.startswith("timestamp") and type(precision) is int:
                physical_type = physical_type.replace("timestamp", f"timestamp({precision})", 1)
            catalog_columns[name] = ColumnMetadata(
                name=name,
                type=physical_type,
                nullable=_bool_from_nullable(row.get("is_nullable")),
                comment=None,
                ordinal_position=int(str(ordinal)) if ordinal is not None else None,
            )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    "postgres column metadata query failed: "
                    f"{_backend_failure_summary(exc).message}"
                ),
            )
        )

    try:
        comment_rows = _postgres_rows(backend, "postgres.comment.columns", {"qualified": qualified})
        if not comment_rows and schema_columns:
            warnings.append(
                MetadataWarning(
                    kind="column_comments_unavailable",
                    message="PostgreSQL column comment metadata was unavailable for the resolved relation.",
                )
            )
        comments = {
            str(row["column_name"]): _empty_to_none(row.get("comment")) for row in comment_rows
        }
        for schema_column in schema_columns:
            if schema_column.name in comments:
                comment_column = catalog_columns.get(schema_column.name, schema_column)
                catalog_columns[schema_column.name] = replace(
                    comment_column, comment=comments[schema_column.name]
                )
    except Exception:
        warnings.append(
            MetadataWarning(
                kind="column_comments_unavailable",
                message="PostgreSQL column comments could not be read for the resolved relation.",
            )
        )

    primary_keys: tuple[str, ...] = ()
    unique_constraints: list[UniqueConstraintMetadata] = []
    try:
        constraint_rows = _postgres_rows(backend, "postgres.constraints", {"qualified": qualified})
        pk_names: list[str] = []
        unique_by_position: dict[str, list[str]] = {}
        for row in constraint_rows:
            kind = str(row.get("constraint_kind") or "")
            column_name = row.get("column_name")
            if not isinstance(column_name, str) or not column_name:
                continue
            if kind == "p":
                pk_names.append(column_name)
            elif kind == "u":
                unique_by_position.setdefault(str(row["constraint_id"]), []).append(column_name)
        primary_keys = tuple(pk_names)
        unique_constraints = [
            UniqueConstraintMetadata(name=None, columns=tuple(names), kind="unique")
            for names in unique_by_position.values()
        ]
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    f"postgres constraint query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    is_view: bool | None = None
    view_definition: str | None = None
    try:
        kind_rows = _postgres_rows(
            backend, "postgres.tables.kind", {"schema": schema_name, "table": table}
        )
        if kind_rows:
            relation_kind = str(kind_rows[0].get("relation_kind") or "")
            is_view = relation_kind in ("v", "m")
            if is_view:
                view_definition = _empty_to_none(kind_rows[0].get("view_definition"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    f"postgres view metadata query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    columns = _merge_columns(schema_columns, catalog_columns)
    column_lookup = {column.name: column for column in columns}
    partitions_by_name: dict[str, PartitionMetadata] = {}
    partition_state: Literal["known", "none", "unknown"] = "unknown"
    if include_partitions:
        try:
            partition_rows = _postgres_rows(
                backend, "postgres.partition.key", {"schema": schema_name, "table": table}
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
                    message=(
                        "postgres partition metadata query failed: "
                        f"{_backend_failure_summary(exc).message}"
                    ),
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
        profile_rows = _postgres_rows(
            backend, "postgres.profile.physical", {"schema": schema_name, "table": table}
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
                message=(
                    "postgres physical profile query failed: "
                    f"{_backend_failure_summary(exc).message}"
                ),
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
        is_view=is_view,
        view_definition=view_definition,
        primary_keys=primary_keys,
        unique_constraints=tuple(unique_constraints),
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


def classify_table_resolution_failure(exc: Exception) -> Literal["metadata_unavailable"] | None:
    """Classify PostgreSQL metadata permission denial from SQLSTATE."""
    for candidate in structured_exception_chain(exc):
        if getattr(candidate, "sqlstate", None) == "42501":
            return "metadata_unavailable"
    return None


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    if getattr(backend, "_marivo_terminal_timeout_seconds", None) != timeout_seconds:
        raise RuntimeError("postgres terminal connection has no configured statement timeout")
    connection = backend.con
    if connection.read_only is not True or connection.autocommit:
        raise RuntimeError("postgres terminal connection is not in read-only transaction mode")
    try:
        yield
    finally:
        connection.rollback()


PROFILE = EngineProfile(
    name="postgres",
    aliases=("postgresql", "redshift"),
    authoring_func="postgres",
    required_modules=("ibis.backends.postgres",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
    identifier_quote='"',
    table_name_parts=table_name_parts,
    inspect_partition_values=None,
    metadata=EngineMetadataIntrospection(
        inspect_table=inspect_table,
        classify_table_resolution_failure=classify_table_resolution_failure,
    ),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=True,
    ),
    translate_strptime_format=python_to_postgres_strptime,
    datetime_decode_policy="local_naive_label",
    exact_count_distinct=True,
    exact_quantile=True,
    quantile=None,
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
)
