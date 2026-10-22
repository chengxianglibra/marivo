"""MySQL engine profile."""

from __future__ import annotations

import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from ibis.backends import BaseBackend

from marivo.datasource._lifetime import BackendLease
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
from marivo.datasource.errors import (
    DatasourceConnectionError,
    DatasourceSourceCapabilityError,
    repair,
)
from marivo.datasource.ir import DatasourceIR, TableSourceIR
from marivo.datasource.strptime import python_to_mysql_strptime

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    from ibis.backends.mysql import Backend

    # Ibis optional backend classes do not ship typing metadata.
    class CheckedBackend(Backend):  # type: ignore[misc]
        def _post_connect(self) -> None:
            # Ibis owns the UTC initialization statement. A warning means it failed.
            with warnings.catch_warnings():
                warnings.filterwarnings("error", message="Unable to set session timezone to UTC.*")
                try:
                    super()._post_connect()
                except Warning as cause:
                    BackendLease(self).close(suppress_errors=False)
                    raise DatasourceConnectionError(
                        message="The MySQL reader could not initialize UTC.",
                        expected="successful Ibis UTC reader initialization",
                        received="timezone initialization warning",
                        repair=repair(
                            kind="reconnect",
                            canonical_id="test",
                            action="Restore MySQL UTC timezone support and retry the datasource connection.",
                        ),
                    ) from cause
            self._marivo_timezone_name = "UTC"

    host = require_field(name, kwargs, "host", help_target="mysql")
    database = require_field(name, kwargs, "database", help_target="mysql")
    connect_kwargs: dict[str, Any] = dict(kwargs)
    connect_kwargs["host"] = host
    connect_kwargs["database"] = database
    return CheckedBackend().connect(**connect_kwargs)


def table_name_parts(request: TableRefRequest) -> tuple[str, ...]:
    database = request.source.database
    schema_name = (
        str(database) if database is not None and not isinstance(database, tuple) else None
    )
    if schema_name is None:
        schema_name = str(request.datasource_ir.fields["database"])
    return (schema_name, request.source.table)


