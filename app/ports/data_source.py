from __future__ import annotations

from typing import Protocol

from app.contracts.values import LogicalQuery, QueryResult, SourceRef, SourceSchema


class DataSource(Protocol):
    def execute(self, query: LogicalQuery) -> QueryResult: ...
    def schema(self, source_ref: SourceRef) -> SourceSchema: ...
