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

    def _mock_view_property_row(self) -> dict[str, object]:
        return {
            "comment": "OTT user profile view",
            "spark.sql.sources.schema.part.0": (
                '{"type":"struct","fields":['
                '{"name":"buvid","type":"string","nullable":true,"metadata":{"comment":"BUVID"}},'
                '{"name":"predict_age_range","type":"long","nullable":true,"metadata":{"comment":"Predicted age bucket"}},'
                '{"name":"age","type":"string","nullable":false,"metadata":{"comment":"Age label"}},'
                '{"name":"sex","type":"string","nullable":false,"metadata":{"comment":"Gender label"}}'
                "]}"
            ),
            "view.query.out.numcols": "3",
            "view.query.out.col.0": "buvid",
            "view.query.out.col.1": "age",
            "view.query.out.col.2": "sex",
        }

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
            ["Schema"],
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
        mock_query.side_effect = [
            [{"Table": "events"}, {"Table": "users"}],
            [
                {"table_name": "events", "column_count": 5},
                {"table_name": "users", "column_count": 3},
            ],
        ]
        tables = self.adapter.list_tables("analytics")
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0].native_name, "events")
        self.assertEqual(tables[0].properties["table_type"], "BASE TABLE")
        self.assertEqual(tables[0].properties["column_count"], 5)
        self.assertEqual(tables[1].native_name, "users")
        self.assertEqual(tables[1].properties["column_count"], 3)

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_tables_falls_back_to_show_columns_for_counts(self, mock_query) -> None:
        mock_query.side_effect = [
            [{"Table": "events"}, {"Table": "users"}],
            Exception("Not an Iceberg table"),
            [
                {"Column": "id", "Type": "integer", "Extra": "", "Comment": ""},
                {"Column": "name", "Type": "varchar", "Extra": "", "Comment": ""},
            ],
            [{"Column": "user_id", "Type": "bigint", "Extra": "", "Comment": ""}],
        ]
        tables = self.adapter.list_tables("analytics")
        self.assertEqual(tables[0].properties["column_count"], 2)
        self.assertEqual(tables[1].properties["column_count"], 1)

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_tables_empty_schema(self, mock_query) -> None:
        mock_query.return_value = []
        tables = self.adapter.list_tables("missing_schema")
        self.assertEqual(tables, [])
        self.assertEqual(mock_query.call_count, 1)

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail(self, mock_query) -> None:
        mock_query.side_effect = [
            # table existence check
            [{"table_name": "events", "table_type": "TABLE"}],
            # table$properties
            [
                {"key": "comment", "value": "Events table"},
                {"key": "owner", "value": "analytics_team"},
            ],
            # SHOW CREATE TABLE
            [
                {
                    "Create Table": (
                        "CREATE TABLE hive.analytics.events (\n"
                        "   id integer,\n"
                        "   name varchar\n"
                        ")\n"
                        "WITH (format = 'ORC')"
                    )
                }
            ],
            # columns
            [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "ordinal_position": 1,
                    "is_nullable": "NO",
                },
                {
                    "column_name": "name",
                    "data_type": "varchar",
                    "ordinal_position": 2,
                    "is_nullable": "YES",
                },
            ],
            # SHOW COLUMNS for comments
            [
                {"Column": "id", "Type": "integer", "Extra": "", "Comment": "Primary key"},
                {"Column": "name", "Type": "varchar", "Extra": "", "Comment": "User name"},
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
        self.assertEqual(detail.properties["table_properties"]["format"], "ORC")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail_not_found(self, mock_query) -> None:
        mock_query.return_value = []
        with self.assertRaises(KeyError):
            self.adapter.get_table_detail("analytics", "nonexistent")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail_reads_hive_hidden_table_properties(self, mock_query) -> None:
        mock_query.side_effect = [
            [{"table_name": "events", "table_type": "TABLE"}],
            Exception("Column 'key' cannot be resolved"),
            [
                {
                    "comment": "Hive events table",
                    "external_location": "s3://bucket/events",
                    "format": "PARQUET",
                    "partitioned_by": ["ds"],
                }
            ],
            [
                {
                    "Create Table": (
                        "CREATE TABLE hive.analytics.events (\n"
                        "   id integer\n"
                        ")\n"
                        "WITH (format = 'PARQUET', partitioned_by = ARRAY['ds'])"
                    )
                }
            ],
            [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "ordinal_position": 1,
                    "is_nullable": "NO",
                }
            ],
            [{"Column": "id", "Type": "integer", "Extra": "", "Comment": ""}],
        ]

        detail = self.adapter.get_table_detail("analytics", "events")

        self.assertEqual(detail.properties["comment"], "Hive events table")
        self.assertEqual(
            detail.properties["table_properties"]["external_location"], "s3://bucket/events"
        )
        self.assertEqual(detail.properties["table_properties"]["format"], "PARQUET")
        self.assertEqual(detail.properties["table_properties"]["partitioned_by"], ["ds"])
        self.assertIn("raw_table_properties", detail.properties)

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_columns(self, mock_query) -> None:
        mock_query.side_effect = [
            # columns query
            [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "ordinal_position": 1,
                    "is_nullable": "NO",
                },
                {
                    "column_name": "ts",
                    "data_type": "timestamp",
                    "ordinal_position": 2,
                    "is_nullable": "YES",
                },
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
    def test_list_columns_falls_back_to_show_columns(self, mock_query) -> None:
        mock_query.side_effect = [
            Exception("Not an Iceberg table"),
            [
                {"Column": "id", "Type": "integer", "Extra": "", "Comment": "ID column"},
                {"Column": "ts", "Type": "timestamp", "Extra": "", "Comment": "Timestamp"},
            ],
            [
                {"Column": "id", "Type": "integer", "Extra": "", "Comment": "ID column"},
                {"Column": "ts", "Type": "timestamp", "Extra": "", "Comment": "Timestamp"},
            ],
        ]
        columns = self.adapter.list_columns("analytics", "events")
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0].properties["comment"], "ID column")
        self.assertTrue(columns[0].properties["nullable"])

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_stats(self, mock_query) -> None:
        mock_query.return_value = [
            {
                "column_name": "id",
                "data_size": 100,
                "distinct_values_count": 50,
                "nulls_fraction": 0.0,
                "row_count": None,
                "low_value": "1",
                "high_value": "50",
            },
            {
                "column_name": "name",
                "data_size": 500,
                "distinct_values_count": 30,
                "nulls_fraction": 0.1,
                "row_count": None,
                "low_value": None,
                "high_value": None,
            },
            {
                "column_name": None,
                "data_size": None,
                "distinct_values_count": None,
                "nulls_fraction": None,
                "row_count": 1000,
                "low_value": None,
                "high_value": None,
            },
        ]
        stats = self.adapter.get_table_stats("analytics", "events")
        self.assertEqual(stats["row_count"], 1000)
        self.assertIn("id", stats["columns"])
        self.assertEqual(stats["columns"]["id"]["distinct_count"], 50)
        self.assertEqual(stats["columns"]["name"]["nulls_fraction"], 0.1)

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail_expands_view_columns_from_table_properties(self, mock_query) -> None:
        mock_query.side_effect = [
            [{"table_name": "v_profiles", "table_type": "VIEW"}],
            Exception("Column 'key' cannot be resolved"),
            [self._mock_view_property_row()],
            [],
            [
                {
                    "column_name": "buvid",
                    "data_type": "varchar",
                    "ordinal_position": 1,
                    "is_nullable": "YES",
                },
                {
                    "column_name": "predict_age_range",
                    "data_type": "bigint",
                    "ordinal_position": 2,
                    "is_nullable": "YES",
                },
            ],
            [
                {"Column": "buvid", "Type": "varchar", "Extra": "", "Comment": ""},
                {
                    "Column": "predict_age_range",
                    "Type": "bigint",
                    "Extra": "",
                    "Comment": "",
                },
            ],
        ]

        detail = self.adapter.get_table_detail("analytics", "v_profiles")

        self.assertEqual(detail.properties["column_count"], 3)
        self.assertEqual(
            [column["name"] for column in detail.properties["columns"]],
            ["buvid", "age", "sex"],
        )
        self.assertEqual(detail.properties["table_type"], "VIEW")
        self.assertEqual(detail.properties["columns"][1]["type"], "varchar")
        self.assertEqual(detail.properties["columns"][1]["comment"], "Age label")
        self.assertFalse(detail.properties["columns"][2]["nullable"])

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_get_table_detail_corrects_base_table_to_view_from_view_properties(
        self, mock_query
    ) -> None:
        mock_query.side_effect = [
            [{"table_name": "v_profiles", "table_type": "BASE TABLE"}],
            Exception("Column 'key' cannot be resolved"),
            [self._mock_view_property_row()],
            [],
            [
                {
                    "column_name": "buvid",
                    "data_type": "varchar",
                    "ordinal_position": 1,
                    "is_nullable": "YES",
                }
            ],
            [{"Column": "buvid", "Type": "varchar", "Extra": "", "Comment": ""}],
        ]

        detail = self.adapter.get_table_detail("analytics", "v_profiles")

        self.assertEqual(detail.properties["table_type"], "VIEW")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_columns_expands_view_columns_from_table_properties(self, mock_query) -> None:
        mock_query.side_effect = [
            [
                {
                    "column_name": "buvid",
                    "data_type": "varchar",
                    "ordinal_position": 1,
                    "is_nullable": "YES",
                },
                {
                    "column_name": "predict_age_range",
                    "data_type": "bigint",
                    "ordinal_position": 2,
                    "is_nullable": "YES",
                },
            ],
            [
                {"Column": "buvid", "Type": "varchar", "Extra": "", "Comment": ""},
                {
                    "Column": "predict_age_range",
                    "Type": "bigint",
                    "Extra": "",
                    "Comment": "",
                },
            ],
            Exception("Column 'key' cannot be resolved"),
            [self._mock_view_property_row()],
        ]

        columns = self.adapter.list_columns("analytics", "v_profiles")

        self.assertEqual([column.native_name for column in columns], ["buvid", "age", "sex"])
        self.assertEqual(columns[1].properties["data_type"], "varchar")
        self.assertEqual(columns[1].properties["comment"], "Age label")

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_columns_applies_view_schema_metadata_when_names_already_match(
        self, mock_query
    ) -> None:
        mock_query.side_effect = [
            [
                {
                    "column_name": "buvid",
                    "data_type": "varchar",
                    "ordinal_position": 1,
                    "is_nullable": "YES",
                },
                {
                    "column_name": "age",
                    "data_type": "varchar",
                    "ordinal_position": 2,
                    "is_nullable": "YES",
                },
                {
                    "column_name": "sex",
                    "data_type": "varchar",
                    "ordinal_position": 3,
                    "is_nullable": "YES",
                },
            ],
            [
                {"Column": "buvid", "Type": "varchar", "Extra": "", "Comment": ""},
                {"Column": "age", "Type": "varchar", "Extra": "", "Comment": ""},
                {"Column": "sex", "Type": "varchar", "Extra": "", "Comment": ""},
            ],
            Exception("Column 'key' cannot be resolved"),
            [
                {
                    **self._mock_view_property_row(),
                    "view.query.out.col.1": "age",
                    "view.query.out.col.2": "sex",
                }
            ],
        ]

        columns = self.adapter.list_columns("analytics", "v_profiles")

        self.assertEqual([column.native_name for column in columns], ["buvid", "age", "sex"])
        self.assertEqual(columns[1].properties["comment"], "Age label")
        self.assertFalse(columns[1].properties["nullable"])
        self.assertFalse(columns[2].properties["nullable"])

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_preview_table_uses_expanded_view_output_columns(self, mock_query) -> None:
        mock_query.side_effect = [
            Exception("Column 'key' cannot be resolved"),
            [self._mock_view_property_row()],
            [],
            [
                {
                    "column_name": "buvid",
                    "data_type": "varchar",
                    "ordinal_position": 1,
                    "is_nullable": "YES",
                },
                {
                    "column_name": "predict_age_range",
                    "data_type": "bigint",
                    "ordinal_position": 2,
                    "is_nullable": "YES",
                },
            ],
            [
                {"Column": "buvid", "Type": "varchar", "Extra": "", "Comment": ""},
                {
                    "Column": "predict_age_range",
                    "Type": "bigint",
                    "Extra": "",
                    "Comment": "",
                },
            ],
            [{"buvid": "b1", "age": "25-30", "sex": "男"}],
        ]

        preview = self.adapter.preview_table("analytics", "v_profiles", limit=5)

        self.assertEqual(
            [column["name"] for column in preview.columns],
            ["buvid", "age", "sex"],
        )
        self.assertEqual(preview.rows[0]["age"], "25-30")
        self.assertFalse(preview.truncated)

    def test_source_type_registered_in_factory(self) -> None:
        """Verify 'trino' source type is handled by the adapter factory."""
        from app.adapters.trino_adapter import TrinoCatalogAdapter
        from app.sources import _build_adapter

        adapter = _build_adapter(
            "trino",
            {
                "host": "localhost",
                "port": 8080,
                "catalog": "hive",
            },
        )
        self.assertIsInstance(adapter, TrinoCatalogAdapter)

    @patch("app.adapters.trino_adapter.TrinoCatalogAdapter._query")
    def test_list_columns_without_comments(self, mock_query) -> None:
        """Test that list_columns handles missing comments gracefully."""
        mock_query.side_effect = [
            # columns query
            [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "ordinal_position": 1,
                    "is_nullable": "NO",
                },
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
            # key/value hidden table path fails
            Exception("Table not found"),
            # wide hidden table path also fails
            Exception("Table not found"),
            # SHOW CREATE TABLE also fails
            Exception("Table not found"),
            # columns
            [
                {
                    "column_name": "id",
                    "data_type": "integer",
                    "ordinal_position": 1,
                    "is_nullable": "NO",
                },
            ],
            # SHOW COLUMNS fails
            Exception("Permission denied"),
        ]
        detail = self.adapter.get_table_detail("analytics", "events")
        self.assertNotIn("table_properties", detail.properties)
        self.assertNotIn("comment", detail.properties)
        # Column comment should be empty
        self.assertEqual(detail.properties["columns"][0]["comment"], "")


if __name__ == "__main__":
    unittest.main()
