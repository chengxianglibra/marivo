from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import run

from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
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
