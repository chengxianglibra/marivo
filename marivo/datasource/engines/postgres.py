"""Postgres engine profile."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import (
    replace,
)
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
from ibis.backends import BaseBackend

from marivo.datasource.engines.aggregate_policy import POSTGRES_AGGREGATES
from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    TableRefRequest,
    quote_identifier,
    require_field,
    structured_exception_chain,
)
from marivo.datasource.engines.query import (
    EngineQueryPolicy,
    KeyRows,
    OriginalStringPolicy,
    QueryDialectProfile,
    finite_calendar_clock,
    floating_order_keys,
    zipped_key_arrays,
)
from marivo.datasource.engines.requirements import POSTGRES_REQUIREMENT
from marivo.datasource.engines.statements import (
    ProviderStatement,
    execute_provider_statement,
    register_provider_statements,
)
from marivo.datasource.engines.strptime import python_to_postgres_strptime
from marivo.datasource.errors import (
    _backend_failure_summary,
)
from marivo.datasource.ir import DatasourceIR, TableSourceIR
from marivo.datasource.metadata_types import (
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
    _schema_columns,
)


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:

    host = require_field(name, kwargs, "host", help_target="postgres")
    database = require_field(name, kwargs, "database", help_target="postgres")
    connect_kwargs: dict[str, object] = dict(kwargs)
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
            allowed_purposes=frozenset({"datasource.metadata.postgres"}),
        ),
        "column.collation": ProviderStatement(
            statement_id="postgres.column.collation",
            template=(
                "SELECT COALESCE(c.collisdeterministic, TRUE) AS deterministic "
                "FROM pg_catalog.pg_attribute a "
                "LEFT JOIN pg_catalog.pg_collation c ON c.oid = a.attcollation "
                "WHERE a.attrelid = pg_catalog.to_regclass({qualified}) "
                "AND a.attname = {column} AND a.attnum > 0 AND NOT a.attisdropped"
            ),
            literal_slots=frozenset({"qualified", "column"}),
            allowed_purposes=frozenset({"datasource.metadata.postgres"}),
        ),
        "columns": ProviderStatement(
            statement_id="postgres.columns",
            template=(
                "SELECT column_name, data_type, is_nullable, ordinal_position, datetime_precision, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_schema = {schema} "
                "AND table_name = {table} "
                "ORDER BY ordinal_position"
            ),
            literal_slots=frozenset({"schema", "table"}),
            allowed_purposes=frozenset({"datasource.metadata.postgres"}),
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
            allowed_purposes=frozenset({"datasource.metadata.postgres"}),
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
            allowed_purposes=frozenset({"datasource.metadata.postgres"}),
        ),
        "tables.kind": ProviderStatement(
            statement_id="postgres.tables.kind",
            template=(
                "SELECT c.relkind AS relation_kind, pg_get_viewdef(c.oid) AS view_definition "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.oid = pg_catalog.to_regclass({qualified}) "
                "LIMIT 1"
            ),
            literal_slots=frozenset({"qualified"}),
            allowed_purposes=frozenset({"datasource.metadata.postgres"}),
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
            allowed_purposes=frozenset({"datasource.metadata.postgres"}),
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
            allowed_purposes=frozenset({"datasource.metadata.postgres"}),
        ),
    },
)


def deterministic_collation(
    backend: BaseBackend, datasource: DatasourceIR, source: TableSourceIR, column: str
) -> bool:
    """Read one physical column's deterministic-collation fact from the catalog."""
    qualified = ".".join(
        quote_identifier(part, PROFILE)
        for part in table_name_parts(TableRefRequest(source, datasource))
    )
    rows = execute_provider_statement(
        backend,
        PROFILE,
        "postgres.column.collation",
        values={"qualified": qualified, "column": column},
        purpose="datasource.metadata.postgres",
    )
    return len(rows) == 1 and rows[0].get("deterministic") is True


def _postgres_rows(
    backend: BaseBackend, statement_id: str, values: Mapping[str, object] = {}
) -> tuple[dict[str, object], ...]:
    return execute_provider_statement(
        backend, PROFILE, statement_id, values=values, purpose="datasource.metadata.postgres"
    )


def _postgres_partitions(
    backend: BaseBackend,
    schema_name: str,
    table: str,
    columns: tuple[ColumnMetadata, ...],
    warnings: list[MetadataWarning],
    *,
    include_partitions: bool,
) -> tuple[tuple[PartitionMetadata, ...], Literal["known", "none", "unknown"]]:
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
    return tuple(partitions_by_name.values()), partition_state


def _postgres_view(
    backend: BaseBackend, qualified: str, warnings: list[MetadataWarning]
) -> tuple[bool | None, str | None]:
    is_view: bool | None = None
    view_definition: str | None = None
    try:
        kind_rows = _postgres_rows(backend, "postgres.tables.kind", {"qualified": qualified})
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
    return is_view, view_definition


