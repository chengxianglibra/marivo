from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import run

from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.schema import METADATA_DDL
from app.storage.sqlite_metadata import SQLiteMetadataStore


class SQLiteMetadataStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.temp_dir.name) / "meta.sqlite")
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initialize_creates_tables(self) -> None:
        row = self.store.query_one("SELECT COUNT(*) AS cnt FROM sessions")
        self.assertIsNotNone(row)
        self.assertEqual(row["cnt"], 0)

    def test_initialize_uses_current_sessions_schema(self) -> None:
        rows = self.store.query_rows("PRAGMA table_info(sessions)")
        column_names = {str(row["name"]) for row in rows}
        self.assertTrue(
            {
                "raw_filter",
                "terminal_reason",
                "ended_at",
                "rollover_from_session_id",
                "updated_at",
            }.issubset(column_names)
        )

    def test_execute_and_query(self) -> None:
        self.store.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
            ["s1", "test goal", "{}", "{}", "{}", "open"],
        )
        row = self.store.query_one("SELECT * FROM sessions WHERE session_id = ?", ["s1"])
        self.assertIsNotNone(row)
        self.assertEqual(row["goal"], "test goal")

    def test_query_rows(self) -> None:
        self.store.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
            ["s1", "g1", "{}", "{}", "{}", "open"],
        )
        self.store.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
            ["s2", "g2", "{}", "{}", "{}", "open"],
        )
        rows = self.store.query_rows("SELECT * FROM sessions ORDER BY session_id")
        self.assertEqual(len(rows), 2)

    def test_query_one_returns_none_for_no_match(self) -> None:
        row = self.store.query_one("SELECT * FROM sessions WHERE session_id = ?", ["nonexistent"])
        self.assertIsNone(row)

    def test_execute_many(self) -> None:
        self.store.execute_many(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
            [("s1", "g1", "{}", "{}", "{}", "open"), ("s2", "g2", "{}", "{}", "{}", "open")],
        )
        rows = self.store.query_rows("SELECT * FROM sessions")
        self.assertEqual(len(rows), 2)

    def test_initialize_migrates_time_bindings_timestamp_format_no_constraint(self) -> None:
        """Migration removes timestamp_format CHECK constraint, allowing custom formats."""
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite"
        legacy_store = SQLiteMetadataStore(legacy_path)
        with legacy_store.connect() as con:
            for ddl in METADATA_DDL:
                if "CREATE TABLE IF NOT EXISTS time_bindings" in ddl:
                    con.execute(
                        """
                        CREATE TABLE time_bindings (
                            time_binding_id TEXT PRIMARY KEY,
                            binding_id TEXT NOT NULL REFERENCES typed_bindings(binding_id) ON DELETE CASCADE,
                            carrier_binding_key TEXT NOT NULL,
                            target_kind TEXT NOT NULL CHECK (
                                target_kind IN ('primary_time', 'analysis_window_anchor')
                            ),
                            target_key TEXT NOT NULL,
                            context_ref TEXT,
                            semantic_ref TEXT NOT NULL CHECK (substr(semantic_ref, 1, 5) = 'time.'),
                            resolution_kind TEXT NOT NULL CHECK (
                                resolution_kind IN ('timestamp_column', 'date_column', 'date_hour_columns')
                            ),
                            timestamp_surface_ref TEXT CHECK (
                                timestamp_surface_ref IS NULL OR substr(timestamp_surface_ref, 1, 6) = 'field.'
                            ),
                            timestamp_format TEXT CHECK (
                                timestamp_format IS NULL OR timestamp_format IN ('native', 'iso8601_t_naive')
                            ),
                            date_surface_ref TEXT CHECK (
                                date_surface_ref IS NULL OR substr(date_surface_ref, 1, 6) = 'field.'
                            ),
                            date_format TEXT,
                            hour_surface_ref TEXT CHECK (
                                hour_surface_ref IS NULL OR substr(hour_surface_ref, 1, 6) = 'field.'
                            ),
                            hour_format TEXT,
                            timezone_strategy TEXT CHECK (
                                timezone_strategy IS NULL OR timezone_strategy = 'session_consistent_naive'
                            ),
                            created_at TEXT NOT NULL DEFAULT (datetime('now')),
                            UNIQUE(binding_id, carrier_binding_key, target_kind, target_key, semantic_ref)
                        )
                        """
                    )
                else:
                    con.execute(ddl)
            con.commit()

        migrated_store = SQLiteMetadataStore(legacy_path)
        migrated_store.initialize()
        migrated_store.execute(
            """
            INSERT INTO typed_bindings (
                binding_id, binding_ref, binding_scope, bound_object_ref,
                binding_contract_version, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            ["bind_1", "binding.test", "entity", "entity.test", "binding.v1", "draft"],
        )
        # After migration, CHECK constraint is removed, so custom formats are accepted
        migrated_store.execute(
            """
            INSERT INTO time_bindings (
                time_binding_id, binding_id, carrier_binding_key, target_kind, target_key,
                context_ref, semantic_ref, resolution_kind, timestamp_surface_ref,
                timestamp_format, date_surface_ref, date_format, hour_surface_ref,
                hour_format, timezone_strategy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "tb_1",
                "bind_1",
                "primary",
                "primary_time",
                "time.test",
                None,
                "time.test",
                "timestamp_column",
                "field.create_time",
                "%Y%m%d %H:%M:%S",
                None,
                None,
                None,
                None,
                "session_consistent_naive",
            ],
        )
        row = migrated_store.query_one(
            "SELECT timestamp_format FROM time_bindings WHERE time_binding_id = ?",
            ["tb_1"],
        )
        self.assertEqual(row["timestamp_format"], "%Y%m%d %H:%M:%S")

    def test_initialize_migrates_legacy_source_and_engine_contracts(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_source_engine.sqlite"
        legacy_store = SQLiteMetadataStore(legacy_path)
        with legacy_store.connect() as con:
            for ddl in METADATA_DDL:
                if "CREATE TABLE IF NOT EXISTS sources" in ddl:
                    con.execute(
                        """
                        CREATE TABLE sources (
                            source_id         TEXT PRIMARY KEY,
                            source_type       TEXT NOT NULL,
                            display_name      TEXT NOT NULL,
                            connection_json   TEXT NOT NULL,
                            capabilities_json TEXT NOT NULL DEFAULT '{}',
                            sync_mode         TEXT NOT NULL DEFAULT 'by_select',
                            status            TEXT NOT NULL DEFAULT 'active',
                            created_at        TEXT NOT NULL,
                            updated_at        TEXT NOT NULL
                        )
                        """
                    )
                elif "CREATE TABLE IF NOT EXISTS engines" in ddl:
                    con.execute(
                        """
                        CREATE TABLE engines (
                            engine_id         TEXT PRIMARY KEY,
                            engine_type       TEXT NOT NULL,
                            display_name      TEXT NOT NULL,
                            connection_json   TEXT NOT NULL,
                            capabilities_json TEXT NOT NULL DEFAULT '{}',
                            status            TEXT NOT NULL DEFAULT 'active',
                            created_at        TEXT NOT NULL,
                            updated_at        TEXT NOT NULL
                        )
                        """
                    )
                else:
                    con.execute(ddl)
            con.execute(
                """
                INSERT INTO sources (
                    source_id, source_type, display_name, connection_json,
                    capabilities_json, sync_mode, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "src_legacy",
                    "duckdb",
                    "Legacy Source",
                    '{"path": "/tmp/legacy.duckdb"}',
                    '{"supports_partitions": false}',
                    "by_select",
                    "active",
                    "2026-04-22T00:00:00Z",
                    "2026-04-22T00:00:00Z",
                ],
            )
            con.execute(
                """
                INSERT INTO engines (
                    engine_id, engine_type, display_name, connection_json,
                    capabilities_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "eng_legacy",
                    "trino",
                    "Legacy Engine",
                    '{"host": "localhost", "port": 8080, "catalog": "iceberg", "schema": "analytics"}',
                    (
                        '{"supported_step_types": ["metric_query"], '
                        '"min_staleness_minutes": 15, '
                        '"policy_support": ["catalog_governed"], '
                        '"performance_class": "embedded", '
                        '"supported_sql_features": ["connector_pushdown"], '
                        '"metadata": {"runtime": "legacy"}}'
                    ),
                    "active",
                    "2026-04-22T00:00:00Z",
                    "2026-04-22T00:00:00Z",
                ],
            )
            con.commit()

        migrated_store = SQLiteMetadataStore(legacy_path)
        migrated_store.initialize()

        source_columns = {
            str(row["name"]) for row in migrated_store.query_rows("PRAGMA table_info(sources)")
        }
        self.assertIn("authority_json", source_columns)
        self.assertIn("intrinsic_capabilities_json", source_columns)
        self.assertIn("policy_json", source_columns)
        source_row = migrated_store.query_one(
            """
            SELECT authority_json, sync_mode, intrinsic_capabilities_json, policy_json
            FROM sources
            WHERE source_id = ?
            """,
            ["src_legacy"],
        )
        self.assertEqual(
            source_row["authority_json"],
            '{"catalog_system": "duckdb", "connection": {"path": "/tmp/legacy.duckdb"}, "synthetic_catalog": "main"}',
        )
        self.assertEqual(source_row["sync_mode"], "selected")
        self.assertEqual(
            source_row["intrinsic_capabilities_json"], '{"supports_partitions": false}'
        )
        self.assertEqual(
            source_row["policy_json"], '{"allow_live_browse": true, "allow_sync": true}'
        )

        engine_columns = {
            str(row["name"]) for row in migrated_store.query_rows("PRAGMA table_info(engines)")
        }
        self.assertIn("default_namespace_json", engine_columns)
        self.assertIn("deployment_capabilities_json", engine_columns)
        self.assertIn("policy_json", engine_columns)
        engine_row = migrated_store.query_one(
            """
            SELECT
                connection_json,
                default_namespace_json,
                intrinsic_capabilities_json,
                deployment_capabilities_json,
                policy_json
            FROM engines
            WHERE engine_id = ?
            """,
            ["eng_legacy"],
        )
        self.assertEqual(
            engine_row["connection_json"],
            '{"host": "localhost", "port": 8080, "catalog": "iceberg", "schema": "analytics"}',
        )
        self.assertEqual(
            engine_row["default_namespace_json"], '{"catalog": "iceberg", "schema": "analytics"}'
        )
        self.assertEqual(
            engine_row["intrinsic_capabilities_json"],
            '{"materialization_support": "catalog_table", "performance_class": "distributed", "federation_support": "connector"}',
        )
        self.assertEqual(
            engine_row["deployment_capabilities_json"],
            (
                '{"supported_step_types": ["metric_query"], '
                '"min_staleness_minutes": 15, '
                '"policy_support": ["catalog_governed"], '
                '"performance_class": "embedded", '
                '"supported_sql_features": ["connector_pushdown"], '
                '"metadata": {"runtime": "legacy"}}'
            ),
        )
        self.assertEqual(
            engine_row["policy_json"],
            '{"allowed_step_types": [], "required_policy_support": []}',
        )

    def test_new_tables_exist(self) -> None:
        for table in [
            "sources",
            "source_objects",
            "semantic_entity_contracts",
            "semantic_entity_key_refs",
            "semantic_entity_stable_descriptors",
            "semantic_metric_contracts",
            "semantic_process_objects",
            "semantic_process_exported_dimension_refs",
            "semantic_dimension_contracts",
            "semantic_time_objects",
            "semantic_enum_sets",
            "semantic_enum_set_versions",
            "semantic_enum_set_values",
            "sync_jobs",
        ]:
            row = self.store.query_one(f"SELECT COUNT(*) AS cnt FROM {table}")
            self.assertIsNotNone(row, f"Table {table} should exist")


class ResetMetadataScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script_path = self.repo_root / "scripts" / "reset-metadata-sqlite.sh"
        self.duckdb_path = Path(self.temp_dir.name) / "scratch.duckdb"
        self.metadata_path = self.duckdb_path.with_suffix(".meta.sqlite")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_script_removes_metadata_sqlite_and_sidecars(self) -> None:
        for path in (
            self.metadata_path,
            Path(f"{self.metadata_path}-wal"),
            Path(f"{self.metadata_path}-shm"),
        ):
            path.write_text("x", encoding="utf-8")

        result = run(
            ["/bin/bash", str(self.script_path), str(self.duckdb_path)],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.metadata_path.exists())
        self.assertFalse(Path(f"{self.metadata_path}-wal").exists())
        self.assertFalse(Path(f"{self.metadata_path}-shm").exists())


class DuckDBAnalyticsEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from tests.shared_fixtures import get_seeded_duckdb_path

        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = get_seeded_duckdb_path(Path(cls.temp_dir.name) / "test_engine.duckdb")
        cls.engine = DuckDBAnalyticsEngine(db_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_initialize_seeds_data(self) -> None:
        count = self.engine.table_row_count("analytics.watch_events")
        self.assertGreater(count, 0)

    def test_table_exists(self) -> None:
        self.assertTrue(self.engine.table_exists("analytics.watch_events"))
        self.assertFalse(self.engine.table_exists("nonexistent_table"))

    def test_query_rows(self) -> None:
        rows = self.engine.query_rows(
            "SELECT DISTINCT platform FROM analytics.watch_events ORDER BY platform"
        )
        platforms = {row["platform"] for row in rows}
        self.assertIn("android", platforms)
        self.assertIn("ios", platforms)
        self.assertIn("web", platforms)


if __name__ == "__main__":
    unittest.main()
