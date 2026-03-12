"""Tests for catalog adapters: DuckDB, Unity Catalog, AWS Glue, Polaris."""

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


class UnityCatalogAdapterTests(unittest.TestCase):
    """Tests for UnityCatalogAdapter with mocked HTTP responses."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.adapters.unity_adapter import UnityCatalogAdapter
        cls.adapter = UnityCatalogAdapter(
            host="https://mock-unity.example.com",
            token="test-token",
            catalog_name="main",
        )

    def _mock_response(self, json_data, status_code=200):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = json_data
        mock.raise_for_status = MagicMock()
        if status_code >= 400:
            mock.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        return mock

    def test_source_type(self) -> None:
        self.assertEqual(self.adapter.source_type(), "unity_catalog")

    def test_capabilities(self) -> None:
        caps = self.adapter.capabilities()
        self.assertTrue(caps.supports_schemas)

    @patch("app.adapters.unity_adapter.requests.get")
    def test_test_connection(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({"catalogs": []})
        self.assertTrue(self.adapter.test_connection())
        mock_get.assert_called_once()

    @patch("app.adapters.unity_adapter.requests.get")
    def test_test_connection_failure(self, mock_get) -> None:
        mock_get.side_effect = Exception("Connection refused")
        self.assertFalse(self.adapter.test_connection())

    @patch("app.adapters.unity_adapter.requests.get")
    def test_list_schemas(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({
            "schemas": [
                {"name": "main.default", "catalog_name": "main", "comment": "Default schema"},
                {"name": "main.analytics", "catalog_name": "main", "comment": "Analytics"},
            ]
        })
        schemas = self.adapter.list_schemas("main")
        self.assertEqual(len(schemas), 2)
        self.assertEqual(schemas[0].native_name, "default")
        self.assertEqual(schemas[0].object_type, "schema")

    @patch("app.adapters.unity_adapter.requests.get")
    def test_list_tables(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({
            "tables": [
                {"name": "events", "catalog_name": "main", "schema_name": "analytics",
                 "table_type": "MANAGED", "columns": [{"name": "id", "type_name": "INT"}]},
            ]
        })
        tables = self.adapter.list_tables("analytics")
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].native_name, "events")
        self.assertEqual(tables[0].object_type, "table")

    @patch("app.adapters.unity_adapter.requests.get")
    def test_get_table_detail(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({
            "name": "events",
            "catalog_name": "main",
            "schema_name": "analytics",
            "table_type": "MANAGED",
            "columns": [
                {"name": "id", "type_name": "INT", "position": 0},
                {"name": "name", "type_name": "STRING", "position": 1},
            ],
        })
        detail = self.adapter.get_table_detail("analytics", "events")
        self.assertEqual(detail.native_name, "events")
        self.assertIn("columns", detail.properties)
        self.assertEqual(len(detail.properties["columns"]), 2)

    @patch("app.adapters.unity_adapter.requests.get")
    def test_list_columns(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({
            "name": "events",
            "columns": [
                {"name": "id", "type_name": "INT", "position": 0},
                {"name": "name", "type_name": "STRING", "position": 1},
            ],
        })
        columns = self.adapter.list_columns("analytics", "events")
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0].native_name, "id")
        self.assertEqual(columns[0].properties["data_type"], "INT")


class GlueCatalogAdapterTests(unittest.TestCase):
    """Tests for GlueCatalogAdapter with mocked boto3."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.adapters.glue_adapter import GlueCatalogAdapter
        cls.adapter = GlueCatalogAdapter(region="us-east-1")

    def test_source_type(self) -> None:
        self.assertEqual(self.adapter.source_type(), "aws_glue")

    def test_capabilities(self) -> None:
        caps = self.adapter.capabilities()
        self.assertTrue(caps.supports_schemas)
        self.assertTrue(caps.supports_partitions)

    @patch("app.adapters.glue_adapter.GlueCatalogAdapter._get_client")
    def test_test_connection(self, mock_client) -> None:
        client = MagicMock()
        client.get_databases.return_value = {"DatabaseList": []}
        mock_client.return_value = client
        self.assertTrue(self.adapter.test_connection())

    @patch("app.adapters.glue_adapter.GlueCatalogAdapter._get_client")
    def test_list_schemas(self, mock_client) -> None:
        client = MagicMock()
        client.get_databases.return_value = {
            "DatabaseList": [
                {"Name": "analytics", "Description": "Analytics DB"},
                {"Name": "raw", "Description": "Raw data"},
            ]
        }
        mock_client.return_value = client
        schemas = self.adapter.list_schemas()
        self.assertEqual(len(schemas), 2)
        self.assertEqual(schemas[0].native_name, "analytics")

    @patch("app.adapters.glue_adapter.GlueCatalogAdapter._get_client")
    def test_list_tables(self, mock_client) -> None:
        client = MagicMock()
        client.get_tables.return_value = {
            "TableList": [
                {"Name": "events", "DatabaseName": "analytics", "TableType": "EXTERNAL_TABLE",
                 "StorageDescriptor": {"Columns": [{"Name": "id", "Type": "int"}]}},
            ]
        }
        mock_client.return_value = client
        tables = self.adapter.list_tables("analytics")
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].native_name, "events")

    @patch("app.adapters.glue_adapter.GlueCatalogAdapter._get_client")
    def test_get_table_detail(self, mock_client) -> None:
        client = MagicMock()
        client.get_table.return_value = {
            "Table": {
                "Name": "events",
                "DatabaseName": "analytics",
                "TableType": "EXTERNAL_TABLE",
                "StorageDescriptor": {
                    "Columns": [
                        {"Name": "id", "Type": "int"},
                        {"Name": "name", "Type": "string"},
                    ],
                    "Location": "s3://bucket/path",
                },
                "PartitionKeys": [{"Name": "dt", "Type": "string"}],
            }
        }
        mock_client.return_value = client
        detail = self.adapter.get_table_detail("analytics", "events")
        self.assertEqual(detail.native_name, "events")
        self.assertEqual(len(detail.properties["columns"]), 2)

    @patch("app.adapters.glue_adapter.GlueCatalogAdapter._get_client")
    def test_list_columns(self, mock_client) -> None:
        client = MagicMock()
        client.get_table.return_value = {
            "Table": {
                "Name": "events",
                "StorageDescriptor": {
                    "Columns": [
                        {"Name": "id", "Type": "int"},
                        {"Name": "name", "Type": "string"},
                    ],
                },
                "PartitionKeys": [],
            }
        }
        mock_client.return_value = client
        columns = self.adapter.list_columns("analytics", "events")
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0].properties["data_type"], "int")


