from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from app.storage.metadata import MetadataStore


class PostgresMetadataStore(MetadataStore):
    """PostgreSQL-backed metadata store — stub for future implementation."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def initialize(self) -> None:
        raise NotImplementedError("PostgresMetadataStore is not yet implemented.")

    @contextmanager
    def connect(self) -> Iterator[Any]:
        raise NotImplementedError("PostgresMetadataStore is not yet implemented.")
        yield  # type: ignore[misc]

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        raise NotImplementedError("PostgresMetadataStore is not yet implemented.")

    def execute_many(self, sql: str, rows: list[tuple]) -> None:
        raise NotImplementedError("PostgresMetadataStore is not yet implemented.")

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError("PostgresMetadataStore is not yet implemented.")

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        raise NotImplementedError("PostgresMetadataStore is not yet implemented.")
