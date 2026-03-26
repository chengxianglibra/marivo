"""Tests for catalog adapters: DuckDB, Trino."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.adapters.base import PhysicalObject
from app.adapters.duckdb_adapter import DuckDBCatalogAdapter
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from tests.shared_fixtures import get_seeded_duckdb_path


class DuckDBCatalogAdapterTests(unittest.TestCase):
    """Tests for DuckDBCatalogAdapter using a real DuckDB file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "adapter_test.duckdb"
        # Initialize with analytics demo data
        get_seeded_duckdb_path(cls.db_path)
        ae = DuckDBAnalyticsEngine(cls.db_path)
        ae.initialize()
        cls.adapter = DuckDBCatalogAdapter(cls.db_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_source_type(self) -> None:
        self.assertEqual(self.adapter.source_type(), "duckdb")

    def test_capabilities(self) -> None:
        caps = self.adapter.capabilities()
        self.assertTrue(caps.supports_schemas)
        self.assertFalse(caps.supports_partitions)
        self.assertFalse(caps.supports_lineage)

    def test_test_connection(self) -> None:
        self.assertTrue(self.adapter.test_connection())

    def test_test_connection_bad_path(self) -> None:
        bad = DuckDBCatalogAdapter("/nonexistent/path.duckdb")
        self.assertFalse(bad.test_connection())

    def test_list_schemas(self) -> None:
        schemas = self.adapter.list_schemas()
        schema_names = [s.native_name for s in schemas]
        self.assertIn("analytics", schema_names)
        for s in schemas:
            self.assertEqual(s.object_type, "schema")
            self.assertIsInstance(s, PhysicalObject)

    def test_list_tables(self) -> None:
        tables = self.adapter.list_tables("analytics")
        table_names = [t.native_name for t in tables]
        self.assertIn("watch_events", table_names)
        self.assertIn("player_qoe", table_names)
        self.assertIn("ad_events", table_names)
        self.assertIn("recommendation_events", table_names)
        for t in tables:
            self.assertEqual(t.object_type, "table")
            self.assertEqual(t.parent_path, "analytics")
            self.assertIn("column_count", t.properties)
            self.assertGreater(t.properties["column_count"], 0)

    def test_list_tables_empty_schema(self) -> None:
        tables = self.adapter.list_tables("nonexistent_schema")
        self.assertEqual(tables, [])

    def test_get_table_detail(self) -> None:
        detail = self.adapter.get_table_detail("analytics", "watch_events")
        self.assertEqual(detail.native_name, "watch_events")
        self.assertEqual(detail.object_type, "table")
        self.assertIn("columns", detail.properties)
        self.assertGreater(len(detail.properties["columns"]), 0)
        col_names = [c["name"] for c in detail.properties["columns"]]
        self.assertIn("event_date", col_names)
        self.assertIn("play_duration_seconds", col_names)

    def test_get_table_detail_not_found(self) -> None:
        with self.assertRaises(KeyError):
            self.adapter.get_table_detail("analytics", "nonexistent_table")

    def test_list_columns(self) -> None:
        columns = self.adapter.list_columns("analytics", "watch_events")
        self.assertGreater(len(columns), 0)
        col_names = [c.native_name for c in columns]
        self.assertIn("event_date", col_names)
        self.assertIn("platform", col_names)
        for c in columns:
            self.assertEqual(c.object_type, "column")
            self.assertIn("data_type", c.properties)

    def test_list_columns_empty_table(self) -> None:
        columns = self.adapter.list_columns("analytics", "nonexistent")
        self.assertEqual(columns, [])

    def test_source_type_registered_in_factory(self) -> None:
        """Verify 'duckdb' source type is handled by the adapter factory."""
        from app.sources import _build_adapter
        adapter = _build_adapter("duckdb", {"path": str(self.db_path)})
        self.assertIsInstance(adapter, DuckDBCatalogAdapter)


class TrinoCatalogAdapterTests(unittest.TestCase):
    """Tests for TrinoCatalogAdapter with mocked trino connection."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.adapters.trino_adapter import TrinoCatalogAdapter
        cls.adapter = TrinoCatalogAdapter(
            host="mock-trino.example.com",
            port=8080,
            user="test",
            catalog="hive",
            schema="default",
        )

    def _mock_cursor(self, rows, columns):
        """Create a mock cursor that returns the given rows/columns."""
        cursor = MagicMock()
        cursor.description = [(c,) for c in columns]
        cursor.fetchall.return_value = rows
        cursor.fetchone.return_value = rows[0] if rows else None
        return cursor

    def _mock_connection(self, cursor):
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn

    def test_source_type(self) -> None:
        self.assertEqual(self.adapter.source_type(), "trino")

    def test_capabilities(self) -> None:
        caps = self.adapter.capabilities()
        self.assertTrue(caps.supports_schemas)
        self.assertTrue(caps.supports_column_stats)
        self.assertFalse(caps.supports_partitions)
        self.assertTrue(caps.supports_column_comments)
        self.assertTrue(caps.supports_table_properties)

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._connect")
    def test_test_connection(self, mock_connect) -> None:
        cursor = self._mock_cursor([(1,)], ["_col0"])
        mock_connect.return_value = self._mock_connection(cursor)
        self.assertTrue(self.adapter.test_connection())

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._connect")
    def test_test_connection_failure(self, mock_connect) -> None:
        mock_connect.side_effect = Exception("Connection refused")
        self.assertFalse(self.adapter.test_connection())

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._connect")
    def test_list_schemas(self, mock_connect) -> None:
        cursor = self._mock_cursor(
            [("default",), ("analytics",), ("information_schema",)],
            ["schema_name"],
        )
        mock_connect.return_value = self._mock_connection(cursor)
        # Pass explicit catalog_name; None now means "aggregate all catalogs"
        schemas = self.adapter.list_schemas("hive")
        # information_schema should be filtered out
        self.assertEqual(len(schemas), 2)
        self.assertEqual(schemas[0].native_name, "default")
        self.assertEqual(schemas[1].native_name, "analytics")
        self.assertEqual(schemas[0].object_type, "schema")
        self.assertEqual(schemas[0].parent_path, "hive")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_tables(self, mock_query) -> None:
        # Single JOIN query returns table_name, table_type, column_count
        mock_query.return_value = [
            {"table_name": "events", "table_type": "TABLE", "column_count": 5},
            {"table_name": "users", "table_type": "VIEW", "column_count": 3},
        ]
        tables = self.adapter.list_tables("analytics")
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0].native_name, "events")
        self.assertEqual(tables[0].properties["table_type"], "TABLE")
        self.assertEqual(tables[0].properties["column_count"], 5)
        self.assertEqual(tables[1].native_name, "users")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail(self, mock_query) -> None:
        mock_query.side_effect = [
            # table existence check
            [{"table_name": "events", "table_type": "TABLE"}],
            # columns
            [
                {"column_name": "id", "data_type": "integer", "ordinal_position": 1, "is_nullable": "NO"},
                {"column_name": "name", "data_type": "varchar", "ordinal_position": 2, "is_nullable": "YES"},
            ],
            # SHOW COLUMNS for comments
            [
                {"Column": "id", "Type": "integer", "Extra": "", "Comment": "Primary key"},
                {"Column": "name", "Type": "varchar", "Extra": "", "Comment": "User name"},
            ],
            # table$properties
            [
                {"key": "comment", "value": "Events table"},
                {"key": "owner", "value": "analytics_team"},
            ],
        ]
        detail = self.adapter.get_table_detail("analytics", "events")
        self.assertEqual(detail.native_name, "events")
        self.assertIn("columns", detail.properties)
        self.assertEqual(len(detail.properties["columns"]), 2)
        self.assertEqual(detail.properties["columns"][0]["name"], "id")
        self.assertEqual(detail.properties["columns"][0]["comment"], "Primary key")
        self.assertFalse(detail.properties["columns"][0]["nullable"])
        self.assertTrue(detail.properties["columns"][1]["nullable"])
        self.assertIn("table_properties", detail.properties)
        self.assertEqual(detail.properties["comment"], "Events table")
        self.assertEqual(detail.properties["owner"], "analytics_team")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail_not_found(self, mock_query) -> None:
        mock_query.return_value = []
        with self.assertRaises(KeyError):
            self.adapter.get_table_detail("analytics", "nonexistent")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_columns(self, mock_query) -> None:
        mock_query.side_effect = [
            # columns query
            [
                {"column_name": "id", "data_type": "integer", "ordinal_position": 1, "is_nullable": "NO"},
                {"column_name": "ts", "data_type": "timestamp", "ordinal_position": 2, "is_nullable": "YES"},
            ],
            # SHOW COLUMNS for comments
            [
                {"Column": "id", "Type": "integer", "Extra": "", "Comment": "ID column"},
                {"Column": "ts", "Type": "timestamp", "Extra": "", "Comment": "Timestamp"},
            ],
        ]
        columns = self.adapter.list_columns("analytics", "events")
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0].native_name, "id")
        self.assertEqual(columns[0].properties["data_type"], "integer")
        self.assertEqual(columns[0].properties["comment"], "ID column")
        self.assertFalse(columns[0].properties["nullable"])
        self.assertEqual(columns[0].parent_path, "analytics.events")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_stats(self, mock_query) -> None:
        mock_query.return_value = [
            {"column_name": "id", "data_size": 100, "distinct_values_count": 50,
             "nulls_fraction": 0.0, "row_count": None, "low_value": "1", "high_value": "50"},
            {"column_name": "name", "data_size": 500, "distinct_values_count": 30,
             "nulls_fraction": 0.1, "row_count": None, "low_value": None, "high_value": None},
            {"column_name": None, "data_size": None, "distinct_values_count": None,
             "nulls_fraction": None, "row_count": 1000, "low_value": None, "high_value": None},
        ]
        stats = self.adapter.get_table_stats("analytics", "events")
        self.assertEqual(stats["row_count"], 1000)
        self.assertIn("id", stats["columns"])
        self.assertEqual(stats["columns"]["id"]["distinct_count"], 50)
        self.assertEqual(stats["columns"]["name"]["nulls_fraction"], 0.1)

    def test_source_type_registered_in_factory(self) -> None:
        """Verify 'trino' source type is handled by the adapter factory."""
        from app.sources import _build_adapter
        from app.adapters.trino_adapter import TrinoCatalogAdapter
        adapter = _build_adapter("trino", {
            "host": "localhost",
            "port": 8080,
            "catalog": "hive",
        })
        self.assertIsInstance(adapter, TrinoCatalogAdapter)

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_columns_without_comments(self, mock_query) -> None:
        """Test that list_columns handles missing comments gracefully."""
        mock_query.side_effect = [
            # columns query
            [
                {"column_name": "id", "data_type": "integer", "ordinal_position": 1, "is_nullable": "NO"},
            ],
            # SHOW COLUMNS fails
            Exception("Permission denied"),
        ]
        columns = self.adapter.list_columns("analytics", "events")
        self.assertEqual(len(columns), 1)
        self.assertEqual(columns[0].properties["comment"], "")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail_without_properties(self, mock_query) -> None:
        """Test that get_table_detail handles missing table properties gracefully."""
        mock_query.side_effect = [
            # table existence check
            [{"table_name": "events", "table_type": "TABLE"}],
            # columns
            [
                {"column_name": "id", "data_type": "integer", "ordinal_position": 1, "is_nullable": "NO"},
            ],
            # SHOW COLUMNS fails
            Exception("Permission denied"),
            # table$properties fails (non-Iceberg table)
            Exception("Table not found"),
        ]
        detail = self.adapter.get_table_detail("analytics", "events")
        self.assertNotIn("table_properties", detail.properties)
        self.assertNotIn("comment", detail.properties)
        # Column comment should be empty
        self.assertEqual(detail.properties["columns"][0]["comment"], "")


if __name__ == "__main__":
    unittest.main()
