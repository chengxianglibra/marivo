"""MySQL engine profile."""

from __future__ import annotations

import socket
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from threading import Event, Timer
from typing import TYPE_CHECKING, Any, Literal

from ibis.backends import BaseBackend

from marivo.datasource.capabilities import (
    ProviderStatement,
    execute_provider_statement,
    provider_statement_log,
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
from marivo.datasource.strptime import python_to_mysql_strptime

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis.expr.schema as sch
    import pandas as pd
    from ibis.backends.mysql import Backend
    from ibis.backends.mysql.converter import MySQLPandasData

    from marivo.datasource.engines.scalar_decode import ScalarCursor, checked_dataframe

    # Ibis optional backend classes do not ship typing metadata.
    class CheckedBackend(Backend):  # type: ignore[misc]
        def _post_connect(self) -> None:
            # Ibis owns the UTC initialization statement. A warning means it failed.
            with warnings.catch_warnings():
                warnings.filterwarnings("error", message="Unable to set session timezone to UTC.*")
                try:
                    super()._post_connect()
                except Warning as cause:
                    self.con.close()
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

        def disconnect(self) -> None:
            control = getattr(self, "_marivo_authoring_cancel_control", None)
            try:
                if control is not None:
                    self._marivo_authoring_cancel_submissions = provider_statement_log(control)
                    control.disconnect()
            finally:
                self._marivo_authoring_cancel_control = None
                super().disconnect()

        def _fetch_from_cursor(self, cursor: ScalarCursor, schema: sch.Schema) -> pd.DataFrame:
            return checked_dataframe(cursor, schema, MySQLPandasData.convert_table)

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
        "analysis.cancel_owned_query": ProviderStatement(
            statement_id="mysql.analysis.cancel_owned_query",
            template="KILL QUERY {thread_id}",
            literal_slots=frozenset({"thread_id"}),
            integer_ranges=(("thread_id", 1, 18446744073709551615),),
            allowed_purposes=frozenset(
                {"analysis.cancel_owned_query", "datasource.authoring.deadline"}
            ),
        ),
        "authoring.install_select_deadline": ProviderStatement(
            statement_id="mysql.authoring.install_select_deadline",
            template="SET SESSION max_execution_time = {timeout_ms}",
            literal_slots=frozenset({"timeout_ms"}),
            integer_ranges=(("timeout_ms", 1, 4294967295),),
            allowed_purposes=frozenset({"semantic.certified_preview.deadline"}),
        ),
        "authoring.read_select_deadline": ProviderStatement(
            statement_id="mysql.authoring.read_select_deadline",
            template="SELECT @@session.max_execution_time",
            allowed_purposes=frozenset({"semantic.certified_preview.deadline"}),
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
    },
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
def certification_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    """Install a verified SELECT limit only on an isolated certification connection."""
    milliseconds = timeout_seconds * 1000
    if (
        type(timeout_seconds) is not int
        or not 1 <= milliseconds <= 4294967295
        or getattr(backend, "_marivo_certified_authoring", False) is not True
    ):
        raise DatasourceSourceCapabilityError(
            message="MySQL certification requires an isolated bounded authoring connection.",
            expected="fresh certification connection and timeout in 1..4294967 seconds",
            received=str(timeout_seconds),
            location="semantic.certified_preview",
            repair=repair(
                kind="configure",
                canonical_id="test",
                action="Retry certification with a supported positive scope timeout.",
            ),
        )
    purpose = "semantic.certified_preview.deadline"
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
        raise DatasourceSourceCapabilityError(
            message="MySQL certification deadline preparation failed before the source read.",
            expected="successful installation and verification of the scoped SELECT deadline",
            received=type(cause).__name__,
            location="semantic.certified_preview",
            repair=repair(
                kind="configure",
                canonical_id="test",
                action="Restore session-variable access for this reader and retry certification.",
            ),
        ) from cause
    if rows != ({"@@session.max_execution_time": milliseconds},):
        raise DatasourceSourceCapabilityError(
            message="MySQL did not confirm the requested certification deadline.",
            expected=str(milliseconds),
            received=str(rows),
            location="semantic.certified_preview",
            repair=repair(
                kind="configure",
                canonical_id="test",
                action="Restore MySQL session timeout support and retry certification.",
            ),
        )
    try:
        yield
    finally:
        backend._marivo_certified_authoring = False


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    """Cancel only this isolated authoring reader's current query at its deadline."""
    connection = getattr(backend, "con", None)
    control = getattr(backend, "_marivo_authoring_cancel_control", None)
    identity = getattr(backend, "_marivo_authoring_thread_id", None)
    if (
        type(timeout_seconds) is not int
        or timeout_seconds <= 0
        or getattr(backend, "_marivo_terminal_timeout_seconds", None) != timeout_seconds
        or control is None
        or type(identity) is not int
        or identity <= 0
        or connection is None
        or connection.thread_id() != identity
    ):
        raise RuntimeError(
            "MySQL authoring timeout requires its isolated owned reader and control connection"
        )
    expired = Event()
    # Prepare the data socket while its driver is idle. The deadline thread
    # must not call connection metadata methods during a native query.
    owned_socket = socket.fromfd(connection.fileno(), socket.AF_INET, socket.SOCK_STREAM)

    def cancel() -> None:
        expired.set()
        try:
            if getattr(backend, "con", None) is not connection:
                return
            execute_provider_statement(
                control,
                PROFILE,
                "mysql.analysis.cancel_owned_query",
                values={"thread_id": identity},
                purpose="datasource.authoring.deadline",
            )
        except Exception:
            # The channel retains failure; shutdown alone does not prove server termination.
            pass
        finally:
            # Wake an owner blocked on fetch even when server cancellation fails.
            with suppress(OSError, ValueError):
                owned_socket.shutdown(socket.SHUT_RDWR)

    with owned_socket:
        timer = Timer(timeout_seconds, cancel)
        timer.daemon = True
        try:
            timer.start()
            yield
            if expired.is_set():
                raise TimeoutError("MySQL authoring deadline expired")
        finally:
            timer.cancel()
            timer.join()


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
    authoring_timeout=authoring_timeout,
    certification_timeout=certification_timeout,
)
