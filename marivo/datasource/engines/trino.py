"""Trino engine profile."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import replace
from functools import partial
from operator import itemgetter
from typing import Literal, cast

import ibis
import ibis.expr.schema as sch
import ibis.expr.types as ir
from ibis.backends import BaseBackend
from ibis.backends.sql.datatypes import TrinoType
from ibis.common.exceptions import TableNotFound
from sqlglot import expressions as sge

from marivo.datasource.engines.aggregate_policy import TRINO_AGGREGATES
from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    PartitionProbeRequest,
    PartitionProbeResult,
    TableRefRequest,
    require_field,
    structured_exception_chain,
)
from marivo.datasource.engines.query import (
    EngineQueryPolicy,
    OriginalStringPolicy,
    QueryDialectProfile,
)
from marivo.datasource.engines.requirements import TRINO_REQUIREMENT
from marivo.datasource.engines.statements import (
    ProviderStatement,
    execute_provider_statement,
    register_provider_statements,
)
from marivo.datasource.engines.strptime import python_to_mysql_strptime
from marivo.datasource.engines.trino_metadata import (
    partitions_from_show_create as _trino_partitions_from_show_create,
)
from marivo.datasource.engines.trino_metadata import (
    table_comment_from_show_create as _trino_table_comment_from_show_create,
)
from marivo.datasource.engines.trino_query import (
    _trino_namespace,
    inline_key_relation,
    string_bytes,
    varchar_columns,
)
from marivo.datasource.errors import (
    _backend_failure_summary,
)
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.metadata_types import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
    TablePhysicalProfile,
    UniqueConstraintMetadata,
    _bool_from_nullable,
    _empty_to_none,
    _int_or_none,
    _merge_columns,
    _schema_columns,
    schema_only_metadata,
)
from marivo.datasource.metadata_types import (
    ColumnMetadata as _ColumnMetadata,
)
from marivo.datasource.metadata_types import (
    MetadataWarning as _Warning,
)
from marivo.datasource.metadata_types import (
    TablePhysicalProfile as _Profile,
)


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:

    host = require_field(name, kwargs, "host", help_target="trino")
    catalog = require_field(name, kwargs, "catalog", help_target="trino")
    user = require_field(name, kwargs, "user", help_target="trino")
    connect_kwargs: dict[str, object] = dict(kwargs)
    connect_kwargs.pop("catalog", None)
    connect_kwargs.update(host=host, database=catalog, user=user)
    if "client_tags" in kwargs:
        tags = kwargs["client_tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        if not isinstance(tags, Iterable):
            raise TypeError(f"'{type(tags).__name__}' object is not iterable")
        connect_kwargs["client_tags"] = list(tags)
    if "session_properties" in kwargs and isinstance(kwargs["session_properties"], dict):
        connect_kwargs["session_properties"] = dict(kwargs["session_properties"])
    backend: BaseBackend = ibis.trino.connect(**connect_kwargs)
    _configure_schema_lookup(backend)
    return backend


def _get_table_schema(
    backend: BaseBackend,
    table_name: str,
    *,
    catalog: str | None,
    database: str | None,
) -> sch.Schema:
    """Read one table's physical columns and restore ordinal order locally."""
    query = (
        sge.select(
            sge.column("column_name"),
            sge.column("data_type"),
            sge.column("is_nullable").eq(sge.convert("YES")).as_("nullable"),
            sge.column("ordinal_position"),
        )
        .from_(sge.table_("columns", db="information_schema", catalog=catalog))
        .where(
            sge.column("table_name").eq(sge.convert(table_name)),
            sge.column("table_schema").eq(sge.convert(database or backend.current_database)),
        )
    )
    with closing(backend.raw_sql(query)) as cursor:
        # Trino returns lists. The fixed projection determines each tuple's field types.
        rows = cast(
            "list[tuple[str, str, bool | None, int]]",
            [tuple(row) for row in cursor.fetchall()],
        )
    if not rows:
        raise TableNotFound(sge.table_(table_name, db=database, catalog=catalog).sql(backend.name))
    type_mapper: type[TrinoType] = backend.compiler.type_mapper
    return sch.Schema(
        {
            name: type_mapper.from_string(data_type, nullable=nullable)
            for name, data_type, nullable, _ in sorted(rows, key=itemgetter(3))
        }
    )