register_provider_statements(
    "mysql",
    {
        "authoring.install_select_deadline": ProviderStatement(
            statement_id="mysql.authoring.install_select_deadline",
            template="SET SESSION max_execution_time = {timeout_ms}",
            literal_slots=frozenset({"timeout_ms"}),
            integer_ranges=(("timeout_ms", 1, 4294967295),),
            allowed_purposes=frozenset({"datasource.native_query_timeout"}),
        ),
        "authoring.read_select_deadline": ProviderStatement(
            statement_id="mysql.authoring.read_select_deadline",
            template="SELECT @@session.max_execution_time",
            allowed_purposes=frozenset({"datasource.native_query_timeout"}),
        ),
        "tables.comment": ProviderStatement(
            statement_id="mysql.tables.comment",
            template=(
                "SELECT TABLE_COMMENT, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH "
                "FROM information_schema.tables "
                "WHERE table_name = {table}"
            ),
            literal_slots=frozenset({"table"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "tables.comment_schema": ProviderStatement(
            statement_id="mysql.tables.comment_schema",
            template=(
                "SELECT TABLE_COMMENT, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH "
                "FROM information_schema.tables "
                "WHERE table_name = {table} AND table_schema = {schema}"
            ),
            literal_slots=frozenset({"table", "schema"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "columns.show": ProviderStatement(
            statement_id="mysql.columns.show",
            template="SHOW FULL COLUMNS FROM {table_ref}",
            identifier_slots=frozenset({"table_ref"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "partitions": ProviderStatement(
            statement_id="mysql.partitions",
            template=(
                "SELECT DISTINCT PARTITION_EXPRESSION FROM information_schema.PARTITIONS "
                "WHERE TABLE_NAME = {table} "
                "AND PARTITION_NAME IS NOT NULL"
            ),
            literal_slots=frozenset({"table"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "partitions_schema": ProviderStatement(
            statement_id="mysql.partitions_schema",
            template=(
                "SELECT DISTINCT PARTITION_EXPRESSION FROM information_schema.PARTITIONS "
                "WHERE TABLE_NAME = {table} "
                "AND PARTITION_NAME IS NOT NULL AND TABLE_SCHEMA = {schema}"
            ),
            literal_slots=frozenset({"table", "schema"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "tables.type": ProviderStatement(
            statement_id="mysql.tables.type",
            template=(
                "SELECT TABLE_TYPE FROM information_schema.tables WHERE table_name = {table}"
            ),
            literal_slots=frozenset({"table"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "tables.type_schema": ProviderStatement(
            statement_id="mysql.tables.type_schema",
            template=(
                "SELECT TABLE_TYPE FROM information_schema.tables "
                "WHERE table_name = {table} AND table_schema = {schema}"
            ),
            literal_slots=frozenset({"table", "schema"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "views.definition": ProviderStatement(
            statement_id="mysql.views.definition",
            template=(
                "SELECT VIEW_DEFINITION FROM information_schema.views WHERE table_name = {table}"
            ),
            literal_slots=frozenset({"table"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "views.definition_schema": ProviderStatement(
            statement_id="mysql.views.definition_schema",
            template=(
                "SELECT VIEW_DEFINITION FROM information_schema.views "
                "WHERE table_name = {table} AND table_schema = {schema}"
            ),
            literal_slots=frozenset({"table", "schema"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "indexes.primary": ProviderStatement(
            statement_id="mysql.indexes.primary",
            template="SHOW INDEX FROM {table_ref} WHERE Key_name = 'PRIMARY'",
            identifier_slots=frozenset({"table_ref"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "original.column": ProviderStatement(
            statement_id="mysql.original.column",
            template=(
                "SELECT COLLATION_NAME FROM information_schema.columns "
                "WHERE TABLE_SCHEMA = {schema} AND TABLE_NAME = {table} AND COLUMN_NAME = {column}"
            ),
            literal_slots=frozenset({"schema", "table", "column"}),
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "query.packet_limit": ProviderStatement(
            statement_id="mysql.query.packet_limit",
            template="SELECT @@session.max_allowed_packet AS packet_limit",
            allowed_purposes=frozenset({"datasource.metadata.mysql"}),
        ),
        "timezone.verify": ProviderStatement(
            statement_id="mysql.timezone.verify",
            template=(
                "SELECT CONVERT_TZ('2000-01-01 00:00:00', '+00:00', {timezone}) AS converted"
            ),
            literal_slots=frozenset({"timezone"}),
            allowed_purposes=frozenset({"datasource.timezone"}),
        ),
    },
)


def verify_source_timezone(backend: BaseBackend, name: str) -> None:
    """Require MySQL's native projection to recognize one selected timezone."""
    try:
        rows = execute_provider_statement(
            backend,
            PROFILE,
            "mysql.timezone.verify",
            values={"timezone": name},
            purpose="datasource.timezone",
        )
    except Exception as cause:
        raise _source_timezone_unavailable(name) from cause
    converted = rows[0].get("converted") if len(rows) == 1 else None
    if not isinstance(converted, datetime):
        raise _source_timezone_unavailable(name)


def _source_timezone_unavailable(name: str) -> DatasourceConnectionError:
    return DatasourceConnectionError(
        message="The MySQL reader cannot convert the selected source timezone.",
        expected="a non-NULL native CONVERT_TZ result for the selected timezone",
        received=name,
        location="datasource.timezone",
        repair=repair(
            kind="configure",
            canonical_id="mysql",
            action=(
                "Load MySQL time zone tables for the selected timezone and reconnect "
                "the datasource before time-sensitive analysis."
            ),
        ),
    )


def _mysql_rows(
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
        purpose="datasource.metadata.mysql",
    )


def original_string_column(
    backend: BaseBackend, datasource: DatasourceIR, source: TableSourceIR, column: str
) -> bool:
    """Require the qualified binary NO PAD collation for original grid keys."""
    schema = source.database or datasource.fields.get("database")
    if not isinstance(schema, str):
        return False
    rows = _mysql_rows(
        backend,
        "mysql.original.column",
        values={
            "schema": schema,
            "table": source.table,
            "column": column,
        },
    )
    return len(rows) == 1 and rows[0].get("COLLATION_NAME") == "utf8mb4_0900_bin"


def query_packet_limit(backend: BaseBackend) -> tuple[int, str]:
    """Read this connection's native packet limit and SQL encoding."""
    rows = _mysql_rows(backend, "mysql.query.packet_limit")
    limit = rows[0].get("packet_limit") if len(rows) == 1 else None
    encoding = getattr(getattr(backend, "con", None), "encoding", None)
    if not isinstance(limit, int) or limit < 1 or not isinstance(encoding, str):
        raise DatasourceSourceCapabilityError(
            message="The MySQL key request capacity is unavailable.",
            expected="the active connection's positive max_allowed_packet and SQL encoding",
            received="missing packet limit or connection encoding",
            repair=repair(
                kind="reconnect",
                canonical_id="mysql",
                action="Restore MySQL session metadata access and reconnect the datasource.",
            ),
        )
    return limit, encoding


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
    from marivo.datasource.errors import _backend_failure_summary
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
        _schema_columns,
    )

    schema_columns = _schema_columns(table_expr)
    schema_name = _database_label(database) or default_database
    table_comment: str | None = None
    physical_profile: TablePhysicalProfile | None = None
    warnings: list[MetadataWarning] = []

    try:
        if schema_name is not None:
            table_rows = _mysql_rows(
                backend,
                "mysql.tables.comment_schema",
                values={"table": table, "schema": schema_name},
            )
        else:
            table_rows = _mysql_rows(backend, "mysql.tables.comment", values={"table": table})
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
                message=(
                    f"mysql table comment query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    catalog_columns: dict[str, ColumnMetadata] = {}
    parts = (
        (*database, table)
        if isinstance(database, tuple)
        else (database, table)
        if database is not None
        else (table,)
    )
    try:
        column_rows = _mysql_rows(backend, "mysql.columns.show", identifiers={"table_ref": parts})
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
                message=(
                    f"mysql column metadata query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    partitions_by_name: dict[str, PartitionMetadata] = {}
    partition_state: Literal["known", "none", "unknown"] = "unknown"
    if include_partitions:
        try:
            if schema_name is not None:
                partition_rows = _mysql_rows(
                    backend,
                    "mysql.partitions_schema",
                    values={"table": table, "schema": schema_name},
                )
            else:
                partition_rows = _mysql_rows(backend, "mysql.partitions", values={"table": table})
            saw_partition_expression = False
            for row in partition_rows:
                if row.get("PARTITION_EXPRESSION") not in (None, ""):
                    saw_partition_expression = True
                column_name = _partition_column_from_expression(row.get("PARTITION_EXPRESSION"))
                column = catalog_columns.get(column_name or "")
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
                        "mysql partition metadata query failed: "
                        f"{_backend_failure_summary(exc).message}"
                    ),
                )
            )
        if partition_state == "unknown":
            warnings.append(
                MetadataWarning(
                    kind="partitions_unavailable",
                    message="mysql partition metadata did not expose mappable column partitions",
                )
            )

    is_view: bool | None = None
    view_definition: str | None = None
    try:
        if schema_name is not None:
            type_rows = _mysql_rows(
                backend, "mysql.tables.type_schema", values={"table": table, "schema": schema_name}
            )
        else:
            type_rows = _mysql_rows(backend, "mysql.tables.type", values={"table": table})
        if type_rows:
            is_view = str(type_rows[0].get("TABLE_TYPE") or "").upper() == "VIEW"
        if is_view:
            is_view = True
            if schema_name is not None:
                def_rows = _mysql_rows(
                    backend,
                    "mysql.views.definition_schema",
                    values={"table": table, "schema": schema_name},
                )
            else:
                def_rows = _mysql_rows(backend, "mysql.views.definition", values={"table": table})
            if def_rows:
                view_definition = _empty_to_none(def_rows[0].get("VIEW_DEFINITION"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    f"mysql view metadata query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    primary_keys: tuple[str, ...] = ()
    try:
        index_rows = _mysql_rows(backend, "mysql.indexes.primary", identifiers={"table_ref": parts})
        pk_names = [
            str(row.get("Column_name"))
            for row in index_rows
            if isinstance(row.get("Column_name"), str)
        ]
        primary_keys = tuple(pk_names)
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    f"mysql primary key query failed: {_backend_failure_summary(exc).message}"
                ),
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
        partition_state=partition_state,
        warnings=tuple(warnings),
        is_view=is_view,
        view_definition=view_definition,
        primary_keys=primary_keys,
        unique_constraints=(),
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


def classify_table_resolution_failure(exc: Exception) -> Literal["metadata_unavailable"] | None:
    """Classify MySQL table-metadata permission denial from native errno."""
    for candidate in structured_exception_chain(exc):
        args = getattr(candidate, "args", ())
        if isinstance(args, tuple) and args and args[0] in {1142, 1143}:
            return "metadata_unavailable"
    return None


@contextmanager
def native_query_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    """Install the native read-only SELECT limit on an isolated reader."""
    milliseconds = timeout_seconds * 1000
    if (
        type(timeout_seconds) is not int
        or not 1 <= milliseconds <= 4294967295
        or getattr(backend, "_marivo_terminal_timeout_seconds", None) != timeout_seconds
    ):
        raise DatasourceSourceCapabilityError(
            message="MySQL native timeout configuration requires an isolated reader.",
            expected="isolated reader and timeout in 1..4294967 seconds",
            received=str(timeout_seconds),
            location="datasource.native_query_timeout",
            repair=repair(
                kind="configure",
                canonical_id="test",
                action="Retry certification with a supported positive scope timeout.",
            ),
        )
    if (
        getattr(backend, "_marivo_configured_query_timeout_seconds", None) == timeout_seconds
        or getattr(backend, "_marivo_native_timeout_unsupported", False) is True
    ):
        yield
        return
    purpose = "datasource.native_query_timeout"
    try:
        execute_provider_statement(
            backend,
            PROFILE,
            "mysql.authoring.install_select_deadline",
            values={"timeout_ms": milliseconds},
            purpose=purpose,
        )
        rows = execute_provider_statement(
            backend,
            PROFILE,
            "mysql.authoring.read_select_deadline",
            purpose=purpose,
        )
    except Exception as cause:
        if any(
            getattr(candidate, "args", ())[:1] == (1193,)
            for candidate in structured_exception_chain(cause)
        ):
            backend._marivo_native_timeout_unsupported = True
            yield
            return
        raise DatasourceSourceCapabilityError(
            message="MySQL native timeout configuration failed before the source read.",
            expected="successful installation and verification of the native SELECT timeout",
            received=type(cause).__name__,
            location="datasource.native_query_timeout",
            repair=repair(
                kind="configure",
                canonical_id="test",
                action="Restore session-variable access for this reader and retry the read.",
            ),
        ) from cause
    if rows != ({"@@session.max_execution_time": milliseconds},):
        raise DatasourceSourceCapabilityError(
            message="MySQL did not confirm the requested native SELECT timeout.",
            expected=str(milliseconds),
            received=str(rows),
            location="datasource.native_query_timeout",
            repair=repair(
                kind="configure",
                canonical_id="test",
                action="Restore MySQL session timeout support and retry the read.",
            ),
        )
    backend._marivo_configured_query_timeout_seconds = timeout_seconds
    yield


PROFILE = EngineProfile(
    name="mysql",
    aliases=(),
    authoring_func="mysql",
    required_modules=("ibis.backends.mysql",),
    connect=connect,
    apply_read_only_kwargs=identity_read_only_kwargs,
    identifier_quote="`",
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
    translate_strptime_format=python_to_mysql_strptime,
    datetime_decode_policy="local_naive_label",
    exact_count_distinct=True,
    quantile=None,
    percentile_uses_approx_quantile=False,
    native_query_timeout=native_query_timeout,
)
