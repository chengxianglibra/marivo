from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import FactumConfig, SyncConfig, UIConfig, load_config
from app.main import create_app
from app.sources import SourceService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class LoadConfigTests(unittest.TestCase):
    def test_load_valid_yaml(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(
                "sources:\n"
                '  - name: "Demo"\n'
                "    type: local\n"
                '  - name: "Prod Hive"\n'
                "    type: hive_metastore\n"
                "    connection:\n"
                "      host: hive.internal\n"
                "      port: 9083\n"
            )
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertIsInstance(cfg, FactumConfig)
        self.assertEqual(len(cfg.sources), 2)
        self.assertEqual(cfg.sources[0].name, "Demo")
        self.assertEqual(cfg.sources[0].type, "local")
        self.assertEqual(cfg.sources[0].connection, {})
        self.assertEqual(cfg.sources[1].name, "Prod Hive")
        self.assertEqual(cfg.sources[1].connection["host"], "hive.internal")
        self.assertEqual(cfg.sources[1].connection["port"], 9083)

    def test_load_missing_file(self) -> None:
        cfg = load_config(Path("/nonexistent/factum.yaml"))
        self.assertIsInstance(cfg, FactumConfig)
        self.assertEqual(cfg.sources, [])

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

        self.assertIsInstance(cfg, FactumConfig)
        self.assertEqual(cfg.sources, [])

    def test_sync_mode_by_select_parses(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(
                "sources:\n"
                '  - name: "Prod Hive"\n'
                "    type: hive_metastore\n"
                "    connection:\n"
                "      host: hive.internal\n"
                "    sync:\n"
                "      mode: by_select\n"
            )
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertEqual(len(cfg.sources), 1)
        self.assertEqual(cfg.sources[0].sync.mode, "by_select")

    def test_ui_enabled_parses(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("ui:\n  enabled: true\n")
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertIsInstance(cfg.ui, UIConfig)
        self.assertTrue(cfg.ui.enabled)

    def test_ui_defaults_to_disabled(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("sources: []\n")
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertIsInstance(cfg.ui, UIConfig)
        self.assertFalse(cfg.ui.enabled)

    def test_sync_mode_defaults_to_all(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(
                "sources:\n"
                '  - name: "Demo"\n'
                "    type: local\n"
            )
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertEqual(len(cfg.sources), 1)
        self.assertEqual(cfg.sources[0].sync.mode, "all")


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
        source = self.source_service.ensure_source("local", "My Source", {})
        self.assertEqual(source["display_name"], "My Source")
        self.assertEqual(source["source_type"], "local")
        self.assertTrue(source["source_id"].startswith("src_"))

    def test_ensure_source_idempotent(self) -> None:
        s1 = self.source_service.ensure_source("local", "Same Name", {})
        s2 = self.source_service.ensure_source("local", "Same Name", {})
        self.assertEqual(s1["source_id"], s2["source_id"])
        sources = self.source_service.list_sources()
        matching = [s for s in sources if s["display_name"] == "Same Name"]
        self.assertEqual(len(matching), 1)


class StartupWithConfigTests(unittest.TestCase):
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

    def test_startup_registers_and_syncs_config_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "factum.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "Config Demo"\n'
                "    type: local\n"
                "    connection:\n"
                f"      path: {Path(self.class_tmp.name) / 'shared.duckdb'}\n"
            )
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            app = create_app(
                metadata_store=metadata,
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client = TestClient(app)

            resp = client.get("/sources")
            self.assertEqual(resp.status_code, 200)
            sources = resp.json()
            names = [s["display_name"] for s in sources]
            self.assertIn("Config Demo", names)

            source_id = next(s["source_id"] for s in sources if s["display_name"] == "Config Demo")
            resp = client.get(f"/sources/{source_id}/objects?type=table")
            self.assertEqual(resp.status_code, 200)
            tables = resp.json()
            self.assertGreater(len(tables), 0)

            client.close()

    def test_startup_without_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "nonexistent.yaml"
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            app = create_app(
                metadata_store=metadata,
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client = TestClient(app)

            resp = client.get("/sources")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), [])

            client.close()

    def test_startup_idempotent_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "factum.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "Restart Test"\n'
                "    type: local\n"
                "    connection:\n"
                f"      path: {Path(self.class_tmp.name) / 'shared.duckdb'}\n"
            )
            meta_path = Path(tmp) / "test.meta.sqlite"

            # First "boot" — shared analytics, fresh metadata
            metadata1 = SQLiteMetadataStore(meta_path)
            app1 = create_app(
                metadata_store=metadata1,
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client1 = TestClient(app1)
            resp1 = client1.get("/sources")
            sources1 = resp1.json()
            client1.close()

            # Second "boot" — same metadata DB file, same config
            metadata2 = SQLiteMetadataStore(meta_path)
            app2 = create_app(
                metadata_store=metadata2,
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client2 = TestClient(app2)
            resp2 = client2.get("/sources")
            sources2 = resp2.json()
            client2.close()

            restart_sources = [s for s in sources2 if s["display_name"] == "Restart Test"]
            self.assertEqual(len(restart_sources), 1)
            self.assertEqual(sources1[0]["source_id"], restart_sources[0]["source_id"])

    def test_startup_sync_mode_none_skips_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "factum.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "No Sync Source"\n'
                "    type: local\n"
                "    connection:\n"
                f"      path: {Path(self.class_tmp.name) / 'shared.duckdb'}\n"
                "    sync:\n"
                "      mode: none\n"
            )
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            app = create_app(
                metadata_store=metadata,
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client = TestClient(app)

            resp = client.get("/sources")
            sources = resp.json()
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["display_name"], "No Sync Source")
            self.assertEqual(sources[0]["sync_mode"], "none")

            # No objects should have been synced
            source_id = sources[0]["source_id"]
            resp = client.get(f"/sources/{source_id}/objects")
            self.assertEqual(resp.json(), [])
            client.close()

    def test_startup_sync_mode_by_select_no_selections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "factum.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "Selective Source"\n'
                "    type: local\n"
                "    connection:\n"
                f"      path: {Path(self.class_tmp.name) / 'shared.duckdb'}\n"
                "    sync:\n"
                "      mode: by_select\n"
            )
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            app = create_app(
                metadata_store=metadata,
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client = TestClient(app)

            resp = client.get("/sources")
            sources = resp.json()
            self.assertEqual(sources[0]["sync_mode"], "by_select")

            # No objects synced since no selections exist yet
            source_id = sources[0]["source_id"]
            resp = client.get(f"/sources/{source_id}/objects")
            self.assertEqual(resp.json(), [])
            client.close()


if __name__ == "__main__":
    unittest.main()