def _configure_schema_lookup(backend: BaseBackend) -> None:
    """Bind ordinary tables and qualified partition metadata tables."""
    if not callable(getattr(backend, "get_schema", None)):
        return

    def get_schema(
        table_name: str,
        *,
        catalog: str | None = None,
        database: str | None = None,
    ) -> sch.Schema:
        if catalog is None or database is None or not table_name.endswith("$partitions"):
            return _get_table_schema(backend, table_name, catalog=catalog, database=database)
        rows = _trino_rows(
            backend,
            "trino.show_columns",
            identifiers={"table_ref": (catalog, database, table_name)},
        )
        if not rows:
            raise RuntimeError("SHOW COLUMNS returned no columns for the partition metadata table")
        return sch.Schema(
            {
                str(row["Column"]): TrinoType.from_string(str(row["Type"]), nullable=True)
                for row in rows
            }
        )

    backend.get_schema = get_schema


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
    identity = f"partition-metadata:{catalog}.{schema_name}.{table_name}"
    with request.open_reader() as session:
        relation = session.bind(
            TableSourceIR(f"{table_name}$partitions", database=(catalog, schema_name)),
            source_identity=identity,
        ).relation
        expression = _partition_projection(
            relation, request.partition_columns, request.order, request.limit
        )
        frame = session.collect_bounded(
            expression,
            source_identities=(identity,),
            purpose="datasource.partition_metadata",
            max_rows=request.limit,
        )
        rows: list[dict[str, object]] = [
            {str(name): value for name, value in row.items()}
            for row in frame.to_dict(orient="records")
        ]
    return PartitionProbeResult(rows=tuple(rows), value_source="metadata")


def _partition_projection(
    relation: ir.Table,
    columns: tuple[str, ...],
    order: Literal["asc", "desc"],
    limit: int,
) -> ir.Table:
    nested = "partition" in relation.columns and not all(
        column in relation.columns for column in columns
    )
    values = relation.select(
        **{
            column: (relation["partition"][column] if nested else relation[column])
            for column in columns
        }
    )
    sort_keys = (
        value.asc() if order == "asc" else value.desc()
        for value in (values[column] for column in columns)
    )
    return values.order_by(*sort_keys).limit(limit)


def classify_table_resolution_failure(exc: Exception) -> Literal["metadata_unavailable"] | None:
    """Classify Trino metadata permission denial from the server error name."""
    for candidate in structured_exception_chain(exc):
        if getattr(candidate, "error_name", None) == "PERMISSION_DENIED":
            return "metadata_unavailable"
    return None


@contextmanager
def native_query_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    properties = getattr(backend.con, "session_properties", None)
    if (
        getattr(backend, "_marivo_terminal_timeout_seconds", None) != timeout_seconds
        or not isinstance(properties, dict)
        or properties.get("query_max_run_time") != f"{timeout_seconds}s"
    ):
        raise RuntimeError("trino terminal connection has no configured query timeout")
    yield


