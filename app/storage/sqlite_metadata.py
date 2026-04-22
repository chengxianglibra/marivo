from __future__ import annotations

import json
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
            self._ensure_sources_contract_schema(con)
            self._ensure_engines_contract_schema(con)
            self._ensure_time_bindings_timestamp_format_schema(con)
            con.commit()

    def _ensure_sources_contract_schema(self, con: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in con.execute("PRAGMA table_info(sources)")}
        if "authority_json" in columns and "intrinsic_capabilities_json" in columns:
            return

        con.execute("ALTER TABLE sources RENAME TO sources__legacy")
        con.execute(
            """
            CREATE TABLE sources (
                source_id                    TEXT PRIMARY KEY,
                source_type                  TEXT NOT NULL,
                display_name                 TEXT NOT NULL,
                authority_json               TEXT NOT NULL,
                sync_mode                    TEXT NOT NULL DEFAULT 'selected',
                intrinsic_capabilities_json  TEXT NOT NULL DEFAULT '{}',
                policy_json                  TEXT NOT NULL DEFAULT '{}',
                status                       TEXT NOT NULL DEFAULT 'active',
                created_at                   TEXT NOT NULL,
                updated_at                   TEXT NOT NULL
            )
            """
        )

        legacy_rows = con.execute("SELECT * FROM sources__legacy").fetchall()
        for row in legacy_rows:
            source_type = str(row["source_type"])
            connection = json.loads(str(row["connection_json"]))
            authority = {
                "catalog_system": source_type,
                "connection": connection,
                "synthetic_catalog": "main" if source_type == "duckdb" else None,
            }
            legacy_sync_mode = str(row["sync_mode"] or "by_select")
            sync_mode = "selected" if legacy_sync_mode == "by_select" else legacy_sync_mode
            intrinsic_capabilities = {"supports_partitions": False}
            policy = {
                "allow_live_browse": True,
                "allow_sync": True,
            }
            con.execute(
                """
                INSERT INTO sources (
                    source_id,
                    source_type,
                    display_name,
                    authority_json,
                    sync_mode,
                    intrinsic_capabilities_json,
                    policy_json,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row["source_id"],
                    source_type,
                    row["display_name"],
                    json.dumps(authority),
                    sync_mode,
                    json.dumps(intrinsic_capabilities),
                    json.dumps(policy),
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                ],
            )
        con.execute("DROP TABLE sources__legacy")

    def _ensure_engines_contract_schema(self, con: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in con.execute("PRAGMA table_info(engines)")}
        if "default_namespace_json" in columns and "deployment_capabilities_json" in columns:
            return

        con.execute("ALTER TABLE engines RENAME TO engines__legacy")
        con.execute(
            """
            CREATE TABLE engines (
                engine_id                    TEXT PRIMARY KEY,
                engine_type                  TEXT NOT NULL,
                display_name                 TEXT NOT NULL,
                connection_json              TEXT NOT NULL,
                default_namespace_json       TEXT NOT NULL DEFAULT '{}',
                intrinsic_capabilities_json  TEXT NOT NULL DEFAULT '{}',
                deployment_capabilities_json TEXT NOT NULL DEFAULT '{}',
                policy_json                  TEXT NOT NULL DEFAULT '{}',
                status                       TEXT NOT NULL DEFAULT 'active',
                created_at                   TEXT NOT NULL,
                updated_at                   TEXT NOT NULL
            )
            """
        )

        legacy_rows = con.execute("SELECT * FROM engines__legacy").fetchall()
        for row in legacy_rows:
            engine_type = str(row["engine_type"])
            connection = json.loads(str(row["connection_json"]))
            intrinsic_capabilities = (
                {
                    "materialization_support": "temporary_table",
                    "performance_class": "embedded",
                    "federation_support": "none",
                }
                if engine_type == "duckdb"
                else {
                    "materialization_support": "catalog_table",
                    "performance_class": "distributed",
                    "federation_support": "connector",
                }
            )
            default_namespace = {
                "catalog": connection.get("catalog") if engine_type == "trino" else None,
                "schema": connection.get("schema") if engine_type == "trino" else None,
            }
            deployment_capabilities = dict(json.loads(str(row["capabilities_json"])))
            policy: dict[str, list[str]] = {
                "allowed_step_types": [],
                "required_policy_support": [],
            }
            con.execute(
                """
                INSERT INTO engines (
                    engine_id,
                    engine_type,
                    display_name,
                    connection_json,
                    default_namespace_json,
                    intrinsic_capabilities_json,
                    deployment_capabilities_json,
                    policy_json,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row["engine_id"],
                    engine_type,
                    row["display_name"],
                    row["connection_json"],
                    json.dumps(default_namespace),
                    json.dumps(intrinsic_capabilities),
                    json.dumps(deployment_capabilities),
                    json.dumps(policy),
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                ],
            )
        con.execute("DROP TABLE engines__legacy")

    def _ensure_time_bindings_timestamp_format_schema(self, con: sqlite3.Connection) -> None:
        """Ensure timestamp_format column exists without CHECK constraint.

        Custom timestamp formats (strftime-style) are now supported, so the
        CHECK constraint restricting values to 'native', 'iso8601_t_naive',
        and 'YYYYMMDD hh:mm:ss' has been removed.
        """
        columns = {str(row["name"]) for row in con.execute("PRAGMA table_info(time_bindings)")}
        if "timestamp_format" not in columns:
            # Add column without CHECK constraint
            con.execute("ALTER TABLE time_bindings ADD COLUMN timestamp_format TEXT")
            return

        create_sql_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'time_bindings'"
        ).fetchone()
        create_sql = str(create_sql_row["sql"]) if create_sql_row and create_sql_row["sql"] else ""

        # If there's a CHECK constraint on timestamp_format, rebuild table to remove it.
        # Normalize whitespace in create_sql to handle both `timestamp_format TEXT CHECK`
        # and `timestamp_format    TEXT CHECK` (padded DDL from historical versions).
        normalized_sql = " ".join(create_sql.split())
        if "timestamp_format TEXT CHECK" in normalized_sql:
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
                    timestamp_format    TEXT,
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
