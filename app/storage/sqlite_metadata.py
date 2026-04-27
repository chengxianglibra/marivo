from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.storage.metadata import MetadataStore
from app.storage.schema import METADATA_DDL

_ENGINE_SCHEMA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("auth_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("default_namespace_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("intrinsic_capabilities_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("deployment_capabilities_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("policy_json", "TEXT NOT NULL DEFAULT '{}'"),
)


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
            self._upgrade_legacy_schema(con)
            for ddl in METADATA_DDL:
                con.execute(ddl)
            con.commit()

    def _upgrade_legacy_schema(self, con: sqlite3.Connection) -> None:
        engine_columns = {
            str(row["name"]) for row in con.execute("PRAGMA table_info(engines)").fetchall()
        }
        for column_name, column_definition in _ENGINE_SCHEMA_COLUMNS:
            if column_name not in engine_columns:
                con.execute(f"ALTER TABLE engines ADD COLUMN {column_name} {column_definition}")
        self._rebuild_legacy_source_fk_tables(con)
        self._drop_legacy_tables(con)

    def _rebuild_legacy_source_fk_tables(self, con: sqlite3.Connection) -> None:
        if self._table_references_legacy_source(con, "source_objects"):
            self._rebuild_source_objects(con)
        if self._table_references_legacy_source(con, "source_execution_mappings"):
            self._rebuild_source_execution_mappings(con)
        if self._table_references_legacy_source(con, "sync_jobs"):
            self._rebuild_sync_jobs(con)
        if self._table_references_legacy_source(con, "sync_selections"):
            self._rebuild_sync_selections(con)

    def _table_references_legacy_source(self, con: sqlite3.Connection, table_name: str) -> bool:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            [table_name],
        ).fetchone()
        return row is not None and "sources__legacy" in str(row["sql"])

    def _rebuild_source_objects(self, con: sqlite3.Connection) -> None:
        source_object_columns = self._column_names(con, "source_objects")
        authority_locator_expr = (
            "authority_locator_json"
            if "authority_locator_json" in source_object_columns
            else "'{}'"
        )
        con.execute("ALTER TABLE source_objects RENAME TO source_objects__legacy_fk")
        con.execute(
            """
            CREATE TABLE source_objects (
                object_id       TEXT PRIMARY KEY,
                source_id       TEXT NOT NULL REFERENCES sources(source_id),
                object_type     TEXT NOT NULL,
                parent_id       TEXT,
                native_name     TEXT NOT NULL,
                native_id       TEXT,
                fqn             TEXT NOT NULL,
                authority_locator_json TEXT NOT NULL DEFAULT '{}',
                properties_json TEXT NOT NULL DEFAULT '{}',
                sync_version    TEXT,
                synced_at       TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
            """
        )
        con.execute(
            f"""
            INSERT OR IGNORE INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at,
                updated_at
            )
            SELECT
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                {authority_locator_expr}, properties_json, sync_version, synced_at, created_at,
                updated_at
            FROM source_objects__legacy_fk
            WHERE EXISTS (
                SELECT 1 FROM sources WHERE sources.source_id = source_objects__legacy_fk.source_id
            )
            """
        )
        con.execute("DROP TABLE source_objects__legacy_fk")

    def _rebuild_source_execution_mappings(self, con: sqlite3.Connection) -> None:
        con.execute(
            "ALTER TABLE source_execution_mappings RENAME TO source_execution_mappings__legacy_fk"
        )
        con.execute(
            """
            CREATE TABLE source_execution_mappings (
                mapping_id             TEXT PRIMARY KEY,
                source_id              TEXT NOT NULL REFERENCES sources(source_id),
                engine_id              TEXT NOT NULL REFERENCES engines(engine_id),
                priority               INTEGER NOT NULL DEFAULT 0,
                catalog_mappings_json  TEXT NOT NULL DEFAULT '[]',
                status                 TEXT NOT NULL DEFAULT 'active',
                created_at             TEXT NOT NULL,
                updated_at             TEXT NOT NULL,
                UNIQUE(source_id, engine_id)
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO source_execution_mappings (
                mapping_id, source_id, engine_id, priority, catalog_mappings_json, status,
                created_at, updated_at
            )
            SELECT
                mapping_id, source_id, engine_id, priority, catalog_mappings_json, status,
                created_at, updated_at
            FROM source_execution_mappings__legacy_fk
            WHERE EXISTS (
                SELECT 1 FROM sources
                WHERE sources.source_id = source_execution_mappings__legacy_fk.source_id
            )
            AND EXISTS (
                SELECT 1 FROM engines
                WHERE engines.engine_id = source_execution_mappings__legacy_fk.engine_id
            )
            """
        )
        con.execute("DROP TABLE source_execution_mappings__legacy_fk")

    def _rebuild_sync_jobs(self, con: sqlite3.Connection) -> None:
        con.execute("ALTER TABLE sync_jobs RENAME TO sync_jobs__legacy_fk")
        con.execute(
            """
            CREATE TABLE sync_jobs (
                job_id          TEXT PRIMARY KEY,
                source_id       TEXT NOT NULL REFERENCES sources(source_id),
                job_type        TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                started_at      TEXT,
                finished_at     TEXT,
                objects_synced  INTEGER DEFAULT 0,
                error_message   TEXT,
                created_at      TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO sync_jobs (
                job_id, source_id, job_type, status, started_at, finished_at, objects_synced,
                error_message, created_at
            )
            SELECT
                job_id, source_id, job_type, status, started_at, finished_at, objects_synced,
                error_message, created_at
            FROM sync_jobs__legacy_fk
            WHERE EXISTS (
                SELECT 1 FROM sources WHERE sources.source_id = sync_jobs__legacy_fk.source_id
            )
            """
        )
        con.execute("DROP TABLE sync_jobs__legacy_fk")

    def _rebuild_sync_selections(self, con: sqlite3.Connection) -> None:
        con.execute("ALTER TABLE sync_selections RENAME TO sync_selections__legacy_fk")
        con.execute(
            """
            CREATE TABLE sync_selections (
                selection_id  TEXT PRIMARY KEY,
                source_id     TEXT NOT NULL REFERENCES sources(source_id),
                schema_name   TEXT NOT NULL,
                table_name    TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                UNIQUE(source_id, schema_name, table_name)
            )
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO sync_selections (
                selection_id, source_id, schema_name, table_name, created_at
            )
            SELECT selection_id, source_id, schema_name, table_name, created_at
            FROM sync_selections__legacy_fk
            WHERE EXISTS (
                SELECT 1 FROM sources WHERE sources.source_id = sync_selections__legacy_fk.source_id
            )
            """
        )
        con.execute("DROP TABLE sync_selections__legacy_fk")

    def _column_names(self, con: sqlite3.Connection, table_name: str) -> set[str]:
        return {str(row["name"]) for row in con.execute(f"PRAGMA table_info({table_name})")}

    def _drop_legacy_tables(self, con: sqlite3.Connection) -> None:
        for table_name in (
            "source_engine_bindings",
            "sources__legacy",
            "source_objects__legacy_fk",
            "source_execution_mappings__legacy_fk",
            "sync_jobs__legacy_fk",
            "sync_selections__legacy_fk",
        ):
            con.execute(f"DROP TABLE IF EXISTS {table_name}")

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
