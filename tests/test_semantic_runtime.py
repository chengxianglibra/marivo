from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.semantic_runtime import CatalogRuntimeService
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "semantic_runtime.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        cls.metadata_store = cls.client.app.state.metadata_store
        cls.binding_service = cls.client.app.state.binding_service

        entity = cls.client.post(
            "/semantic/entities",
            json={"name": "user", "display_name": "User", "description": "A platform user", "keys": ["user_id"]},
        ).json()
        cls.client.post(f"/semantic/entities/{entity['entity_id']}/publish")
        cls.entity_id = entity["entity_id"]

        metric = cls.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "description": "Average play duration per session",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
            },
        ).json()
        cls.client.post(f"/semantic/metrics/{metric['metric_id']}/publish")
        cls.metric_id = metric["metric_id"]

        source = cls.client.post(
            "/sources",
            json={"source_type": "local", "display_name": "Semantic Runtime Source", "connection": {"path": str(db_path)}},
        ).json()
        cls.source_id = source["source_id"]
        cls.client.post(f"/sources/{cls.source_id}/sync")

        table_objects = {
            table["native_name"]: table
            for table in cls.client.get(f"/sources/{cls.source_id}/objects?type=table").json()
        }
        cls.watch_events_object_id = table_objects["watch_events"]["object_id"]

        cls.client.post(
            "/semantic/mappings",
            json={
                "semantic_type": "metric",
                "semantic_id": cls.metric_id,
                "object_id": cls.watch_events_object_id,
                "mapping_type": "primary_source",
            },
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_semantic_resolver_resolves_published_metric(self) -> None:
        service = self.client.app.state.service

        resolved = service.semantic_resolver.resolve_metric("watch_time")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.definition_sql, "avg(play_duration_seconds)")
        self.assertEqual(
            resolved.dimensions,
            ["platform", "app_version", "network_type", "content_type"],
        )
        self.assertEqual(resolved.metadata["display_name"], "Watch Time")

    def test_planner_context_provider_includes_session_details(self) -> None:
        service = self.client.app.state.service
        session = service.create_session("Semantic runtime test", {}, {}, {})

        context = service.planner_context_provider.build_planner_context(session["session_id"])

        self.assertIn("session", context)
        self.assertEqual(context["session"]["session_id"], session["session_id"])
        self.assertEqual(context["session"]["goal"], "Semantic runtime test")
        self.assertTrue(any(metric["name"] == "watch_time" for metric in context["metrics"]))

    def test_catalog_runtime_search_finds_published_metric(self) -> None:
        runtime = CatalogRuntimeService(self.metadata_store, self.binding_service)

        results = runtime.search("watch", object_type="metric")

        self.assertTrue(any(result["name"] == "watch_time" for result in results))

    def test_catalog_runtime_resolve_returns_assets_and_mappings(self) -> None:
        runtime = CatalogRuntimeService(self.metadata_store, self.binding_service)

        resolved = runtime.resolve("watch_time")

        self.assertEqual(resolved["resolved_type"], "metric")
        self.assertEqual(resolved["semantic_object"]["name"], "watch_time")
        self.assertEqual(resolved["physical_assets"][0]["native_name"], "watch_events")
        self.assertEqual(resolved["mappings"][0]["semantic_id"], self.metric_id)

    def test_catalog_runtime_planner_context_formats_runtime_payload(self) -> None:
        runtime = CatalogRuntimeService(self.metadata_store, self.binding_service)
        session = self.client.app.state.service.create_session("Catalog runtime planner context", {}, {}, {})

        context = runtime.planner_context(session["session_id"])

        self.assertEqual(context["session_id"], session["session_id"])
        self.assertIn("compare_watch_time", context["available_step_types"])
        self.assertTrue(any(entity["name"] == "user" for entity in context["entities"]))


if __name__ == "__main__":
    unittest.main()
