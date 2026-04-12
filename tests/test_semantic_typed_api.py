from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

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

    def _metadata(self):
        return self.client.app.state.services.metadata_store

    def _ensure_source_id(self) -> str:
        row = self._metadata().query_one("SELECT source_id FROM sources ORDER BY source_id LIMIT 1")
        if row is not None:
            return str(row["source_id"])
        source_id = f"src_{uuid4().hex[:12]}"
        now = "2026-04-09T00:00:00+00:00"
        self._metadata().execute(
            """
            INSERT INTO sources (
                source_id, source_type, display_name, connection_json,
                capabilities_json, sync_mode, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [source_id, "duckdb", "Test Source", "{}", "{}", "all", "active", now, now],
        )
        return source_id

    def _insert_source_object(
        self,
        *,
        fqn: str,
        object_type: str = "table",
        native_name: str | None = None,
    ) -> str:
        existing = self._metadata().query_one(
            "SELECT object_id FROM source_objects WHERE fqn = ? AND object_type = ?",
            [fqn, object_type],
        )
        if existing is not None:
            return str(existing["object_id"])
        object_id = f"obj_{uuid4().hex[:12]}"
        now = "2026-04-09T00:00:00+00:00"
        self._metadata().execute(
            """
            INSERT INTO source_objects (
                object_id, source_id, object_type, parent_id, native_name, native_id,
                fqn, properties_json, sync_version, synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                object_id,
                self._ensure_source_id(),
                object_type,
                None,
                native_name or fqn.rsplit(".", 1)[-1],
                None,
                fqn,
                "{}",
                "test_sync_v1",
                now,
                now,
                now,
            ],
        )
        return object_id

    def _assert_publish_error(
        self,
        response: object,
        *,
        code: str,
        message_substring: str,
        category: str | None = None,
    ) -> None:
        detail = response.json()["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["code"], code)
        if category is not None:
            self.assertEqual(detail["category"], category)
        self.assertIn(message_substring, detail["message"])

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
        entity_id = entity_resp.json()["entity_contract_id"]
        publish_entity_resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(publish_entity_resp.status_code, 200, publish_entity_resp.text)

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

        self._insert_source_object(fqn="warehouse.accounts")
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
        self.assertIsNone(profile["subject_revision"])

        resp = self.client.put(
            f"/compiler/compatibility-profiles/{profile_id}",
            json={"requirement": {"entity_refs": ["entity.account", "entity.user"]}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["requirement"]["entity_refs"][1], "entity.user")

        resp = self.client.post(
            f"/semantic/metrics/{metric_resp.json()['metric_contract_id']}/publish"
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        resp = self.client.post(f"/compiler/compatibility-profiles/{profile_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")
        self.assertEqual(resp.json()["subject_revision"], 2)

        resp = self.client.get("/semantic/bindings?status=published")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["status"] == "published" for item in resp.json()["items"]))

        resp = self.client.get("/compiler/compatibility-profiles?status=published")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["status"] == "published" for item in resp.json()["items"]))

    def test_binding_rejects_unknown_surface_for_carrier(self) -> None:
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.surface_case_{uuid4().hex[:8]}",
                    "display_name": "Surface Case",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.surface_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_ref = entity_resp.json()["header"]["entity_ref"]

        resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.surface_case_{uuid4().hex[:8]}",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.surface_case",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.real_id", "physical_name": "real_id"}
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": "key.surface_id",
                            },
                            "semantic_ref": "key.surface_id",
                            "surface_ref": "field.missing_id",
                        }
                    ],
                },
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("surface_ref does not exist", resp.json()["detail"])

    def test_entity_binding_requires_identity_time_and_descriptor_targets(self) -> None:
        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": f"time.account_created_{uuid4().hex[:8]}",
                    "display_name": "Account Created",
                    "semantic_roles": ["business_anchor", "measurement"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_ref = time_resp.json()["header"]["time_ref"]

        dimension_resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": f"dimension.account_country_{uuid4().hex[:8]}",
                    "display_name": "Account Country",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "open",
                    }
                },
            },
        )
        self.assertEqual(dimension_resp.status_code, 200, dimension_resp.text)
        dimension_ref = dimension_resp.json()["header"]["dimension_ref"]

        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.account_contract_{uuid4().hex[:8]}",
                    "display_name": "Account Contract",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.account_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "primary_time_ref": time_ref,
                    "stable_descriptors": [{"dimension_ref": dimension_ref, "cardinality": "one"}],
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_ref = entity_resp.json()["header"]["entity_ref"]

        resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.account_contract_{uuid4().hex[:8]}",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.account_contract",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.account_id", "physical_name": "account_id"},
                                {"surface_ref": "field.created_at", "physical_name": "created_at"},
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
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": time_ref,
                            },
                            "semantic_ref": time_ref,
                            "surface_ref": "field.created_at",
                        },
                    ],
                },
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("stable descriptor", resp.json()["detail"])

    def test_experiment_process_binding_requires_process_context_and_join_relations(self) -> None:
        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": f"time.exposure_case_{uuid4().hex[:8]}",
                    "display_name": "Exposure Time",
                    "semantic_roles": ["business_anchor", "measurement"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_ref = time_resp.json()["header"]["time_ref"]

        process_resp = self.client.post(
            "/semantic/process-objects",
            json={
                "header": {
                    "process_ref": f"process.experiment_case_{uuid4().hex[:8]}",
                    "display_name": "Experiment Case",
                    "process_type": "experiment_context",
                    "process_contract_version": "process.v2",
                },
                "interface_contract": {
                    "contract_mode": "context_provider",
                    "context_kind": "experiment_split",
                    "population_subject_ref": "subject.user",
                    "membership_cardinality": "exclusive_one",
                    "anchor_time_ref": time_ref,
                },
                "payload": {
                    "process_type": "experiment_context",
                    "experiment_key": "checkout-redesign",
                    "variants": [
                        {"variant_key": "control", "population_ref": "population.control"},
                        {"variant_key": "treatment", "population_ref": "population.treatment"},
                    ],
                    "split_basis": {
                        "kind": "assignment",
                        "basis_ref": "event.assignment",
                        "resolution": "first",
                    },
                    "analysis_window": {"size": {"value": 7, "unit": "day"}},
                },
            },
        )
        self.assertEqual(process_resp.status_code, 200, process_resp.text)
        process_ref = process_resp.json()["header"]["process_ref"]

        missing_context_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.experiment_case_{uuid4().hex[:8]}",
                    "binding_scope": "process_object",
                    "bound_object_ref": process_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "assignment",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.exp_assignment",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.user_id", "physical_name": "user_id"},
                                {
                                    "surface_ref": "field.assigned_at",
                                    "physical_name": "assigned_at",
                                },
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "assignment",
                            "target": {
                                "target_kind": "population_subject",
                                "target_key": "key.user_id",
                            },
                            "semantic_ref": "key.user_id",
                            "surface_ref": "field.user_id",
                        },
                        {
                            "carrier_binding_key": "assignment",
                            "target": {
                                "target_kind": "analysis_window_anchor",
                                "target_key": time_ref,
                            },
                            "semantic_ref": time_ref,
                            "surface_ref": "field.assigned_at",
                        },
                    ],
                },
            },
        )
        self.assertEqual(missing_context_resp.status_code, 422, missing_context_resp.text)
        self.assertIn("process_context", missing_context_resp.json()["detail"])

        missing_join_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.experiment_join_{uuid4().hex[:8]}",
                    "binding_scope": "process_object",
                    "bound_object_ref": process_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "assignment",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.exp_assignment",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.user_id", "physical_name": "user_id"},
                                {
                                    "surface_ref": "field.experiment_id",
                                    "physical_name": "experiment_id",
                                },
                                {
                                    "surface_ref": "field.assigned_at",
                                    "physical_name": "assigned_at",
                                },
                            ],
                        },
                        {
                            "binding_key": "exposure",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.exp_exposure",
                            "binding_role": "auxiliary",
                            "field_surfaces": [
                                {"surface_ref": "field.user_id", "physical_name": "user_id"},
                            ],
                        },
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "assignment",
                            "target": {
                                "target_kind": "population_subject",
                                "target_key": "key.user_id",
                            },
                            "semantic_ref": "key.user_id",
                            "surface_ref": "field.user_id",
                        },
                        {
                            "carrier_binding_key": "assignment",
                            "target": {
                                "target_kind": "process_context",
                                "target_key": "process.experiment_id",
                            },
                            "semantic_ref": "process.experiment_id",
                            "surface_ref": "field.experiment_id",
                        },
                        {
                            "carrier_binding_key": "assignment",
                            "target": {
                                "target_kind": "analysis_window_anchor",
                                "target_key": time_ref,
                            },
                            "semantic_ref": time_ref,
                            "surface_ref": "field.assigned_at",
                        },
                        {
                            "carrier_binding_key": "exposure",
                            "target": {
                                "target_kind": "population_subject",
                                "target_key": "key.user_id",
                            },
                            "semantic_ref": "key.user_id",
                            "surface_ref": "field.user_id",
                        },
                    ],
                },
            },
        )
        self.assertEqual(missing_join_resp.status_code, 422, missing_join_resp.text)
        self.assertIn("join_relations", missing_join_resp.json()["detail"])

    def test_rate_metric_binding_requires_numerator_and_denominator(self) -> None:
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.metric_case_{uuid4().hex[:8]}",
                    "display_name": "Metric Case Entity",
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
        entity_ref = entity_resp.json()["header"]["entity_ref"]

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": f"metric.rate_case_{uuid4().hex[:8]}",
                    "display_name": "Rate Case",
                    "metric_family": "rate_metric",
                    "population_subject_ref": "subject.user",
                    "observed_entity_ref": entity_ref,
                    "observation_grain_ref": "grain.user",
                    "sample_kind": "rate",
                    "value_semantics": "ratio",
                    "additivity": "non_additive",
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "rate_metric",
                    "numerator": {
                        "name": "converted",
                        "semantics": "converted users",
                        "aggregation": "count_distinct",
                    },
                    "denominator": {
                        "name": "eligible",
                        "semantics": "eligible users",
                        "aggregation": "count_distinct",
                    },
                },
            },
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        metric_ref = metric_resp.json()["header"]["metric_ref"]

        resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.rate_case_{uuid4().hex[:8]}",
                    "binding_scope": "metric",
                    "bound_object_ref": metric_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "metric_fact",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.rate_case",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.user_id", "physical_name": "user_id"},
                                {"surface_ref": "field.event_id", "physical_name": "event_id"},
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "metric_fact",
                            "target": {
                                "target_kind": "population_subject",
                                "target_key": "key.user_id",
                            },
                            "semantic_ref": "key.user_id",
                            "surface_ref": "field.user_id",
                        },
                        {
                            "carrier_binding_key": "metric_fact",
                            "target": {
                                "target_kind": "metric_input",
                                "target_key": "numerator",
                            },
                            "semantic_ref": "metric_input.converted_users",
                            "surface_ref": "field.event_id",
                        },
                    ],
                },
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("numerator' and 'denominator", resp.json()["detail"])

    def test_binding_publish_requires_published_dependencies_and_grounding(self) -> None:
        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": f"time.publish_case_{uuid4().hex[:8]}",
                    "display_name": "Publish Time",
                    "semantic_roles": ["business_anchor", "measurement"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_contract_id = time_resp.json()["time_contract_id"]
        time_ref = time_resp.json()["header"]["time_ref"]

        dimension_resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": f"dimension.publish_country_{uuid4().hex[:8]}",
                    "display_name": "Publish Country",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "open",
                    }
                },
            },
        )
        self.assertEqual(dimension_resp.status_code, 200, dimension_resp.text)
        dimension_contract_id = dimension_resp.json()["dimension_contract_id"]
        dimension_ref = dimension_resp.json()["header"]["dimension_ref"]

        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.publish_case_{uuid4().hex[:8]}",
                    "display_name": "Publish Case Entity",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.publish_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "primary_time_ref": time_ref,
                    "stable_descriptors": [{"dimension_ref": dimension_ref}],
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_contract_id = entity_resp.json()["entity_contract_id"]
        entity_ref = entity_resp.json()["header"]["entity_ref"]

        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.publish_case_{uuid4().hex[:8]}",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.publish_case_entity",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.publish_id", "physical_name": "publish_id"},
                                {"surface_ref": "field.created_at", "physical_name": "created_at"},
                                {"surface_ref": "field.country", "physical_name": "country"},
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": "key.publish_id",
                            },
                            "semantic_ref": "key.publish_id",
                            "surface_ref": "field.publish_id",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "primary_time", "target_key": time_ref},
                            "semantic_ref": time_ref,
                            "surface_ref": "field.created_at",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "stable_descriptor",
                                "target_key": dimension_ref,
                            },
                            "semantic_ref": dimension_ref,
                            "surface_ref": "field.country",
                        },
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        binding_id = binding_resp.json()["binding_id"]

        publish_resp = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(publish_resp.status_code, 422, publish_resp.text)
        self._assert_publish_error(
            publish_resp,
            code="reference_validation_error",
            category="validation",
            message_substring="must be published",
        )

        self.assertEqual(
            self.client.post(f"/semantic/time/{time_contract_id}/publish").status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"/semantic/dimensions/{dimension_contract_id}/publish").status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"/semantic/entities/{entity_contract_id}/publish").status_code,
            200,
        )
        publish_resp = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(publish_resp.status_code, 422, publish_resp.text)
        self._assert_publish_error(
            publish_resp,
            code="reference_validation_error",
            category="validation",
            message_substring="carrier_locator must resolve",
        )

        self._insert_source_object(fqn="warehouse.publish_case_entity")
        publish_resp = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)
        self.assertEqual(publish_resp.json()["status"], "published")

    def test_binding_publish_requires_imports_to_be_published(self) -> None:
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.import_case_{uuid4().hex[:8]}",
                    "display_name": "Import Case Entity",
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
        entity_contract_id = entity_resp.json()["entity_contract_id"]
        entity_ref = entity_resp.json()["header"]["entity_ref"]
        self.assertEqual(
            self.client.post(f"/semantic/entities/{entity_contract_id}/publish").status_code,
            200,
        )

        imported_binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.imported_case_{uuid4().hex[:8]}",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.imported_case",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.user_id", "physical_name": "user_id"}
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": "key.user_id",
                            },
                            "semantic_ref": "key.user_id",
                            "surface_ref": "field.user_id",
                        }
                    ],
                },
            },
        )
        self.assertEqual(imported_binding_resp.status_code, 200, imported_binding_resp.text)
        imported_binding_ref = imported_binding_resp.json()["header"]["binding_ref"]

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": f"metric.import_case_{uuid4().hex[:8]}",
                    "display_name": "Import Case Metric",
                    "metric_family": "count_metric",
                    "population_subject_ref": "subject.user",
                    "observed_entity_ref": entity_ref,
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
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        metric_contract_id = metric_resp.json()["metric_contract_id"]
        metric_ref = metric_resp.json()["header"]["metric_ref"]
        self.assertEqual(
            self.client.post(f"/semantic/metrics/{metric_contract_id}/publish").status_code,
            200,
        )

        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.metric_import_case_{uuid4().hex[:8]}",
                    "binding_scope": "metric",
                    "bound_object_ref": metric_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "imports": [
                        {
                            "import_key": "identity_binding",
                            "binding_ref": imported_binding_ref,
                        }
                    ],
                    "carrier_bindings": [
                        {
                            "binding_key": "metric_fact",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.metric_import_case",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.user_id", "physical_name": "user_id"},
                                {"surface_ref": "field.event_id", "physical_name": "event_id"},
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "metric_fact",
                            "target": {
                                "target_kind": "population_subject",
                                "target_key": "key.user_id",
                            },
                            "semantic_ref": "key.user_id",
                            "surface_ref": "field.user_id",
                        },
                        {
                            "carrier_binding_key": "metric_fact",
                            "target": {
                                "target_kind": "metric_input",
                                "target_key": "count_target",
                            },
                            "semantic_ref": "metric_input.user_count",
                            "surface_ref": "field.event_id",
                        },
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        binding_id = binding_resp.json()["binding_id"]

        publish_resp = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(publish_resp.status_code, 422, publish_resp.text)
        self._assert_publish_error(
            publish_resp,
            code="reference_validation_error",
            category="validation",
            message_substring="Imported binding must be published",
        )

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
        self.assertEqual(resp.json()["revision"], 2)

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
        self.assertEqual(resp.json()["revision"], 2)

        resp = self.client.put(
            f"/semantic/dimensions/{dimension_contract_id}",
            json={"description": "Updated dimension description"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["description"], "Updated dimension description")
        self.assertEqual(resp.json()["revision"], 2)

        resp = self.client.put(
            f"/semantic/process-objects/{process_contract_id}",
            json={"description": "Updated process description"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["description"], "Updated process description")
        self.assertEqual(resp.json()["revision"], 2)

        for path in [
            f"/semantic/time/{time_contract_id}/publish",
            f"/semantic/enum-sets/{enum_set_contract_id}/publish",
            f"/semantic/dimensions/{dimension_contract_id}/publish",
            f"/semantic/process-objects/{process_contract_id}/publish",
        ]:
            resp = self.client.post(path)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["status"], "published")
            self.assertEqual(resp.json()["revision"], 3)

        for path in [
            f"/semantic/time/{time_contract_id}",
            f"/semantic/enum-sets/{enum_set_contract_id}",
            f"/semantic/dimensions/{dimension_contract_id}",
            f"/semantic/process-objects/{process_contract_id}",
        ]:
            resp = self.client.put(path, json={"description": "Should fail after publish"})
            self.assertEqual(resp.status_code, 422, resp.text)
            self.assertIn("not in draft status", resp.json()["detail"])

        for path in [
            f"/semantic/time/{time_contract_id}/publish",
            f"/semantic/enum-sets/{enum_set_contract_id}/publish",
            f"/semantic/dimensions/{dimension_contract_id}/publish",
            f"/semantic/process-objects/{process_contract_id}/publish",
        ]:
            resp = self.client.post(path)
            self.assertEqual(resp.status_code, 422, resp.text)
            self._assert_publish_error(
                resp,
                code="publish_state_error",
                category="state",
                message_substring="not in draft status",
            )

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

    def test_publish_requires_published_cross_object_refs(self) -> None:
        enum_resp = self.client.post(
            "/semantic/enum-sets",
            json={
                "header": {"enum_set_ref": "enum.lifecycle_status", "value_type": "string"},
                "display_name": "Lifecycle Status",
                "description": "Draft enum for publish validation",
                "versions": [
                    {
                        "enum_version": "v1",
                        "values": [
                            {"value_key": "new", "raw_value": "new", "label": "New"},
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
                    "dimension_ref": "dimension.lifecycle_status",
                    "display_name": "Lifecycle Status",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                        "enum_set_ref": "enum.lifecycle_status",
                        "enum_version": "v1",
                    }
                },
            },
        )
        self.assertEqual(dimension_resp.status_code, 200, dimension_resp.text)
        dimension_contract_id = dimension_resp.json()["dimension_contract_id"]

        resp = self.client.post(f"/semantic/dimensions/{dimension_contract_id}/publish")
        self.assertEqual(resp.status_code, 422, resp.text)
        self._assert_publish_error(
            resp,
            code="reference_validation_error",
            category="validation",
            message_substring="must be published",
        )

        resp = self.client.post(f"/semantic/enum-sets/{enum_set_contract_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)

        resp = self.client.post(f"/semantic/dimensions/{dimension_contract_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)

        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": "time.lifecycle_anchor",
                    "display_name": "Lifecycle Anchor",
                    "semantic_roles": ["business_anchor"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_contract_id = time_resp.json()["time_contract_id"]

        process_resp = self.client.post(
            "/semantic/process-objects",
            json={
                "header": {
                    "process_ref": "process.lifecycle_cohort",
                    "display_name": "Lifecycle Cohort",
                    "process_type": "cohort_definition",
                    "process_contract_version": "process.v2",
                },
                "interface_contract": {
                    "contract_mode": "context_provider",
                    "context_kind": "cohort_membership",
                    "population_subject_ref": "subject.user",
                    "membership_cardinality": "exclusive_one",
                    "anchor_time_ref": "time.lifecycle_anchor",
                    "exported_dimension_refs": ["dimension.lifecycle_status"],
                },
                "payload": {
                    "process_type": "cohort_definition",
                    "cohort_key": "lifecycle_users",
                    "entry_population": {"base_population_ref": "population.users"},
                    "cohort_anchor_ref": "time.lifecycle_anchor",
                },
            },
        )
        self.assertEqual(process_resp.status_code, 200, process_resp.text)
        process_contract_id = process_resp.json()["process_contract_id"]

        resp = self.client.post(f"/semantic/process-objects/{process_contract_id}/publish")
        self.assertEqual(resp.status_code, 422, resp.text)
        self._assert_publish_error(
            resp,
            code="reference_validation_error",
            category="validation",
            message_substring="must be published",
        )

        resp = self.client.post(f"/semantic/time/{time_contract_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)

        resp = self.client.post(f"/semantic/process-objects/{process_contract_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)

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

    def test_publish_requires_published_entity_and_metric_refs(self) -> None:
        # Create a time object but do NOT publish it yet
        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": "time.entity_test_anchor",
                    "display_name": "Entity Test Anchor",
                    "semantic_roles": ["business_anchor"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_contract_id = time_resp.json()["time_contract_id"]

        # Create a dimension to use as stable_descriptor
        dim_resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": "dimension.entity_test_status",
                    "display_name": "Entity Test Status",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "open",
                    }
                },
            },
        )
        self.assertEqual(dim_resp.status_code, 200, dim_resp.text)
        dim_contract_id = dim_resp.json()["dimension_contract_id"]

        # Create entity with primary_time_ref and stable_descriptors
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.publish_test_user",
                    "display_name": "Publish Test User",
                    "entity_contract_version": "entity.v1",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.publish_test_user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "primary_time_ref": "time.entity_test_anchor",
                    "stable_descriptors": [
                        {"dimension_ref": "dimension.entity_test_status", "cardinality": "one"}
                    ],
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_contract_id = entity_resp.json()["entity_contract_id"]

        # Publishing entity should fail because primary_time_ref is not published
        resp = self.client.post(f"/semantic/entities/{entity_contract_id}/publish")
        self.assertEqual(resp.status_code, 422, resp.text)
        self._assert_publish_error(
            resp,
            code="reference_validation_error",
            category="validation",
            message_substring="must be published",
        )

        # Publish the time object first
        resp = self.client.post(f"/semantic/time/{time_contract_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)

        # Publishing entity still fails because stable_descriptor dimension is not published
        resp = self.client.post(f"/semantic/entities/{entity_contract_id}/publish")
        self.assertEqual(resp.status_code, 422, resp.text)
        self._assert_publish_error(
            resp,
            code="reference_validation_error",
            category="validation",
            message_substring="must be published",
        )

        # Publish the dimension
        resp = self.client.post(f"/semantic/dimensions/{dim_contract_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)

        # Now entity should publish successfully
        resp = self.client.post(f"/semantic/entities/{entity_contract_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")

        # Create a metric with observed_entity_ref pointing to a draft entity
        draft_entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.publish_test_draft",
                    "display_name": "Draft Entity",
                    "entity_contract_version": "entity.v1",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.publish_test_draft_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(draft_entity_resp.status_code, 200, draft_entity_resp.text)

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.publish_test_dau",
                    "display_name": "Publish Test DAU",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.publish_test_draft",
                    "observation_grain_ref": "grain.publish_test_user",
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
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        metric_contract_id = metric_resp.json()["metric_contract_id"]

        # Publishing metric should fail because observed_entity_ref is draft
        resp = self.client.post(f"/semantic/metrics/{metric_contract_id}/publish")
        self.assertEqual(resp.status_code, 422, resp.text)
        self._assert_publish_error(
            resp,
            code="reference_validation_error",
            category="validation",
            message_substring="must be published",
        )

    def test_publish_ref_validation_distinguishes_unknown_from_draft(self) -> None:
        # Create entity with a primary_time_ref that does NOT exist
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.unknown_ref_test",
                    "display_name": "Unknown Ref Test Entity",
                    "entity_contract_version": "entity.v1",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.unknown_ref_test_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "primary_time_ref": "time.does_not_exist",
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_contract_id = entity_resp.json()["entity_contract_id"]

        resp = self.client.post(f"/semantic/entities/{entity_contract_id}/publish")
        self.assertEqual(resp.status_code, 422, resp.text)
        detail = resp.json()["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["code"], "reference_validation_error")
        self.assertIn("Unknown", detail["message"])
        self.assertNotIn("must be published", detail["message"])
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
        payload = resp.json()
        self.assertIsInstance(payload["detail"], list)
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertEqual(payload["guidance"]["docs_url"], "docs/api/semantic.md")
        self.assertEqual(
            payload["guidance"]["schema_url"], "/openapi/schemas/TypedEntityCreateRequest?depth=2"
        )
        self.assertEqual(
            payload["guidance"]["contract_url"],
            "/openapi/paths/L3NlbWFudGljL2VudGl0aWVz?operation=post&expand=request,schemas&depth=2",
        )
        self.assertGreaterEqual(len(payload["guidance"]["examples"]), 1)

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
        payload = resp.json()
        self.assertIsInstance(payload["detail"], list)
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertEqual(payload["guidance"]["docs_url"], "docs/api/semantic.md")
        self.assertIn("contract_url", payload["guidance"])

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
        payload = resp.json()
        self.assertIsInstance(payload["detail"], list)
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertEqual(payload["guidance"]["docs_url"], "docs/api/semantic.md")

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

    def test_binding_and_profile_readiness_surfaces_update_after_dependencies_change(self) -> None:
        suffix = uuid4().hex[:8]

        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.readiness_case_{suffix}",
                    "display_name": "Readiness Case",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.readiness_id_{suffix}"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_id = entity_resp.json()["entity_contract_id"]
        self.assertEqual(
            self.client.post(f"/semantic/entities/{entity_id}/publish").status_code, 200
        )

        binding_ref = f"binding.readiness_case_{suffix}"
        source_fqn = f"warehouse.readiness_case_{suffix}"
        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": binding_ref,
                    "display_name": "Readiness Binding",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_resp.json()["header"]["entity_ref"],
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": source_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {
                                    "surface_ref": "field.entity_id",
                                    "physical_name": "entity_id",
                                }
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": f"key.readiness_id_{suffix}",
                            },
                            "semantic_ref": f"key.readiness_id_{suffix}",
                            "surface_ref": "field.entity_id",
                        }
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        binding_id = binding_resp.json()["binding_id"]
        self._insert_source_object(fqn=source_fqn)
        publish_binding_resp = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(publish_binding_resp.status_code, 200, publish_binding_resp.text)

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": f"metric.readiness_case_{suffix}",
                    "display_name": "Readiness Metric",
                    "metric_family": "count_metric",
                    "observed_entity_ref": entity_resp.json()["header"]["entity_ref"],
                    "observation_grain_ref": "grain.account",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity": "additive",
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "rows",
                        "semantics": "row count",
                        "aggregation": "count",
                    },
                },
            },
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        metric_id = metric_resp.json()["metric_contract_id"]
        self.assertEqual(
            self.client.post(f"/semantic/metrics/{metric_id}/publish").status_code, 200
        )

        profile_resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": f"compiler_profile.readiness_case_{suffix}",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": metric_resp.json()["header"]["metric_ref"],
                "requirement": {"entity_refs": [entity_resp.json()["header"]["entity_ref"]]},
            },
        )
        self.assertEqual(profile_resp.status_code, 200, profile_resp.text)
        profile_id = profile_resp.json()["profile_id"]
        publish_profile_resp = self.client.post(
            f"/compiler/compatibility-profiles/{profile_id}/publish"
        )
        self.assertEqual(publish_profile_resp.status_code, 200, publish_profile_resp.text)

        self._metadata().execute("DELETE FROM source_objects WHERE fqn = ?", [source_fqn])
        self._metadata().execute(
            "UPDATE semantic_metric_contracts SET revision = revision + 1 WHERE metric_contract_id = ?",
            [metric_id],
        )

        binding_detail_resp = self.client.get(f"/semantic/bindings/{binding_id}")
        self.assertEqual(binding_detail_resp.status_code, 200, binding_detail_resp.text)
        self.assertEqual(binding_detail_resp.json()["readiness_status"], "not_ready")
        self.assertEqual(
            binding_detail_resp.json()["dependency_refs"],
            [
                entity_resp.json()["header"]["entity_ref"],
                source_fqn,
            ],
        )
        self.assertIn(
            "BINDING_CARRIER_SOURCE_MISSING",
            {item["code"] for item in binding_detail_resp.json()["blocking_requirements"]},
        )

        profile_detail_resp = self.client.get(f"/compiler/compatibility-profiles/{profile_id}")
        self.assertEqual(profile_detail_resp.status_code, 200, profile_detail_resp.text)
        self.assertEqual(profile_detail_resp.json()["readiness_status"], "stale")
        self.assertEqual(
            profile_detail_resp.json()["dependency_refs"],
            [metric_resp.json()["header"]["metric_ref"]],
        )
        self.assertIn(
            "PROFILE_SUBJECT_REVISION_MISMATCH",
            {item["code"] for item in profile_detail_resp.json()["blocking_requirements"]},
        )

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
