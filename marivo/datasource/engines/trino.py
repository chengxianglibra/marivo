"""Trino engine profile."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

import ibis.expr.types as ir
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
    PartitionProbeRequest,
    PartitionProbeResult,
    QuantileCapability,
    TableRefRequest,
    identity_read_only_kwargs,
    require_field,
    structured_exception_chain,
)
from marivo.datasource.strptime import python_to_mysql_strptime

if TYPE_CHECKING:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        PartitionMetadata,
        TableMetadata,
        TablePhysicalProfile,
    )


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    host = require_field(name, kwargs, "host", help_target="trino")
    catalog = require_field(name, kwargs, "catalog", help_target="trino")
    user = require_field(name, kwargs, "user", help_target="trino")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs.pop("catalog", None)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = catalog
    connect_kwargs["user"] = user
    if "client_tags" in kwargs:
        tags: Any = kwargs["client_tags"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        connect_kwargs["client_tags"] = list(tags)
    if "session_properties" in kwargs and isinstance(kwargs["session_properties"], dict):
        connect_kwargs["session_properties"] = dict(kwargs["session_properties"])
    backend = ibis.trino.connect(**connect_kwargs)
    backend._marivo_timezone_name = str(connect_kwargs.get("timezone", "UTC"))
    return backend


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
    from marivo.datasource.adapters import SourceSession
    from marivo.datasource.ir import TableSourceIR

    catalog, schema_name, table_name = _partition_table_parts(request)
    if schema_name is None:
        raise RuntimeError("trino partition inspection requires database= or datasource schema")
    identity = f"partition-metadata:{catalog}.{schema_name}.{table_name}"
    with SourceSession(
        PROFILE, request.datasource_ir, request.backend, owns_backend=False
    ) as session:
        relation = session.bind(
            TableSourceIR(f"{table_name}$partitions", database=(catalog, schema_name)),
            source_identity=identity,
        ).relation
        expression = _partition_projection(
            relation, request.partition_columns, request.order, request.limit
        )
        rows = session.collect_bounded(
            expression,
            source_identities=(identity,),
            purpose="datasource.partition_metadata",
            max_rows=request.limit,
        ).to_pylist()
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
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
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
        ),
        "show_columns": ProviderStatement(
            statement_id="trino.show_columns",
            template="SHOW COLUMNS FROM {table_ref}",
            identifier_slots=frozenset({"table_ref"}),
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
        ),
        "show_create": ProviderStatement(
            statement_id="trino.show_create",
            template="SHOW CREATE TABLE {table_ref}",
            identifier_slots=frozenset({"table_ref"}),
        ),
        "show_stats": ProviderStatement(
            statement_id="trino.show_stats",
            template="SHOW STATS FOR {table_ref}",
            identifier_slots=frozenset({"table_ref"}),
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
        ),
    },
)


def _trino_rows(
    backend: Any,
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


def _trino_columns_from_rows(rows: Iterable[Mapping[str, object]]) -> dict[str, ColumnMetadata]:
    from marivo.datasource.metadata import ColumnMetadata as _ColumnMetadata
    from marivo.datasource.metadata import _bool_from_nullable

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
    from marivo.datasource.metadata import PartitionMetadata as _PartitionMetadata

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
    return _PartitionMetadata(
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
    from marivo.datasource.errors import _backend_failure_summary
    from marivo.datasource.metadata import MetadataWarning as _Warning

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
    from marivo.datasource.errors import _backend_failure_summary
    from marivo.datasource.metadata import (
        MetadataWarning as _Warning,
    )
    from marivo.datasource.metadata import (
        TablePhysicalProfile as _Profile,
    )
    from marivo.datasource.metadata import (
        _int_or_none,
    )

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
    from marivo.datasource.errors import _backend_failure_summary
    from marivo.datasource.metadata import (
        MetadataWarning,
        TableMetadata,
        UniqueConstraintMetadata,
        _empty_to_none,
        _merge_columns,
        _schema_columns,
        _schema_only,
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
    predicates = {"catalog": catalog_name, "schema": schema_name, "table": table}

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

    is_view = False
    view_definition: str | None = None
    try:
        type_rows = _trino_rows(backend, "trino.tables.type", values=predicates)
        if type_rows and str(type_rows[0].get("table_type") or "").upper() == "VIEW":
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
    name="trino",
    aliases=("presto",),
    authoring_func="trino",
    required_modules=("ibis.backends.trino",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
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
    quantile=QuantileCapability(mode="approximate", method="approx_percentile"),
    percentile_uses_approx_quantile=True,
    authoring_timeout=authoring_timeout,
)
