from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.storage.metadata import MetadataStore
from app.storage.schema import METADATA_DDL, METADATA_MIGRATIONS


class SQLiteMetadataStore(MetadataStore):
    """SQLite-backed metadata store for tests and local development."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            from contextlib import suppress

            for ddl in METADATA_DDL:
                con.execute(ddl)
            for migration in METADATA_MIGRATIONS:
                with suppress(Exception):
                    # column already exists
                    con.execute(migration)
            con.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
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
