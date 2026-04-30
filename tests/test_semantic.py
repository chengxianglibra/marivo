from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

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
        detail = resp.json()["detail"]
        message = detail["message"] if isinstance(detail, dict) else detail
        self.assertIn(
            "cannot activate from status=published; expected draft",
            message,
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

    def _create_published_entity(self, entity_ref: str) -> None:
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": entity_ref,
                    "display_name": entity_ref.removeprefix("entity."),
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.{entity_ref.removeprefix('entity.')}_id"],
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

    def _metric_create_payload(self, metric_ref: str, entity_ref: str) -> dict[str, Any]:
        metric_name = metric_ref.removeprefix("metric.")
        return {
            "header": {
                "metric_ref": metric_ref,
                "display_name": metric_name,
                "description": f"{metric_name} metric",
                "metric_family": "count_metric",
                "observed_entity_ref": entity_ref,
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
                    "name": metric_name,
                    "semantics": f"{metric_name} events",
                    "aggregation": "count",
                },
            },
        }

    def _assert_metric_ref_conflict(
        self, metric_ref: str, existing: dict[str, Any], resp: Any
    ) -> None:
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["error"]["code"], "semantic_ref_conflict")
        self.assertEqual(detail["error"]["category"], "conflict")
        self.assertEqual(detail["error"]["field_path"], "header.metric_ref")
        remediation = detail["guidance"]["remediation"]
        self.assertEqual(remediation["existing_object_kind"], "metric")
        self.assertEqual(remediation["existing_object_id"], existing["metric_contract_id"])
        self.assertEqual(remediation["existing_ref"], metric_ref)
        self.assertEqual(remediation["existing_status"], existing["status"])
        self.assertEqual(remediation["existing_revision"], existing["revision"])
        self.assertIn("recommended_actions", remediation)
        self.assertGreaterEqual(len(detail["guidance"]["examples"]), 3)

    def test_metric_create_duplicate_ref_returns_structured_409_for_draft(self) -> None:
        entity_ref = "entity.metric_ref_conflict_draft_user"
        metric_ref = "metric.ref_conflict_draft"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)

        duplicate_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self._assert_metric_ref_conflict(metric_ref, create_resp.json(), duplicate_resp)

    def test_metric_create_duplicate_ref_returns_structured_409_for_published(self) -> None:
        entity_ref = "entity.metric_ref_conflict_published_user"
        metric_ref = "metric.ref_conflict_published"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        publish_resp = self.client.post(
            f"/semantic/metrics/{create_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        duplicate_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self._assert_metric_ref_conflict(metric_ref, publish_resp.json(), duplicate_resp)
        self.assertEqual(
            duplicate_resp.json()["detail"]["guidance"]["remediation"]["existing_lifecycle_status"],
            "active",
        )

    def test_metric_create_duplicate_ref_returns_structured_409_for_deprecated(self) -> None:
        entity_ref = "entity.metric_ref_conflict_deprecated_user"
        metric_ref = "metric.ref_conflict_deprecated"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        publish_resp = self.client.post(
            f"/semantic/metrics/{create_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)
        deprecate_resp = self.client.post(
            f"/semantic/metrics/{create_resp.json()['metric_contract_id']}/deprecate"
        )
        self.assertEqual(deprecate_resp.status_code, 200, deprecate_resp.text)

        duplicate_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self._assert_metric_ref_conflict(metric_ref, deprecate_resp.json(), duplicate_resp)
        self.assertEqual(
            duplicate_resp.json()["detail"]["guidance"]["remediation"]["ref_ownership"],
            "deprecated objects retain semantic ref ownership",
        )

    def test_metric_revision_routes_keep_ref_stable_and_default_latest_active(self) -> None:
        entity_ref = "entity.metric_revision_route_user"
        metric_ref = "metric.revision_route_avg"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        metric_id = create_resp.json()["metric_contract_id"]
        publish_resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)
        self.assertEqual(publish_resp.json()["revision"], 1)
        self.assertEqual(publish_resp.json()["is_latest_active"], True)

        replacement = self._metric_create_payload(metric_ref, entity_ref)
        replacement["header"]["description"] = "revision route metric in milliseconds"
        revision_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Fix unit label",
                "expected_change_scope": "display_metadata",
                "replacement": replacement,
            },
        )
        self.assertEqual(revision_resp.status_code, 200, revision_resp.text)
        self.assertEqual(revision_resp.json()["classified_compatibility"], "compatible")
        self.assertNotIn("compatibility", revision_resp.json())
        self.assertEqual(revision_resp.json()["required_actions"], [])
        self.assertIs(revision_resp.json()["can_activate_now"], True)
        self.assertEqual(revision_resp.json()["revision"], 2)
        self.assertEqual(revision_resp.json()["status"], "draft")
        self.assertEqual(revision_resp.json()["base_revision"], 1)

        default_resp = self.client.get(f"/semantic/metrics/{metric_ref}")
        self.assertEqual(default_resp.status_code, 200, default_resp.text)
        self.assertEqual(default_resp.json()["revision"], 1)

        revision_one_resp = self.client.get(f"/semantic/metrics/{metric_ref}/revisions/1")
        self.assertEqual(revision_one_resp.status_code, 200, revision_one_resp.text)
        self.assertEqual(revision_one_resp.json()["revision"], 1)

        list_with_draft_revision_resp = self.client.get("/semantic/metrics")
        self.assertEqual(
            list_with_draft_revision_resp.status_code, 200, list_with_draft_revision_resp.text
        )
        listed_with_draft_revision = [
            item
            for item in list_with_draft_revision_resp.json()["items"]
            if item["header"]["metric_ref"] == metric_ref
        ]
        self.assertEqual(len(listed_with_draft_revision), 1)
        self.assertEqual(listed_with_draft_revision[0]["revision"], 1)

        validate_resp = self.client.post(f"/semantic/metrics/{metric_ref}/revisions/2/validate")
        self.assertEqual(validate_resp.status_code, 200, validate_resp.text)
        default_after_validate = self.client.get(f"/semantic/metrics/{metric_ref}")
        self.assertEqual(default_after_validate.json()["revision"], 1)

        activate_resp = self.client.post(f"/semantic/metrics/{metric_ref}/revisions/2/activate")
        self.assertEqual(activate_resp.status_code, 200, activate_resp.text)
        self.assertEqual(activate_resp.json()["revision"], 2)
        self.assertEqual(activate_resp.json()["is_latest_active"], True)

        default_after_activate = self.client.get(f"/semantic/metrics/{metric_ref}")
        self.assertEqual(default_after_activate.status_code, 200, default_after_activate.text)
        self.assertEqual(default_after_activate.json()["revision"], 2)
        old_revision_resp = self.client.get(f"/semantic/metrics/{metric_ref}/revisions/1")
        self.assertEqual(old_revision_resp.json()["is_latest_active"], False)

        list_resp = self.client.get("/semantic/metrics?status=published&detail=true")
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        listed = [
            item for item in list_resp.json()["items"] if item["header"]["metric_ref"] == metric_ref
        ]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["revision"], 2)

        list_after_activate_resp = self.client.get("/semantic/metrics")
        self.assertEqual(list_after_activate_resp.status_code, 200, list_after_activate_resp.text)
        listed_after_activate = [
            item
            for item in list_after_activate_resp.json()["items"]
            if item["header"]["metric_ref"] == metric_ref
        ]
        self.assertEqual(len(listed_after_activate), 1)
        self.assertEqual(listed_after_activate[0]["revision"], 2)

    def test_metric_binding_authoring_rejects_metric_scope(self) -> None:
        entity_ref = "entity.draft_metric_binding_user"
        metric_ref = "metric.draft_metric_binding_count"
        self._create_published_entity(entity_ref)
        metric_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        self.assertEqual(metric_resp.json()["status"], "draft")

        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "binding.draft_metric_binding_count_primary",
                    "display_name": "Draft Metric Binding Count Primary",
                    "binding_scope": "metric",
                    "bound_object_ref": metric_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.draft_metric_binding_count",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {
                                    "surface_ref": "field.count_target",
                                    "physical_name": "count_target",
                                }
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "metric_input",
                                "target_key": "count_target",
                            },
                            "semantic_ref": "metric_input.count_target",
                            "surface_ref": "field.count_target",
                        }
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 422, binding_resp.text)
        self.assertEqual(
            binding_resp.json()["detail"]["code"], "typed_binding_scope_not_authorable"
        )
        self.assertEqual(
            binding_resp.json()["detail"]["field_path"],
            "header.binding_scope",
        )

    def test_metric_revision_rejects_stale_base_revision_without_switching(self) -> None:
        entity_ref = "entity.metric_revision_stale_user"
        metric_ref = "metric.revision_stale"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        metric_id = create_resp.json()["metric_contract_id"]
        publish_resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        replacement = self._metric_create_payload(metric_ref, entity_ref)
        first_revision = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "First revision",
                "expected_change_scope": "display_metadata",
                "replacement": replacement,
            },
        )
        self.assertEqual(first_revision.status_code, 200, first_revision.text)
        activate_resp = self.client.post(f"/semantic/metrics/{metric_ref}/revisions/2/activate")
        self.assertEqual(activate_resp.status_code, 200, activate_resp.text)

        stale_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Stale revision",
                "expected_change_scope": "display_metadata",
                "replacement": replacement,
            },
        )
        self.assertEqual(stale_resp.status_code, 409, stale_resp.text)
        detail = stale_resp.json()["detail"]
        self.assertEqual(detail["error"]["code"], "semantic_ref_conflict")
        self.assertEqual(detail["error"]["field_path"], "base_revision")

        default_resp = self.client.get(f"/semantic/metrics/{metric_ref}")
        self.assertEqual(default_resp.status_code, 200, default_resp.text)
        self.assertEqual(default_resp.json()["revision"], 2)

    def test_metric_revision_rejects_legacy_compatibility_request_field(self) -> None:
        entity_ref = "entity.metric_revision_legacy_field_user"
        metric_ref = "metric.revision_legacy_field"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        metric_id = create_resp.json()["metric_contract_id"]
        publish_resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        replacement = self._metric_create_payload(metric_ref, entity_ref)
        revision_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Legacy request field",
                "compatibility": "compatible",
                "replacement": replacement,
            },
        )
        self.assertEqual(revision_resp.status_code, 422, revision_resp.text)

    def test_metric_revision_guardrail_mismatch_does_not_persist_draft(self) -> None:
        entity_ref = "entity.revision_guardrail_user"
        metric_ref = "metric.revision_guardrail"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        publish_resp = self.client.post(
            f"/semantic/metrics/{create_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        replacement = self._metric_create_payload(metric_ref, entity_ref)
        replacement["payload"]["count_target"]["aggregation"] = "count_distinct"
        mismatch = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Change aggregation semantics",
                "expected_compatibility": "compatible",
                "replacement": replacement,
            },
        )

        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        detail = mismatch.json()["detail"]
        self.assertEqual(detail["error"]["code"], "revision_guardrail_mismatch")
        remediation = detail["guidance"]["remediation"]
        self.assertEqual(remediation["classified_compatibility"], "breaking")
        self.assertEqual(remediation["expected_compatibility"], "compatible")
        history = self.client.get(f"/semantic/metrics/{metric_ref}/revisions")
        self.assertEqual(history.json()["total"], 1)

    def test_metric_revision_change_scope_guardrail_mismatch_does_not_persist_draft(
        self,
    ) -> None:
        entity_ref = "entity.revision_scope_guardrail_user"
        metric_ref = "metric.revision_scope_guardrail"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        publish_resp = self.client.post(
            f"/semantic/metrics/{create_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        replacement = self._metric_create_payload(metric_ref, entity_ref)
        replacement["payload"]["count_target"]["aggregation"] = "count_distinct"
        mismatch = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Change aggregation semantics",
                "expected_change_scope": "display_metadata",
                "replacement": replacement,
            },
        )

        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        detail = mismatch.json()["detail"]
        self.assertEqual(detail["error"]["code"], "revision_guardrail_mismatch")
        self.assertEqual(detail["error"]["field_path"], "expected_change_scope")
        remediation = detail["guidance"]["remediation"]
        self.assertEqual(remediation["expected_change_scope"], "display_metadata")
        self.assertEqual(remediation["classified_compatibility"], "breaking")
        history = self.client.get(f"/semantic/metrics/{metric_ref}/revisions")
        self.assertEqual(history.json()["total"], 1)

    def test_metric_revision_activation_blocks_pending_required_actions(self) -> None:
        entity_ref = "entity.revision_activation_gate_user"
        metric_ref = "metric.revision_activation_gate"
        self._create_published_entity(entity_ref)
        create_resp = self.client.post(
            "/semantic/metrics",
            json=self._metric_create_payload(metric_ref, entity_ref),
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        publish_resp = self.client.post(
            f"/semantic/metrics/{create_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        replacement = self._metric_create_payload(metric_ref, entity_ref)
        replacement["payload"]["count_target"]["aggregation"] = "count_distinct"
        revision_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Change aggregation semantics",
                "replacement": replacement,
            },
        )
        self.assertEqual(revision_resp.status_code, 200, revision_resp.text)
        self.assertEqual(revision_resp.json()["classified_compatibility"], "breaking")
        self.assertFalse(revision_resp.json()["can_activate_now"])
        self.assertTrue(revision_resp.json()["required_actions"])

        activate_resp = self.client.post(f"/semantic/metrics/{metric_ref}/revisions/2/activate")
        self.assertEqual(activate_resp.status_code, 409, activate_resp.text)
        detail = activate_resp.json()["detail"]
        self.assertEqual(detail["error"]["code"], "revision_activation_blocked")
        self.assertTrue(detail["guidance"]["remediation"]["required_actions"])

        default_resp = self.client.get(f"/semantic/metrics/{metric_ref}")
        self.assertEqual(default_resp.status_code, 200, default_resp.text)
        self.assertEqual(default_resp.json()["revision"], 1)

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
        self.assertEqual(resp.json()["revision"], 1)

        resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")
        self.assertEqual(resp.json()["lifecycle_status"], "active")
        self.assertEqual(resp.json()["readiness_status"], "not_ready")
        self.assertEqual(
            resp.json()["blocking_requirements"][0]["code"], "METRIC_INPUT_FIELD_MISSING"
        )
        self.assertEqual(
            resp.json()["blocking_requirements"][0]["details"]["component"], "count_target"
        )
        capabilities = resp.json()["capabilities"]
        self.assertEqual(capabilities["supports_observe"], True)
        self.assertEqual(capabilities["supports_compare"], False)
        self.assertEqual(capabilities["supports_decompose"], False)
        self.assertEqual(capabilities["supports_attribute"], False)
        self.assertEqual(capabilities["supports_test"], True)
        self.assertEqual(capabilities["supports_detect"], False)
        self.assertEqual(capabilities["supports_validate"], False)
        self.assertEqual(resp.json()["revision"], 1)
        self.assertEqual(resp.json()["is_latest_active"], True)

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
        detail = resp.json()["detail"]
        message = detail["message"] if isinstance(detail, dict) else detail
        self.assertIn(
            "cannot activate from status=published; expected draft",
            message,
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
