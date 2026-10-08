"""Independent native SQL witnesses for datasource and Store ownership."""

from __future__ import annotations

import importlib
import json
import os
import re
import sqlite3
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import FrameType
from typing import Literal, TypeVar, overload

import pytest
from typing_extensions import Self

import marivo
from marivo.datasource.adapters import SourceSession, _IssuedRead
from marivo.datasource.capabilities import ProviderStatementSubmission, provider_statement

Category = Literal[
    "governed_ibis",
    "provider",
    "raw_sql_terminal",
    "ibis_metadata_preparation",
    "native_driver_setup",
    "store",
    "test_administration",
    "unknown",
]

_DEPTH: ContextVar[int] = ContextVar("sql_ownership_native_submission_depth", default=0)
_SQLITE_ACTIVE: ContextVar[NativeSubmission | None] = ContextVar(
    "sql_ownership_sqlite_input", default=None
)
_PRODUCT_ROOT = Path(marivo.__file__).resolve().parent.as_posix() + "/"
_ADMIN_CREDENTIAL_LITERAL = re.compile(
    r"(\bPASSWORD\s+|\bIDENTIFIED\s+(?:WITH\s+\w+\s+)?BY\s+)"
    r"(?:E)?'(?:''|\\.|[^'\\])*'",
    re.IGNORECASE,
)
_IBIS_OWNERS = frozenset(
    {
        "_post_connect",
        "get_schema",
        "_metadata",
        "_get_schema_using_query",
        "list_tables",
        "list_databases",
        "list_catalogs",
        "current_database",
        "current_catalog",
        "_register_in_memory_table",
        "_register_udfs",
        "_register_builtin_udfs",
        "create_table",
        "drop_table",
        "read_csv",
        "read_json",
        "read_parquet",
        "_create_temp_view",
        "_register_temp_view",
    }
)


@dataclass(frozen=True)
class Origin:
    category: Category
    owner: str
    purpose: str = ""
    expected_sql: str | None = None
    parameter_names: tuple[str, ...] = ()
    source_identity: str | None = None
    expression_identity: int | None = None
    schema: tuple[tuple[str, str], ...] = ()


@dataclass
class NativeSubmission:
    backend: str
    boundary: str
    connection: int
    sql: str
    category: Category
    owner: str
    purpose: str
    parameter_names: tuple[str, ...]
    expected_sql: str | None
    source_identity: str | None
    expression_identity: int | None
    schema: tuple[tuple[str, str], ...]
    state: Literal["submitted", "succeeded", "failed"] = "submitted"
    error_type: str | None = None
    engine_statements: list[str] = field(default_factory=list)


def frames() -> Iterator[FrameType]:
    frame: FrameType | None = sys._getframe(1)
    while frame is not None:
        yield frame
        frame = frame.f_back