register_provider_statements(
    "trino",
    {
        "columns": ProviderStatement(
            statement_id="trino.columns",
            template=(
                "SELECT column_name, data_type, is_nullable, ordinal_position "
                "FROM information_schema.columns "
                "WHERE table_catalog = {catalog} "
                "AND table_schema = {schema} "
                "AND table_name = {table} "
                "ORDER BY ordinal_position"
            ),
            literal_slots=frozenset({"catalog", "schema", "table"}),
            allowed_purposes=frozenset({"datasource.metadata.trino"}),
        ),
        "show_columns": ProviderStatement(
            statement_id="trino.show_columns",
            template="SHOW COLUMNS FROM {table_ref}",
            identifier_slots=frozenset({"table_ref"}),
            allowed_purposes=frozenset({"datasource.metadata.trino"}),
        ),
        "tables.type": ProviderStatement(
            statement_id="trino.tables.type",
            template=(
                "SELECT table_type FROM information_schema.tables "
                "WHERE table_catalog = {catalog} "
                "AND table_schema = {schema} "
                "AND table_name = {table} "
                "LIMIT 1"
            ),
            literal_slots=frozenset({"catalog", "schema", "table"}),
            allowed_purposes=frozenset({"datasource.metadata.trino"}),
        ),
        "views.definition": ProviderStatement(
            statement_id="trino.views.definition",
            template=(
                "SELECT view_definition FROM information_schema.views "
                "WHERE table_catalog = {catalog} "
                "AND table_schema = {schema} "
                "AND table_name = {table} "
                "LIMIT 1"
            ),
            literal_slots=frozenset({"catalog", "schema", "table"}),
            allowed_purposes=frozenset({"datasource.metadata.trino"}),
        ),
        "show_create": ProviderStatement(
            statement_id="trino.show_create",
            template="SHOW CREATE TABLE {table_ref}",
            identifier_slots=frozenset({"table_ref"}),
            allowed_purposes=frozenset({"datasource.metadata.trino"}),
        ),
        "show_stats": ProviderStatement(
            statement_id="trino.show_stats",
            template="SHOW STATS FOR {table_ref}",
            identifier_slots=frozenset({"table_ref"}),
            allowed_purposes=frozenset({"datasource.metadata.trino"}),
        ),
        "constraints": ProviderStatement(
            statement_id="trino.constraints",
            template=(
                "SELECT tc.constraint_type AS constraint_type, kcu.column_name AS column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON kcu.constraint_schema = tc.constraint_schema "
                "AND kcu.table_name = tc.table_name "
                "AND kcu.constraint_name = tc.constraint_name "
                "WHERE tc.constraint_schema = {schema} "
                "AND tc.table_name = {table} "
                "AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE') "
                "ORDER BY tc.constraint_name, kcu.ordinal_position"
            ),
            literal_slots=frozenset({"schema", "table"}),
            allowed_purposes=frozenset({"datasource.metadata.trino"}),
        ),
    },
)


def _trino_rows(
    backend: BaseBackend,
    statement_id: str,
    *,
    values: Mapping[str, object] = {},
    identifiers: Mapping[str, str | tuple[str, ...]] = {},
) -> tuple[dict[str, object], ...]:
    return execute_provider_statement(
        backend,
        PROFILE,
        statement_id,
        values=values,
        identifiers=identifiers,
        purpose="datasource.metadata.trino",
    )


