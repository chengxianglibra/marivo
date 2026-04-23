from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.app_factory import create_app
from app.engines import EngineService
from app.mappings import MappingService
from app.routing import QueryRouter
from app.sources import SourceService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore


def _duckdb_source_payload(path: str, display_name: str) -> dict[str, object]:
    return {
        "source_type": "duckdb",
        "display_name": display_name,
        "authority": {
            "catalog_system": "duckdb",
            "connection": {"path": path},
            "synthetic_catalog": "main",
        },
        "sync": {"mode": "none"},
        "policy": {"allow_live_browse": True, "allow_sync": False},
    }


class MappingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.meta_path = Path(self.temp_dir.name) / "test-mappings.meta.sqlite"
        self.db_path = Path(self.temp_dir.name) / "test-mappings.duckdb"
        self.metadata = SQLiteMetadataStore(self.meta_path)
        self.metadata.initialize()
        self.source_service = SourceService(self.metadata)
        self.engine_service = EngineService(self.metadata)
        self.mapping_service = MappingService(self.metadata)
        self.router = QueryRouter(self.metadata, self.engine_service)

        self.source = self.source_service.register_source(
            "duckdb",
            "DuckDB Source",
            authority={"catalog_system": "duckdb", "connection": {"path": str(self.db_path)}},
            sync={"mode": "none"},
            policy={"allow_live_browse": True, "allow_sync": False},
        )
        self.engine = self.engine_service.register_engine(
            "duckdb",
            "DuckDB Engine",
            connection={"path": str(self.db_path)},
        )
        now = "2026-04-23T00:00:00+00:00"
        self.metadata.execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id, fqn,
                authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            )
            VALUES (?, ?, 'table', NULL, ?, NULL, ?, ?, '{}', 'v_seed', ?, ?, ?)
            """,
            [
                "obj_watch_events",
                self.source["source_id"],
                "watch_events",
                "duckdb.analytics.watch_events",
                json.dumps({"catalog": "main", "schema": "analytics", "table": "watch_events"}),
                now,
                now,
                now,
            ],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_mapping_and_route_table(self) -> None:
        mapping = self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            priority=10,
            catalog_mappings=[
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        self.assertEqual(mapping["readiness_status"], "ready")
        route = self.router.resolve_tables(["watch_events"])
        self.assertEqual(route.engine_id, self.engine["engine_id"])
        self.assertEqual(
            route.qualified_names["watch_events"], "duckdb_runtime.analytics.watch_events"
        )
        self.assertEqual(
            route.routing_detail["execution_locators"]["watch_events"]["authority_locator"],
            {"catalog": "main", "schema": "analytics", "table": "watch_events"},
        )

    def test_mapping_incomplete_fails_closed(self) -> None:
        self.mapping_service.create_mapping(
            self.source["source_id"],
            self.engine["engine_id"],
            catalog_mappings=[
                {
                    "authority_catalog": "other_catalog",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

        with self.assertRaisesRegex(ValueError, "mapping_incomplete"):
            self.router.resolve_tables(["watch_events"])

    def test_duplicate_authority_catalog_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate authority_catalog"):
            self.mapping_service.create_mapping(
                self.source["source_id"],
                self.engine["engine_id"],
                catalog_mappings=[
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                        "default_schema": None,
                    },
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_alt",
                        "default_schema": None,
                    },
                ],
            )


class MappingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "mapping-api.duckdb"
        self.meta_path = Path(self.temp_dir.name) / "mapping-api.meta.sqlite"
        self.metadata = SQLiteMetadataStore(self.meta_path)
        self.analytics = DuckDBAnalyticsEngine(self.db_path)
        app = create_app(
            db_path=self.db_path,
            metadata_store=self.metadata,
            analytics_engine=self.analytics,
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_create_and_get_mapping(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json=_duckdb_source_payload(str(self.db_path), "API Source"),
        )
        engine_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "API Engine",
                "connection": {"path": str(self.db_path)},
            },
        )
        resp = self.client.post(
            "/mappings",
            json={
                "source_id": source_resp.json()["source_id"],
                "engine_id": engine_resp.json()["engine_id"],
                "priority": 5,
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                        "default_schema": None,
                    }
                ],
            },
        )
        self.assertEqual(resp.status_code, 200)
        mapping_id = resp.json()["mapping_id"]

        detail = self.client.get(f"/mappings/{mapping_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["mapping_id"], mapping_id)
        self.assertEqual(detail.json()["catalog_mappings"][0]["authority_catalog"], "main")

    def test_bindings_route_removed(self) -> None:
        self.assertEqual(self.client.get("/bindings").status_code, 404)


if __name__ == "__main__":
    unittest.main()