def classify(sql: str, stack: tuple[FrameType, ...], *, store_connection: bool) -> Origin:
    """Bind the actual driver argument to its current owning call, never SQL syntax."""
    if store_connection:
        return Origin("store", "analysis.materialization.store.SessionStore", "persistence")
    for frame in stack:
        path = frame.f_code.co_filename.replace("\\", "/")
        name = frame.f_code.co_name
        local = frame.f_locals
        if (
            path.endswith("/marivo/datasource/capabilities.py")
            and name == "execute_provider_statement"
        ):
            submission = local.get("submission")
            if not isinstance(submission, ProviderStatementSubmission):
                return Origin("unknown", "provider submission missing")
            statement = provider_statement(submission.provider, submission.statement_id)
            if sql != submission.sql or submission.purpose not in statement.allowed_purposes:
                return Origin("unknown", "provider text or purpose mismatch")
            parameters = local.get("parameters")
            names = (
                tuple(sorted(str(key) for key in parameters))
                if isinstance(parameters, Mapping)
                else ()
            )
            return Origin(
                "provider", submission.statement_id, submission.purpose, submission.sql, names
            )
        if path.endswith("/marivo/datasource/adapters.py") and name == "batches":
            owner = local.get("self")
            proof = local.get("proof")
            if (
                isinstance(owner, SourceSession)
                and isinstance(proof, _IssuedRead)
                and sql == proof.sql
            ):
                return Origin(
                    "governed_ibis",
                    "SourceSession.batches",
                    proof.purpose,
                    proof.sql,
                    source_identity=proof.source_identity,
                    expression_identity=id(proof.expression),
                    schema=tuple((field.name, str(field.type)) for field in proof.schema),
                )
            return Origin("unknown", "SourceSession issued text mismatch")
        if path.endswith("/marivo/datasource/adapters.py") and name == "_probe_backend":
            expected = local.get("sql")
            if isinstance(expected, str) and sql == expected:
                return Origin(
                    "governed_ibis",
                    "datasource.adapters._probe_backend",
                    "datasource.connectivity",
                    expected,
                )
            return Origin("unknown", "Ibis probe text mismatch")
        if path.endswith("/marivo/datasource/manage.py") and name == "raw_sql":
            expected = local.get("statement")
            if isinstance(expected, str) and sql == expected:
                return Origin(
                    "raw_sql_terminal",
                    "datasource.manage.raw_sql",
                    str(local.get("reason", "")),
                    expected,
                )
    # A closed set of installed Ibis producers owns their native metadata and
    # preparation SQL. Generic raw_sql/execute frames alone grant no authority.
    for frame in stack:
        path = frame.f_code.co_filename.replace("\\", "/")
        name = frame.f_code.co_name
        if "/ibis/backends/" in path and name in _IBIS_OWNERS:
            return Origin(
                "ibis_metadata_preparation", path.split("/ibis/", 1)[-1] + ":" + name, name
            )
        if "/clickhouse_connect/driver/" in path and name == "_init_common_settings":
            return Origin(
                "native_driver_setup",
                "clickhouse_connect.Client._init_common_settings",
                "connection_metadata",
            )
    product = [
        frame
        for frame in stack
        if frame.f_code.co_filename.replace("\\", "/").startswith(_PRODUCT_ROOT)
    ]
    if not product:
        return Origin("test_administration", "test-owned native connection", "fixture_or_oracle")
    frame = product[0]
    return Origin(
        "unknown", frame.f_code.co_filename.split("/marivo/", 1)[-1] + ":" + frame.f_code.co_name
    )


@dataclass
class DriverAudit:
    submissions: list[NativeSubmission] = field(default_factory=list)
    _connections: dict[int, int] = field(default_factory=dict)
    _store_connections: set[int] = field(default_factory=set)
    _connection_objects: list[object] = field(default_factory=list)

    def observe(
        self, backend: str, boundary: str, connection: object, sql: str
    ) -> NativeSubmission:
        identity = id(connection)
        if identity not in self._connections:
            self._connections[identity] = len(self._connections) + 1
            self._connection_objects.append(connection)
        number = self._connections[identity]
        origin = classify(
            sql, tuple(frames()), store_connection=identity in self._store_connections
        )
        record = NativeSubmission(
            backend,
            boundary,
            number,
            sql,
            origin.category,
            origin.owner,
            origin.purpose,
            origin.parameter_names,
            origin.expected_sql,
            origin.source_identity,
            origin.expression_identity,
            origin.schema,
        )
        self.submissions.append(record)
        return record

    def submit(
        self,
        backend: str,
        boundary: str,
        connection: object,
        query: object,
        operation: Callable[[], object],
    ) -> object:
        if _DEPTH.get():
            return operation()
        sql = sql_text(query, connection)
        record = self.observe(backend, boundary, connection, sql)
        token = _DEPTH.set(1)
        sqlite_token = _SQLITE_ACTIVE.set(record if backend == "sqlite" else None)
        try:
            result = operation()
        except BaseException as error:
            record.state = "failed"
            record.error_type = type(error).__name__
            raise
        else:
            record.state = "succeeded"
            return result
        finally:
            _SQLITE_ACTIVE.reset(sqlite_token)
            _DEPTH.reset(token)

    def assert_classified(self) -> None:
        unknown = [
            (item.backend, item.boundary, item.owner)
            for item in self.submissions
            if item.category == "unknown"
        ]
        assert not unknown, f"Unclassified native submissions: {unknown}"
        assert self.submissions

    def save(
        self, name: str, environment: dict[str, object], assertions: dict[str, object]
    ) -> None:
        self.assert_classified()
        if directory := os.environ.get("MARIVO_R95_EVIDENCE_DIR"):
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            counts = {
                category: sum(item.category == category for item in self.submissions)
                for category in sorted({item.category for item in self.submissions})
            }
            (path / (name + ".json")).write_text(
                json.dumps(
                    {
                        "environment": environment,
                        "assertions": assertions,
                        "submissions": [serialized_submission(item) for item in self.submissions],
                        "classification_counts": counts,
                        "unknown_submissions": 0,
                        "boundary": "Native SQL API arguments and SQLite engine trace; business qualification retains its original R9.2-R9.4 authority",
                    },
                    sort_keys=True,
                )
            )


