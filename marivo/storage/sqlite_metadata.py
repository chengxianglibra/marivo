from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from marivo.storage.dialect import SQLITE_METADATA_DIALECT, MetadataDialect
from marivo.storage.metadata import MetadataStore
from marivo.storage.schema import metadata_ddl_for_backend, metadata_schema_marker_row

_CATALOG_METADATA_SCHEMA_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("semantic_entity_contracts", "catalog_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("semantic_metric_contracts", "catalog_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("semantic_process_objects", "catalog_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("semantic_dimension_contracts", "catalog_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("semantic_time_objects", "catalog_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("semantic_predicate_contracts", "catalog_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("compiler_compatibility_profiles", "catalog_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("semantic_entity_relationships", "catalog_metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
)

_ENTITY_GROUNDING_SCHEMA_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "entity_kind",
        "TEXT NOT NULL DEFAULT 'business_entity' CHECK "
        "(entity_kind IN ('business_entity', 'event_entity', 'fact_entity', "
        "'snapshot_entity', 'derived_entity'))",
    ),
    ("fields_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("binding_json", "TEXT"),
)

_ENTITY_FIELD_REFERENCING_SCHEMA_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("semantic_dimension_contracts", "source_field_ref", "TEXT"),
    ("semantic_time_objects", "source_field_ref", "TEXT"),
)


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
            self._drop_legacy_tables(con)
            for ddl in metadata_ddl_for_backend("sqlite"):
                con.execute(ddl)
            self._upgrade_legacy_semantic_schema(con)
            marker = metadata_schema_marker_row("sqlite")
            con.execute(
                self.dialect.insert_ignore_sql(
                    "metadata_schema_marker",
                    ["backend", "schema_version", "ddl_fingerprint"],
                ),
                [marker["backend"], marker["schema_version"], marker["ddl_fingerprint"]],
            )
            con.commit()

    def _upgrade_legacy_semantic_schema(self, con: sqlite3.Connection) -> None:
        for table_name, column_name, column_definition in _CATALOG_METADATA_SCHEMA_COLUMNS:
            columns = self._column_names(con, table_name)
            if column_name not in columns:
                con.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
                )
        entity_columns = self._column_names(con, "semantic_entity_contracts")
        for column_name, column_definition in _ENTITY_GROUNDING_SCHEMA_COLUMNS:
            if column_name not in entity_columns:
                con.execute(
                    f"ALTER TABLE semantic_entity_contracts ADD COLUMN {column_name} {column_definition}"
                )
        for table_name, column_name, column_definition in _ENTITY_FIELD_REFERENCING_SCHEMA_COLUMNS:
            columns = self._column_names(con, table_name)
            if column_name not in columns:
                con.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
                )

    def _column_names(self, con: sqlite3.Connection, table_name: str) -> set[str]:
        return {str(row["name"]) for row in con.execute(f"PRAGMA table_info({table_name})")}

    def _drop_legacy_tables(self, con: sqlite3.Connection) -> None:
        # Temporarily disable foreign keys so that legacy tables referenced
        # by current-schema tables can be dropped without integrity errors.
        con.execute("PRAGMA foreign_keys=OFF")
        try:
            for table_name in (
                "source_engine_bindings",
                "sources__legacy",
                "source_execution_mappings__legacy_fk",
                "sources",
                "engines",
                "source_execution_mappings",
            ):
                con.execute(f"DROP TABLE IF EXISTS {table_name}")
        finally:
            con.execute("PRAGMA foreign_keys=ON")

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
