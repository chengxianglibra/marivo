from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from marivo.adapters.dialect import MetadataDialect


class MetadataStore(ABC):
    """Pluggable backend for Marivo control-plane tables (sessions, steps,
    artifacts, observations, claims, edges, sources,
    semantic objects, etc.)."""

    @property
    @abstractmethod
    def dialect(self) -> MetadataDialect: ...

    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    @contextmanager
    def connect(self) -> Iterator[Any]: ...

    @abstractmethod
    def execute(self, sql: str, params: list[Any] | None = None) -> None: ...

    @abstractmethod
    def execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None: ...

    @abstractmethod
    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None: ...

    @abstractmethod
    @contextmanager
    def transaction(self) -> Iterator[MetadataTransaction]: ...

    def execute_sql(self, con: Any, sql: str, params: list[Any] | None = None) -> Any:
        return con.execute(self.dialect.compile_sql(sql), params or [])

    def insert_ignore(self, table: str, columns: list[str], values: list[Any]) -> None:
        self.execute(self.dialect.insert_ignore_sql(table, columns), values)

    def insert_ignore_sql(self, table: str, columns: list[str]) -> str:
        return self.dialect.insert_ignore_sql(table, columns)

    def upsert_by_key(
        self,
        table: str,
        insert_columns: list[str],
        values: list[Any],
        conflict_columns: list[str],
        update_columns: list[str],
        *,
        updated_at_column: str | None = None,
    ) -> None:
        self.execute(
            self.dialect.upsert_sql(
                table,
                insert_columns,
                conflict_columns,
                update_columns,
                updated_at_column=updated_at_column,
            ),
            values,
        )


class MetadataTransaction:
    """Connection-bound metadata operations inside one transaction."""

    def __init__(self, store: MetadataStore, con: Any) -> None:
        self.store = store
        self.con = con

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        cursor = self.store.execute_sql(self.con, sql, params)
        close = getattr(cursor, "close", None)
        if close is not None:
            close()

    def execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        cursor = self.con.cursor()
        try:
            cursor.executemany(self.store.dialect.compile_sql(sql), rows)
        finally:
            cursor.close()

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        cursor = self.store.execute_sql(self.con, sql, params)
        try:
            rows = cursor.fetchall()
            if rows and isinstance(rows[0], dict):
                return [dict(row) for row in rows]
            if getattr(cursor, "description", None) is not None:
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row, strict=False)) for row in rows]
            return [dict(row) for row in rows]
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        rows = self.query_rows(sql, params)
        return rows[0] if rows else None
