"""Independent adapter/driver submission capture for source projection acceptance."""

from __future__ import annotations

from collections.abc import Callable

import duckdb
import psycopg
import pytest
from psycopg.abc import Params
from psycopg.sql import SQL, Composed

from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter
from marivo.analysis.materialization.execution import Statement
from tests.lazy_scalar_source_fixtures import capture_submissions


def capture_source_sql(monkeypatch: pytest.MonkeyPatch) -> Callable[[], tuple[str, ...]]:
    statements: list[str] = []
    scalar_statements = capture_submissions(monkeypatch)
    original_submit = DuckDBExecutionAdapter.submit
    original_cursor = psycopg.Cursor.execute
    original_server = psycopg.ServerCursor.execute

    def submit(adapter: DuckDBExecutionAdapter, statement: Statement) -> duckdb.DuckDBPyConnection:
        result = original_submit(adapter, statement)
        statements.append(statement.sql)
        return result

    def cursor_execute(
        cursor: psycopg.Cursor[tuple[object, ...]],
        query: str | bytes | SQL | Composed,
        params: Params | None = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> psycopg.Cursor[tuple[object, ...]]:
        result = original_cursor(cursor, query, params, prepare=prepare, binary=binary)
        if isinstance(query, str):
            statements.append(query)
        return result

    def server_execute(
        cursor: psycopg.ServerCursor[tuple[object, ...]],
        query: str | bytes | SQL | Composed,
        params: Params | None = None,
        *,
        binary: bool | None = None,
        **kwargs: object,
    ) -> psycopg.ServerCursor[tuple[object, ...]]:
        result = original_server(cursor, query, params, binary=binary, **kwargs)
        if isinstance(query, str):
            statements.append(query)
        return result

    def snapshot() -> tuple[str, ...]:
        result = list(statements)
        for entry in scalar_statements:
            sql = entry["sql"]
            assert isinstance(sql, str)
            result.append(sql)
        return tuple(result)

    monkeypatch.setattr(DuckDBExecutionAdapter, "submit", submit)
    monkeypatch.setattr(psycopg.Cursor, "execute", cursor_execute)
    monkeypatch.setattr(psycopg.ServerCursor, "execute", server_execute)
    return snapshot
