from __future__ import annotations

from typing import Any, Protocol

from app.contracts.values import LogicalQuery, QueryResult, SourceRef, SourceSchema


class DataSource(Protocol):
    def execute(self, query: LogicalQuery) -> QueryResult: ...
    def schema(self, source_ref: SourceRef) -> SourceSchema: ...
    def resolve_tables(self, table_names: list[str], *, session_id: str | None = None) -> Any: ...
