from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from marivo.adapters.dialect import SQLITE_METADATA_DIALECT, MetadataDialect
from marivo.adapters.metadata import MetadataStore, MetadataTransaction
from marivo.adapters.schema import metadata_ddl_for_backend, metadata_schema_marker_row


class SQLiteMetadataStore(MetadataStore):
    """SQLite-backed metadata store for tests and local development."""

    db_path: Path
    dialect: MetadataDialect = SQLITE_METADATA_DIALECT

    def __init__(self, db_path: str | Path) -> None:
        if str(db_path) == ":memory:":
            raise ValueError(
                "SQLite metadata store requires a file path; ':memory:' is not supported"
            )
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            for ddl in metadata_ddl_for_backend("sqlite"):
                con.execute(ddl)
            marker = metadata_schema_marker_row("sqlite")
            con.execute(
                self.dialect.insert_ignore_sql(
                    "metadata_schema_marker",
                    ["backend", "schema_version", "ddl_fingerprint"],
                ),
                [marker["backend"], marker["schema_version"], marker["ddl_fingerprint"]],
            )
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
            self.execute_sql(con, sql, params)
            con.commit()

    def execute_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        with self.connect() as con:
            con.executemany(self.dialect.compile_sql(sql), rows)
            con.commit()

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        with self.connect() as con:
            cursor = self.execute_sql(con, sql, params)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        rows = self.query_rows(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[MetadataTransaction]:
        with self.connect() as con:
            try:
                yield MetadataTransaction(self, con)
            except Exception:
                con.rollback()
                raise
            else:
                con.commit()
