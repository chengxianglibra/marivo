"""SQLite engine profile."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Timer
from typing import TYPE_CHECKING

import ibis.expr.datatypes as dt
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
    default_table_name_parts,
)

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata

register_provider_statements(
    "sqlite",
    {
        "schema.kind": ProviderStatement(
            statement_id="sqlite.schema.kind",
            template=(
                "SELECT type, sql FROM {schema}.sqlite_schema "
                "WHERE name = {table} AND type IN ('table', 'view') LIMIT 1"
            ),
            literal_slots=frozenset({"table"}),
            identifier_slots=frozenset({"schema"}),
            allowed_purposes=frozenset({"datasource.metadata.sqlite"}),
        ),
        "pragma.table_info": ProviderStatement(
            statement_id="sqlite.pragma.table_info",
            template=(
                "SELECT cid, name, type, [notnull] AS is_not_null, pk "
                "FROM {schema}.pragma_table_info({table}) "
                "ORDER BY cid"
            ),
            literal_slots=frozenset({"table"}),
            identifier_slots=frozenset({"schema"}),
            allowed_purposes=frozenset({"datasource.metadata.sqlite"}),
        ),
        "pragma.index_list": ProviderStatement(
            statement_id="sqlite.pragma.index_list",
            template=(
                "SELECT name, [unique] AS is_unique, origin, partial "
                "FROM {schema}.pragma_index_list({table}) "
                "ORDER BY seq"
            ),
            literal_slots=frozenset({"table"}),
            identifier_slots=frozenset({"schema"}),
            allowed_purposes=frozenset({"datasource.metadata.sqlite"}),
        ),
        "pragma.index_info": ProviderStatement(
            statement_id="sqlite.pragma.index_info",
            template=("SELECT name FROM {schema}.pragma_index_info({index}) ORDER BY seqno"),
            literal_slots=frozenset({"index"}),
            identifier_slots=frozenset({"schema"}),
            allowed_purposes=frozenset({"datasource.metadata.sqlite"}),
        ),
    },
)


def declared_scalar_type(declaration: str) -> dt.DataType | None:
    """Map the bounded SQLite storage declarations used by typed analysis."""
    kind = declaration.strip().upper()
    if kind in {"INTEGER", "INT", "BIGINT", "TINYINT", "SMALLINT", "MEDIUMINT", "INT2", "INT8"}:
        return dt.int64
    if kind in {"REAL", "DOUBLE", "DOUBLE PRECISION", "FLOAT"}:
        return dt.float64
    if re.fullmatch(r"(?:TEXT|CLOB|(?:VAR)?CHAR(?:\([1-9][0-9]*\))?)", kind):
        return dt.string
    if kind in {"BOOL", "BOOLEAN"}:
        return dt.boolean
    if kind in {"DATETIME", "TIMESTAMP"}:
        return dt.Timestamp(scale=6)
    return dt.date if kind == "DATE" else None


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis.expr.schema as sch
    import pandas as pd
    from ibis.backends.sqlite import Backend
    from ibis.backends.sqlite.converter import SQLitePandasData

    from marivo.datasource.engines.scalar_decode import ScalarCursor, checked_dataframe

    # Ibis optional backend classes do not ship typing metadata.
    class CheckedBackend(Backend):  # type: ignore[misc]
        def _fetch_from_cursor(self, cursor: ScalarCursor, schema: sch.Schema) -> pd.DataFrame:
            return checked_dataframe(cursor, schema, SQLitePandasData.convert_table)

    connect_kwargs = dict(kwargs)
    path = connect_kwargs.pop("path", ":memory:")
    read_only = bool(connect_kwargs.pop("read_only", False))
    connect_kwargs["database"] = path
    backend = CheckedBackend().connect(**connect_kwargs)
    if read_only:
        backend.con.set_authorizer(_read_only_authorizer)
    return backend


_READ_ONLY_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
    }
)
_READ_ONLY_PRAGMAS = frozenset(
    {"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list", "database_list"}
)


def _read_only_authorizer(
    action: int,
    name: str | None,
    value: str | None,
    _database: str | None,
    _origin: str | None,
) -> int:
    if action in _READ_ONLY_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and name in _READ_ONLY_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextmanager
def owned_temporary_writes(backend: BaseBackend, names: frozenset[str]) -> Iterator[None]:
    """Permit only issued staging tables while keeping persistent databases read-only."""
    connection = getattr(backend, "con", None)
    if (
        not isinstance(connection, sqlite3.Connection)
        or not names
        or any(not re.fullmatch(r"mv_graph_[0-9a-f]{32}(?:_input)?", name) for name in names)
    ):
        raise ValueError("Expected an owned SQLite staging connection and exact table names")

    def authorize(
        action: int, name: str | None, value: str | None, database: str | None, origin: str | None
    ) -> int:
        if (
            database == "temp"
            and name in names | {"sqlite_temp_master", "sqlite_master"}
            and action
            in {
                sqlite3.SQLITE_CREATE_TEMP_TABLE,
                sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_DROP_TABLE,
                sqlite3.SQLITE_DROP_TEMP_TABLE,
                sqlite3.SQLITE_INSERT,
                sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE,
            }
        ):
            return sqlite3.SQLITE_OK
        return _read_only_authorizer(action, name, value, database, origin)

    connection.set_authorizer(authorize)
    try:
        yield
    finally:
        connection.set_authorizer(_read_only_authorizer)


def apply_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    out = dict(kwargs)
    out["read_only"] = True
    return out


def _sqlite_rows(
    backend: object,
    statement_id: str,
    *,
    values: Mapping[str, object] = {},
    identifiers: Mapping[str, str | tuple[str, ...]] = {},
) -> tuple[dict[str, object], ...]:
    from marivo.datasource.adapters import provider_for

    return execute_provider_statement(
        backend,
        provider_for("sqlite"),
        statement_id,
        values=values,
        identifiers=identifiers,
        purpose="datasource.metadata.sqlite",
    )


def _primary_key_forces_not_null(
    *,
    declared_type: str,
    primary_position: int,
    primary_key_size: int,
    primary_key_index_present: bool,
    table_definition: str | None,
) -> bool:
    if primary_position < 1:
        return False
    definition = (table_definition or "").upper().rstrip()
    table_options = re.compile(r"(?:^|[\s,])(?:STRICT|WITHOUT\s+ROWID)(?:\s*,|\s*$)")
    if table_options.search(definition):
        return True
    return (
        primary_key_size == 1
        and declared_type.strip().upper() == "INTEGER"
        and not primary_key_index_present
    )


def _inspect_sqlite(request: MetadataInspectRequest) -> TableMetadata:
    from marivo.datasource.metadata import (
        ColumnMetadata,
        MetadataWarning,
        TableMetadata,
        UniqueConstraintMetadata,
        _int_or_none,
        _merge_columns,
        _schema_columns,
    )

    database = request.database
    if isinstance(database, tuple):
        raise ValueError("SQLite table database must be a single namespace name")
    namespace = database or "main"

    table_rows = _sqlite_rows(
        request.backend,
        "sqlite.schema.kind",
        values={"table": request.table},
        identifiers={"schema": namespace},
    )
    table_kind = str(table_rows[0].get("type")) if table_rows else "table"
    definition = str(table_rows[0].get("sql")) if table_rows and table_rows[0].get("sql") else None

    column_rows = _sqlite_rows(
        request.backend,
        "sqlite.pragma.table_info",
        values={"table": request.table},
        identifiers={"schema": namespace},
    )
    index_rows = _sqlite_rows(
        request.backend,
        "sqlite.pragma.index_list",
        values={"table": request.table},
        identifiers={"schema": namespace},
    )
    primary_key_size = sum(1 for row in column_rows if (_int_or_none(row.get("pk")) or 0) > 0)
    primary_key_index_present = any(row.get("origin") == "pk" for row in index_rows)
    catalog_columns: dict[str, ColumnMetadata] = {}
    primary_key_rows: list[tuple[int, str]] = []
    for row in column_rows:
        column_name = str(row.get("name"))
        primary_position = _int_or_none(row.get("pk")) or 0
        declared_type = str(row.get("type") or "")
        not_null = bool(row.get("is_not_null")) or _primary_key_forces_not_null(
            declared_type=declared_type,
            primary_position=primary_position,
            primary_key_size=primary_key_size,
            primary_key_index_present=primary_key_index_present,
            table_definition=definition,
        )
        catalog_columns[column_name] = ColumnMetadata(
            name=column_name,
            type=declared_type,
            nullable=not not_null,
            comment=None,
            ordinal_position=(_int_or_none(row.get("cid")) or 0) + 1,
        )
        if primary_position > 0:
            primary_key_rows.append((primary_position, column_name))

    unique_constraints: list[UniqueConstraintMetadata] = []
    for row in index_rows:
        if not bool(row.get("is_unique")) or row.get("origin") == "pk" or row.get("partial"):
            continue
        index_name = str(row.get("name"))
        index_columns = _sqlite_rows(
            request.backend,
            "sqlite.pragma.index_info",
            values={"index": index_name},
            identifiers={"schema": namespace},
        )
        if any(not item.get("name") for item in index_columns):
            continue
        columns = tuple(str(item["name"]) for item in index_columns)
        if columns:
            unique_constraints.append(
                UniqueConstraintMetadata(name=index_name, columns=columns, kind="unique")
            )

    return TableMetadata(
        datasource=request.datasource,
        table=request.table,
        database=request.database,
        backend_type="sqlite",
        comment=None,
        columns=_merge_columns(_schema_columns(request.table_expr), catalog_columns),
        partitions=(),
        partition_state="none",
        warnings=(
            MetadataWarning(
                kind="comments_unavailable",
                message="sqlite does not expose table or column comments",
            ),
        ),
        is_view=table_kind == "view",
        view_definition=definition if table_kind == "view" else None,
        primary_keys=tuple(name for _, name in sorted(primary_key_rows)),
        unique_constraints=tuple(unique_constraints),
        physical_profile=None,
    )


def inspect_table(request: MetadataInspectRequest) -> TableMetadata:
    return _inspect_sqlite(request)


def identity_strptime(value: str) -> str:
    """SQLite parses text with a connection-local ``datetime.strptime`` scalar.

    That scalar consumes the authored Python format itself, so translation is
    the identity. It replaces an earlier rejection hook that predated the
    scalar: the format still never reaches a server parser, because SQLite has
    no native strptime expression to receive it.
    """
    return value


@contextmanager
def authoring_timeout(backend: BaseBackend, timeout_seconds: int) -> Iterator[None]:
    connection = getattr(backend, "con", None)
    interrupt = getattr(connection, "interrupt", None)
    if not callable(interrupt):
        raise RuntimeError("sqlite backend does not expose connection.interrupt()")
    timer = Timer(timeout_seconds, interrupt)
    try:
        timer.start()
        yield
    finally:
        timer.cancel()


PROFILE = EngineProfile(
    name="sqlite",
    aliases=("sqlite3",),
    authoring_func="sqlite",
    required_modules=("ibis.backends.sqlite",),
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
    translate_strptime_format=identity_strptime,
    datetime_decode_policy="local_naive_label",
    exact_count_distinct=True,
    quantile=None,
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
)