def redact_admin_sql(sql: str) -> str:
    """Remove fixture authentication literals only after native ownership classification."""
    return _ADMIN_CREDENTIAL_LITERAL.sub(r"\1'[REDACTED]'", sql)


def serialized_submission(item: NativeSubmission) -> dict[str, object]:
    """Retain the in-memory native argument while redacting persisted fixture secrets."""
    record: dict[str, object] = asdict(item)
    if item.category == "test_administration":
        record["sql"] = redact_admin_sql(item.sql)
        record["expected_sql"] = (
            redact_admin_sql(item.expected_sql) if item.expected_sql is not None else None
        )
        record["engine_statements"] = [redact_admin_sql(sql) for sql in item.engine_statements]
    return record


def sql_text(query: object, connection: object) -> str:
    if isinstance(query, str):
        return query
    if isinstance(query, bytes):
        return query.decode("utf-8")
    render = getattr(query, "as_string", None)
    if callable(render):
        result = render(connection)
        if isinstance(result, str):
            return result
    raise TypeError("The native audit requires the driver's concrete SQL argument")


class DuckDBConnectionWitness:
    """Delegate to the immutable native connection while observing execute calls."""

    def __init__(self, native: object, audit: DriverAudit):
        self._native = native
        self._audit = audit

    def __getattr__(self, name: str) -> object:
        return getattr(self._native, name)

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        operation = getattr(self._native, "fetchmany", None)
        assert callable(operation)
        rows = operation(size)
        assert isinstance(rows, Sequence)
        assert all(isinstance(row, Sequence) for row in rows)
        return rows

    def execute(self, query: object, *args: object, **kwargs: object) -> object:
        operation = getattr(self._native, "execute", None)
        assert callable(operation)
        return self._audit.submit(
            "duckdb",
            "DuckDBPyConnection.execute",
            self._native,
            query,
            lambda: operation(query, *args, **kwargs),
        )


class SQLiteCursorWitness(sqlite3.Cursor):
    """Observe the native cursor input before SQLite expands parameters or pragmas."""

    def execute(self, sql: str, parameters: object = ()) -> Self:
        connection = self.connection
        assert isinstance(connection, SQLiteConnectionWitness)
        native: Callable[..., sqlite3.Cursor] = super().execute
        result = connection.audit.submit(
            "sqlite", "sqlite3.Cursor.execute", connection, sql, lambda: native(sql, parameters)
        )
        assert result is self
        return self

    def executemany(self, sql: str, seq_of_parameters: object) -> Self:
        connection = self.connection
        assert isinstance(connection, SQLiteConnectionWitness)
        native: Callable[..., sqlite3.Cursor] = super().executemany
        result = connection.audit.submit(
            "sqlite",
            "sqlite3.Cursor.executemany",
            connection,
            sql,
            lambda: native(sql, seq_of_parameters),
        )
        assert result is self
        return self

    def executescript(self, sql_script: str) -> sqlite3.Cursor:
        connection = self.connection
        assert isinstance(connection, SQLiteConnectionWitness)
        native = super().executescript
        result = connection.audit.submit(
            "sqlite",
            "sqlite3.Cursor.executescript",
            connection,
            sql_script,
            lambda: native(sql_script),
        )
        assert isinstance(result, sqlite3.Cursor)
        return result


_CursorT = TypeVar("_CursorT", bound=sqlite3.Cursor)


class SQLiteConnectionWitness(sqlite3.Connection):
    """Keep native SQLite connection identity, transactions and authorizer intact."""

    audit: DriverAudit

    @overload
    def cursor(self, factory: None = None) -> sqlite3.Cursor: ...

    @overload
    def cursor(self, factory: Callable[[sqlite3.Connection], _CursorT]) -> _CursorT: ...

    def cursor(
        self, factory: Callable[[sqlite3.Connection], sqlite3.Cursor] | None = None
    ) -> sqlite3.Cursor:
        return super().cursor(factory or SQLiteCursorWitness)

    def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
        native: Callable[..., sqlite3.Cursor] = super().execute
        result = self.audit.submit(
            "sqlite", "sqlite3.Connection.execute", self, sql, lambda: native(sql, parameters)
        )
        assert isinstance(result, sqlite3.Cursor)
        return result

    def executemany(self, sql: str, parameters: object) -> sqlite3.Cursor:
        native: Callable[..., sqlite3.Cursor] = super().executemany
        result = self.audit.submit(
            "sqlite", "sqlite3.Connection.executemany", self, sql, lambda: native(sql, parameters)
        )
        assert isinstance(result, sqlite3.Cursor)
        return result

    def executescript(self, sql_script: str) -> sqlite3.Cursor:
        native = super().executescript
        result = self.audit.submit(
            "sqlite",
            "sqlite3.Connection.executescript",
            self,
            sql_script,
            lambda: native(sql_script),
        )
        assert isinstance(result, sqlite3.Cursor)
        return result


