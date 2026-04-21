from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticEntityRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_semantic_routes.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_entity_routes_use_typed_contract(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.user",
                    "display_name": "User",
                    "description": "A registered user",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        entity = resp.json()
        entity_id = entity["entity_contract_id"]
        self.assertEqual(entity["header"]["entity_ref"], "entity.user")
        self.assertEqual(entity["status"], "draft")
        self.assertEqual(entity["lifecycle_status"], "draft")
        self.assertEqual(entity["readiness_status"], "not_ready")
        self.assertEqual(entity["blocking_requirements"], [])
        self.assertEqual(entity["capabilities"], {})
        self.assertEqual(entity["dependency_refs"], [])
        self.assertEqual(entity["dependent_refs"], [])
        self.assertEqual(entity["revision"], 1)

        resp = self.client.get(f"/semantic/entities/{entity_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["entity_ref"], "entity.user")

        resp = self.client.put(
            f"/semantic/entities/{entity_id}",
            json={"description": "Updated description"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["description"], "Updated description")
        self.assertEqual(resp.json()["revision"], 2)

        resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")
        self.assertEqual(resp.json()["lifecycle_status"], "active")
        self.assertEqual(resp.json()["readiness_status"], "ready")
        self.assertEqual(resp.json()["revision"], 3)

        resp = self.client.get("/semantic/entities?status=published")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["status"] == "published" for item in resp.json()["items"]))

        resp = self.client.put(
            f"/semantic/entities/{entity_id}",
            json={"description": "Should fail after publish"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn(
            "cannot activate from status=published; expected draft", resp.json()["detail"]
        )

        resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn(
            "cannot activate from status=published; expected draft",
            resp.json()["detail"]["message"],
        )

    def test_entity_routes_reject_legacy_contract_and_missing_object(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "user", "display_name": "User", "keys": ["user_id"]},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

        resp = self.client.get("/semantic/entities/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_entity_detail_read_accepts_canonical_ref(self) -> None:
        create_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.user_read_surface",
                    "display_name": "User Read Surface",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_read_surface_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)

        resp = self.client.get("/semantic/entities/entity.user_read_surface")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["entity_ref"], "entity.user_read_surface")

    def test_entity_properties_patch_route_is_removed(self) -> None:
        entity = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.account",
                    "display_name": "Account",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.account_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        ).json()
        resp = self.client.patch(
            f"/semantic/entities/{entity['entity_contract_id']}/properties",
            json={"properties": {"unit": "seconds"}},
        )
        self.assertEqual(resp.status_code, 404)


class SemanticMetricRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_metric_routes.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_metric_routes_use_typed_contract(self) -> None:
        # Publish the entity that the metric references before creating/publishing the metric
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.user",
                    "display_name": "User",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_id = entity_resp.json()["entity_contract_id"]
        publish_resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.dau",
                    "display_name": "DAU",
                    "description": "Daily active users",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.user",
                    "observation_grain_ref": "grain.user",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity_constraints": {
                        "dimension_policy": "none",
                        "time_axis_policy": "non_additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "users",
                        "semantics": "distinct users",
                        "aggregation": "count_distinct",
                    },
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        metric = resp.json()
        metric_id = metric["metric_contract_id"]
        self.assertEqual(metric["header"]["metric_ref"], "metric.dau")
        self.assertEqual(metric["status"], "draft")
        self.assertEqual(metric["lifecycle_status"], "draft")
        self.assertEqual(metric["readiness_status"], "not_ready")
        self.assertEqual(metric["blocking_requirements"], [])
        self.assertEqual(metric["dependency_refs"], ["entity.user"])
        self.assertEqual(
            metric["capabilities"]["supports_observe"],
            True,
        )
        # count_distinct with dimension_policy=none does not support decompose
        self.assertEqual(metric["capabilities"]["supports_compare"], False)
        self.assertEqual(metric["capabilities"]["supports_decompose"], False)
        self.assertEqual(metric["capabilities"]["supports_attribute"], False)
        # sample_kind=numeric means supports_test=True
        self.assertEqual(metric["capabilities"]["supports_test"], True)
        self.assertEqual(metric["capabilities"]["supports_detect"], False)
        self.assertEqual(metric["capabilities"]["supports_validate"], False)

        resp = self.client.get(f"/semantic/metrics/{metric_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["metric_ref"], "metric.dau")

        resp = self.client.put(
            f"/semantic/metrics/{metric_id}",
            json={
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "active_users",
                        "semantics": "active users",
                        "aggregation": "count_distinct",
                    },
                }
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["payload"]["count_target"]["name"], "active_users")
        self.assertEqual(resp.json()["revision"], 2)

        resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")
        self.assertEqual(resp.json()["lifecycle_status"], "active")
        self.assertEqual(resp.json()["readiness_status"], "not_ready")
        self.assertEqual(resp.json()["blocking_requirements"][0]["code"], "METRIC_BINDING_MISSING")
        self.assertEqual(
            resp.json()["blocking_requirements"][0]["details"]["required_binding_scope"], "metric"
        )
        capabilities = resp.json()["capabilities"]
        self.assertEqual(capabilities["supports_observe"], True)
        self.assertEqual(capabilities["supports_compare"], False)
        self.assertEqual(capabilities["supports_decompose"], False)
        self.assertEqual(capabilities["supports_attribute"], False)
        self.assertEqual(capabilities["supports_test"], True)
        self.assertEqual(capabilities["supports_detect"], False)
        self.assertEqual(capabilities["supports_validate"], False)
        self.assertEqual(resp.json()["revision"], 3)

        resp = self.client.get("/semantic/metrics?status=published")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["status"] == "published" for item in resp.json()["items"]))
        # List items use lightweight format - no payload, no dependency_refs
        self.assertNotIn("payload", resp.json()["items"][0])
        self.assertNotIn("dependency_refs", resp.json()["items"][0])
        self.assertIn("blocker_count", resp.json()["items"][0])
        self.assertIn("capabilities_summary", resp.json()["items"][0])
        # With detail=true, returns full format
        resp = self.client.get("/semantic/metrics?status=published&detail=true")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("payload", resp.json()["items"][0])
        self.assertIn("dependency_refs", resp.json()["items"][0])

        resp = self.client.get("/semantic/metrics?lifecycle_status=active")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["lifecycle_status"] == "active" for item in resp.json()["items"]))

        resp = self.client.get("/semantic/metrics?status=active")
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("Unsupported status filter", resp.text)

        resp = self.client.get(
            "/semantic/metrics?lifecycle_status=active&readiness_status=not_ready"
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(
            all(item["readiness_status"] == "not_ready" for item in resp.json()["items"])
        )

        resp = self.client.get("/semantic/metrics?status=draft&lifecycle_status=active")
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("filters conflict", resp.json()["detail"])

        resp = self.client.put(
            f"/semantic/metrics/{metric_id}",
            json={
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "should_fail",
                        "semantics": "should fail",
                        "aggregation": "count_distinct",
                    },
                }
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn(
            "cannot activate from status=published; expected draft", resp.json()["detail"]
        )

        resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn(
            "cannot activate from status=published; expected draft",
            resp.json()["detail"]["message"],
        )

    def test_metric_routes_reject_legacy_contract_and_missing_object(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

        resp = self.client.get("/semantic/metrics/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_metric_detail_read_accepts_canonical_ref(self) -> None:
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.metric_read_subject",
                    "display_name": "Metric Read Subject",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.metric_read_subject_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        publish_resp = self.client.post(
            f"/semantic/entities/{entity_resp.json()['entity_contract_id']}/publish"
        )
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        create_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.watch_time_read_surface",
                    "display_name": "Watch Time Read Surface",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.metric_read_subject",
                    "observation_grain_ref": "grain.user",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity_constraints": {
                        "dimension_policy": "all",
                        "time_axis_policy": "additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "watch_time_read_surface",
                        "semantics": "Count target for read surface test",
                        "aggregation": "count",
                    },
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)

        resp = self.client.get("/semantic/metrics/metric.watch_time_read_surface")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["metric_ref"], "metric.watch_time_read_surface")


class SemanticDimensionRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_dimension_routes.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_ready_dimension_list_filter_matches_resolve_readiness(self) -> None:
        create_resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": "dimension.discovery_channel",
                    "display_name": "Discovery Channel",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "open",
                    },
                    "grouping": {"supports_grouping": True},
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        dimension_id = create_resp.json()["dimension_contract_id"]

        publish_resp = self.client.post(f"/semantic/dimensions/{dimension_id}/publish")
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)
        self.assertEqual(publish_resp.json()["lifecycle_status"], "active")
        self.assertEqual(publish_resp.json()["readiness_status"], "ready")

        list_resp = self.client.get(
            "/semantic/dimensions?lifecycle_status=active&readiness_status=ready"
        )
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        list_items = list_resp.json()["items"]
        list_item = next(
            item
            for item in list_items
            if item["header"]["dimension_ref"] == "dimension.discovery_channel"
        )
        self.assertEqual(list_item["lifecycle_status"], "active")
        self.assertEqual(list_item["readiness_status"], "ready")
        self.assertNotIn("interface_contract", list_item)

        detail_list_resp = self.client.get(
            "/semantic/dimensions?lifecycle_status=active&readiness_status=ready&detail=true"
        )
        self.assertEqual(detail_list_resp.status_code, 200, detail_list_resp.text)
        detail_list_item = next(
            item
            for item in detail_list_resp.json()["items"]
            if item["header"]["dimension_ref"] == "dimension.discovery_channel"
        )
        self.assertEqual(detail_list_item["readiness_status"], "ready")
        self.assertIn("interface_contract", detail_list_item)

        resolve_resp = self.client.get("/semantic/resolve/dimension.discovery_channel")
        self.assertEqual(resolve_resp.status_code, 200, resolve_resp.text)
        resolved_object = resolve_resp.json()["semantic_object"]
        self.assertEqual(resolved_object["lifecycle_status"], list_item["lifecycle_status"])
        self.assertEqual(resolved_object["readiness_status"], list_item["readiness_status"])


if __name__ == "__main__":
    unittest.main()
