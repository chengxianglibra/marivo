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

        resp = self.client.get("/semantic/entities")
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

        resp = self.client.get("/semantic/metrics")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertGreaterEqual(resp.json()["total"], 1)

    def test_entity_routes_reject_legacy_payloads(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "user", "display_name": "User", "keys": ["user_id"]},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

    def test_metric_routes_reject_legacy_payloads(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "name": "dau",
                "display_name": "DAU",
                "definition_sql": "COUNT(DISTINCT user_id)",
                "dimensions": ["event_date"],
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

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

        resp = self.client.get("/semantic/bindings?status=published")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["status"] == "published" for item in resp.json()["items"]))

        resp = self.client.get("/compiler/compatibility-profiles?status=published")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["status"] == "published" for item in resp.json()["items"]))

    def test_process_object_dimension_time_and_enum_set_lifecycle(self) -> None:
        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": "time.signup_time",
                    "display_name": "Signup Time",
                    "semantic_roles": ["business_anchor", "measurement"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_contract_id = time_resp.json()["time_contract_id"]

        enum_resp = self.client.post(
            "/semantic/enum-sets",
            json={
                "header": {"enum_set_ref": "enum.country_code", "value_type": "string"},
                "display_name": "Country Code",
                "description": "ISO countries",
                "versions": [
                    {
                        "enum_version": "v1",
                        "values": [
                            {"value_key": "CN", "raw_value": "CN", "label": "China"},
                            {
                                "value_key": "US",
                                "raw_value": "US",
                                "label": "United States",
                            },
                        ],
                    }
                ],
            },
        )
        self.assertEqual(enum_resp.status_code, 200, enum_resp.text)
        enum_set_contract_id = enum_resp.json()["enum_set_contract_id"]

        dimension_resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": "dimension.signup_country",
                    "display_name": "Signup Country",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                        "enum_set_ref": "enum.country_code",
                        "enum_version": "v1",
                    },
                    "grouping": {"supports_grouping": True},
                },
            },
        )
        self.assertEqual(dimension_resp.status_code, 200, dimension_resp.text)
        dimension_contract_id = dimension_resp.json()["dimension_contract_id"]

        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.signup_event",
                    "display_name": "Signup Event",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.signup_event_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "ephemeral",
                    },
                    "primary_time_ref": "time.signup_time",
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)

        process_resp = self.client.post(
            "/semantic/process-objects",
            json={
                "header": {
                    "process_ref": "process.new_user_cohort",
                    "display_name": "New User Cohort",
                    "process_type": "cohort_definition",
                    "process_contract_version": "process.v2",
                },
                "interface_contract": {
                    "contract_mode": "context_provider",
                    "context_kind": "cohort_membership",
                    "population_subject_ref": "subject.user",
                    "membership_cardinality": "exclusive_one",
                    "anchor_time_ref": "time.signup_time",
                    "exported_dimension_refs": ["dimension.signup_country"],
                },
                "payload": {
                    "process_type": "cohort_definition",
                    "cohort_key": "new_users",
                    "entry_population": {"base_population_ref": "population.users"},
                    "cohort_anchor_ref": "time.signup_time",
                },
            },
        )
        self.assertEqual(process_resp.status_code, 200, process_resp.text)
        process_contract_id = process_resp.json()["process_contract_id"]

        resp = self.client.put(
            f"/semantic/time/{time_contract_id}",
            json={"semantic_roles": ["business_anchor"]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["semantic_roles"], ["business_anchor"])

        resp = self.client.put(
            f"/semantic/enum-sets/{enum_set_contract_id}",
            json={
                "description": "Updated countries",
                "versions": [
                    {
                        "enum_version": "v2",
                        "values": [
                            {"value_key": "CN", "raw_value": "CN", "label": "China"},
                            {"value_key": "JP", "raw_value": "JP", "label": "Japan"},
                        ],
                    }
                ],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["description"], "Updated countries")
        self.assertEqual(resp.json()["versions"][0]["enum_version"], "v2")

        resp = self.client.put(
            f"/semantic/dimensions/{dimension_contract_id}",
            json={"description": "Updated dimension description"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["description"], "Updated dimension description")

        resp = self.client.put(
            f"/semantic/process-objects/{process_contract_id}",
            json={"description": "Updated process description"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["description"], "Updated process description")

        for path in [
            f"/semantic/time/{time_contract_id}/publish",
            f"/semantic/enum-sets/{enum_set_contract_id}/publish",
            f"/semantic/dimensions/{dimension_contract_id}/publish",
            f"/semantic/process-objects/{process_contract_id}/publish",
        ]:
            resp = self.client.post(path)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["status"], "published")

        for path in [
            "/semantic/time",
            "/semantic/enum-sets",
            "/semantic/dimensions",
            "/semantic/process-objects",
        ]:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertGreaterEqual(resp.json()["total"], 1)

    def test_new_typed_object_routes_use_consistent_404_detail(self) -> None:
        for path in [
            "/semantic/process-objects/proc_missing",
            "/semantic/dimensions/dimc_missing",
            "/semantic/time/timec_missing",
            "/semantic/enum-sets/enumc_missing",
        ]:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 404, resp.text)
            self.assertIsInstance(resp.json()["detail"], str)

    def test_new_typed_object_routes_reject_unknown_cross_object_refs(self) -> None:
        resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": "dimension.invalid_country",
                    "display_name": "Invalid Country",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                        "enum_set_ref": "enum.missing",
                        "enum_version": "v1",
                    }
                },
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], str)

        resp = self.client.post(
            "/semantic/process-objects",
            json={
                "header": {
                    "process_ref": "process.invalid_cohort",
                    "display_name": "Invalid Cohort",
                    "process_type": "cohort_definition",
                    "process_contract_version": "process.v2",
                },
                "interface_contract": {
                    "contract_mode": "context_provider",
                    "context_kind": "cohort_membership",
                    "population_subject_ref": "subject.user",
                    "membership_cardinality": "exclusive_one",
                    "anchor_time_ref": "time.missing",
                    "exported_dimension_refs": ["dimension.missing"],
                },
                "payload": {
                    "process_type": "cohort_definition",
                    "cohort_key": "invalid_users",
                    "entry_population": {"base_population_ref": "population.users"},
                    "cohort_anchor_ref": "time.missing",
                },
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], str)

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

        resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "invalid_binding_ref",
                    "display_name": "Invalid Binding",
                    "binding_scope": "entity",
                    "bound_object_ref": "entity.account",
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [],
                    "field_bindings": [],
                },
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

        resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": "invalid_profile_ref",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": "metric.error_case",
                "requirement": {"entity_refs": ["entity.account"]},
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

    def test_enum_set_update_rejects_raw_value_type_mismatch(self) -> None:
        """Updating enum set versions must reject raw_values that don't match
        the immutable value_type declared at creation time."""
        create_resp = self.client.post(
            "/semantic/enum-sets",
            json={
                "header": {"enum_set_ref": "enum.status_code", "value_type": "integer"},
                "display_name": "Status Code",
                "description": "Integer status codes",
                "versions": [
                    {
                        "enum_version": "v1",
                        "values": [
                            {"value_key": "active", "raw_value": 1, "label": "Active"},
                            {"value_key": "inactive", "raw_value": 0, "label": "Inactive"},
                        ],
                    }
                ],
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        enum_set_contract_id = create_resp.json()["enum_set_contract_id"]

        # Attempt to update versions with string raw_values on an integer enum set
        resp = self.client.put(
            f"/semantic/enum-sets/{enum_set_contract_id}",
            json={
                "versions": [
                    {
                        "enum_version": "v2",
                        "values": [
                            {"value_key": "active", "raw_value": "active", "label": "Active"},
                        ],
                    }
                ]
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("value_type", resp.json()["detail"])
