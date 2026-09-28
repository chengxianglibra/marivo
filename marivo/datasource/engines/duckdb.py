"""DuckDB engine profile."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Timer
from typing import TYPE_CHECKING, Any

from ibis.backends import BaseBackend

from marivo.datasource.capabilities import (
    ProviderStatement,
    execute_provider_statement,
    register_provider_statements,
    url_is_in_http_scope,
)
from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    QuantileCapability,
    default_table_name_parts,
    identity_str,
)
from marivo.datasource.errors import DatasourceFieldInvalidError, repair

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata

register_provider_statements(
    "duckdb",
    {
        "http_secret_bearer": ProviderStatement(
            statement_id="duckdb.http_secret_bearer",
            template=(
                "CREATE OR REPLACE SECRET marivo_http_auth (TYPE HTTP, BEARER_TOKEN ?, SCOPE ?)"
            ),
            parameterized=True,
        ),
        "http_secret_headers": ProviderStatement(
            statement_id="duckdb.http_secret_headers",
            template=(
                "CREATE OR REPLACE SECRET marivo_http_auth "
                "(TYPE HTTP, EXTRA_HTTP_HEADERS ?, SCOPE ?)"
            ),
            parameterized=True,
        ),
        "tables.comment_size": ProviderStatement(
            statement_id="duckdb.tables.comment_size",
            template=(
                "SELECT comment, estimated_size FROM duckdb_tables() "
                "WHERE database_name = {database} AND schema_name = {schema} AND table_name = {table} LIMIT 1"
            ),
            literal_slots=frozenset({"database", "schema", "table"}),
        ),
        "tables.comment": ProviderStatement(
            statement_id="duckdb.tables.comment",
            template=(
                "SELECT comment FROM duckdb_tables() WHERE database_name = {database} AND schema_name = {schema} AND table_name = {table} LIMIT 1"
            ),
            literal_slots=frozenset({"database", "schema", "table"}),
        ),
        "tables.columns": ProviderStatement(
            statement_id="duckdb.tables.columns",
            template=(
                "SELECT column_name, data_type, is_nullable, comment "
                "FROM duckdb_columns() "
                "WHERE database_name = {database} AND schema_name = {schema} AND table_name = {table} "
                "ORDER BY column_index"
            ),
            literal_slots=frozenset({"database", "schema", "table"}),
        ),
        "namespace.current": ProviderStatement(
            statement_id="duckdb.namespace.current",
            template="SELECT current_database() AS database_name, current_schema() AS schema_name",
        ),
        "views.schema_qualified": ProviderStatement(
            statement_id="duckdb.views.schema_qualified",
            template=(
                "SELECT sql FROM duckdb_views() "
                "WHERE view_name = {table} AND internal = false AND schema_name = {schema} "
                "LIMIT 1"
            ),
            literal_slots=frozenset({"table", "schema"}),
        ),
        "views.database_qualified": ProviderStatement(
            statement_id="duckdb.views.database_qualified",
            template=(
                "SELECT sql FROM duckdb_views() "
                "WHERE view_name = {table} AND internal = false "
                "AND database_name = {database} AND schema_name = {schema} "
                "LIMIT 1"
            ),
            literal_slots=frozenset({"table", "database", "schema"}),
        ),
        "constraints": ProviderStatement(
            statement_id="duckdb.constraints",
            template=(
                "SELECT constraint_type, constraint_column_names "
                "FROM duckdb_constraints() "
                "WHERE database_name = {database} AND schema_name = {schema} AND table_name = {table}"
            ),
            literal_slots=frozenset({"database", "schema", "table"}),
        ),
    },
)


@dataclass(frozen=True)
class DuckDbHttpCredentials:
    """Scoped HTTP credentials installed on one DuckDB connection."""

    scope: str
    headers: tuple[tuple[str, str], ...]

    def headers_for(self, url: str) -> dict[str, str]:
        if not url_is_in_http_scope(url, self.scope):
            return {}
        return dict(self.headers)


def http_credentials(
    backend: BaseBackend,
    *,
    scope: object,
    bearer_token: object,
    headers: object,
) -> DuckDbHttpCredentials | None:
    """Install the declared scoped HTTP secret and return in-memory headers.

    The secret values are passed as statement parameters, so they never appear
    in rendered SQL text or the capability submission log.
    """
    if bearer_token is None and not headers:
        return None
    raw_sql = getattr(backend, "raw_sql", None)
    if not callable(raw_sql):
        raise DatasourceFieldInvalidError(
            message="DuckDB HTTP auth requires a backend with raw_sql support",
            expected="a DuckDB backend",
            received=type(backend).__name__,
            location="DuckDB HTTP auth",
            repair=repair(
                kind="reconnect",
                canonical_id="test",
                action="Reconnect using the declared DuckDB datasource.",
            ),
        )
    if not isinstance(scope, str):
        raise DatasourceFieldInvalidError(
            message="DuckDB HTTP auth scope was not resolved",
            expected="an HTTP(S) scope string",
            received=repr(scope),
            location="DuckDB HTTP auth",
            repair=repair(
                kind="reauthor",
                canonical_id="duckdb",
                action="Declare an explicit HTTP(S) scope on the DuckDB datasource.",
            ),
        )
    if isinstance(bearer_token, str):
        execute_provider_statement(
            backend,
            PROFILE,
            "duckdb.http_secret_bearer",
            purpose="datasource.http_credentials",
            parameters=[bearer_token, scope],
        )
        return DuckDbHttpCredentials(
            scope=scope,
            headers=(("Authorization", f"Bearer {bearer_token}"),),
        )
    if (
        isinstance(headers, Mapping)
        and headers
        and all(isinstance(name, str) and isinstance(value, str) for name, value in headers.items())
    ):
        execute_provider_statement(
            backend,
            PROFILE,
            "duckdb.http_secret_headers",
            purpose="datasource.http_credentials",
            parameters=[dict(headers), scope],
        )
        return DuckDbHttpCredentials(scope=scope, headers=tuple(headers.items()))
    raise DatasourceFieldInvalidError(
        message="DuckDB custom HTTP authentication was not fully resolved",
        expected="environment-sourced custom HTTP headers",
        received="incomplete HTTP authentication fields",
        location="DuckDB HTTP auth",
        repair=repair(
            kind="reauthor",
            canonical_id="duckdb",
            action="Declare one complete environment-backed HTTP auth mode.",
        ),
    )


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    path = kwargs.get("path", ":memory:")
    connect_kwargs: dict[str, object] = dict(kwargs)
    connect_kwargs.pop("path", None)
    connect_kwargs["database"] = path
    connect_kwargs["threads"] = 1
    connect_kwargs["TimeZone"] = "UTC"
    if "read_only" in connect_kwargs:
        connect_kwargs["read_only"] = bool(connect_kwargs["read_only"])
    backend = ibis.duckdb.connect(**connect_kwargs)
    backend._marivo_timezone_name = "UTC"
    return backend


def apply_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    out = dict(kwargs)
    out["read_only"] = True
    return out


def _duckdb_rows(
    backend: Any, statement_id: str, values: Mapping[str, object] = {}
) -> tuple[dict[str, object], ...]:
    return execute_provider_statement(
        backend, PROFILE, statement_id, values=values, purpose="datasource.metadata.duckdb"
    )


def _inspect_duckdb(
    *,
    datasource: str,
    backend: Any,
    table: str,
    database: str | tuple[str, ...] | None,
    table_expr: Any,
    include_partitions: bool,
) -> TableMetadata:
    from marivo.datasource.errors import _backend_failure_summary
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
        _schema_columns,
        _schema_only,
    )

    schema_columns = _schema_columns(table_expr)
    warnings: list[MetadataWarning] = []
    table_comment: str | None = None
    catalog_columns: dict[str, ColumnMetadata] = {}
    is_view: bool | None = None
    view_definition: str | None = None
    physical_profile: TablePhysicalProfile | None = None

    namespace = table_expr.op().namespace
    try:
        current = _duckdb_rows(backend, "duckdb.namespace.current")[0]
        facts = {
            "database": namespace.catalog or current["database_name"],
            "schema": namespace.database or current["schema_name"],
            "table": table,
        }
    except Exception as exc:
        return _schema_only(
            datasource=datasource,
            table=table,
            database=database,
            backend_type="duckdb",
            table_expr=table_expr,
            warnings=(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=f"duckdb namespace metadata unavailable: {_backend_failure_summary(exc).message}",
                ),
            ),
        )

    try:
        table_rows = _duckdb_rows(backend, "duckdb.tables.comment_size", facts)
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
            table_rows = _duckdb_rows(backend, "duckdb.tables.comment", facts)
            if table_rows:
                table_comment = _empty_to_none(table_rows[0].get("comment"))
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=(
                        "duckdb physical profile query failed: "
                        f"{_backend_failure_summary(exc).message}"
                    ),
                )
            )
        except Exception as exc2:
            warnings.append(
                MetadataWarning(
                    kind="metadata_query_failed",
                    message=(
                        "duckdb table metadata query failed: "
                        f"{_backend_failure_summary(exc2).message}"
                    ),
                )
            )

    try:
        column_rows = _duckdb_rows(backend, "duckdb.tables.columns", facts)
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
                message=(
                    f"duckdb column metadata query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    try:
        view_rows = _duckdb_rows(backend, "duckdb.views.database_qualified", facts)
        is_view = bool(view_rows)
        if view_rows:
            is_view = True
            view_definition = _empty_to_none(view_rows[0].get("sql"))
    except Exception as exc:
        warnings.append(
            MetadataWarning(
                kind="metadata_query_failed",
                message=(
                    f"duckdb view metadata query failed: {_backend_failure_summary(exc).message}"
                ),
            )
        )

    primary_keys: tuple[str, ...] = ()
    unique_constraints: tuple[UniqueConstraintMetadata, ...] = ()
    try:
        constraint_rows = _duckdb_rows(backend, "duckdb.constraints", facts)
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
                message=(
                    f"duckdb constraint query failed: {_backend_failure_summary(exc).message}"
                ),
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
        partition_state="unknown",
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


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    connection = getattr(backend, "con", None)
    interrupt = getattr(connection, "interrupt", None)
    if not callable(interrupt):
        raise RuntimeError("duckdb backend does not expose connection.interrupt()")
    timer = Timer(timeout_seconds, interrupt)
    try:
        timer.start()
        yield
    finally:
        timer.cancel()


PROFILE = EngineProfile(
    name="duckdb",
    aliases=(),
    authoring_func="duckdb",
    required_modules=("ibis.backends.duckdb",),
    connect=connect,
    apply_read_only_kwargs=apply_read_only_kwargs,
    identifier_quote='"',
    table_name_parts=default_table_name_parts,
    inspect_partition_values=None,
    metadata=EngineMetadataIntrospection(inspect_table=inspect_table),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=False,
    ),
    translate_strptime_format=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=QuantileCapability(mode="exact", method="linear_interpolation"),
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
    http_credentials=http_credentials,
)