def _trino_columns_from_rows(rows: Iterable[Mapping[str, object]]) -> dict[str, ColumnMetadata]:

    columns: dict[str, _ColumnMetadata] = {}
    for row in rows:
        name = str(row.get("column_name"))
        ordinal = row.get("ordinal_position")
        columns[name] = _ColumnMetadata(
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


def _trino_show_create_table(
    *,
    backend: BaseBackend,
    table: str,
    catalog: str,
    schema_name: str,
    warnings: list[MetadataWarning],
) -> str | None:

    try:
        rows = _trino_rows(
            backend,
            "trino.show_create",
            identifiers={"table_ref": (catalog, schema_name, table)},
        )
    except Exception as exc:
        warnings.append(
            _Warning(
                kind="metadata_query_failed",
                message=(
                    f"trino show create table query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )
        return None
    if not rows:
        return None
    for value in rows[0].values():
        if value is not None:
            return str(value)
    return None


def _trino_physical_profile(
    *,
    backend: BaseBackend,
    table: str,
    catalog: str,
    schema_name: str,
    warnings: list[MetadataWarning],
) -> TablePhysicalProfile | None:

    try:
        rows = _trino_rows(
            backend,
            "trino.show_stats",
            identifiers={"table_ref": (catalog, schema_name, table)},
        )
    except Exception as exc:
        warnings.append(
            _Warning(
                kind="metadata_query_failed",
                message=(
                    f"trino physical profile query failed: {_backend_failure_summary(exc).message}"
                ),
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
    return _Profile(
        row_count=row_count,
        row_count_kind="estimate" if row_count is not None else "unknown",
        size_bytes=size_bytes if saw_size else None,
        size_kind="table_stats" if saw_size else "unknown",
        source="trino.show_stats",
    )


def _trino_column_metadata(
    backend: BaseBackend,
    catalog_name: str,
    schema_name: str,
    table: str,
    predicates: Mapping[str, object],
    warnings: list[MetadataWarning],
) -> dict[str, ColumnMetadata]:
    catalog_columns: dict[str, ColumnMetadata] = {}
    try:
        column_rows = _trino_rows(backend, "trino.columns", values=predicates)
        catalog_columns = _trino_columns_from_rows(column_rows)
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    f"trino column metadata query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    if catalog_columns:
        try:
            show_column_rows = _trino_rows(
                backend,
                "trino.show_columns",
                identifiers={"table_ref": (catalog_name, schema_name, table)},
            )
            catalog_columns = _trino_columns_with_show_comments(
                catalog_columns,
                show_column_rows,
            )
        except Exception as exc:
            warnings.append(
                MetadataWarning(
                    kind="column_comments_unavailable",
                    message=(
                        "trino column comments are unavailable: "
                        f"{_backend_failure_summary(exc).message}"
                    ),
                )
            )

    return catalog_columns


def _trino_definition_metadata(
    backend: BaseBackend,
    catalog_name: str,
    schema_name: str,
    table: str,
    columns: tuple[ColumnMetadata, ...],
    is_view: bool | None,
    include_partitions: bool,
    warnings: list[MetadataWarning],
) -> tuple[str | None, tuple[PartitionMetadata, ...]]:
    table_comment: str | None = None
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

    return table_comment, partitions


def _inspect_trino(
    *,
    datasource: str,
    backend: BaseBackend,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: ir.Table,
    include_partitions: bool,
    catalog: str,
    default_schema: str | None,
) -> TableMetadata:

    schema_columns = _schema_columns(table_expr)
    catalog_name, schema_name = _trino_namespace(
        database,
        catalog=catalog,
        default_schema=default_schema,
    )
    if schema_name is None:
        return schema_only_metadata(
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
    physical_profile: TablePhysicalProfile | None = None
    predicates = {"catalog": catalog_name, "schema": schema_name, "table": table}

    catalog_columns = _trino_column_metadata(
        backend, catalog_name, schema_name, table, predicates, warnings
    )
    is_view: bool | None = None
    view_definition: str | None = None
    try:
        type_rows = _trino_rows(backend, "trino.tables.type", values=predicates)
        if type_rows:
            is_view = str(type_rows[0].get("table_type") or "").upper() == "VIEW"
        if is_view:
            is_view = True
            def_rows = _trino_rows(backend, "trino.views.definition", values=predicates)
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("view_definition"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    f"trino view metadata query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    primary_keys: tuple[str, ...] = ()
    unique_constraints: list[UniqueConstraintMetadata] = []
    try:
        constraint_rows = _trino_rows(
            backend, "trino.constraints", values={"schema": schema_name, "table": table}
        )
        pk_names: list[str] = []
        unique_names: list[str] = []
        for row in constraint_rows:
            kind = str(row.get("constraint_type") or "").upper()
            column_name = row.get("column_name")
            if not isinstance(column_name, str) or not column_name:
                continue
            if kind == "PRIMARY KEY" and column_name not in pk_names:
                pk_names.append(column_name)
            elif kind == "UNIQUE" and column_name not in unique_names:
                unique_names.append(column_name)
        primary_keys = tuple(pk_names)
        if unique_names:
            unique_constraints.append(
                UniqueConstraintMetadata(name=None, columns=tuple(unique_names), kind="unique")
            )
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(f"trino constraint query failed: {_backend_failure_summary(exc).message}"),
            )
        )

    columns = _merge_columns(schema_columns, catalog_columns)
    table_comment, partitions = _trino_definition_metadata(
        backend, catalog_name, schema_name, table, columns, is_view, include_partitions, warnings
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
        primary_keys=primary_keys,
        unique_constraints=tuple(unique_constraints),
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


PROFILE = EngineProfile(
    dialect=QueryDialectProfile(
        aggregates=TRINO_AGGREGATES,
        string_bytes=string_bytes,
        original_strings=OriginalStringPolicy(columns=partial(varchar_columns, read=_trino_rows)),
        native_subday_grains=frozenset({"second", "minute", "hour"}),
        preview_requires_terminal_scope=True,
        supports_per_row_attribute_time=True,
        supports_native_frame_top=True,
    ),
    arrow_reads=True,
    query_policy=EngineQueryPolicy(
        key_fusion="numeric",
        sample_fusion=True,
        inline_key_relation=inline_key_relation,
    ),
    name="trino",
    aliases=("presto",),
    authoring_func="trino",
    required_modules=(TRINO_REQUIREMENT,),
    connect=connect,
    identifier_quote='"',
    table_name_parts=table_name_parts,
    inspect_partition_values=inspect_partition_values,
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
    translate_strptime_format=python_to_mysql_strptime,
    datetime_decode_policy="local_naive_label",
    native_query_timeout=native_query_timeout,
)
