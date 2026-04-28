from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from tests.semantic_test_helpers import seed_duckdb_source_object
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticRevisionDependencyPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "dependency_plan.duckdb"
        get_seeded_duckdb_path(db_path)
        self.client = TestClient(create_app(db_path))

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_breaking_metric_revision_returns_binding_and_profile_dependency_actions(
        self,
    ) -> None:
        entity_ref = "entity.dependency_plan_user"
        metric_ref = "metric.dependency_plan_count"
        binding_ref = "binding.dependency_plan_count_primary"
        profile_ref = "compiler_profile.dependency_plan_count_requirement"

        self._create_published_entity(entity_ref)
        metric_resp = self.client.post(
            "/semantic/metrics",
            json=self._count_metric_payload(metric_ref, entity_ref),
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        publish_metric_resp = self.client.post(
            f"/semantic/metrics/{metric_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_metric_resp.status_code, 200, publish_metric_resp.text)

        binding_resp = self.client.post(
            "/semantic/bindings",
            json=self._metric_binding_payload(
                binding_ref=binding_ref,
                metric_ref=metric_ref,
                covered_input_refs=["metric_input.count_target"],
            ),
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        self._mark_binding_published(binding_resp.json()["binding_id"])

        profile_resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": profile_ref,
                "profile_kind": "requirement",
                "schema_version": "v1",
                "subject_kind": "metric",
                "subject_ref": metric_ref,
                "requirement": {"contract_modes": ["entity_stream"]},
            },
        )
        self.assertEqual(profile_resp.status_code, 200, profile_resp.text)
        publish_profile_resp = self.client.post(
            f"/compiler/compatibility-profiles/{profile_resp.json()['profile_id']}/publish"
        )
        self.assertEqual(publish_profile_resp.status_code, 200, publish_profile_resp.text)

        replacement = self._count_metric_payload(metric_ref, entity_ref)
        replacement["payload"]["required_inputs"].append({"input_ref": "metric_input.denominator"})
        revision_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Add denominator input",
                "replacement": replacement,
            },
        )
        self.assertEqual(revision_resp.status_code, 200, revision_resp.text)

        revision = revision_resp.json()["revision"]
        self._assert_dependency_plan(
            revision_resp.json(),
            binding_ref=binding_ref,
            profile_ref=profile_ref,
            missing_input_ref="metric_input.denominator",
        )

        read_resp = self.client.get(f"/semantic/metrics/{metric_ref}/revisions/{revision}")
        self.assertEqual(read_resp.status_code, 200, read_resp.text)
        self._assert_dependency_plan(
            read_resp.json(),
            binding_ref=binding_ref,
            profile_ref=profile_ref,
            missing_input_ref="metric_input.denominator",
        )

        validate_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions/{revision}/validate"
        )
        self.assertEqual(validate_resp.status_code, 200, validate_resp.text)
        self._assert_dependency_plan(
            validate_resp.json()["semantic_object"],
            binding_ref=binding_ref,
            profile_ref=profile_ref,
            missing_input_ref="metric_input.denominator",
        )

        history_resp = self.client.get(f"/semantic/metrics/{metric_ref}/revisions")
        self.assertEqual(history_resp.status_code, 200, history_resp.text)
        history_revision = next(
            item for item in history_resp.json()["items"] if item["revision"] == revision
        )
        self._assert_dependency_plan(
            history_revision,
            binding_ref=binding_ref,
            profile_ref=profile_ref,
            missing_input_ref="metric_input.denominator",
        )

    def test_missing_input_coverage_is_computed_per_binding(self) -> None:
        entity_ref = "entity.dependency_plan_per_binding_user"
        metric_ref = "metric.dependency_plan_per_binding"
        binding_a_ref = "binding.dependency_plan_per_binding_a"
        binding_b_ref = "binding.dependency_plan_per_binding_b"

        self._create_published_entity(entity_ref)
        metric_resp = self.client.post(
            "/semantic/metrics",
            json=self._count_metric_payload(metric_ref, entity_ref),
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        publish_metric_resp = self.client.post(
            f"/semantic/metrics/{metric_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_metric_resp.status_code, 200, publish_metric_resp.text)

        binding_a_resp = self.client.post(
            "/semantic/bindings",
            json=self._metric_binding_payload(
                binding_ref=binding_a_ref,
                metric_ref=metric_ref,
                covered_input_refs=["metric_input.count_target"],
            ),
        )
        self.assertEqual(binding_a_resp.status_code, 200, binding_a_resp.text)
        self._mark_binding_published(binding_a_resp.json()["binding_id"])

        binding_b_resp = self.client.post(
            "/semantic/bindings",
            json=self._metric_binding_payload(
                binding_ref=binding_b_ref,
                metric_ref=metric_ref,
                covered_input_refs=["metric_input.denominator"],
                target_key_by_input_ref={"metric_input.denominator": "count_target"},
            ),
        )
        self.assertEqual(binding_b_resp.status_code, 200, binding_b_resp.text)
        self._mark_binding_published(binding_b_resp.json()["binding_id"])

        replacement = self._count_metric_payload(metric_ref, entity_ref)
        replacement["payload"]["required_inputs"].append({"input_ref": "metric_input.denominator"})
        revision_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Add denominator input",
                "replacement": replacement,
            },
        )
        self.assertEqual(revision_resp.status_code, 200, revision_resp.text)

        coverage_expectations = {
            (
                action["target_ref"],
                action["completion_criteria"]["expected"]["coverage_target"],
            )
            for action in revision_resp.json()["required_actions"]
            if action["action"] == "add_binding_coverage"
        }
        self.assertIn((binding_a_ref, "metric_input.denominator"), coverage_expectations)
        self.assertIn((binding_b_ref, "metric_input.count_target"), coverage_expectations)

    def test_guardrail_mismatch_returns_concrete_dependency_actions(self) -> None:
        entity_ref = "entity.dependency_plan_guardrail_user"
        metric_ref = "metric.dependency_plan_guardrail"
        binding_ref = "binding.dependency_plan_guardrail_primary"
        profile_ref = "compiler_profile.dependency_plan_guardrail_requirement"

        self._create_published_entity(entity_ref)
        metric_resp = self.client.post(
            "/semantic/metrics",
            json=self._count_metric_payload(metric_ref, entity_ref),
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        publish_metric_resp = self.client.post(
            f"/semantic/metrics/{metric_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_metric_resp.status_code, 200, publish_metric_resp.text)
        binding_resp = self.client.post(
            "/semantic/bindings",
            json=self._metric_binding_payload(
                binding_ref=binding_ref,
                metric_ref=metric_ref,
                covered_input_refs=["metric_input.count_target"],
            ),
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        self._mark_binding_published(binding_resp.json()["binding_id"])
        profile_resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": profile_ref,
                "profile_kind": "requirement",
                "schema_version": "v1",
                "subject_kind": "metric",
                "subject_ref": metric_ref,
                "requirement": {"contract_modes": ["entity_stream"]},
            },
        )
        self.assertEqual(profile_resp.status_code, 200, profile_resp.text)
        publish_profile_resp = self.client.post(
            f"/compiler/compatibility-profiles/{profile_resp.json()['profile_id']}/publish"
        )
        self.assertEqual(publish_profile_resp.status_code, 200, publish_profile_resp.text)

        replacement = self._count_metric_payload(metric_ref, entity_ref)
        replacement["payload"]["required_inputs"].append({"input_ref": "metric_input.denominator"})
        mismatch_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Add denominator input",
                "expected_compatibility": "compatible",
                "replacement": replacement,
            },
        )

        self.assertEqual(mismatch_resp.status_code, 409, mismatch_resp.text)
        remediation = mismatch_resp.json()["detail"]["guidance"]["remediation"]
        action_names = {action["action"] for action in remediation["required_actions"]}
        self.assertIn("derive_revision", action_names)
        self.assertIn("add_binding_coverage", action_names)
        self.assertIn("reuse_after_revalidate", action_names)
        self.assertNotIn("resolve_breaking_revision_plan", action_names)

    def test_completed_dependency_actions_allow_breaking_metric_revision_activation(
        self,
    ) -> None:
        entity_ref = "entity.dependency_plan_complete_user"
        metric_ref = "metric.dependency_plan_complete_rate"
        binding_ref = "binding.dependency_plan_complete_rate_primary"
        profile_ref = "compiler_profile.dependency_plan_complete_requirement"

        self._create_published_entity(entity_ref)
        metric_resp = self.client.post(
            "/semantic/metrics",
            json=self._rate_metric_payload(metric_ref, entity_ref),
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        publish_metric_resp = self.client.post(
            f"/semantic/metrics/{metric_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(publish_metric_resp.status_code, 200, publish_metric_resp.text)

        binding_resp = self.client.post(
            "/semantic/bindings",
            json=self._metric_binding_payload(
                binding_ref=binding_ref,
                metric_ref=metric_ref,
                covered_input_refs=["metric_input.numerator", "metric_input.denominator"],
            ),
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        base_binding_id = binding_resp.json()["binding_id"]
        metadata = self.client.app.state.metadata_store
        now = datetime.now(UTC).isoformat()
        seed_duckdb_source_object(
            metadata,
            source_id="src_dependency_plan_complete",
            object_id="obj_dependency_plan_complete",
            display_name="Dependency plan complete fact",
            table_name="dependency_plan_count",
            table_fqn="warehouse.dependency_plan_count",
            authority_locator={
                "catalog": None,
                "schema": "warehouse",
                "table": "dependency_plan_count",
            },
            now=now,
        )
        metadata.execute(
            "DELETE FROM field_bindings WHERE binding_id = ? AND semantic_ref = ?",
            [base_binding_id, "metric_input.denominator"],
        )
        self._mark_binding_published(base_binding_id)

        profile_resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": profile_ref,
                "profile_kind": "requirement",
                "schema_version": "v1",
                "subject_kind": "metric",
                "subject_ref": metric_ref,
                "requirement": {"contract_modes": ["entity_stream"]},
            },
        )
        self.assertEqual(profile_resp.status_code, 200, profile_resp.text)
        publish_profile_resp = self.client.post(
            f"/compiler/compatibility-profiles/{profile_resp.json()['profile_id']}/publish"
        )
        self.assertEqual(publish_profile_resp.status_code, 200, publish_profile_resp.text)

        replacement = self._rate_metric_payload(metric_ref, entity_ref)
        replacement["payload"]["required_inputs"].append({"input_ref": "metric_input.denominator"})
        revision_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Add denominator input",
                "replacement": replacement,
            },
        )
        self.assertEqual(revision_resp.status_code, 200, revision_resp.text)
        self.assertFalse(revision_resp.json()["can_activate_now"])

        derive_resp = self.client.post(
            f"/semantic/bindings/{binding_ref}/revisions/derive",
            json={
                "base_revision": 1,
                "source_action_id": "act_dependency_plan_complete_derive",
                "target_metric_ref": metric_ref,
                "target_metric_revision": 2,
                "reuse_sections": ["carrier", "time", "imports", "satisfied_field_coverage"],
                "coverage_additions": [
                    {
                        "coverage_target": "metric_input.denominator",
                        "field_ref": "field.denominator",
                    }
                ],
            },
        )
        self.assertEqual(derive_resp.status_code, 200, derive_resp.text)
        derived_binding = derive_resp.json()
        self.assertEqual(derived_binding["revision"], 2)
        activate_binding_resp = self.client.post(
            f"/semantic/bindings/{derived_binding['binding_id']}/publish"
        )
        self.assertEqual(activate_binding_resp.status_code, 200, activate_binding_resp.text)
        self.assertEqual(activate_binding_resp.json()["revision"], 2)
        published_binding_rows = metadata.query_rows(
            "SELECT binding_id FROM typed_bindings WHERE binding_ref = ? AND status = 'published'",
            [binding_ref],
        )
        self.assertEqual(len(published_binding_rows), 1)
        self.assertEqual(published_binding_rows[0]["binding_id"], derived_binding["binding_id"])

        revalidate_resp = self.client.post(
            f"/compiler/compatibility-profiles/{profile_ref}/revalidate",
            json={"subject_revision": 2},
        )
        self.assertEqual(revalidate_resp.status_code, 200, revalidate_resp.text)
        self.assertEqual(revalidate_resp.json()["subject_revision"], 2)

        read_revision_resp = self.client.get(f"/semantic/metrics/{metric_ref}/revisions/2")
        self.assertEqual(read_revision_resp.status_code, 200, read_revision_resp.text)
        self.assertTrue(read_revision_resp.json()["can_activate_now"])
        self.assertTrue(
            all(
                action["action_status"] == "satisfied"
                for action in read_revision_resp.json()["required_actions"]
            )
        )

        activate_metric_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions/2/activate"
        )
        self.assertEqual(activate_metric_resp.status_code, 200, activate_metric_resp.text)
        self.assertEqual(activate_metric_resp.json()["revision"], 2)

    def _assert_dependency_plan(
        self,
        payload: dict[str, Any],
        *,
        binding_ref: str,
        profile_ref: str,
        missing_input_ref: str,
    ) -> None:
        self.assertEqual(payload["classified_compatibility"], "breaking")
        self.assertIs(payload["can_activate_now"], False)
        actions = payload["required_actions"]
        action_names = {action["action"] for action in actions}
        self.assertIn("derive_revision", action_names)
        self.assertIn("add_binding_coverage", action_names)
        self.assertIn("reuse_after_revalidate", action_names)
        self.assertNotIn("resolve_breaking_revision_plan", action_names)
        self.assertTrue(
            any(
                action["action"] == "add_binding_coverage"
                and action["target_ref"] == binding_ref
                and action["completion_criteria"]["expected"]["coverage_target"]
                == missing_input_ref
                and action["depends_on"]
                and action["blocking"]
                for action in actions
            )
        )
        dependent_refs = {dependent["ref"] for dependent in payload["affected_dependents"]}
        self.assertIn(binding_ref, dependent_refs)
        self.assertIn(profile_ref, dependent_refs)

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

    def _mark_binding_published(self, binding_id: str) -> None:
        metadata = self.client.app.state.metadata_store
        metadata.execute(
            "UPDATE typed_bindings SET status = 'published' WHERE binding_id = ?",
            [binding_id],
        )

    def _count_metric_payload(self, metric_ref: str, entity_ref: str) -> dict[str, Any]:
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
                    "name": "users",
                    "semantics": "Users",
                    "aggregation": "count",
                },
                "required_inputs": [{"input_ref": "metric_input.count_target"}],
            },
        }

    def _rate_metric_payload(self, metric_ref: str, entity_ref: str) -> dict[str, Any]:
        metric_name = metric_ref.removeprefix("metric.")
        return {
            "header": {
                "metric_ref": metric_ref,
                "display_name": metric_name,
                "description": f"{metric_name} metric",
                "metric_family": "rate_metric",
                "observed_entity_ref": entity_ref,
                "observation_grain_ref": "grain.user",
                "sample_kind": "rate",
                "value_semantics": "ratio",
                "additivity_constraints": {
                    "dimension_policy": "none",
                    "time_axis_policy": "non_additive",
                },
                "metric_contract_version": "metric.v1",
            },
            "payload": {
                "metric_family": "rate_metric",
                "numerator": {
                    "name": "converted_users",
                    "semantics": "converted users",
                    "aggregation": "count_distinct",
                },
                "denominator": {
                    "name": "eligible_users",
                    "semantics": "eligible users",
                    "aggregation": "count_distinct",
                },
                "required_inputs": [{"input_ref": "metric_input.numerator"}],
            },
        }

    def _metric_binding_payload(
        self,
        *,
        binding_ref: str,
        metric_ref: str,
        covered_input_refs: list[str],
        target_key_by_input_ref: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        field_surfaces = [
            {
                "surface_ref": f"field.{input_ref.removeprefix('metric_input.')}",
                "physical_name": input_ref.removeprefix("metric_input."),
            }
            for input_ref in covered_input_refs
        ]
        return {
            "header": {
                "binding_ref": binding_ref,
                "display_name": binding_ref.removeprefix("binding."),
                "binding_scope": "metric",
                "bound_object_ref": metric_ref,
                "binding_contract_version": "binding.v1",
            },
            "interface_contract": {
                "carrier_bindings": [
                    {
                        "binding_key": "primary",
                        "carrier_kind": "table",
                        "carrier_locator": "warehouse.dependency_plan_count",
                        "binding_role": "primary",
                        "field_surfaces": field_surfaces,
                    }
                ],
                "field_bindings": [
                    {
                        "carrier_binding_key": "primary",
                        "target": {
                            "target_kind": "metric_input",
                            "target_key": (target_key_by_input_ref or {}).get(
                                input_ref, input_ref.removeprefix("metric_input.")
                            ),
                        },
                        "semantic_ref": input_ref,
                        "surface_ref": f"field.{input_ref.removeprefix('metric_input.')}",
                    }
                    for input_ref in covered_input_refs
                ],
            },
        }


if __name__ == "__main__":
    unittest.main()