def _patch_cursor(
    audit: DriverAudit,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    cursor_type: object,
    name: str,
) -> None:
    original = getattr(cursor_type, "execute", None)
    assert callable(original)

    def execute(cursor: object, query: object, *args: object, **kwargs: object) -> object:
        connection = getattr(cursor, "connection", None) or getattr(cursor, "_connection", None)
        return audit.submit(
            backend,
            name,
            connection or cursor,
            query,
            lambda: original(cursor, query, *args, **kwargs),
        )

    monkeypatch.setattr(cursor_type, "execute", execute)


@contextmanager
def native_audit(monkeypatch: pytest.MonkeyPatch, backend: str) -> Iterator[DriverAudit]:
    """Install only selected native drivers; admin and product channels stay distinct."""
    audit = DriverAudit()
    with monkeypatch.context() as patch:
        original_sqlite: Callable[..., sqlite3.Connection] = sqlite3.connect

        def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            assert "factory" not in kwargs
            connection = original_sqlite(*args, **kwargs, factory=SQLiteConnectionWitness)
            assert isinstance(connection, SQLiteConnectionWitness)
            connection.audit = audit
            if any(
                frame.f_code.co_filename.replace("\\", "/").endswith(
                    "/marivo/analysis/materialization/store.py"
                )
                for frame in frames()
            ):
                audit._store_connections.add(id(connection))

            def traced(sql: str) -> None:
                # SQLite calls this immediately before its native VM executes.
                active = _SQLITE_ACTIVE.get()
                if active is not None:
                    active.engine_statements.append(sql)
                else:
                    audit.observe(
                        "sqlite", "sqlite3.Connection.set_trace_callback", connection, sql
                    )

            connection.set_trace_callback(traced)
            return connection

        patch.setattr(sqlite3, "connect", connect)
        if backend == "duckdb":
            driver = importlib.import_module("duckdb")
            original = driver.connect
            assert callable(original)

            def duckdb_connect(*args: object, **kwargs: object) -> DuckDBConnectionWitness:
                return DuckDBConnectionWitness(original(*args, **kwargs), audit)

            patch.setattr(driver, "connect", duckdb_connect)
        elif backend == "postgres":
            driver = importlib.import_module("psycopg")
            for name in ("Cursor", "ServerCursor"):
                _patch_cursor(
                    audit, patch, backend, getattr(driver, name), "psycopg." + name + ".execute"
                )
        elif backend == "mysql":
            driver = importlib.import_module("MySQLdb.cursors")
            _patch_cursor(audit, patch, backend, driver.BaseCursor, "MySQLdb.BaseCursor.execute")
        elif backend == "trino":
            driver = importlib.import_module("trino.dbapi")
            _patch_cursor(audit, patch, backend, driver.Cursor, "trino.dbapi.Cursor.execute")
        elif backend == "clickhouse":
            client = importlib.import_module("clickhouse_connect.driver.client")
            http = importlib.import_module("clickhouse_connect.driver.httpclient")
            for cls, name in (
                (client.Client, "query"),
                (client.Client, "query_rows_stream"),
                (http.HttpClient, "raw_query"),
                (http.HttpClient, "command"),
            ):
                original_method = getattr(cls, name)
                assert callable(original_method)

                def wrap(operation: Callable[..., object], boundary: str) -> Callable[..., object]:
                    def call(
                        owner: object, query: object, *args: object, **kwargs: object
                    ) -> object:
                        return audit.submit(
                            backend,
                            boundary,
                            owner,
                            query,
                            lambda: operation(owner, query, *args, **kwargs),
                        )

                    return call

                patch.setattr(cls, name, wrap(original_method, "clickhouse_connect." + name))
        elif backend != "sqlite":
            raise ValueError(f"No native driver audit for {backend}")
        yield audit
