from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.engines import EngineService, _build_analytics_engine
from app.execution.capabilities import build_engine_capability_profile
from app.main import create_app
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class EngineServiceTests(unittest.TestCase):
    """Unit tests for EngineService using SQLiteMetadataStore directly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "test_engines.meta.sqlite"
        cls.metadata = SQLiteMetadataStore(meta_path)
        cls.metadata.initialize()
        cls.service = EngineService(cls.metadata)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_register_and_list_engines(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Test Trino",
            connection={"host": "localhost", "port": 8080, "user": "test", "catalog": "hive", "schema": "default"},
        )
        self.assertTrue(engine["engine_id"].startswith("eng_"))
        self.assertEqual(engine["engine_type"], "trino")
        self.assertEqual(engine["display_name"], "Test Trino")
        self.assertEqual(engine["status"], "active")

        engines = self.service.list_engines()
        self.assertTrue(any(e["engine_id"] == engine["engine_id"] for e in engines))

    def test_get_engine(self) -> None:
        engine = self.service.register_engine(
            engine_type="duckdb",
            display_name="Get Test Engine",
            connection={"path": "/tmp/test.duckdb"},
        )
        fetched = self.service.get_engine(engine["engine_id"])
        self.assertEqual(fetched["engine_id"], engine["engine_id"])
        self.assertEqual(fetched["connection"]["path"], "/tmp/test.duckdb")
        self.assertEqual(fetched["capabilities"]["engine_type"], "duckdb")
        self.assertEqual(fetched["capabilities"]["performance_class"], "embedded")

    def test_get_capability_profile_merges_defaults_and_overrides(self) -> None:
        engine = self.service.register_engine(
            engine_type="trino",
            display_name="Capability Trino",
            connection={"host": "localhost"},
            capabilities={"metadata": {"deployment": "prod"}, "min_staleness_minutes": 15},
        )

        profile = self.service.get_capability_profile(engine["engine_id"])

        self.assertEqual(profile.engine_type, "trino")
        self.assertEqual(profile.performance_class, "distributed")
        self.assertEqual(profile.min_staleness_minutes, 15)
        self.assertEqual(profile.metadata["deployment"], "prod")

    def test_get_engine_404(self) -> None:
        with self.assertRaises(KeyError):
            self.service.get_engine("eng_nonexistent")

    def test_ensure_engine_idempotent(self) -> None:
        e1 = self.service.ensure_engine(
            engine_type="duckdb",
            display_name="Idempotent Engine",
            connection={"path": "/tmp/idem.duckdb"},
        )
        e2 = self.service.ensure_engine(
            engine_type="duckdb",
            display_name="Idempotent Engine",
            connection={"path": "/tmp/idem.duckdb"},
        )
        self.assertEqual(e1["engine_id"], e2["engine_id"])

    def test_build_duckdb_engine(self) -> None:
        engine = self.service.register_engine(
            engine_type="duckdb",
            display_name="Build Test DuckDB",
            connection={"path": str(Path(self.temp_dir.name) / "build_test.duckdb")},
        )
        analytics = self.service.build_analytics_engine(engine["engine_id"])
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        self.assertIsInstance(analytics, DuckDBAnalyticsEngine)


class EngineAPITests(unittest.TestCase):
    """Integration tests for engine endpoints via TestClient."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_engine_api.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_post_and_get_engine(self) -> None:
        resp = self.client.post(
            "/engines",
            json={
                "engine_type": "trino",
                "display_name": "API Trino",
                "connection": {"host": "localhost", "port": 8080, "user": "test", "catalog": "hive", "schema": "default"},
            },
        )
        self.assertEqual(resp.status_code, 200)
        engine = resp.json()
        self.assertTrue(engine["engine_id"].startswith("eng_"))
        self.assertEqual(engine["engine_type"], "trino")

        resp = self.client.get(f"/engines/{engine['engine_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["engine_id"], engine["engine_id"])

    def test_list_engines(self) -> None:
        self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "API DuckDB",
                "connection": {"path": "/tmp/api.duckdb"},
            },
        )
        resp = self.client.get("/engines")
        self.assertEqual(resp.status_code, 200)
        engines = resp.json()
        self.assertIsInstance(engines, list)
        self.assertTrue(any(e["display_name"] == "API DuckDB" for e in engines))

    def test_get_engine_404(self) -> None:
        resp = self.client.get("/engines/eng_nonexistent")
        self.assertEqual(resp.status_code, 404)


