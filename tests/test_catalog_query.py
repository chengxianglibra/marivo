from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.semantic_test_helpers import (
    create_typed_entity,
    create_typed_metric,
    create_typed_metric_binding,
    publish_typed_entity,
    publish_typed_metric,
)
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
                "authority": {
                    "catalog_system": "duckdb",
                    "connection": {"path": str(cls.db_path)},
                    "synthetic_catalog": "main",
                },
            },
        )
        cls.source_id = resp.json()["source_id"]
        cls.client.post(
            f"/sources/{cls.source_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "watch_events"},
                    {"schema_name": "analytics", "table_name": "player_qoe"},
                    {"schema_name": "analytics", "table_name": "ad_events"},
                    {"schema_name": "analytics", "table_name": "recommendation_events"},
                ]
            },
        )
        cls.client.post(f"/sources/{cls.source_id}/sync")

        # Get synced table objects
        resp = cls.client.get(f"/sources/{cls.source_id}/objects?type=table")
        cls.table_objects = {t["native_name"]: t for t in resp.json()}

        # Create and publish entities
        entity = create_typed_entity(
            cls.client,
            name="user",
            display_name="User",
            description="A platform user",
            keys=["user_id"],
        )
        cls.user_entity_id = entity["entity_contract_id"]
        publish_typed_entity(cls.client, cls.user_entity_id)

        # Create and publish metrics
        metric = create_typed_metric(
            cls.client,
            name="watch_time",
            display_name="Watch Time",
            description="Average play duration per session",
            definition_sql="avg(play_duration_seconds)",
            dimensions=["platform", "app_version", "network_type", "content_type"],
            entity_ref="entity.user",
            grain="session",
            measure_type="average",
            allowed_dimensions=["platform", "network_type", "content_type"],
            quality_expectations={"min_group_size": 100},
        )
        cls.watch_metric_id = metric["metric_contract_id"]
        publish_typed_metric(cls.client, cls.watch_metric_id)

        # Create mapping: metric -> table
        watch_obj_id = cls.table_objects["watch_events"]["object_id"]
        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.watch_time",
            object_id=watch_obj_id,
            carrier_locator=str(cls.table_objects["watch_events"]["fqn"]),
            metric_input_target_keys=["numerator"],
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_search_by_metric_name(self) -> None:
        resp = self.client.get("/catalog/search?q=watch")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertFalse(any(r["ref"] == "metric.watch_time" for r in results))

    def test_search_by_type_filter(self) -> None:
        resp = self.client.get("/catalog/search?q=watch&type=metric")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(all(r["object_kind"] == "metric" for r in results))
        self.assertEqual(results, [])

    def test_search_by_readiness_filter_returns_not_ready_metrics(self) -> None:
        resp = self.client.get("/catalog/search?q=watch&type=metric&readiness=not_ready")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(
            any(
                r["name"] == "watch_time"
                and r["object_kind"] == "metric"
                and r["ref"] == "metric.watch_time"
                and r["detail_path"] == f"/catalog/objects/metric/{self.watch_metric_id}"
                and r["resolve_path"] == "/semantic/resolve/metric.watch_time"
                and r["lifecycle_status"] == "active"
                and r["readiness_status"] == "not_ready"
                and r["blocker_count"] == 1
                and r["blocking_requirements_preview"][0]["code"] == "METRIC_INPUT_COVERAGE_MISSING"
                and r["capabilities_summary"]["supports_validate"] is False
                for r in results
            )
        )

    def test_search_entity(self) -> None:
        resp = self.client.get("/catalog/search?q=user&type=entity")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(any(r["name"] == "user" and r["ref"] == "entity.user" for r in results))

    def test_search_asset(self) -> None:
        resp = self.client.get("/catalog/search?q=watch_events&type=asset")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(
            any(
                r["name"] == "watch_events"
                and r["object_kind"] == "asset"
                and r["detail_path"] == f"/catalog/objects/asset/{r['object_id']}"
                and r["source_object_path"] == f"/sources/{self.source_id}/objects/{r['object_id']}"
                for r in results
            )
        )

    def test_search_calendar_policy(self) -> None:
        resp = self.client.get("/catalog/search?q=holiday&type=calendar_policy")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertTrue(
            any(
                r["ref"] == "calendar_policy.calendar_yoy"
                and r["object_kind"] == "calendar_policy"
                and r["readiness_status"] == "ready"
                and r["comparison_basis"] == "yoy"
                for r in results
            )
        )

    def test_resolve_calendar_policy(self) -> None:
        resp = self.client.get("/semantic/resolve/calendar_policy.calendar_yoy")
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["object_kind"], "calendar_policy")
        self.assertEqual(result["ref"], "calendar_policy.calendar_yoy")
        self.assertEqual(result["semantic_object"]["comparison_basis"], "yoy")
        self.assertEqual(result["semantic_object"]["resolved_alignment_mode"], "calendar_aware")
        self.assertTrue(
            result["semantic_object"]["capabilities"]["supports_observe_calendar_alignment"]
        )

    def test_search_rejects_invalid_type_filter(self) -> None:
        resp = self.client.get("/catalog/search?q=watch&type=profile")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported catalog object type filter", resp.json()["detail"])

    def test_search_rejects_invalid_readiness_filter(self) -> None:
        resp = self.client.get("/catalog/search?q=watch&readiness=blocked")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported catalog readiness filter", resp.json()["detail"])

    def test_resolve_metric(self) -> None:
        resp = self.client.get("/semantic/resolve/metric.watch_time")
        self.assertEqual(resp.status_code, 409)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "semantic_not_ready")
        self.assertEqual(detail["category"], "readiness")
        self.assertEqual(detail["subject_ref"], "metric.watch_time")
        self.assertEqual(detail["lifecycle_status"], "active")
        self.assertEqual(detail["readiness_status"], "not_ready")
        self.assertEqual(
            detail["blocking_requirements"][0]["code"],
            "METRIC_INPUT_COVERAGE_MISSING",
        )
        self.assertIn("entity.user", detail["dependency_refs"])

    def test_resolve_entity(self) -> None:
        resp = self.client.get("/semantic/resolve/entity.user")
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["object_kind"], "entity")
        self.assertEqual(result["ref"], "entity.user")
        self.assertEqual(result["semantic_object"]["header"]["entity_ref"], "entity.user")
        self.assertEqual(
            result["semantic_object"]["interface_contract"]["identity"]["key_refs"],
            ["key.user_id"],
        )

    def test_catalog_detail_round_trip_for_metric(self) -> None:
        search_resp = self.client.get("/catalog/search?q=watch&type=metric&readiness=not_ready")
        self.assertEqual(search_resp.status_code, 200, search_resp.text)
        summary = next(
            item for item in search_resp.json() if item["object_id"] == self.watch_metric_id
        )

        detail_resp = self.client.get(summary["detail_path"])
        self.assertEqual(detail_resp.status_code, 200, detail_resp.text)
        detail = detail_resp.json()

        self.assertEqual(detail["object_kind"], "metric")
        self.assertEqual(detail["object_id"], self.watch_metric_id)
        self.assertEqual(detail["semantic_object"]["lifecycle_status"], "active")
        self.assertEqual(detail["semantic_object"]["readiness_status"], "not_ready")
        self.assertEqual(
            detail["semantic_object"]["blocking_requirements"][0]["code"],
            "METRIC_INPUT_COVERAGE_MISSING",
        )
        self.assertIn("entity.user", detail["semantic_object"]["dependency_refs"])
        self.assertIn("time.event_date", detail["semantic_object"]["dependency_refs"])
        self.assertIsInstance(detail["semantic_object"]["dependent_refs"], list)
        self.assertEqual(detail["semantic_object"]["header"]["metric_ref"], "metric.watch_time")

    def test_catalog_detail_round_trip_for_asset(self) -> None:
        search_resp = self.client.get("/catalog/search?q=watch_events&type=asset")
        self.assertEqual(search_resp.status_code, 200, search_resp.text)
        summary = next(item for item in search_resp.json() if item["name"] == "watch_events")

        detail_resp = self.client.get(summary["detail_path"])
        self.assertEqual(detail_resp.status_code, 200, detail_resp.text)
        detail = detail_resp.json()

        self.assertEqual(detail["object_kind"], "asset")
        self.assertEqual(detail["object_id"], summary["object_id"])
        self.assertEqual(detail["source_object"]["source_id"], self.source_id)
        self.assertEqual(detail["source_object"]["native_name"], "watch_events")

    def test_resolve_requires_explicit_typed_refs(self) -> None:
        metric_resp = self.client.get("/semantic/resolve/watch_time")
        entity_resp = self.client.get("/semantic/resolve/user")
        self.assertEqual(metric_resp.status_code, 404)
        self.assertEqual(entity_resp.status_code, 404)

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
        self.assertTrue(
            any(
                policy["policy_ref"] == "calendar_policy.calendar_yoy"
                for policy in ctx["calendar_policies"]
            )
        )
        self.assertFalse(
            any(metric["header"]["metric_ref"] == "metric.watch_time" for metric in ctx["metrics"])
        )
        user_entity = next(
            entity for entity in ctx["entities"] if entity["header"]["entity_ref"] == "entity.user"
        )
        self.assertEqual(
            user_entity["interface_contract"]["identity"]["key_refs"],
            ["key.user_id"],
        )
        self.assertNotIn("legacy", user_entity)

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
