from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from fastapi.testclient import TestClient

from app.api.app_factory import create_app
from app.config import MarivoConfig, load_config
from app.sources import SourceService
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

    def test_load_defaults_governance_when_ui_block_absent(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("observability:\n  log_level: INFO\n")
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertTrue(cfg.governance.enabled)


class EnsureSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(self.temp_dir.name) / "meta.sqlite"
        self.metadata = SQLiteMetadataStore(meta_path)
        self.metadata.initialize()
        self.source_service = SourceService(self.metadata)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ensure_source_creates_new(self) -> None:
        source = self.source_service.ensure_source(
            "duckdb", "My Source", {"synthetic_catalog": "main"}
        )
        self.assertEqual(source["display_name"], "My Source")
        self.assertEqual(source["source_type"], "duckdb")
        self.assertTrue(source["source_id"].startswith("src_"))
        self.assertEqual(source["readiness_status"], "not_ready")
        self.assertEqual(source["failure_code"], "source_invalid_connection")

    def test_register_source_rejects_unsupported_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported source type"):
            self.source_service.register_source("mysql", "Unsupported Source", {})

    def test_ensure_source_idempotent(self) -> None:
        s1 = self.source_service.ensure_source("duckdb", "Same Name", {"synthetic_catalog": "main"})
        s2 = self.source_service.ensure_source("duckdb", "Same Name", {"synthetic_catalog": "main"})
        self.assertEqual(s1["source_id"], s2["source_id"])
        sources = self.source_service.list_sources()
        matching = [s for s in sources if s["display_name"] == "Same Name"]
        self.assertEqual(len(matching), 1)

    def test_ensure_source_updates_existing_source_type(self) -> None:
        existing = self.source_service.register_source(
            "duckdb",
            "Local Demo",
            {"path": "/tmp/old.duckdb", "synthetic_catalog": "main"},
        )

        updated = self.source_service.ensure_source(
            "trino",
            "Local Demo",
            {"catalog_system": "trino", "connection": {"host": "trino.local"}},
            sync={"mode": "selected"},
        )

        self.assertEqual(updated["source_id"], existing["source_id"])
        self.assertEqual(updated["source_type"], "trino")
        self.assertEqual(updated["authority"]["connection"]["host"], "trino.local")
        self.assertEqual(updated["sync"]["mode"], "selected")
        persisted = self.source_service.get_source(existing["source_id"])
        self.assertEqual(persisted["source_type"], "trino")

    def test_update_source_rejects_synthetic_catalog_rewrite(self) -> None:
        source = self.source_service.register_source(
            "duckdb", "Immutable Catalog", {"synthetic_catalog": "main"}
        )

        with self.assertRaisesRegex(ValueError, "synthetic_catalog is immutable"):
            self.source_service.update_source(
                source["source_id"],
                authority={
                    "catalog_system": "duckdb",
                    "connection": {"path": "/tmp/new.duckdb"},
                    "synthetic_catalog": "alt",
                },
            )

    def test_ensure_source_rejects_unsupported_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported source type"):
            self.source_service.ensure_source("mysql", "Unsupported Source", {})

    def test_validate_source_reports_invalid_connection(self) -> None:
        source = self.source_service.register_source(
            "duckdb", "Unconfigured DuckDB", {"synthetic_catalog": "main"}
        )

        validation = self.source_service.validate_source(source["source_id"])

        self.assertEqual(
            validation,
            {
                "source_id": source["source_id"],
                "is_valid": False,
                "readiness_status": "not_ready",
                "failure_code": "source_invalid_connection",
            },
        )

    def test_get_source_readiness_reports_ready_source(self) -> None:
        db_path = Path(self.temp_dir.name) / "ready-source.duckdb"
        get_seeded_duckdb_path(db_path)
        source = self.source_service.register_source(
            "duckdb",
            "Configured DuckDB",
            {
                "catalog_system": "duckdb",
                "connection": {"path": str(db_path)},
                "synthetic_catalog": "main",
            },
        )

        readiness = self.source_service.get_source_readiness(source["source_id"])

        self.assertEqual(
            readiness,
            {
                "source_id": source["source_id"],
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
                "Marivo config must define metadata.engine=sqlite and metadata.path",
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
            resp = client.get("/sources")
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

            self.assertEqual(client.get("/sources").json(), [])
            self.assertEqual(client.get("/engines").json(), [])
            self.assertEqual(client.get("/mappings").json(), [])
            client.close()


if __name__ == "__main__":
    unittest.main()
