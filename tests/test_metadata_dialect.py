from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from marivo.adapters.dialect import MySQLMetadataDialect
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore


class MetadataDialectTests(unittest.TestCase):
    def test_mysql_placeholder_compile_skips_literals_identifiers_and_comments(self) -> None:
        dialect = MySQLMetadataDialect()

        sql = """
        SELECT ?, '? literal', "quoted ? identifier", `backtick ? identifier`
        FROM table_name
        -- comment ? stays
        # mysql comment ? stays
        WHERE a = ? AND note = 'it''s ? still literal'
        /* block ? stays */
        AND b = ?
        """

        compiled = dialect.compile_sql(sql)

        self.assertIn("SELECT %s", compiled)
        self.assertIn("WHERE a = %s", compiled)
        self.assertIn("AND b = %s", compiled)
        self.assertIn("'? literal'", compiled)
        self.assertIn('"quoted ? identifier"', compiled)
        self.assertIn("`backtick ? identifier`", compiled)
        self.assertIn("comment ? stays", compiled)
        self.assertIn("mysql comment ? stays", compiled)
        self.assertIn("block ? stays", compiled)

    def test_sqlite_insert_ignore_helper_is_noop_on_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteMetadataStore(Path(temp_dir) / "meta.sqlite")
            store.initialize()
            columns = [
                "session_id",
                "goal",
                "constraints_json",
                "budget_json",
                "status",
            ]
            values = ["sess_1", "goal", "{}", "{}", "open"]

            store.insert_ignore("sessions", columns, values)
            store.insert_ignore(
                "sessions",
                columns,
                ["sess_1", "changed", "{}", "{}", "closed"],
            )

            row = store.query_one("SELECT COUNT(*) AS cnt, goal, status FROM sessions")
            self.assertIsNotNone(row)
            self.assertEqual(row["cnt"], 1)
            self.assertEqual(row["goal"], "goal")
            self.assertEqual(row["status"], "open")

    def test_sqlite_upsert_by_key_updates_existing_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteMetadataStore(Path(temp_dir) / "meta.sqlite")
            store.initialize()
            store.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, status) VALUES (?, ?, ?, ?, ?)",
                ["sess_1", "goal", "{}", "{}", "open"],
            )
            store.execute(
                "INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json) VALUES (?, ?, ?, ?, ?, ?)",
                ["step_1", "sess_1", "metric_query", "completed", "summary", "{}"],
            )

            store.upsert_by_key(
                "step_metadata",
                ["step_id", "metadata_kind", "semantic_snapshot_json"],
                ["step_1", "semantic", '{"v": 1}'],
                ["step_id"],
                ["metadata_kind", "semantic_snapshot_json"],
                updated_at_column="updated_at",
            )
            first = store.query_one(
                "SELECT updated_at FROM step_metadata WHERE step_id = ?",
                ["step_1"],
            )
            store.upsert_by_key(
                "step_metadata",
                ["step_id", "metadata_kind", "semantic_snapshot_json"],
                ["step_1", "semantic", '{"v": 2}'],
                ["step_id"],
                ["metadata_kind", "semantic_snapshot_json"],
                updated_at_column="updated_at",
            )

            row = store.query_one(
                "SELECT COUNT(*) AS cnt, semantic_snapshot_json, updated_at FROM step_metadata"
            )
            self.assertIsNotNone(first)
            self.assertIsNotNone(row)
            self.assertEqual(row["cnt"], 1)
            self.assertEqual(row["semantic_snapshot_json"], '{"v": 2}')
            self.assertIsNotNone(row["updated_at"])


if __name__ == "__main__":
    unittest.main()