def _postgres_columns(
    backend: BaseBackend,
    schema_name: str,
    table: str,
    qualified: str,
    schema_columns: tuple[ColumnMetadata, ...],
    warnings: list[MetadataWarning],
) -> dict[str, ColumnMetadata]:
    catalog_columns: dict[str, ColumnMetadata] = {}
    try:
        column_rows = _postgres_rows(
            backend, "postgres.columns", {"schema": schema_name, "table": table}
        )
        for row in column_rows:
            name = str(row.get("column_name"))
            ordinal = row.get("ordinal_position")
            physical_type = str(row.get("data_type") or "")
            width = row.get("character_maximum_length")
            if physical_type == "character" and type(width) is int:
                physical_type = f"character({width})"
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

    return catalog_columns


def _inspect_postgres(
    *,
    datasource: str,
    backend: BaseBackend,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: ir.Table,
    include_partitions: bool,
    default_schema: str | None,
) -> TableMetadata:

    schema_columns = _schema_columns(table_expr)
    schema_name = _database_label(database) or default_schema or "public"
    warnings: list[MetadataWarning] = []
    comment_schema = _database_label(database) or default_schema
    qualified = ".".join(
        quote_identifier(part, PROFILE)
        for part in ((comment_schema, table) if comment_schema else (table,))
    )
    table_comment: str | None = None
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

    catalog_columns = _postgres_columns(
        backend, schema_name, table, qualified, schema_columns, warnings
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

    is_view, view_definition = _postgres_view(backend, qualified, warnings)

    columns = _merge_columns(schema_columns, catalog_columns)
    partitions, partition_state = _postgres_partitions(
        backend, schema_name, table, columns, warnings, include_partitions=include_partitions
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
        partitions=partitions,
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
def native_query_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    if getattr(backend, "_marivo_terminal_timeout_seconds", None) != timeout_seconds:
        raise RuntimeError("postgres terminal connection has no configured statement timeout")
    yield


def _postgres_json(value: str) -> object:
    raise NotImplementedError("native PostgreSQL to_json")


def _postgres_encode(value: str, encoding: str) -> bytes:
    raise NotImplementedError("native PostgreSQL convert_to")


_POSTGRES_JSON: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="to_json", signature=(("string",), "json")
)(_postgres_json)
_POSTGRES_ENCODE: Callable[[ir.Value, str], ir.Value] = ibis.udf.scalar.builtin(
    name="convert_to", signature=(("string", "string"), "binary")
)(_postgres_encode)


def returned_text(value: ir.Value) -> ir.Value:
    """Preserve native CHAR padding before nullable text/key normalization."""
    encoded = _POSTGRES_JSON(value)
    assert isinstance(encoded, ir.JSONValue)
    return encoded.unwrap_as("str")


def string_bytes(value: ir.Value) -> ir.Value:
    # JSON preserves CHAR padding as returned to the reader; CHAR -> text does not.
    if not isinstance(value.op(), ops.Literal):
        value = returned_text(value)
    return _POSTGRES_ENCODE(value, "UTF8")


def _inline_key_relation(schema: sch.Schema, records: KeyRows) -> ir.Table | None:
    key_types = (dt.int64, dt.string, dt.boolean, dt.date)
    if (
        not records
        or not schema
        or any(dtype.copy(nullable=True) not in key_types for dtype in schema.types)
    ):
        return None
    return zipped_key_arrays(schema, records)


PROFILE = EngineProfile(
    dialect=QueryDialectProfile(
        aggregates=POSTGRES_AGGREGATES,
        string_bytes=string_bytes,
        returned_string=returned_text,
        source_membership_uses_returned_strings=True,
        original_strings=OriginalStringPolicy(column=deterministic_collation),
        native_subday_grains=frozenset({"second", "minute", "hour"}),
        finite_calendar_clock=finite_calendar_clock,
        preview_requires_terminal_scope=True,
        supports_native_frame_top=True,
        conditional_aggregate_style="filter_where",
        supports_sql_window_frames=True,
        window_frame_nulls_safe=True,
        supports_grouping_sets=True,
        grouping_sets_decimal_accumulator_precision=38,
        grouping_sets_wide_integer_sum_bytes=frozenset({8}),
        float_order_keys=floating_order_keys,
    ),
    arrow_reads=True,
    issued_query_only=True,
    query_policy=EngineQueryPolicy(
        key_fusion="key_counts",
        sample_fusion=True,
        semi_join_membership=True,
        inline_key_relation=_inline_key_relation,
    ),
    name="postgres",
    aliases=("postgresql", "redshift"),
    authoring_func="postgres",
    required_modules=(POSTGRES_REQUIREMENT,),
    connect=connect,
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
    native_query_timeout=native_query_timeout,
)
