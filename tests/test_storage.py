from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.schema import METADATA_SCHEMA_MARKER_TABLE, metadata_schema_marker_row
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests import shared_fixtures


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

        marker = self.store.query_one(
            """
            SELECT COUNT(*) AS cnt
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            [METADATA_SCHEMA_MARKER_TABLE],
        )
        self.assertIsNotNone(marker)
        self.assertEqual(marker["cnt"], 1)

        marker_row = self.store.query_one(
            "SELECT backend, schema_version, ddl_fingerprint FROM metadata_schema_marker"
        )
        self.assertEqual(marker_row, metadata_schema_marker_row("sqlite"))

    def test_reset_metadata_file_rebuilds_current_schema(self) -> None:
        self.store.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
            ["s1", "test goal", "{}", "{}", "{}", "open"],
        )
        self.store.db_path.unlink()

        rebuilt_store = SQLiteMetadataStore(self.store.db_path)
        rebuilt_store.initialize()

        row = rebuilt_store.query_one("SELECT COUNT(*) AS cnt FROM sessions")
        self.assertIsNotNone(row)
        self.assertEqual(row["cnt"], 0)
        self.assert_current_mapping_only_schema(rebuilt_store)

    def test_seeded_metadata_template_uses_current_mapping_only_schema(self) -> None:
        template_path = shared_fixtures.get_seeded_metadata_path(
            Path(self.temp_dir.name) / "seeded_meta.sqlite"
        )
        template_store = SQLiteMetadataStore(template_path)

        self.assert_current_mapping_only_schema(template_store)

    def test_initialize_uses_current_sessions_schema(self) -> None:
        rows = self.store.query_rows("PRAGMA table_info(sessions)")
        column_names = {str(row["name"]) for row in rows}
        self.assertTrue(
            {
                "execution_identity_json",
                "raw_filter",
                "terminal_reason",
                "ended_at",
                "rollover_from_session_id",
                "updated_at",
            }.issubset(column_names)
        )

    def test_initialize_sets_current_session_execution_identity_default(self) -> None:
        row = self.store.query_one(
            """
            SELECT dflt_value
            FROM pragma_table_info('sessions')
            WHERE name = 'execution_identity_json'
            """
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["dflt_value"], "'{}'")

    def test_initialize_sets_current_datasource_connection_default(self) -> None:
        row = self.store.query_one(
            """
            SELECT dflt_value
            FROM pragma_table_info('datasources')
            WHERE name = 'connection_json'
            """
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["dflt_value"], "'{}'")

    def test_initialize_uses_entity_grounding_json_columns(self) -> None:
        rows = self.store.query_rows("PRAGMA table_info(semantic_entity_contracts)")
        column_names = {str(row["name"]) for row in rows}

        self.assertTrue({"fields_json", "binding_json"}.issubset(column_names))
        entity_kind_row = next(row for row in rows if row["name"] == "entity_kind")
        self.assertEqual(entity_kind_row["dflt_value"], "'business_entity'")

    def test_initialize_adds_entity_grounding_columns_to_legacy_table(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_entity_schema.sqlite"
        con = sqlite3.connect(legacy_path)
        try:
            con.executescript(
                """
                CREATE TABLE semantic_entity_contracts (
                    entity_contract_id      TEXT PRIMARY KEY,
                    entity_ref              TEXT NOT NULL UNIQUE,
                    display_name            TEXT NOT NULL,
                    description             TEXT NOT NULL DEFAULT '',
                    properties_json         TEXT NOT NULL DEFAULT '{}',
                    catalog_metadata_json   TEXT NOT NULL DEFAULT '{}',
                    entity_contract_version TEXT NOT NULL,
                    uniqueness_scope        TEXT NOT NULL,
                    id_stability            TEXT NOT NULL,
                    nullable_key_policy     TEXT NOT NULL DEFAULT 'reject',
                    parent_entity_ref       TEXT,
                    cardinality_to_parent   TEXT,
                    ownership_semantics     TEXT,
                    primary_time_ref        TEXT,
                    status                  TEXT NOT NULL DEFAULT 'draft',
                    revision                INTEGER NOT NULL DEFAULT 1,
                    created_at              TEXT NOT NULL,
                    updated_at              TEXT NOT NULL
                );
                """
            )
            con.commit()
        finally:
            con.close()

        legacy_store = SQLiteMetadataStore(legacy_path)
        legacy_store.initialize()

        rows = legacy_store.query_rows("PRAGMA table_info(semantic_entity_contracts)")
        column_names = {str(row["name"]) for row in rows}
        self.assertTrue({"fields_json", "binding_json", "entity_kind"}.issubset(column_names))
        entity_kind_row = next(row for row in rows if row["name"] == "entity_kind")
        self.assertEqual(entity_kind_row["dflt_value"], "'business_entity'")

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

    def test_initialize_uses_current_metric_schema(self) -> None:
        rows = self.store.query_rows("PRAGMA table_info(semantic_metric_contracts)")
        column_names = {str(row["name"]) for row in rows}
        self.assertIn("default_predicate_refs_json", column_names)
        self.assertIn("base_revision", column_names)
        self.assertIn("change_summary", column_names)
        self.assertIn("revision_compatibility", column_names)
        self.assertIn("is_latest_active", column_names)

    def test_new_tables_exist(self) -> None:
        for table in [
            "datasources",
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
        ]:
            row = self.store.query_one(f"SELECT COUNT(*) AS cnt FROM {table}")
            self.assertIsNotNone(row, f"Table {table} should exist")

    def assert_current_mapping_only_schema(self, store: SQLiteMetadataStore) -> None:
        datasources_row = store.query_one(
            """
            SELECT COUNT(*) AS cnt
            FROM sqlite_master
            WHERE type = 'table' AND name = 'datasources'
            """
        )
        legacy_row = store.query_one(
            """
            SELECT COUNT(*) AS cnt
            FROM sqlite_master
            WHERE type = 'table' AND name = 'source_engine_bindings'
            """
        )
        self.assertIsNotNone(datasources_row)
        self.assertEqual(datasources_row["cnt"], 1)
        self.assertIsNotNone(legacy_row)
        self.assertEqual(legacy_row["cnt"], 0)


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
