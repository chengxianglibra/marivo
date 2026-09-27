"""SQLite engine profile."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Timer

import ibis.expr.datatypes as dt
from ibis.backends import BaseBackend

from marivo.datasource.engines.base import (
    AuthoringCapabilities,
    EngineMetadataIntrospection,
    EngineProfile,
    default_table_name_parts,
    schema_only_metadata_inspect,
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


def apply_read_only_kwargs(kwargs: Mapping[str, object]) -> dict[str, object]:
    out = dict(kwargs)
    out["read_only"] = True
    return out


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
    metadata=EngineMetadataIntrospection(inspect_table=schema_only_metadata_inspect),
    authoring_capabilities=AuthoringCapabilities(
        partition_predicate_supported=True,
        transformed_partition_supported=False,
        timeout_enforced=True,
        byte_estimate_supported=False,
    ),
    translate_strptime_format=identity_strptime,
    datetime_decode_policy="local_naive_label",
    quantile=None,
    percentile_uses_approx_quantile=False,
    authoring_timeout=authoring_timeout,
)
