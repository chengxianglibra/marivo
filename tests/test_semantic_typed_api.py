from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticTypedApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_semantic_typed.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_typed_entity_lifecycle(self) -> None:
        resp = self.client.post(
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
        self.assertEqual(resp.status_code, 200, resp.text)
        entity = resp.json()
        entity_id = entity["entity_contract_id"]
        self.assertEqual(entity["header"]["entity_ref"], "entity.user")

        resp = self.client.put(
            f"/semantic/entities/{entity_id}",
            json={"description": "Updated description"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["description"], "Updated description")

        resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")

        resp = self.client.get("/semantic/entities?surface=typed")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertGreaterEqual(resp.json()["total"], 1)

    def test_typed_metric_lifecycle(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.dau",
                    "display_name": "DAU",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.user",
                    "observation_grain_ref": "grain.user",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity": "additive",
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

        resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")

        resp = self.client.get("/semantic/metrics?surface=typed")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertGreaterEqual(resp.json()["total"], 1)

    def test_typed_binding_and_profile_lifecycle(self) -> None:
        entity_resp = self.client.post(
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
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)

        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "binding.account_primary",
                    "display_name": "Account Binding",
                    "binding_scope": "entity",
                    "bound_object_ref": "entity.account",
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.accounts",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {
                                    "surface_ref": "field.account_id",
                                    "physical_name": "account_id",
                                }
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": "key.account_id",
                            },
                            "semantic_ref": "key.account_id",
                            "surface_ref": "field.account_id",
                        }
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        binding = binding_resp.json()
        binding_id = binding["binding_id"]
        self.assertEqual(binding["header"]["binding_ref"], "binding.account_primary")

        resp = self.client.put(
            f"/semantic/bindings/{binding_id}",
            json={"description": "Updated binding description"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["description"], "Updated binding description")

        resp = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.account_count",
                    "display_name": "Account Count",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.account",
                    "observation_grain_ref": "grain.account",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity": "additive",
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "accounts",
                        "semantics": "distinct accounts",
                        "aggregation": "count_distinct",
                    },
                },
            },
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)

        profile_resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": "compiler_profile.account_count_requirement",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": "metric.account_count",
                "requirement": {"entity_refs": ["entity.account"]},
            },
        )
        self.assertEqual(profile_resp.status_code, 200, profile_resp.text)
        profile = profile_resp.json()
        profile_id = profile["profile_id"]
        self.assertEqual(profile["profile_ref"], "compiler_profile.account_count_requirement")

        resp = self.client.put(
            f"/compiler/compatibility-profiles/{profile_id}",
            json={"requirement": {"entity_refs": ["entity.account", "entity.user"]}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["requirement"]["entity_refs"][1], "entity.user")

        resp = self.client.post(f"/compiler/compatibility-profiles/{profile_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")

    def test_typed_object_routes_use_consistent_404_detail(self) -> None:
        binding_resp = self.client.get("/semantic/bindings/bind_missing")
        self.assertEqual(binding_resp.status_code, 404, binding_resp.text)
        self.assertIsInstance(binding_resp.json()["detail"], str)

        profile_resp = self.client.get("/compiler/compatibility-profiles/cprof_missing")
        self.assertEqual(profile_resp.status_code, 404, profile_resp.text)
        self.assertIsInstance(profile_resp.json()["detail"], str)

    def test_typed_object_routes_keep_validation_error_detail_shape(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.invalid",
                    "display_name": "Invalid",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

    def test_typed_object_routes_map_service_value_error_to_422(self) -> None:
        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.error_case",
                    "display_name": "Error Case Metric",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.account",
                    "observation_grain_ref": "grain.account",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity": "additive",
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "accounts",
                        "semantics": "distinct accounts",
                        "aggregation": "count_distinct",
                    },
                },
            },
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)

        profile_resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": "compiler_profile.error_case_requirement",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": "metric.error_case",
                "requirement": {"entity_refs": ["entity.account"]},
            },
        )
        self.assertEqual(profile_resp.status_code, 200, profile_resp.text)
        profile_id = profile_resp.json()["profile_id"]

        resp = self.client.put(
            f"/compiler/compatibility-profiles/{profile_id}",
            json={"capability": {"inferential_ready": True}},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], str)
