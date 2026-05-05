from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from fastapi.testclient import TestClient

from app.api.app_factory import create_app
from app.config import MarivoConfig, load_config
from app.datasources import DatasourceService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


def runtime_config_yaml() -> str:
    return (
        "metadata:\n"
        "  engine: sqlite\n"
        "  path: data/marivo.meta.sqlite\n"
        "observability:\n"
        "  log_level: DEBUG\n"
        "  metrics_enabled: false\n"
    )


class LoadConfigTests(unittest.TestCase):
    def test_load_metadata_config(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("metadata:\n  engine: sqlite\n  path: data/marivo.meta.sqlite\n")
            f.flush()
            cfg = load_config(Path(f.name))

        assert cfg.metadata is not None
        self.assertEqual(cfg.metadata.engine, "sqlite")
        self.assertEqual(cfg.metadata.path, "data/marivo.meta.sqlite")

    def test_load_mysql_metadata_config_from_explicit_fields(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(
                "metadata:\n"
                "  engine: mysql\n"
                "  host: db.example\n"
                "  port: 3307\n"
                "  database: marivo\n"
                "  user: marivo\n"
                "  password: secret\n"
                "  connect_timeout: 7\n"
                "  pool_size: 3\n"
                "  ssl: true\n"
            )
            f.flush()
            cfg = load_config(Path(f.name))

        assert cfg.metadata is not None
        mysql_config = cfg.metadata.mysql_connection_config()
        self.assertEqual(cfg.metadata.engine, "mysql")
        self.assertEqual(mysql_config["host"], "db.example")
        self.assertEqual(mysql_config["port"], 3307)
        self.assertEqual(mysql_config["database"], "marivo")
        self.assertEqual(mysql_config["user"], "marivo")
        self.assertEqual(mysql_config["password"], "secret")
        self.assertEqual(mysql_config["connect_timeout"], 7)
        self.assertEqual(mysql_config["pool_size"], 3)
        self.assertTrue(mysql_config["ssl"])

    def test_load_mysql_metadata_config_from_dsn(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(
                "metadata:\n"
                "  engine: mysql\n"
                "  dsn: mysql+pymysql://marivo:secret@db.example:3307/marivo"
                "?connect_timeout=8&pool_size=2&ssl=true\n"
            )
            f.flush()
            cfg = load_config(Path(f.name))

        assert cfg.metadata is not None
        mysql_config = cfg.metadata.mysql_connection_config()
        self.assertEqual(mysql_config["host"], "db.example")
        self.assertEqual(mysql_config["port"], 3307)
        self.assertEqual(mysql_config["database"], "marivo")
        self.assertEqual(mysql_config["user"], "marivo")
        self.assertEqual(mysql_config["password"], "secret")
        self.assertEqual(mysql_config["connect_timeout"], 8)
        self.assertEqual(mysql_config["pool_size"], 2)
        self.assertTrue(mysql_config["ssl"])

    def test_load_rejects_invalid_metadata_combinations(self) -> None:
        cases = [
            "metadata:\n  engine: sqlite\n",
            "metadata:\n  engine: sqlite\n  path: meta.sqlite\n  host: db.example\n",
            "metadata:\n  engine: mysql\n  host: db.example\n  database: marivo\n",
            "metadata:\n  engine: mysql\n  path: meta.sqlite\n  dsn: mysql://u:p@db/marivo\n",
        ]
        for raw_yaml in cases:
            with (
                self.subTest(raw_yaml=raw_yaml),
                tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f,
            ):
                f.write(raw_yaml)
                f.flush()
                with self.assertRaises(Exception):
                    load_config(Path(f.name))

    def test_load_config_validation_error_redacts_mysql_secrets(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(
                "metadata:\n"
                "  engine: mysql\n"
                "  path: meta.sqlite\n"
                "  dsn: mysql://marivo:secret@db.example/marivo\n"
                "  password: secret\n"
            )
            f.flush()

            with self.assertRaises(Exception) as ctx:
                load_config(Path(f.name))

        self.assertNotIn("secret", str(ctx.exception))
        self.assertIn("***", str(ctx.exception))

    def test_load_valid_yaml(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(runtime_config_yaml())
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertIsInstance(cfg, MarivoConfig)
        assert cfg.metadata is not None
        self.assertEqual(cfg.metadata.engine, "sqlite")
        self.assertEqual(cfg.metadata.path, "data/marivo.meta.sqlite")
        self.assertEqual(cfg.observability.log_level, "DEBUG")
        self.assertFalse(cfg.observability.metrics_enabled)

    def test_load_missing_file(self) -> None:
        cfg = load_config(Path("/nonexistent/marivo.yaml"))
        self.assertIsInstance(cfg, MarivoConfig)
        self.assertIsNone(cfg.metadata)

    def test_load_invalid_yaml(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("sources:\n  - name: 123\n    type: [not, a, string]\n")
            f.flush()
            with self.assertRaises(Exception):
                load_config(Path(f.name))

    def test_load_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("")
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertIsInstance(cfg, MarivoConfig)
        self.assertIsNone(cfg.metadata)

    def test_load_rejects_sources_inventory_config(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write('sources:\n  - display_name: "Demo"\n')
            f.flush()
            with self.assertRaises(Exception):
                load_config(Path(f.name))

    def test_load_rejects_engines_inventory_config(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write('engines:\n  - display_name: "Batch Engine"\n')
            f.flush()
            with self.assertRaises(Exception):
                load_config(Path(f.name))

    def test_load_rejects_bindings_inventory_config(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write('bindings:\n  - source: "src"\n')
            f.flush()
            with self.assertRaises(Exception):
                load_config(Path(f.name))

    def test_load_rejects_mappings_inventory_config(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write('mappings:\n  - source_id: "src_1"\n')
            f.flush()
            with self.assertRaises(Exception):
                load_config(Path(f.name))


class EnsureDatasourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(self.temp_dir.name) / "meta.sqlite"
        self.metadata = SQLiteMetadataStore(meta_path)
        self.metadata.initialize()
        self.datasource_service = DatasourceService(self.metadata)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ensure_datasource_creates_new(self) -> None:
        ds = self.datasource_service.ensure_datasource(
            "duckdb", "My DS", {"path": "/tmp/test.duckdb"}
        )
        self.assertEqual(ds["display_name"], "My DS")
        self.assertEqual(ds["datasource_type"], "duckdb")
        self.assertTrue(ds["datasource_id"].startswith("ds_"))

    def test_register_datasource_rejects_unsupported_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported datasource type"):
            self.datasource_service.register_datasource("mysql", "Unsupported DS", {})

    def test_ensure_datasource_idempotent(self) -> None:
        ds1 = self.datasource_service.ensure_datasource(
            "duckdb", "Same Name", {"path": "/tmp/test.duckdb"}
        )
        ds2 = self.datasource_service.ensure_datasource(
            "duckdb", "Same Name", {"path": "/tmp/test.duckdb"}
        )
        self.assertEqual(ds1["datasource_id"], ds2["datasource_id"])
        datasources = self.datasource_service.list_datasources()
        matching = [d for d in datasources if d["display_name"] == "Same Name"]
        self.assertEqual(len(matching), 1)

    def test_ensure_datasource_updates_existing(self) -> None:
        existing = self.datasource_service.register_datasource(
            "duckdb",
            "Local Demo",
            {"path": "/tmp/old.duckdb"},
        )

        updated = self.datasource_service.ensure_datasource(
            "trino",
            "Local Demo",
            {"host": "trino.local"},
        )

        self.assertEqual(updated["datasource_id"], existing["datasource_id"])
        self.assertEqual(updated["datasource_type"], "trino")
        self.assertEqual(updated["connection"]["host"], "trino.local")
        persisted = self.datasource_service.get_datasource(existing["datasource_id"])
        self.assertEqual(persisted["datasource_type"], "trino")

    def test_ensure_datasource_rejects_unsupported_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported datasource type"):
            self.datasource_service.ensure_datasource("mysql", "Unsupported DS", {})

    def test_validate_datasource_reports_invalid_connection(self) -> None:
        ds = self.datasource_service.register_datasource("duckdb", "Unconfigured DuckDB", {})

        validation = self.datasource_service.validate_datasource(ds["datasource_id"])

        self.assertEqual(
            validation,
            {
                "datasource_id": ds["datasource_id"],
                "is_valid": False,
                "readiness_status": "not_ready",
                "failure_code": "datasource_invalid_connection",
            },
        )

    def test_get_datasource_readiness_reports_ready_datasource(self) -> None:
        db_path = Path(self.temp_dir.name) / "ready-ds.duckdb"
        get_seeded_duckdb_path(db_path)
        ds = self.datasource_service.register_datasource(
            "duckdb",
            "Configured DuckDB",
            {"path": str(db_path)},
        )

        readiness = self.datasource_service.get_datasource_readiness(ds["datasource_id"])

        self.assertEqual(
            readiness,
            {
                "datasource_id": ds["datasource_id"],
                "readiness_status": "ready",
                "failure_code": None,
            },
        )


class StartupWithConfigTests(unittest.TestCase):
    class_tmp: ClassVar[tempfile.TemporaryDirectory[str]]
    shared_analytics: ClassVar[DuckDBAnalyticsEngine]

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

    def test_startup_without_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "nonexistent.yaml"
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            with self.assertRaisesRegex(
                RuntimeError,
                f"Config file not found: {config_path}",
            ):
                create_app(
                    metadata_store=metadata,
                    analytics_engine=self.shared_analytics,
                    config_path=config_path,
                )

    def test_startup_requires_metadata_config_when_store_not_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text("observability:\n  log_level: INFO\n")

            with self.assertRaisesRegex(
                RuntimeError,
                r"Marivo config must define metadata.engine=sqlite\|mysql",
            ):
                create_app(
                    db_path=Path(tmp) / "test.duckdb",
                    analytics_engine=self.shared_analytics,
                    config_path=config_path,
                )

    def test_startup_builds_metadata_store_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text("metadata:\n  engine: sqlite\n  path: test.meta.sqlite\n")

            app = create_app(
                db_path=Path(tmp) / "test.duckdb",
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client = TestClient(app)

            self.assertTrue((Path(tmp) / "test.meta.sqlite").exists())
            resp = client.get("/datasources")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), [])
            client.close()

    def test_startup_rejects_inventory_config_in_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text('sources:\n  - display_name: "Config Trino"\n')
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            with self.assertRaises(Exception):
                create_app(
                    metadata_store=metadata,
                    analytics_engine=self.shared_analytics,
                    config_path=config_path,
                )

    def test_startup_does_not_register_sources_or_engines_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text("observability:\n  log_level: DEBUG\n")
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            app = create_app(
                metadata_store=metadata,
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client = TestClient(app)

            self.assertEqual(client.get("/datasources").json(), [])
            client.close()


if __name__ == "__main__":
    unittest.main()
