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

    db_path: Path

    def __init__(self, db_path: str | Path) -> None:
        if str(db_path) == ":memory:":
            raise ValueError(
                "SQLite metadata store requires a file path; ':memory:' is not supported"
            )
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            for ddl in METADATA_DDL:
                con.execute(ddl)
            self._ensure_time_bindings_timestamp_format_schema(con)
            con.commit()

    def _ensure_time_bindings_timestamp_format_schema(self, con: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in con.execute("PRAGMA table_info(time_bindings)")}
        if "timestamp_format" not in columns:
            con.execute(
                "ALTER TABLE time_bindings "
                "ADD COLUMN timestamp_format TEXT "
                "CHECK (timestamp_format IS NULL OR "
                "timestamp_format IN ('native', 'iso8601_t_naive', 'YYYYMMDD hh:mm:ss'))"
            )
            return

        create_sql_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'time_bindings'"
        ).fetchone()
        create_sql = str(create_sql_row["sql"]) if create_sql_row and create_sql_row["sql"] else ""
        if "YYYYMMDD hh:mm:ss" in create_sql:
            return

        con.execute("ALTER TABLE time_bindings RENAME TO time_bindings__legacy")
        con.execute(
            """
            CREATE TABLE time_bindings (
                time_binding_id    TEXT PRIMARY KEY,
                binding_id         TEXT NOT NULL REFERENCES typed_bindings(binding_id) ON DELETE CASCADE,
                carrier_binding_key TEXT NOT NULL,
                target_kind        TEXT NOT NULL CHECK (
                    target_kind IN (
                        'primary_time',
                        'analysis_window_anchor'
                    )
                ),
                target_key         TEXT NOT NULL,
                context_ref        TEXT,
                semantic_ref       TEXT NOT NULL CHECK (substr(semantic_ref, 1, 5) = 'time.'),
                resolution_kind    TEXT NOT NULL CHECK (
                    resolution_kind IN ('timestamp_column', 'date_column', 'date_hour_columns')
                ),
                timestamp_surface_ref TEXT CHECK (
                    timestamp_surface_ref IS NULL OR substr(timestamp_surface_ref, 1, 6) = 'field.'
                ),
                timestamp_format    TEXT CHECK (
                    timestamp_format IS NULL OR timestamp_format IN (
                        'native',
                        'iso8601_t_naive',
                        'YYYYMMDD hh:mm:ss'
                    )
                ),
                date_surface_ref   TEXT CHECK (
                    date_surface_ref IS NULL OR substr(date_surface_ref, 1, 6) = 'field.'
                ),
                date_format        TEXT,
                hour_surface_ref   TEXT CHECK (
                    hour_surface_ref IS NULL OR substr(hour_surface_ref, 1, 6) = 'field.'
                ),
                hour_format        TEXT,
                timezone_strategy  TEXT CHECK (
                    timezone_strategy IS NULL OR timezone_strategy = 'session_consistent_naive'
                ),
                created_at         TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(binding_id, carrier_binding_key, target_kind, target_key, semantic_ref)
            )
            """
        )
        con.execute(
            """
            INSERT INTO time_bindings (
                time_binding_id,
                binding_id,
                carrier_binding_key,
                target_kind,
                target_key,
                context_ref,
                semantic_ref,
                resolution_kind,
                timestamp_surface_ref,
                timestamp_format,
                date_surface_ref,
                date_format,
                hour_surface_ref,
                hour_format,
                timezone_strategy,
                created_at
            )
            SELECT
                time_binding_id,
                binding_id,
                carrier_binding_key,
                target_kind,
                target_key,
                context_ref,
                semantic_ref,
                resolution_kind,
                timestamp_surface_ref,
                timestamp_format,
                date_surface_ref,
                date_format,
                hour_surface_ref,
                hour_format,
                timezone_strategy,
                created_at
            FROM time_bindings__legacy
            """
        )
        con.execute("DROP TABLE time_bindings__legacy")

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
