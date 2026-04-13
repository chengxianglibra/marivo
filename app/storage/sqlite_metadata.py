from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.storage.metadata import MetadataStore
from app.storage.schema import METADATA_DDL


class SQLiteMetadataStore(MetadataStore):
    """SQLite-backed metadata store for tests and local development."""

    db_path: str | Path
    _is_memory: bool
    _memory_con: sqlite3.Connection | None

    def __init__(self, db_path: str | Path) -> None:
        # Support in-memory SQLite via ":memory:" string
        self._is_memory = str(db_path) == ":memory:"
        if self._is_memory:
            # Keep a persistent connection for in-memory mode
            # All operations use this same connection to share the in-memory DB
            self._memory_con: sqlite3.Connection = sqlite3.connect(":memory:")
            self._memory_con.row_factory = sqlite3.Row
            self._memory_con.execute("PRAGMA foreign_keys=ON")
            self.db_path: str = ":memory:"
        else:
            self.db_path: Path = Path(db_path)
            self._memory_con: sqlite3.Connection | None = None

    def initialize(self) -> None:
        if not self._is_memory:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            for ddl in METADATA_DDL:
                con.execute(ddl)
            con.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self._is_memory:
            # For in-memory mode, yield the persistent connection
            # Do NOT close it - it's shared across all operations
            assert self._memory_con is not None  # Type narrowing for mypy
            yield self._memory_con
        else:
            con = sqlite3.connect(str(self.db_path))
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            try:
                yield con
            finally:
                con.close()

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        with self.connect() as con:
            con.execute(sql, params or [])
            con.commit()

    def execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        with self.connect() as con:
            con.executemany(sql, rows)
            con.commit()

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        with self.connect() as con:
            cursor = con.execute(sql, params or [])
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        rows = self.query_rows(sql, params)
        return rows[0] if rows else None