class PolarisAdapterTests(unittest.TestCase):
    """Tests for PolarisAdapter with mocked HTTP responses."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.adapters.polaris_adapter import PolarisAdapter
        cls.adapter = PolarisAdapter(
            host="https://mock-polaris.example.com",
            token="test-token",
            warehouse="default",
        )

    def _mock_response(self, json_data, status_code=200):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = json_data
        mock.raise_for_status = MagicMock()
        return mock

    def test_source_type(self) -> None:
        self.assertEqual(self.adapter.source_type(), "polaris")

    def test_capabilities(self) -> None:
        caps = self.adapter.capabilities()
        self.assertTrue(caps.supports_schemas)
        self.assertTrue(caps.supports_partitions)

    @patch("app.adapters.polaris_adapter.requests.get")
    def test_test_connection(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({"namespaces": []})
        self.assertTrue(self.adapter.test_connection())

    @patch("app.adapters.polaris_adapter.requests.get")
    def test_list_schemas(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({
            "namespaces": [["analytics"], ["raw"]]
        })
        schemas = self.adapter.list_schemas()
        self.assertEqual(len(schemas), 2)
        self.assertEqual(schemas[0].native_name, "analytics")

    @patch("app.adapters.polaris_adapter.requests.get")
    def test_list_tables(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({
            "identifiers": [
                {"namespace": ["analytics"], "name": "events"},
                {"namespace": ["analytics"], "name": "sessions"},
            ]
        })
        tables = self.adapter.list_tables("analytics")
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0].native_name, "events")

    @patch("app.adapters.polaris_adapter.requests.get")
    def test_get_table_detail(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({
            "metadata": {
                "schema": {
                    "fields": [
                        {"id": 1, "name": "id", "type": "int", "required": True},
                        {"id": 2, "name": "name", "type": "string", "required": False},
                    ]
                },
                "partition-spec": [{"name": "dt", "transform": "day"}],
            },
            "metadata-location": "s3://bucket/metadata.json",
        })
        detail = self.adapter.get_table_detail("analytics", "events")
        self.assertEqual(detail.native_name, "events")
        self.assertIn("columns", detail.properties)

    @patch("app.adapters.polaris_adapter.requests.get")
    def test_list_columns(self, mock_get) -> None:
        mock_get.return_value = self._mock_response({
            "metadata": {
                "schema": {
                    "fields": [
                        {"id": 1, "name": "id", "type": "int", "required": True},
                        {"id": 2, "name": "name", "type": "string", "required": False},
                    ]
                },
            },
        })
        columns = self.adapter.list_columns("analytics", "events")
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0].native_name, "id")
        self.assertEqual(columns[0].properties["data_type"], "int")


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
        ]
        detail = self.adapter.get_table_detail("analytics", "events")
        self.assertEqual(detail.native_name, "events")
        self.assertIn("columns", detail.properties)
        self.assertEqual(len(detail.properties["columns"]), 2)
        self.assertEqual(detail.properties["columns"][0]["name"], "id")
        self.assertFalse(detail.properties["columns"][0]["nullable"])
        self.assertTrue(detail.properties["columns"][1]["nullable"])

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail_not_found(self, mock_query) -> None:
        mock_query.return_value = []
        with self.assertRaises(KeyError):
            self.adapter.get_table_detail("analytics", "nonexistent")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_columns(self, mock_query) -> None:
        mock_query.return_value = [
            {"column_name": "id", "data_type": "integer", "ordinal_position": 1, "is_nullable": "NO"},
            {"column_name": "ts", "data_type": "timestamp", "ordinal_position": 2, "is_nullable": "YES"},
        ]
        columns = self.adapter.list_columns("analytics", "events")
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0].native_name, "id")
        self.assertEqual(columns[0].properties["data_type"], "integer")
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


if __name__ == "__main__":
    unittest.main()
