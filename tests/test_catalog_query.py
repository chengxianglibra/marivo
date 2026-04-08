from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class CatalogQueryTests(unittest.TestCase):
    """Tests for search, resolve, planner-context, and graph endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_catalog_query.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))

        # Set up test data: source + sync + semantic objects + mappings
        resp = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "CQ Test Source",
                "connection": {"path": str(cls.db_path)},
            },
        )
        cls.source_id = resp.json()["source_id"]
        cls.client.post(f"/sources/{cls.source_id}/sync")

        # Get synced table objects
        resp = cls.client.get(f"/sources/{cls.source_id}/objects?type=table")
        cls.table_objects = {t["native_name"]: t for t in resp.json()}

        # Create and publish entities
        resp = cls.client.post(
            "/semantic/entities",
            json={
                "name": "user",
                "display_name": "User",
                "description": "A platform user",
                "keys": ["user_id"],
                "level": "user",
                "join_constraints": {"requires": ["country"]},
                "upstream_dependencies": ["account"],
                "lineage": ["analytics.users"],
                "quality_expectations": {"freshness_hours": 24},
            },
        )
        cls.user_entity_id = resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{cls.user_entity_id}/publish")

        # Create and publish metrics
        resp = cls.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "description": "Average play duration per session",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
                "grain": "session",
                "measure_type": "average",
                "allowed_dimensions": ["platform", "network_type", "content_type"],
                "lineage": ["analytics.watch_events.play_duration_seconds"],
                "quality_expectations": {"min_group_size": 100},
            },
        )
        cls.watch_metric_id = resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{cls.watch_metric_id}/publish")

        # Create mapping: metric -> table
        watch_obj_id = cls.table_objects["watch_events"]["object_id"]
        cls.client.post(
            "/semantic/mappings",
            json={
                "semantic_type": "metric",
                "semantic_id": cls.watch_metric_id,
                "object_id": watch_obj_id,
                "mapping_type": "primary_source",
            },
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_search_by_metric_name(self) -> None:
        resp = self.client.get("/catalog/search?q=watch")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(any(r["name"] == "watch_time" and r["type"] == "metric" for r in results))

    def test_search_by_type_filter(self) -> None:
        resp = self.client.get("/catalog/search?q=watch&type=metric")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(all(r["type"] == "metric" for r in results))

    def test_search_entity(self) -> None:
        resp = self.client.get("/catalog/search?q=user&type=entity")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(any(r["name"] == "user" for r in results))

    def test_search_asset(self) -> None:
        resp = self.client.get("/catalog/search?q=watch_events&type=asset")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(any(r["name"] == "watch_events" for r in results))

    def test_resolve_metric(self) -> None:
        resp = self.client.get("/semantic/resolve/watch_time")
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["resolved_type"], "metric")
        self.assertEqual(result["semantic_object"]["header"]["metric_ref"], "metric.watch_time")
        self.assertEqual(
            result["semantic_object"]["identity"]["observed_entity_ref"], "entity.user"
        )
        self.assertEqual(result["semantic_object"]["legacy"]["grain"], "session")
        self.assertEqual(result["semantic_object"]["legacy"]["measure_type"], "average")
        self.assertEqual(
            result["semantic_object"]["legacy"]["allowed_dimensions"],
            ["platform", "network_type", "content_type"],
        )
        # Should have physical assets from mapping
        self.assertGreaterEqual(len(result["physical_assets"]), 1)
        self.assertEqual(result["physical_assets"][0]["native_name"], "watch_events")

    def test_resolve_entity(self) -> None:
        resp = self.client.get("/semantic/resolve/user")
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["resolved_type"], "entity")
        self.assertEqual(result["semantic_object"]["header"]["entity_ref"], "entity.user")
        self.assertEqual(result["semantic_object"]["legacy"]["level"], "user")
        self.assertEqual(result["semantic_object"]["legacy"]["upstream_dependencies"], ["account"])

    def test_resolve_404(self) -> None:
        resp = self.client.get("/semantic/resolve/nonexistent_thing")
        self.assertEqual(resp.status_code, 404)

    def test_planner_context(self) -> None:
        # Create a session
        resp = self.client.post(
            "/sessions",
            json={"goal": "Test planner context"},
        )
        session_id = resp.json()["session_id"]

        resp = self.client.get(f"/sessions/{session_id}/planner-context")
        self.assertEqual(resp.status_code, 200)
        ctx = resp.json()
        self.assertEqual(ctx["session_id"], session_id)
        self.assertIn("metrics", ctx)
        self.assertIn("entities", ctx)
        self.assertIn("available_step_types", ctx)
        self.assertIn("metric_query", ctx["available_step_types"])
        watch_metric = next(
            metric
            for metric in ctx["metrics"]
            if metric["header"]["metric_ref"] == "metric.watch_time"
        )
        self.assertEqual(watch_metric["identity"]["metric_family"], "average_metric")
        self.assertEqual(watch_metric["legacy"]["grain"], "session")
        self.assertEqual(watch_metric["legacy"]["measure_type"], "average")
        user_entity = next(
            entity for entity in ctx["entities"] if entity["header"]["entity_ref"] == "entity.user"
        )
        self.assertEqual(user_entity["legacy"]["level"], "user")

    def test_graph_traversal(self) -> None:
        # Graph from the metric node
        resp = self.client.get(f"/catalog/graph?root={self.watch_metric_id}&depth=2")
        self.assertEqual(resp.status_code, 200)
        graph = resp.json()
        self.assertEqual(graph["root"], self.watch_metric_id)
        self.assertGreaterEqual(len(graph["nodes"]), 1)
        # Should have a maps_to edge to the watch_events table
        maps_to_edges = [e for e in graph["edges"] if e["edge_type"] == "maps_to"]
        self.assertGreaterEqual(len(maps_to_edges), 1)


if __name__ == "__main__":
    unittest.main()
