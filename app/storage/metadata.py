from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.storage.dialect import MetadataDialect


class MetadataStore(ABC):
    """Pluggable backend for Marivo control-plane tables (sessions, steps,
    artifacts, observations, claims, edges, recommendations, sources,
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