class TrinoAnalyticsEngineTests(unittest.TestCase):
    """Unit tests for TrinoAnalyticsEngine using mocks."""

    def test_init_stores_config(self) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine
        engine = TrinoAnalyticsEngine(host="trino.example.com", port=8443, user="alice", catalog="iceberg", schema="prod")
        self.assertEqual(engine.host, "trino.example.com")
        self.assertEqual(engine.port, 8443)
        self.assertEqual(engine.user, "alice")
        self.assertEqual(engine.catalog, "iceberg")
        self.assertEqual(engine.schema, "prod")

    @patch("app.storage.trino_analytics.TrinoAnalyticsEngine._connect")
    def test_initialize_validates_connectivity(self, mock_connect: MagicMock) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoAnalyticsEngine(host="localhost", port=8080)
        engine.initialize()

        mock_cursor.execute.assert_called_once_with("SELECT 1")
        mock_conn.close.assert_called_once()

    @patch("app.storage.trino_analytics.TrinoAnalyticsEngine._connect")
    def test_query_rows(self, mock_connect: MagicMock) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchall.return_value = [(1, "alice"), (2, "bob")]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoAnalyticsEngine(host="localhost")
        rows = engine.query_rows("SELECT id, name FROM users")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"id": 1, "name": "alice"})
        self.assertEqual(rows[1], {"id": 2, "name": "bob"})
        mock_conn.close.assert_called_once()

    @patch("app.storage.trino_analytics.TrinoAnalyticsEngine._connect")
    def test_table_exists(self, mock_connect: MagicMock) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoAnalyticsEngine(host="localhost", catalog="hive", schema="default")
        result = engine.table_exists("my_table")

        self.assertTrue(result)
        mock_cursor.execute.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch("app.storage.trino_analytics.TrinoAnalyticsEngine._connect")
    def test_table_row_count(self, mock_connect: MagicMock) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (42,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoAnalyticsEngine(host="localhost")
        count = engine.table_row_count("my_table")

        self.assertEqual(count, 42)
        mock_conn.close.assert_called_once()


class BuildAnalyticsEngineTests(unittest.TestCase):
    """Tests for the _build_analytics_engine factory function."""

    def test_build_duckdb(self) -> None:
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        engine = _build_analytics_engine("duckdb", {"path": "/tmp/test.duckdb"})
        self.assertIsInstance(engine, DuckDBAnalyticsEngine)

    def test_build_trino(self) -> None:
        from app.storage.trino_analytics import TrinoAnalyticsEngine
        engine = _build_analytics_engine("trino", {
            "host": "localhost",
            "port": 8080,
            "user": "test",
            "catalog": "hive",
            "schema": "default",
        })
        self.assertIsInstance(engine, TrinoAnalyticsEngine)

    def test_build_unsupported(self) -> None:
        with self.assertRaises(ValueError):
            _build_analytics_engine("spark", {})


class CapabilityProfileTests(unittest.TestCase):
    def test_build_engine_capability_profile_defaults_duckdb(self) -> None:
        profile = build_engine_capability_profile("duckdb")
        self.assertEqual(profile.performance_class, "embedded")
        self.assertIn("temporary_tables", profile.supported_sql_features)


class SparkConnectAnalyticsEngineTests(unittest.TestCase):
    """Unit tests for SparkConnectAnalyticsEngine using mocks."""

    def test_init_stores_config(self) -> None:
        from app.storage.spark_connect_analytics import SparkConnectAnalyticsEngine
        engine = SparkConnectAnalyticsEngine(remote="sc://spark-host:15002")
        self.assertEqual(engine.remote, "sc://spark-host:15002")

    @patch("app.storage.spark_connect_analytics.SparkConnectAnalyticsEngine._connect")
    def test_query_rows(self, mock_connect: MagicMock) -> None:
        from app.storage.spark_connect_analytics import SparkConnectAnalyticsEngine
        mock_spark = MagicMock()
        mock_df = MagicMock()
        mock_df.columns = ["id", "name"]
        mock_df.collect.return_value = [MagicMock(**{"__iter__": lambda s: iter([1, "alice"])}), MagicMock(**{"__iter__": lambda s: iter([2, "bob"])})]
        # Use Row-like tuples
        row1 = (1, "alice")
        row2 = (2, "bob")
        mock_df.collect.return_value = [row1, row2]
        mock_spark.sql.return_value = mock_df
        mock_connect.return_value = mock_spark

        engine = SparkConnectAnalyticsEngine(remote="sc://localhost:15002")
        rows = engine.query_rows("SELECT id, name FROM users")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"id": 1, "name": "alice"})
        self.assertEqual(rows[1], {"id": 2, "name": "bob"})

    @patch("app.storage.spark_connect_analytics.SparkConnectAnalyticsEngine._connect")
    def test_table_exists(self, mock_connect: MagicMock) -> None:
        from app.storage.spark_connect_analytics import SparkConnectAnalyticsEngine
        mock_spark = MagicMock()
        mock_spark.catalog.tableExists.return_value = True
        mock_connect.return_value = mock_spark

        engine = SparkConnectAnalyticsEngine(remote="sc://localhost:15002")
        self.assertTrue(engine.table_exists("my_table"))
        mock_spark.catalog.tableExists.assert_called_once_with("my_table")

    @patch("app.storage.spark_connect_analytics.SparkConnectAnalyticsEngine._connect")
    def test_table_row_count(self, mock_connect: MagicMock) -> None:
        from app.storage.spark_connect_analytics import SparkConnectAnalyticsEngine
        mock_spark = MagicMock()
        mock_df = MagicMock()
        mock_df.columns = ["cnt"]
        mock_df.collect.return_value = [(42,)]
        mock_spark.sql.return_value = mock_df
        mock_connect.return_value = mock_spark

        engine = SparkConnectAnalyticsEngine(remote="sc://localhost:15002")
        count = engine.table_row_count("my_table")
        self.assertEqual(count, 42)


