from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.app_factory import create_app
from app.config import MarivoConfig, UIConfig, load_config
from app.sources import SourceService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


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
            f.write('sources:\n  - name: "Demo"\n    type: duckdb\n')
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertIsInstance(cfg, MarivoConfig)
        self.assertEqual(len(cfg.sources), 1)
        self.assertEqual(cfg.sources[0].name, "Demo")
        self.assertEqual(cfg.sources[0].type, "duckdb")
        self.assertEqual(cfg.sources[0].connection, {})

    def test_load_missing_file(self) -> None:
        cfg = load_config(Path("/nonexistent/marivo.yaml"))
        self.assertIsInstance(cfg, MarivoConfig)
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

        self.assertIsInstance(cfg, MarivoConfig)
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

    def test_sync_mode_defaults_to_by_select(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write('sources:\n  - name: "Demo"\n    type: duckdb\n')
            f.flush()
            cfg = load_config(Path(f.name))

        self.assertEqual(len(cfg.sources), 1)
        self.assertEqual(cfg.sources[0].sync.mode, "by_select")


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
        source = self.source_service.ensure_source("duckdb", "My Source", {})
        self.assertEqual(source["display_name"], "My Source")
        self.assertEqual(source["source_type"], "duckdb")
        self.assertTrue(source["source_id"].startswith("src_"))

    def test_ensure_source_idempotent(self) -> None:
        s1 = self.source_service.ensure_source("duckdb", "Same Name", {})
        s2 = self.source_service.ensure_source("duckdb", "Same Name", {})
        self.assertEqual(s1["source_id"], s2["source_id"])
        sources = self.source_service.list_sources()
        matching = [s for s in sources if s["display_name"] == "Same Name"]
        self.assertEqual(len(matching), 1)

    def test_ensure_source_updates_existing_source_type(self) -> None:
        existing = self.source_service.register_source(
            "local", "Local Demo", {"path": "/tmp/old.duckdb"}
        )

        updated = self.source_service.ensure_source(
            "duckdb",
            "Local Demo",
            {"path": "/tmp/new.duckdb"},
            sync_mode="by_select",
        )

        self.assertEqual(updated["source_id"], existing["source_id"])
        self.assertEqual(updated["source_type"], "duckdb")
        self.assertEqual(updated["connection"]["path"], "/tmp/new.duckdb")
        self.assertEqual(updated["sync_mode"], "by_select")
        persisted = self.source_service.get_source(existing["source_id"])
        self.assertEqual(persisted["source_type"], "duckdb")


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

    def test_startup_registers_and_syncs_config_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "Config Demo"\n'
                "    type: duckdb\n"
                "    sync:\n"
                "      mode: by_select\n"
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

            # Add sync selections and trigger sync
            client.post(
                f"/sources/{source_id}/sync/selections",
                json={
                    "selections": [
                        {"schema_name": "analytics", "table_name": "watch_events"},
                    ]
                },
            )
            client.post(f"/sources/{source_id}/sync")

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

    def test_startup_requires_metadata_config_when_store_not_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text("ui:\n  enabled: true\n")

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
            config_path.write_text(
                "metadata:\n  engine: sqlite\n  path: test.meta.sqlite\nui:\n  enabled: true\n"
            )

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

    def test_startup_requires_trino_dependency_when_config_uses_trino_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "Config Trino"\n'
                "    type: trino\n"
                "    connection:\n"
                "      host: trino.local\n"
            )
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            with patch("app.api.app_factory.importlib.import_module") as mock_import:
                mock_import.side_effect = ModuleNotFoundError("No module named 'trino'")
                with self.assertRaisesRegex(RuntimeError, "optional dependency 'trino'"):
                    create_app(
                        metadata_store=metadata,
                        analytics_engine=self.shared_analytics,
                        config_path=config_path,
                    )

                mock_import.assert_called_once_with("trino")

    def test_startup_requires_trino_dependency_when_config_uses_trino_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                "engines:\n"
                '  - name: "Config Trino Engine"\n'
                "    type: trino\n"
                "    connection:\n"
                "      host: trino.local\n"
            )
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            with patch("app.api.app_factory.importlib.import_module") as mock_import:
                mock_import.side_effect = ModuleNotFoundError("No module named 'trino'")
                with self.assertRaisesRegex(RuntimeError, "optional dependency 'trino'"):
                    create_app(
                        metadata_store=metadata,
                        analytics_engine=self.shared_analytics,
                        config_path=config_path,
                    )

                mock_import.assert_called_once_with("trino")

    def test_startup_idempotent_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "Restart Test"\n'
                "    type: duckdb\n"
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

    def test_startup_reconciles_existing_source_type_with_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "Local Demo"\n'
                "    type: duckdb\n"
                "    sync:\n"
                "      mode: by_select\n"
                "    connection:\n"
                f"      path: {Path(self.class_tmp.name) / 'shared.duckdb'}\n"
            )
            meta_path = Path(tmp) / "test.meta.sqlite"
            metadata = SQLiteMetadataStore(meta_path)
            metadata.initialize()
            metadata.execute(
                """
                INSERT INTO sources (
                    source_id, source_type, display_name, connection_json, capabilities_json, sync_mode, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', datetime('now'), datetime('now'))
                """,
                [
                    "src_existingdemo",
                    "local",
                    "Local Demo",
                    '{"path": "/tmp/old.duckdb"}',
                    "{}",
                    "by_select",
                ],
            )

            app = create_app(
                metadata_store=metadata,
                analytics_engine=self.shared_analytics,
                config_path=config_path,
            )
            client = TestClient(app)

            resp = client.get("/sources")
            self.assertEqual(resp.status_code, 200)
            sources = resp.json()
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["source_id"], "src_existingdemo")
            self.assertEqual(sources[0]["source_type"], "duckdb")
            self.assertEqual(
                sources[0]["connection"]["path"], str(Path(self.class_tmp.name) / "shared.duckdb")
            )

            # Add sync selections and trigger sync to verify source is configured correctly
            client.post(
                "/sources/src_existingdemo/sync/selections",
                json={
                    "selections": [
                        {"schema_name": "analytics", "table_name": "watch_events"},
                    ]
                },
            )
            client.post("/sources/src_existingdemo/sync")

            resp = client.get("/sources/src_existingdemo/objects?type=table")
            self.assertEqual(resp.status_code, 200)
            self.assertGreater(len(resp.json()), 0)

            client.close()

    def test_startup_sync_mode_none_skips_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "No Sync Source"\n'
                "    type: duckdb\n"
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
            config_path = Path(tmp) / "marivo.yaml"
            config_path.write_text(
                "sources:\n"
                '  - name: "Selective Source"\n'
                "    type: duckdb\n"
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
