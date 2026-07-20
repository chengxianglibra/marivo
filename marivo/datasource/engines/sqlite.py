"""SQLite engine profile."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Timer
from typing import TYPE_CHECKING

from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    MetadataInspectRequest,
    default_table_name_parts,
    identity_str,
)

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata


def connect(name: str, kwargs: Mapping[str, object]) -> BaseBackend:
    import ibis

    connect_kwargs = dict(kwargs)
    path = connect_kwargs.pop("path", ":memory:")
    read_only = bool(connect_kwargs.pop("read_only", False))
    connect_kwargs["database"] = path
    backend = ibis.sqlite.connect(**connect_kwargs)
    if read_only:
        backend.raw_sql("PRAGMA query_only = ON")
    return backend


def apply_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    out = dict(kwargs)
    out["read_only"] = True
    return out


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


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
        _query_rows,
        _quote_literal,
        _schema_columns,
    )

    database = request.database
    if isinstance(database, tuple):
        raise ValueError("SQLite table database must be a single namespace name")
    namespace = database or "main"
    namespace_sql = _quote_identifier(namespace)
    table_literal = _quote_literal(request.table)

    table_rows = _query_rows(
        request.backend,
        f"SELECT type, sql FROM {namespace_sql}.sqlite_schema "
        f"WHERE name = {table_literal} AND type IN ('table', 'view') LIMIT 1",
    )
    table_kind = str(table_rows[0].get("type")) if table_rows else "table"
    definition = str(table_rows[0].get("sql")) if table_rows and table_rows[0].get("sql") else None

    column_rows = _query_rows(
        request.backend,
        f"SELECT cid, name, type, [notnull] AS is_not_null, pk "
        f"FROM {namespace_sql}.pragma_table_info({table_literal}) "
        "ORDER BY cid",
    )
    index_rows = _query_rows(
        request.backend,
        f"SELECT name, [unique] AS is_unique, origin "
        f"FROM {namespace_sql}.pragma_index_list({table_literal}) "
        "ORDER BY seq",
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
        if not bool(row.get("is_unique")) or row.get("origin") == "pk":
            continue
        index_name = str(row.get("name"))
        index_columns = _query_rows(
            request.backend,
            f"SELECT name FROM {namespace_sql}.pragma_index_info({_quote_literal(index_name)}) "
            "ORDER BY seqno",
        )
        columns = tuple(str(item.get("name")) for item in index_columns if item.get("name"))
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


def reject_strptime(_value: str) -> str:
    raise ValueError(
        "SQLite does not compile string strptime expressions; use a native temporal column"
    )


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
    timezone_probe_sql=None,
    identifier_quote='"',
    table_name_parts=default_table_name_parts,
    inspect_partition_values=None,
    readonly_tx_start=None,
    metadata=EngineMetadataIntrospection(inspect_table=inspect_table),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=False,
    ),
    translate_strptime_format=reject_strptime,
    postprocess_sql=identity_str,
    datetime_decode_policy="local_naive_label",
    quantile=None,
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
)