class SparkThriftAnalyticsEngineTests(unittest.TestCase):
    """Unit tests for SparkThriftAnalyticsEngine using mocks."""

    def test_init_stores_config(self) -> None:
        from app.storage.spark_thrift_analytics import SparkThriftAnalyticsEngine
        engine = SparkThriftAnalyticsEngine(
            host="kyuubi.example.com",
            port=10009,
            username="alice",
            database="analytics",
            auth="LDAP",
        )
        self.assertEqual(engine.host, "kyuubi.example.com")
        self.assertEqual(engine.port, 10009)
        self.assertEqual(engine.username, "alice")
        self.assertEqual(engine.database, "analytics")
        self.assertEqual(engine.auth, "LDAP")

    @patch("app.storage.spark_thrift_analytics.SparkThriftAnalyticsEngine._connect")
    def test_initialize_validates_connectivity(self, mock_connect: MagicMock) -> None:
        from app.storage.spark_thrift_analytics import SparkThriftAnalyticsEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = SparkThriftAnalyticsEngine(host="localhost")
        engine.initialize()

        mock_cursor.execute.assert_called_once_with("SELECT 1")
        mock_conn.close.assert_called_once()

    @patch("app.storage.spark_thrift_analytics.SparkThriftAnalyticsEngine._connect")
    def test_query_rows(self, mock_connect: MagicMock) -> None:
        from app.storage.spark_thrift_analytics import SparkThriftAnalyticsEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchall.return_value = [(1, "alice"), (2, "bob")]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = SparkThriftAnalyticsEngine(host="localhost")
        rows = engine.query_rows("SELECT id, name FROM users")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"id": 1, "name": "alice"})
        self.assertEqual(rows[1], {"id": 2, "name": "bob"})
        mock_conn.close.assert_called_once()

    @patch("app.storage.spark_thrift_analytics.SparkThriftAnalyticsEngine._connect")
    def test_table_exists(self, mock_connect: MagicMock) -> None:
        from app.storage.spark_thrift_analytics import SparkThriftAnalyticsEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("my_table",)]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = SparkThriftAnalyticsEngine(host="localhost")
        self.assertTrue(engine.table_exists("my_table"))
        mock_conn.close.assert_called_once()

    @patch("app.storage.spark_thrift_analytics.SparkThriftAnalyticsEngine._connect")
    def test_table_row_count(self, mock_connect: MagicMock) -> None:
        from app.storage.spark_thrift_analytics import SparkThriftAnalyticsEngine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (42,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = SparkThriftAnalyticsEngine(host="localhost")
        count = engine.table_row_count("my_table")
        self.assertEqual(count, 42)
        mock_conn.close.assert_called_once()


class BuildSparkEngineTests(unittest.TestCase):
    """Tests for Spark engine types in the _build_analytics_engine factory."""

    def test_build_spark_connect(self) -> None:
        from app.storage.spark_connect_analytics import SparkConnectAnalyticsEngine
        engine = _build_analytics_engine("spark_connect", {"remote": "sc://localhost:15002"})
        self.assertIsInstance(engine, SparkConnectAnalyticsEngine)

    def test_build_spark_thrift(self) -> None:
        from app.storage.spark_thrift_analytics import SparkThriftAnalyticsEngine
        engine = _build_analytics_engine("spark_thrift", {
            "host": "localhost",
            "port": 10009,
            "username": "factum",
            "database": "default",
            "auth": "NOSASL",
        })
        self.assertIsInstance(engine, SparkThriftAnalyticsEngine)


class EngineConfigTests(unittest.TestCase):
    """Tests for YAML config loading with engines."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.class_tmp = tempfile.TemporaryDirectory()
        duck_path = Path(cls.class_tmp.name) / "shared.duckdb"
        get_seeded_duckdb_path(duck_path)
        cls.shared_analytics = DuckDBAnalyticsEngine(duck_path)
        cls.shared_analytics.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.class_tmp.cleanup()

    def test_load_config_with_engines(self) -> None:
        from app.config import load_config
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(textwrap.dedent("""\
                sources:
                  - name: "Demo"
                    type: local
                    connection: {}
                engines:
                  - name: "My DuckDB"
                    type: duckdb
                    connection:
                      path: data/test.duckdb
                  - name: "My Trino"
                    type: trino
                    connection:
                      host: trino.local
                      port: 8080
            """))
            f.flush()
            config = load_config(Path(f.name))

        self.assertEqual(len(config.sources), 1)
        self.assertEqual(len(config.engines), 2)
        self.assertEqual(config.engines[0].name, "My DuckDB")
        self.assertEqual(config.engines[0].type, "duckdb")
        self.assertEqual(config.engines[1].name, "My Trino")
        self.assertEqual(config.engines[1].connection["host"], "trino.local")

    def test_startup_registers_engines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "factum.yaml"
            config_path.write_text(textwrap.dedent("""\
                engines:
                  - name: "Startup DuckDB"
                    type: duckdb
                    connection:
                      path: data/startup.duckdb
            """))
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            client = TestClient(create_app(
                metadata_store=metadata,
                analytics_engine=self.shared_analytics,
                config_path=str(config_path),
            ))

            resp = client.get("/engines")
            self.assertEqual(resp.status_code, 200)
            engines = resp.json()
            self.assertTrue(any(e["display_name"] == "Startup DuckDB" for e in engines))

            client.close()


if __name__ == "__main__":
    unittest.main()
