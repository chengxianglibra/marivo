from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import pytest
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
        row = self._metadata().query_one(
            "SELECT datasource_id FROM datasources ORDER BY datasource_id LIMIT 1"
        )
        if row is not None:
            return str(row["datasource_id"])
        datasource_id = f"ds_{uuid4().hex[:12]}"
        now = "2026-04-09T00:00:00+00:00"
        self._metadata().execute(
            """
            INSERT INTO datasources (
                datasource_id, datasource_type, display_name, connection_json,
                sync_mode, policy_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                datasource_id,
                "duckdb",
                "Test Datasource",
                "{}",
                "all",
                '{"allow_live_browse": true, "allow_sync": true}',
                "active",
                now,
                now,
            ],
        )
        return datasource_id

    def _insert_source_object(
        self,
        *,
        fqn: str,
        object_type: str = "table",
        native_name: str | None = None,
        parent_id: str | None = None,
        properties: dict[str, object] | None = None,
    ) -> str:
        existing = self._metadata().query_one(
            """
            SELECT object_id
            FROM source_objects
            WHERE fqn = ? AND object_type = ? AND parent_id IS ?
            """,
            [fqn, object_type, parent_id],
        )
        if existing is not None:
            return str(existing["object_id"])
        object_id = f"obj_{uuid4().hex[:12]}"
        now = "2026-04-09T00:00:00+00:00"
        fqn_parts = fqn.split(".")
        datasource_row = self._metadata().query_one(
            "SELECT connection_json FROM datasources WHERE datasource_id = ?",
            [self._ensure_source_id()],
        )
        catalog: str | None = None
        if len(fqn_parts) >= 3:
            catalog = fqn_parts[-3]
        elif datasource_row is not None:
            connection = json.loads(str(datasource_row["connection_json"]))
            if isinstance(connection, dict):
                catalog = "main"
        self._metadata().execute(
            """
            INSERT INTO source_objects (
                object_id, datasource_id, object_type, parent_id, native_name, native_id,
                fqn, authority_locator_json, properties_json, sync_version, synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                object_id,
                self._ensure_source_id(),
                object_type,
                parent_id,
                native_name or fqn.rsplit(".", 1)[-1],
                None,
                fqn,
                json.dumps(
                    {
                        "catalog": catalog,
                        "schema": fqn_parts[-2] if len(fqn_parts) >= 2 else None,
                        "table": fqn_parts[-1],
                    }
                ),
                json.dumps(properties or {}),
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

    def _insert_legacy_non_entity_binding(
        self,
        *,
        binding_ref: str,
        binding_scope: str = "metric",
        bound_object_ref: str = "metric.legacy_grounding",
        status: str = "draft",
    ) -> str:
        binding_id = f"bind_{uuid4().hex[:24]}"
        now = "2026-04-09T00:00:00+00:00"
        self._metadata().execute(
            """
            INSERT INTO typed_bindings (
                binding_id, binding_ref, binding_scope, bound_object_ref,
                binding_contract_version, display_name, description,
                status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                binding_id,
                binding_ref,
                binding_scope,
                bound_object_ref,
                "binding.v1",
                "Legacy Non Entity Binding",
                None,
                status,
                1,
                now,
                now,
            ],
        )
        return binding_id

    def _create_time(
        self,
        time_ref: str,
        display_name: str | None = None,
        semantic_roles: list[str] | None = None,
        source_field_ref: str | None = None,
        publish: bool = False,
    ) -> tuple[str, str]:
        """Create time semantic. Returns (time_ref, time_contract_id)."""
        if semantic_roles is None:
            semantic_roles = ["business_anchor"]
        header: dict[str, object] = {
            "time_ref": time_ref,
            "display_name": display_name or time_ref.split(".")[-1].replace("_", " ").title(),
            "semantic_roles": semantic_roles,
            "time_contract_version": "time.v1",
        }
        if source_field_ref is not None:
            header["source_field_ref"] = source_field_ref
        resp = self.client.post(
            "/semantic/time",
            json={"header": header},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        time_contract_id = resp.json()["time_contract_id"]
        if publish:
            self.assertEqual(
                self.client.post(f"/semantic/time/{time_contract_id}/publish").status_code, 200
            )
        return resp.json()["header"]["time_ref"], time_contract_id

    def _create_entity(
        self,
        entity_ref: str,
        key_refs: list[str] | None = None,
        primary_time_ref: str | None = None,
        stable_descriptors: list | None = None,
        fields: list[dict[str, object]] | None = None,
        binding: dict[str, object] | None = None,
        publish: bool = False,
    ) -> tuple[str, str]:
        """Create entity. Returns (entity_ref, entity_contract_id)."""
        interface_contract: dict = {
            "identity": {
                "key_refs": key_refs or [f"key.{entity_ref.split('.')[-1]}_id"],
                "uniqueness_scope": "global",
                "id_stability": "stable",
            }
        }
        if primary_time_ref:
            interface_contract["primary_time_ref"] = primary_time_ref
        if stable_descriptors:
            interface_contract["stable_descriptors"] = stable_descriptors
        if fields:
            interface_contract["fields"] = fields
        if binding:
            interface_contract["binding"] = binding
        resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": entity_ref,
                    "display_name": entity_ref.split(".")[-1].replace("_", " ").title(),
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": interface_contract,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        entity_id = resp.json()["entity_contract_id"]
        if publish:
            self.assertEqual(
                self.client.post(f"/semantic/entities/{entity_id}/publish").status_code, 200
            )
        return resp.json()["header"]["entity_ref"], entity_id

    def test_entity_kind_defaults_and_round_trips(self) -> None:
        default_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.kind_default",
                    "display_name": "Kind Default",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.kind_default_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(default_resp.status_code, 200, default_resp.text)
        default_body = default_resp.json()
        self.assertEqual(default_body["entity_kind"], "business_entity")

        specified_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.kind_event",
                    "display_name": "Kind Event",
                    "entity_contract_version": "entity.v4",
                },
                "entity_kind": "event_entity",
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.kind_event_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(specified_resp.status_code, 200, specified_resp.text)
        specified_body = specified_resp.json()
        self.assertEqual(specified_body["entity_kind"], "event_entity")

        read_resp = self.client.get(f"/semantic/entities/{specified_body['entity_contract_id']}")
        self.assertEqual(read_resp.status_code, 200, read_resp.text)
        self.assertEqual(read_resp.json()["entity_kind"], "event_entity")

        list_resp = self.client.get("/semantic/entities")
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        list_item = next(
            item
            for item in list_resp.json()["items"]
            if item["entity_contract_id"] == specified_body["entity_contract_id"]
        )
        self.assertEqual(list_item["entity_kind"], "event_entity")

        update_resp = self.client.put(
            f"/semantic/entities/{specified_body['entity_contract_id']}",
            json={"entity_kind": "snapshot_entity"},
        )
        self.assertEqual(update_resp.status_code, 200, update_resp.text)
        updated_body = update_resp.json()
        self.assertEqual(updated_body["entity_kind"], "snapshot_entity")
        self.assertEqual(updated_body["revision"], specified_body["revision"] + 1)

        activate_resp = self.client.post(
            f"/semantic/entities/{specified_body['entity_contract_id']}/activate"
        )
        self.assertEqual(activate_resp.status_code, 200, activate_resp.text)

        published_update_resp = self.client.put(
            f"/semantic/entities/{specified_body['entity_contract_id']}",
            json={"entity_kind": "derived_entity"},
        )
        self.assertEqual(published_update_resp.status_code, 422, published_update_resp.text)

    def test_entity_detail_includes_field_reverse_dependency_graph(self) -> None:
        suffix = uuid4().hex[:8]
        entity_ref = f"entity.field_graph_{suffix}"
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": entity_ref,
                    "display_name": "Field Graph",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.field_graph_{suffix}_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {"field_ref": "field.user_id", "physical_column": "user_id"},
                        {"field_ref": "field.country", "physical_column": "country"},
                    ],
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_id = entity_resp.json()["entity_contract_id"]

        predicate_resp = self.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": f"predicate.field_graph_{suffix}",
                    "display_name": "Field Graph Predicate",
                    "subject_ref": entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {
                        "op": "and",
                        "items": [
                            {
                                "op": "eq",
                                "target_ref": f"{entity_ref}.field.country",
                                "value": "US",
                            },
                            {
                                "op": "is_not_null",
                                "target_ref": f"{entity_ref}.field.user_id",
                            },
                        ],
                    },
                    "allowed_usage": ["metric_qualifier"],
                },
            },
        )
        self.assertEqual(predicate_resp.status_code, 200, predicate_resp.text)
        predicate_ref = predicate_resp.json()["header"]["predicate_ref"]

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": f"metric.field_graph_{suffix}",
                    "display_name": "Field Graph Metric",
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
                        "name": "active_users",
                        "semantics": "distinct active users",
                        "aggregation": "count_distinct",
                    },
                    "required_inputs": [{"input_field_ref": f"{entity_ref}.field.user_id"}],
                },
            },
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        metric_ref = metric_resp.json()["header"]["metric_ref"]

        process_resp = self.client.post(
            "/semantic/process-objects",
            json={
                "header": {
                    "process_ref": f"process.field_graph_{suffix}",
                    "display_name": "Field Graph Funnel",
                    "process_type": "funnel_definition",
                    "process_contract_version": "process.v2",
                },
                "interface_contract": {
                    "contract_mode": "entity_stream",
                    "entity_ref": entity_ref,
                    "emitted_grain_ref": "grain.user",
                    "population_subject_ref": "subject.user",
                    "subject_cardinality": "many",
                },
                "payload": {
                    "process_type": "funnel_definition",
                    "funnel_key": "field_graph",
                    "steps": [
                        {
                            "step_key": "identify",
                            "event_ref": f"{entity_ref}.field.user_id",
                        },
                        {"step_key": "convert", "event_ref": "event.convert"},
                    ],
                    "conversion_step_key": "convert",
                },
            },
        )
        self.assertEqual(process_resp.status_code, 200, process_resp.text)
        process_ref = process_resp.json()["header"]["process_ref"]

        profile_ref = f"compiler_profile.field_graph_{suffix}"
        now = "2026-04-09T00:00:00+00:00"
        self._metadata().execute(
            """
            INSERT INTO compiler_compatibility_profiles (
                profile_id, profile_ref, profile_kind, schema_version, subject_kind,
                subject_ref, subject_revision, requirement_json, capability_json,
                catalog_metadata_json, status, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"profile_{suffix}",
                profile_ref,
                "requirement",
                "v1",
                "metric",
                metric_ref,
                None,
                json.dumps({"field_refs": [f"{entity_ref}.field.user_id"]}),
                "{}",
                "{}",
                "draft",
                1,
                now,
                now,
            ],
        )

        detail_resp = self.client.get(f"/semantic/entities/{entity_id}")
        self.assertEqual(detail_resp.status_code, 200, detail_resp.text)
        field_dependency_graph = detail_resp.json()["field_dependency_graph"]

        country_dependents = field_dependency_graph["field.country"]
        self.assertEqual(country_dependents[0]["object_kind"], "predicate")
        self.assertEqual(country_dependents[0]["ref"], predicate_ref)
        self.assertEqual(country_dependents[0]["usage_count"], 1)
        self.assertEqual(
            country_dependents[0]["usage_paths"],
            ["interface_contract.expression.items[0].target_ref"],
        )

        user_id_dependents = field_dependency_graph["field.user_id"]
        user_id_by_kind = {item["object_kind"]: item for item in user_id_dependents}
        self.assertEqual(user_id_by_kind["metric"]["ref"], metric_ref)
        self.assertEqual(
            user_id_by_kind["metric"]["usage_paths"],
            ["payload.required_inputs[0].input_field_ref"],
        )
        self.assertEqual(user_id_by_kind["predicate"]["ref"], predicate_ref)
        self.assertEqual(
            user_id_by_kind["predicate"]["usage_paths"],
            ["interface_contract.expression.items[1].target_ref"],
        )
        self.assertEqual(user_id_by_kind["process"]["ref"], process_ref)
        self.assertEqual(
            user_id_by_kind["process"]["usage_paths"],
            ["payload.steps[0].event_ref"],
        )
        self.assertEqual(user_id_by_kind["profile"]["ref"], profile_ref)
        self.assertEqual(
            user_id_by_kind["profile"]["usage_paths"],
            ["requirement.field_refs[0]"],
        )

        self.assertEqual(
            self.client.app.state.services.semantic_service.field_dependents_for_entity_field(
                entity_ref, "field.country"
            ),
            country_dependents,
        )

        route_resp = self.client.get(
            f"/semantic/entities/{entity_id}/field-dependents",
            params={"field_ref": f"{entity_ref}.field.country"},
        )
        self.assertEqual(route_resp.status_code, 200, route_resp.text)
        self.assertEqual(
            route_resp.json(),
            {
                "entity_id": entity_id,
                "field_ref": f"{entity_ref}.field.country",
                "dependents": country_dependents,
            },
        )

    def test_dimension_and_time_reference_entity_fields(self) -> None:
        suffix = uuid4().hex[:8]
        entity_ref, entity_id = self._create_entity(
            f"entity.field_ref_contract_{suffix}",
            key_refs=[f"key.field_ref_contract_{suffix}_id"],
            fields=[
                {
                    "field_ref": "field.country",
                    "value_type": "string",
                    "physical_column": "country",
                },
                {
                    "field_ref": "field.signup_at",
                    "value_type": "datetime",
                    "physical_column": "signup_at",
                },
            ],
            publish=True,
        )
        dimension_ref, dimension_id = self._create_dimension(
            f"dimension.field_ref_country_{suffix}",
            source_field_ref=f"{entity_ref}.field.country",
            publish=True,
        )
        time_ref, time_id = self._create_time(
            f"time.field_ref_signup_at_{suffix}",
            source_field_ref=f"{entity_ref}.field.signup_at",
            publish=True,
        )

        dimension = self.client.get(f"/semantic/dimensions/{dimension_id}").json()
        self.assertEqual(
            dimension["interface_contract"]["source_field_ref"],
            f"{entity_ref}.field.country",
        )
        time_obj = self.client.get(f"/semantic/time/{time_id}").json()
        self.assertEqual(time_obj["header"]["source_field_ref"], f"{entity_ref}.field.signup_at")

        entity_detail = self.client.get(f"/semantic/entities/{entity_id}").json()
        dependents = entity_detail["field_dependency_graph"]
        self.assertEqual(
            [item["ref"] for item in dependents["field.country"] if item["ref"] == dimension_ref],
            [dimension_ref],
        )
        self.assertEqual(
            [item["ref"] for item in dependents["field.signup_at"] if item["ref"] == time_ref],
            [time_ref],
        )

    def test_dimension_time_predicate_reject_object_level_physical_binding_fields(self) -> None:
        suffix = uuid4().hex[:8]
        dimension_resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": f"dimension.reject_physical_{suffix}",
                    "display_name": "Reject Physical",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "source_field_ref": "field.country",
                    "physical_column": "country",
                    "value_domain": {
                        "structure_kind": "flat",
                        "value_type": "string",
                        "domain_kind": "open",
                    },
                },
            },
        )
        self.assertEqual(dimension_resp.status_code, 422, dimension_resp.text)
        self.assertIn("physical_column", dimension_resp.text)

        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": f"time.reject_physical_{suffix}",
                    "display_name": "Reject Physical",
                    "semantic_roles": ["business_anchor"],
                    "time_contract_version": "time.v1",
                    "physical_column": "event_time",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 422, time_resp.text)
        self.assertIn("physical_column", time_resp.text)

        predicate_resp = self.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": f"predicate.reject_physical_{suffix}",
                    "display_name": "Reject Physical",
                    "subject_ref": "entity.some_subject",
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {
                        "op": "eq",
                        "target_ref": "field.country",
                        "value": "US",
                        "physical_column": "country",
                    },
                    "allowed_usage": ["metric_qualifier"],
                },
            },
        )
        self.assertEqual(predicate_resp.status_code, 422, predicate_resp.text)
        self.assertIn("physical_column", predicate_resp.text)

    def test_field_type_usage_validation_for_dimension_time_and_predicate(self) -> None:
        suffix = uuid4().hex[:8]
        entity_ref, _entity_id = self._create_entity(
            f"entity.field_type_validation_{suffix}",
            key_refs=[f"key.field_type_validation_{suffix}_id"],
            fields=[
                {
                    "field_ref": "field.country",
                    "value_type": "string",
                    "physical_column": "country",
                },
                {
                    "field_ref": "field.event_count",
                    "value_type": "number",
                    "physical_column": "event_count",
                },
            ],
            publish=True,
        )

        _dimension_ref, dimension_id = self._create_dimension(
            f"dimension.invalid_date_source_{suffix}",
            source_field_ref=f"{entity_ref}.field.country",
            value_type="date",
        )
        dimension_publish = self.client.post(f"/semantic/dimensions/{dimension_id}/publish")
        self.assertEqual(dimension_publish.status_code, 422, dimension_publish.text)
        self._assert_publish_error(
            dimension_publish,
            code="reference_validation_error",
            category="validation",
            message_substring="invalid_field_type_for_semantic_object",
        )

        _time_ref, time_id = self._create_time(
            f"time.invalid_string_source_{suffix}",
            source_field_ref=f"{entity_ref}.field.country",
        )
        time_publish = self.client.post(f"/semantic/time/{time_id}/publish")
        self.assertEqual(time_publish.status_code, 422, time_publish.text)
        self._assert_publish_error(
            time_publish,
            code="reference_validation_error",
            category="validation",
            message_substring="invalid_field_type_for_semantic_object",
        )

        predicate_resp = self.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": f"predicate.invalid_ordered_field_{suffix}",
                    "display_name": "Invalid Ordered Field",
                    "subject_ref": entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {
                        "op": "gt",
                        "target_ref": f"{entity_ref}.field.country",
                        "value": "US",
                    },
                    "allowed_usage": ["metric_qualifier"],
                },
            },
        )
        self.assertEqual(predicate_resp.status_code, 200, predicate_resp.text)
        predicate_publish = self.client.post(
            f"/semantic/predicates/{predicate_resp.json()['predicate_contract_id']}/publish"
        )
        self.assertEqual(predicate_publish.status_code, 422, predicate_publish.text)
        self._assert_publish_error(
            predicate_publish,
            code="reference_validation_error",
            category="validation",
            message_substring="invalid_field_type_for_semantic_object",
        )

    def test_predicate_rejects_unqualified_field_ref_before_surface_resolution(self) -> None:
        suffix = uuid4().hex[:8]
        entity_ref, _entity_id = self._create_entity(
            f"entity.legacy_field_surface_{suffix}",
            key_refs=[f"key.legacy_field_surface_{suffix}_id"],
            fields=[
                {
                    "field_ref": "field.identity",
                    "value_type": "string",
                    "physical_column": "identity",
                }
            ],
            publish=True,
        )
        source_object_id = self._insert_source_object(
            fqn=f"warehouse.legacy_field_surface_{suffix}"
        )
        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.legacy_field_surface_{suffix}",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": source_object_id,
                            "carrier_kind": "table",
                            "carrier_locator": f"warehouse.legacy_field_surface_{suffix}",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.legacy_country", "physical_name": "country"}
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": f"key.legacy_field_surface_{suffix}_id",
                            },
                            "semantic_ref": f"key.legacy_field_surface_{suffix}_id",
                            "surface_ref": "field.legacy_country",
                        }
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)

        predicate_resp = self.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": f"predicate.no_surface_resolution_{suffix}",
                    "display_name": "No Surface Resolution",
                    "subject_ref": "entity.no_surface_resolution",
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {"op": "eq", "target_ref": "field.legacy_country", "value": "US"},
                    "allowed_usage": ["metric_qualifier"],
                },
            },
        )
        self.assertEqual(predicate_resp.status_code, 422, predicate_resp.text)
        payload = predicate_resp.json()
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertEqual(payload["guidance"]["authoring_model"], "entity_first")
        self.assertIn(
            "entity.<entity>.field.<field>", payload["guidance"]["entity_first_next_action"]
        )

    def test_predicate_rejects_unqualified_field_ref_for_dependency_graph(
        self,
    ) -> None:
        suffix = uuid4().hex[:8]
        first_entity_ref = f"entity.field_scope_first_{suffix}"
        second_entity_ref = f"entity.field_scope_second_{suffix}"
        for entity_ref in (first_entity_ref, second_entity_ref):
            entity_resp = self.client.post(
                "/semantic/entities",
                json={
                    "header": {
                        "entity_ref": entity_ref,
                        "display_name": entity_ref,
                        "entity_contract_version": "entity.v4",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": [f"key.{entity_ref.rsplit('.', 1)[-1]}_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        },
                        "fields": [
                            {"field_ref": "field.country", "physical_column": "country"},
                        ],
                    },
                },
            )
            self.assertEqual(entity_resp.status_code, 200, entity_resp.text)

        second_predicate_resp = self.client.post(
            "/semantic/predicates",
            json={
                "header": {
                    "predicate_ref": f"predicate.field_scope_second_{suffix}",
                    "display_name": "Second Entity Predicate",
                    "subject_ref": second_entity_ref,
                    "predicate_contract_version": "predicate.v1",
                },
                "interface_contract": {
                    "expression": {"op": "eq", "target_ref": "field.country", "value": "US"},
                    "allowed_usage": ["metric_qualifier"],
                },
            },
        )
        self.assertEqual(second_predicate_resp.status_code, 422, second_predicate_resp.text)
        payload = second_predicate_resp.json()
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertEqual(payload["guidance"]["authoring_model"], "entity_first")

        first_route_resp = self.client.get(
            f"/semantic/entities/{first_entity_ref}/field-dependents",
            params={"field_ref": "field.country"},
        )
        self.assertEqual(first_route_resp.status_code, 200, first_route_resp.text)
        self.assertEqual(first_route_resp.json()["dependents"], [])

    def test_field_reverse_dependency_graph_ignores_ambiguous_unqualified_refs(
        self,
    ) -> None:
        suffix = uuid4().hex[:8]
        first_entity_ref = f"entity.field_ambiguous_first_{suffix}"
        second_entity_ref = f"entity.field_ambiguous_second_{suffix}"
        for entity_ref in (first_entity_ref, second_entity_ref):
            entity_resp = self.client.post(
                "/semantic/entities",
                json={
                    "header": {
                        "entity_ref": entity_ref,
                        "display_name": entity_ref,
                        "entity_contract_version": "entity.v4",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": [f"key.{entity_ref.rsplit('.', 1)[-1]}_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        },
                        "fields": [
                            {"field_ref": "field.country", "physical_column": "country"},
                        ],
                    },
                },
            )
            self.assertEqual(entity_resp.status_code, 200, entity_resp.text)

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": f"metric.field_ambiguous_{suffix}",
                    "display_name": "Ambiguous Field Metric",
                    "metric_family": "rate_metric",
                    "observed_entity_ref": first_entity_ref,
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
                        "name": "first",
                        "semantics": "first side",
                        "aggregation": "count_distinct",
                    },
                    "denominator": {
                        "name": "second",
                        "semantics": "second side",
                        "aggregation": "count_distinct",
                    },
                    "required_inputs": [
                        {"entity_ref": second_entity_ref},
                        {"input_field_ref": "field.country"},
                    ],
                },
            },
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)

        for entity_ref in (first_entity_ref, second_entity_ref):
            route_resp = self.client.get(
                f"/semantic/entities/{entity_ref}/field-dependents",
                params={"field_ref": "field.country"},
            )
            self.assertEqual(route_resp.status_code, 200, route_resp.text)
            self.assertEqual(route_resp.json()["dependents"], [])

    def _create_enum_set(
        self,
        enum_set_ref: str,
        values: list[dict] | None = None,
        publish: bool = False,
    ) -> tuple[str, str]:
        """Create enum-set. Returns (enum_set_ref, enum_set_contract_id)."""
        resp = self.client.post(
            "/semantic/enum-sets",
            json={
                "header": {"enum_set_ref": enum_set_ref, "value_type": "string"},
                "display_name": enum_set_ref.split(".")[-1].replace("_", " ").title(),
                "versions": [
                    {
                        "enum_version": "v1",
                        "values": values
                        or [{"value_key": "CN", "raw_value": "CN", "label": "China"}],
                    }
                ],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        enum_set_id = resp.json()["enum_set_contract_id"]
        if publish:
            self.assertEqual(
                self.client.post(f"/semantic/enum-sets/{enum_set_id}/publish").status_code, 200
            )
        return resp.json()["header"]["enum_set_ref"], enum_set_id

    def _create_dimension(
        self,
        dimension_ref: str,
        enum_set_ref: str | None = None,
        domain_kind: str = "open",
        source_field_ref: str | None = None,
        value_type: str = "string",
        publish: bool = False,
    ) -> tuple[str, str]:
        """Create dimension. Returns (dimension_ref, dimension_contract_id)."""
        value_domain: dict = {
            "structure_kind": "flat",
            "value_type": value_type,
            "domain_kind": domain_kind,
        }
        if enum_set_ref:
            value_domain["enum_set_ref"] = enum_set_ref
            value_domain["enum_version"] = "v1"
            if domain_kind == "open":
                value_domain["domain_kind"] = "enumerated"
        interface_contract: dict[str, object] = {
            "value_domain": value_domain,
            "grouping": {"supports_grouping": True},
        }
        if source_field_ref is not None:
            interface_contract["source_field_ref"] = source_field_ref
        resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": dimension_ref,
                    "display_name": dimension_ref.split(".")[-1].replace("_", " ").title(),
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": interface_contract,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        dimension_id = resp.json()["dimension_contract_id"]
        if publish:
            self.assertEqual(
                self.client.post(f"/semantic/dimensions/{dimension_id}/publish").status_code, 200
            )
        return resp.json()["header"]["dimension_ref"], dimension_id

    def _create_metric(
        self,
        metric_ref: str,
        observed_entity_ref: str,
        metric_family: str = "count_metric",
        publish: bool = False,
    ) -> tuple[str, str]:
        """Create metric. Returns (metric_ref, metric_contract_id)."""
        payload = {
            "metric_family": metric_family,
            "count_target": {
                "name": "rows",
                "semantics": "row count",
                "aggregation": "count",
            },
        }
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": metric_ref,
                    "display_name": metric_ref.split(".")[-1].replace("_", " ").title(),
                    "metric_family": metric_family,
                    "observed_entity_ref": observed_entity_ref,
                    "observation_grain_ref": "grain.account",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity_constraints": {
                        "dimension_policy": "all",
                        "time_axis_policy": "additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": payload,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        metric_id = resp.json()["metric_contract_id"]
        if publish:
            self.assertEqual(
                self.client.post(f"/semantic/metrics/{metric_id}/publish").status_code, 200
            )
        return resp.json()["header"]["metric_ref"], metric_id

    def _create_process_object(
        self,
        process_ref: str,
        anchor_time_ref: str,
        exported_dimension_refs: list[str] | None = None,
        process_type: str = "cohort_definition",
        publish: bool = False,
    ) -> tuple[str, str]:
        """Create process-object. Returns (process_ref, process_contract_id)."""
        interface_contract: dict = {
            "contract_mode": "context_provider",
            "context_kind": "cohort_membership",
            "population_subject_ref": "subject.user",
            "membership_cardinality": "exclusive_one",
            "anchor_time_ref": anchor_time_ref,
        }
        if exported_dimension_refs:
            interface_contract["exported_dimension_refs"] = exported_dimension_refs
        resp = self.client.post(
            "/semantic/process-objects",
            json={
                "header": {
                    "process_ref": process_ref,
                    "display_name": process_ref.split(".")[-1].replace("_", " ").title(),
                    "process_type": process_type,
                    "process_contract_version": "process.v2",
                },
                "interface_contract": interface_contract,
                "payload": {
                    "process_type": process_type,
                    "cohort_key": "new_users",
                    "entry_population": {"base_population_ref": "population.users"},
                    "cohort_anchor_ref": anchor_time_ref,
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        process_id = resp.json()["process_contract_id"]
        if publish:
            self.assertEqual(
                self.client.post(f"/semantic/process-objects/{process_id}/publish").status_code, 200
            )
        return resp.json()["header"]["process_ref"], process_id

    def _create_compatibility_profile(
        self,
        profile_ref: str,
        subject_kind: str,
        subject_ref: str,
        requirement: dict,
        publish: bool = False,
    ) -> tuple[str, str]:
        """Create compatibility profile. Returns (profile_ref, profile_id)."""
        resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": profile_ref,
                "profile_kind": "requirement",
                "subject_kind": subject_kind,
                "subject_ref": subject_ref,
                "requirement": requirement,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        profile_id = resp.json()["profile_id"]
        if publish:
            self.assertEqual(
                self.client.post(
                    f"/compiler/compatibility-profiles/{profile_id}/publish"
                ).status_code,
                200,
            )
        return resp.json()["profile_ref"], profile_id

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

        validate_resp = self.client.post(f"/semantic/entities/{entity_id}/validate")
        self.assertEqual(validate_resp.status_code, 200, validate_resp.text)
        self.assertTrue(validate_resp.json()["ok"])
        self.assertEqual(validate_resp.json()["action"], "validate")
        self.assertEqual(validate_resp.json()["semantic_object"]["status"], "draft")

        resp = self.client.post(f"/semantic/entities/{entity_id}/activate")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")

        publish_alias_resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(publish_alias_resp.status_code, 422, publish_alias_resp.text)

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

        validate_resp = self.client.post(f"/semantic/metrics/{metric_id}/validate")
        self.assertEqual(validate_resp.status_code, 200, validate_resp.text)
        self.assertEqual(validate_resp.json()["action"], "validate")
        self.assertTrue(validate_resp.json()["ok"])

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

    def test_entity_can_be_deprecated_after_activation(self) -> None:
        create_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.deprecation_case",
                    "display_name": "Deprecation Case",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.case_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        entity_id = create_resp.json()["entity_contract_id"]

        activate_resp = self.client.post(f"/semantic/entities/{entity_id}/activate")
        self.assertEqual(activate_resp.status_code, 200, activate_resp.text)
        self.assertEqual(activate_resp.json()["status"], "published")

        deprecate_resp = self.client.post(f"/semantic/entities/{entity_id}/deprecate")
        self.assertEqual(deprecate_resp.status_code, 200, deprecate_resp.text)
        self.assertEqual(deprecate_resp.json()["status"], "deprecated")

    def test_entities_can_share_source_object_with_different_field_subsets(self) -> None:
        source_object_id = self._insert_source_object(fqn="main.analytics.user_events")
        shared_binding = {
            "source_object_ref": source_object_id,
            "source_object_fqn": "main.analytics.user_events",
            "carrier_kind": "table",
        }
        user_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.bound_user",
                    "display_name": "Bound User",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.user_id",
                            "display_name": "User ID",
                            "value_type": "string",
                            "nullable": False,
                            "physical_column": "user_id",
                        },
                        {
                            "field_ref": "field.country",
                            "value_type": "string",
                            "physical_column": "country_code",
                        },
                    ],
                    "binding": shared_binding,
                },
            },
        )
        self.assertEqual(user_resp.status_code, 200, user_resp.text)
        account_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.bound_account",
                    "display_name": "Bound Account",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.account_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.account_id",
                            "value_type": "string",
                            "nullable": False,
                            "physical_column": "account_id",
                        }
                    ],
                    "binding": shared_binding,
                },
            },
        )
        self.assertEqual(account_resp.status_code, 200, account_resp.text)

        user_contract = self.client.get(
            f"/semantic/entities/{user_resp.json()['entity_contract_id']}"
        ).json()["interface_contract"]
        account_contract = self.client.get(
            f"/semantic/entities/{account_resp.json()['entity_contract_id']}"
        ).json()["interface_contract"]

        self.assertEqual(user_contract["binding"]["source_object_ref"], source_object_id)
        self.assertEqual(account_contract["binding"]["source_object_ref"], source_object_id)
        self.assertEqual(
            [field["field_ref"] for field in user_contract["fields"]],
            ["field.user_id", "field.country"],
        )
        self.assertEqual(
            [field["field_ref"] for field in account_contract["fields"]],
            ["field.account_id"],
        )

    def test_entity_validate_returns_binding_readiness_blockers_before_compile(self) -> None:
        source_object_id = self._insert_source_object(fqn="main.analytics.entity_validation_case")
        suffix = uuid4().hex[:8]
        create_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.validation_case_{suffix}",
                    "display_name": "Validation Case",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.validation_case_{suffix}"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.validation_case_id",
                            "value_type": "string",
                            "physical_column": "missing_validation_case_id",
                        }
                    ],
                    "binding": {
                        "source_object_ref": source_object_id,
                        "source_object_fqn": "main.analytics.entity_validation_case",
                        "carrier_kind": "table",
                    },
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        entity_id = create_resp.json()["entity_contract_id"]

        activate_resp = self.client.post(f"/semantic/entities/{entity_id}/activate")
        self.assertEqual(activate_resp.status_code, 200, activate_resp.text)

        validate_resp = self.client.post(f"/semantic/entities/{entity_id}/validate")
        self.assertEqual(validate_resp.status_code, 200, validate_resp.text)
        payload = validate_resp.json()
        self.assertEqual(payload["action"], "validate")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["semantic_object"]["readiness_status"], "not_ready")
        self.assertEqual(
            {item["code"] for item in payload["validation"]["blocking_requirements"]},
            {"ENTITY_FIELD_COLUMN_MISSING"},
        )

    def test_entity_validate_reports_draft_binding_readiness_blockers(self) -> None:
        source_object_id = self._insert_source_object(fqn="main.analytics.entity_draft_validation")
        suffix = uuid4().hex[:8]
        create_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.draft_validation_{suffix}",
                    "display_name": "Draft Validation",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.draft_validation_{suffix}"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.draft_validation_id",
                            "value_type": "string",
                            "physical_column": "missing_draft_validation_id",
                        }
                    ],
                    "binding": {
                        "source_object_ref": source_object_id,
                        "source_object_fqn": "main.analytics.entity_draft_validation",
                        "carrier_kind": "table",
                    },
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)

        validate_resp = self.client.post(
            f"/semantic/entities/{create_resp.json()['entity_contract_id']}/validate"
        )
        self.assertEqual(validate_resp.status_code, 200, validate_resp.text)
        payload = validate_resp.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["semantic_object"]["lifecycle_status"], "draft")
        self.assertEqual(
            {item["code"] for item in payload["validation"]["blocking_requirements"]},
            {"ENTITY_FIELD_COLUMN_MISSING"},
        )

    def test_entity_ready_path_supports_list_readiness_filter(self) -> None:
        source_object_id = self._insert_source_object(fqn="main.analytics.entity_ready_case")
        self._insert_source_object(
            fqn="main.analytics.entity_ready_case.ready_id",
            object_type="column",
            native_name="ready_id",
            parent_id=source_object_id,
            properties={"data_type": "varchar"},
        )
        suffix = uuid4().hex[:8]
        create_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.ready_case_{suffix}",
                    "display_name": "Ready Case",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.ready_case_{suffix}"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.ready_id",
                            "value_type": "string",
                            "physical_column": "ready_id",
                        }
                    ],
                    "binding": {
                        "source_object_ref": source_object_id,
                        "source_object_fqn": "main.analytics.entity_ready_case",
                        "carrier_kind": "table",
                    },
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        entity_id = create_resp.json()["entity_contract_id"]
        activate_resp = self.client.post(f"/semantic/entities/{entity_id}/activate")
        self.assertEqual(activate_resp.status_code, 200, activate_resp.text)
        self.assertEqual(activate_resp.json()["readiness_status"], "ready")

        list_resp = self.client.get("/semantic/entities", params={"readiness_status": "ready"})
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        self.assertIn(
            entity_id,
            {item["entity_contract_id"] for item in list_resp.json()["items"]},
        )

    def test_entity_create_rejects_invalid_field_locator(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.invalid_field_locator",
                    "display_name": "Invalid Field Locator",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.user_id",
                            "value_type": "string",
                        }
                    ],
                },
            },
        )

        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("Entity field requires one physical locator", resp.text)

    def test_entity_create_accepts_controlled_expression_locator(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.expression_field",
                    "display_name": "Expression Field",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.event_day",
                            "value_type": "date",
                            "physical_expression_locator": {
                                "expression_kind": "date_trunc",
                                "input_columns": ["event_ts"],
                                "output_name": "event_day",
                                "parameters": {"unit": "day"},
                            },
                        }
                    ],
                },
            },
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        field = resp.json()["interface_contract"]["fields"][0]
        self.assertEqual(field["physical_expression_locator"]["expression_kind"], "date_trunc")

    def test_entity_create_rejects_raw_expression_locator_parameter(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.raw_expression_field",
                    "display_name": "Raw Expression Field",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "fields": [
                        {
                            "field_ref": "field.price_bucket",
                            "value_type": "number",
                            "physical_expression_locator": {
                                "expression_kind": "bucket",
                                "input_columns": ["price"],
                                "parameters": {"options": [{"sql_expression": "price / 100"}]},
                            },
                        }
                    ],
                },
            },
        )

        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("parameters must not contain raw expression keys", resp.text)

    def test_entity_relationship_validation_and_discovery(self) -> None:
        suffix = uuid4().hex[:8]
        exposure_ref, _ = self._create_entity(
            f"entity.exposure_{suffix}",
            key_refs=[f"key.exposure_id_{suffix}"],
            fields=[
                {
                    "field_ref": "field.user_id",
                    "value_type": "string",
                    "physical_column": "user_id",
                },
                {
                    "field_ref": "field.exposure_at",
                    "value_type": "datetime",
                    "physical_column": "exposure_at",
                },
            ],
            publish=True,
        )
        conversion_ref, _ = self._create_entity(
            f"entity.conversion_{suffix}",
            key_refs=[f"key.conversion_id_{suffix}"],
            fields=[
                {
                    "field_ref": "field.user_id",
                    "value_type": "string",
                    "physical_column": "user_id",
                },
                {
                    "field_ref": "field.conversion_at",
                    "value_type": "datetime",
                    "physical_column": "conversion_at",
                },
            ],
            publish=True,
        )

        relationship_resp = self.client.post(
            "/semantic/relationships",
            json={
                "relationship_ref": f"relationship.exposure_to_conversion_{suffix}",
                "display_name": "Exposure To Conversion",
                "left_entity_ref": exposure_ref,
                "right_entity_ref": conversion_ref,
                "key_alignment": {
                    "left_field_ref": f"{exposure_ref}.field.user_id",
                    "right_field_ref": f"{conversion_ref}.field.user_id",
                },
                "time_alignment": {
                    "left_time_ref": f"{exposure_ref}.field.exposure_at",
                    "right_time_ref": f"{conversion_ref}.field.conversion_at",
                    "alignment_kind": "bounded_after",
                    "window": "P7D",
                },
                "cardinality": "many_to_many",
                "grain_compatibility": {
                    "left_grain_ref": "grain.user_day",
                    "right_grain_ref": "grain.user_day",
                    "compatibility": "same_grain",
                },
            },
        )
        self.assertEqual(relationship_resp.status_code, 200, relationship_resp.text)
        relationship = relationship_resp.json()
        relationship_id = relationship["relationship_id"]

        validate_resp = self.client.post(f"/semantic/relationships/{relationship_id}/validate")
        self.assertEqual(validate_resp.status_code, 200, validate_resp.text)
        self.assertTrue(validate_resp.json()["ok"])

        publish_resp = self.client.post(f"/semantic/relationships/{relationship_id}/publish")
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)
        self.assertEqual(publish_resp.json()["lifecycle_status"], "active")

        pair_resp = self.client.get(
            "/semantic/relationships",
            params={"left_entity_ref": exposure_ref, "right_entity_ref": conversion_ref},
        )
        self.assertEqual(pair_resp.status_code, 200, pair_resp.text)
        self.assertEqual(pair_resp.json()["total"], 1)
        self.assertEqual(
            pair_resp.json()["items"][0]["relationship_ref"],
            relationship["relationship_ref"],
        )

    def test_entity_relationship_rejects_incompatible_key_field_types(self) -> None:
        suffix = uuid4().hex[:8]
        left_ref, _ = self._create_entity(
            f"entity.left_rel_{suffix}",
            fields=[
                {
                    "field_ref": "field.user_id",
                    "value_type": "string",
                    "physical_column": "user_id",
                }
            ],
            publish=True,
        )
        right_ref, _ = self._create_entity(
            f"entity.right_rel_{suffix}",
            fields=[
                {
                    "field_ref": "field.user_id",
                    "value_type": "integer",
                    "physical_column": "user_id",
                }
            ],
            publish=True,
        )

        resp = self.client.post(
            "/semantic/relationships",
            json={
                "relationship_ref": f"relationship.bad_key_type_{suffix}",
                "left_entity_ref": left_ref,
                "right_entity_ref": right_ref,
                "key_alignment": {
                    "left_field_ref": f"{left_ref}.field.user_id",
                    "right_field_ref": f"{right_ref}.field.user_id",
                },
                "cardinality": "many_to_one",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        validate_resp = self.client.post(
            f"/semantic/relationships/{resp.json()['relationship_id']}/validate"
        )
        self.assertEqual(validate_resp.status_code, 422, validate_resp.text)
        self.assertIn("relationship_key_value_type_mismatch", validate_resp.text)

    @pytest.mark.slow
    def test_typed_binding_and_profile_lifecycle(self) -> None:
        entity_ref, entity_id = self._create_entity(
            "entity.account", key_refs=["key.account_id"], publish=True
        )

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
        published_binding_readiness = resp.json()["readiness_status"]

        binding_detail_resp = self.client.get(f"/semantic/bindings/{binding_id}")
        self.assertEqual(binding_detail_resp.status_code, 200, binding_detail_resp.text)
        self.assertEqual(
            binding_detail_resp.json()["readiness_status"], published_binding_readiness
        )

        binding_list_resp = self.client.get("/semantic/bindings?status=published")
        self.assertEqual(binding_list_resp.status_code, 200, binding_list_resp.text)
        binding_list_item = next(
            item for item in binding_list_resp.json()["items"] if item["binding_id"] == binding_id
        )
        self.assertEqual(binding_list_item["readiness_status"], published_binding_readiness)
        self.assertEqual(
            binding_list_item["blocker_count"],
            len(binding_detail_resp.json()["blocking_requirements"]),
        )
        self.assertNotIn("interface_contract", binding_list_item)

    def test_create_typed_binding_rejects_non_entity_scopes(self) -> None:
        for binding_scope, bound_object_ref, target_kind, target_key, semantic_ref in [
            (
                "metric",
                "metric.legacy_metric_grounding",
                "metric_input",
                "measure",
                "metric_input.measure",
            ),
            (
                "process_object",
                "process.legacy_process_grounding",
                "population_subject",
                "key.user_id",
                "key.user_id",
            ),
        ]:
            resp = self.client.post(
                "/semantic/bindings",
                json={
                    "header": {
                        "binding_ref": f"binding.reject_{binding_scope}_{uuid4().hex[:8]}",
                        "binding_scope": binding_scope,
                        "bound_object_ref": bound_object_ref,
                        "binding_contract_version": "binding.v1",
                    },
                    "interface_contract": {
                        "carrier_bindings": [
                            {
                                "binding_key": "primary",
                                "carrier_kind": "table",
                                "carrier_locator": "warehouse.legacy_grounding",
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
                                    "target_kind": target_kind,
                                    "target_key": target_key,
                                },
                                "semantic_ref": semantic_ref,
                                "surface_ref": "field.user_id",
                            }
                        ],
                    },
                },
            )

            self.assertEqual(resp.status_code, 422, resp.text)
            detail = resp.json()["detail"]
            self.assertEqual(detail["code"], "typed_binding_scope_not_authorable")
            self.assertIn("binding_scope='entity'", detail["message"])

    def test_update_rejects_legacy_non_entity_binding(self) -> None:
        binding_id = self._insert_legacy_non_entity_binding(
            binding_ref=f"binding.legacy_metric_{uuid4().hex[:8]}",
            binding_scope="metric",
            bound_object_ref="metric.legacy_metric_grounding",
        )

        resp = self.client.put(
            f"/semantic/bindings/{binding_id}",
            json={"description": "should not remain editable"},
        )

        self.assertEqual(resp.status_code, 422, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "typed_binding_scope_not_authorable")
        self.assertIn("legacy metric", detail["message"])

    def test_create_binding_with_time_bindings(self) -> None:
        time_ref, _ = self._create_time(
            "time.api_time_binding",
            semantic_roles=["measurement", "operational_support"],
            publish=True,
        )
        entity_ref, entity_id = self._create_entity(
            "entity.time_binding_account",
            key_refs=["key.account_id"],
            primary_time_ref="time.api_time_binding",
            publish=True,
        )

        self._insert_source_object(fqn="warehouse.time_binding_accounts")
        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "binding.time_binding_account",
                    "display_name": "Time Binding Account",
                    "binding_scope": "entity",
                    "bound_object_ref": "entity.time_binding_account",
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.time_binding_accounts",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.account_id", "physical_name": "account_id"},
                                {"surface_ref": "field.log_date", "physical_name": "log_date"},
                                {"surface_ref": "field.log_hour", "physical_name": "log_hour"},
                            ],
                            "time_surfaces": [
                                {
                                    "surface_ref": "time_surface.log_date",
                                    "physical_name": "log_date",
                                },
                                {
                                    "surface_ref": "time_surface.log_hour",
                                    "physical_name": "log_hour",
                                },
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
                    "time_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": "time.api_time_binding",
                            },
                            "semantic_ref": "time.api_time_binding",
                            "resolution_kind": "date_hour_columns",
                            "date_surface_ref": "time_surface.log_date",
                            "date_format": "yyyymmdd",
                            "hour_surface_ref": "time_surface.log_hour",
                            "hour_format": "hh",
                            "timezone_strategy": "session_consistent_naive",
                        }
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        time_bindings = binding_resp.json()["interface_contract"]["time_bindings"]
        self.assertEqual(len(time_bindings), 1)
        self.assertEqual(time_bindings[0]["resolution_kind"], "date_hour_columns")

        publish_binding_resp = self.client.post(
            f"/semantic/bindings/{binding_resp.json()['binding_id']}/publish"
        )
        self.assertEqual(publish_binding_resp.status_code, 200, publish_binding_resp.text)
        self.assertEqual(
            publish_binding_resp.json()["interface_contract"]["time_bindings"][0]["date_format"],
            "yyyymmdd",
        )

    def test_create_binding_with_timestamp_format_round_trips(self) -> None:
        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": "time.api_timestamp_binding",
                    "display_name": "API Timestamp Binding",
                    "semantic_roles": ["measurement"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_contract_id = time_resp.json()["time_contract_id"]
        publish_time_resp = self.client.post(f"/semantic/time/{time_contract_id}/publish")
        self.assertEqual(publish_time_resp.status_code, 200, publish_time_resp.text)

        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.timestamp_binding_account",
                    "display_name": "Timestamp Binding Account",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.account_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "primary_time_ref": "time.api_timestamp_binding",
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_id = entity_resp.json()["entity_contract_id"]
        publish_entity_resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(publish_entity_resp.status_code, 200, publish_entity_resp.text)

        self._insert_source_object(fqn="warehouse.timestamp_binding_accounts")
        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "binding.timestamp_binding_account",
                    "display_name": "Timestamp Binding Account",
                    "binding_scope": "entity",
                    "bound_object_ref": "entity.timestamp_binding_account",
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "warehouse.timestamp_binding_accounts",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.account_id", "physical_name": "account_id"},
                                {
                                    "surface_ref": "field.create_time",
                                    "physical_name": "create_time",
                                },
                            ],
                            "time_surfaces": [
                                {
                                    "surface_ref": "time_surface.create_time",
                                    "physical_name": "create_time",
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
                    "time_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": "time.api_timestamp_binding",
                            },
                            "semantic_ref": "time.api_timestamp_binding",
                            "resolution_kind": "timestamp_column",
                            "timestamp_surface_ref": "time_surface.create_time",
                            "timestamp_format": "%Y%m%d %H:%M:%S",
                        }
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        self.assertEqual(
            binding_resp.json()["interface_contract"]["time_bindings"][0]["timestamp_format"],
            "%Y%m%d %H:%M:%S",
        )

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
        self.assertIn("surface_ref does not exist", resp.json()["detail"]["message"])

    def test_entity_binding_requires_identity_time_and_descriptor_targets(self) -> None:
        suffix = uuid4().hex[:8]
        time_ref, _ = self._create_time(
            f"time.account_created_{suffix}",
            semantic_roles=["business_anchor", "measurement"],
        )
        dimension_ref, _ = self._create_dimension(f"dimension.account_country_{suffix}")
        entity_ref, _ = self._create_entity(
            f"entity.account_contract_{suffix}",
            key_refs=["key.account_id"],
            primary_time_ref=time_ref,
            stable_descriptors=[{"dimension_ref": dimension_ref, "cardinality": "one"}],
        )

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
        self.assertIn("stable descriptor", resp.json()["detail"]["message"])

    def test_experiment_process_binding_rejects_legacy_process_scope(self) -> None:
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
                    "analysis_window": {"end_offset": {"value": 7, "unit": "day"}},
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
        detail = missing_context_resp.json()["detail"]
        self.assertEqual(detail["code"], "typed_binding_scope_not_authorable")
        self.assertIn("legacy process_object", detail["message"])

    def test_rate_metric_binding_rejects_legacy_metric_scope_before_input_validation(
        self,
    ) -> None:
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
                    "additivity_constraints": {
                        "dimension_policy": "none",
                        "time_axis_policy": "non_additive",
                    },
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
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "typed_binding_scope_not_authorable")
        self.assertIn("legacy metric", detail["message"])

    def test_derive_binding_revision_rejects_legacy_metric_binding_path(self) -> None:
        suffix = uuid4().hex[:8]
        binding_ref = f"binding.derive_binding_rate_{suffix}"
        metric_ref = f"metric.derive_binding_rate_{suffix}"
        self._insert_legacy_non_entity_binding(
            binding_ref=binding_ref,
            binding_scope="metric",
            bound_object_ref=metric_ref,
            status="published",
        )

        derive_resp = self.client.post(
            f"/semantic/bindings/{binding_ref}/revisions/derive",
            json={
                "base_revision": 1,
                "source_action_id": "action.add_denominator_binding",
                "target_metric_ref": metric_ref,
                "target_metric_revision": 1,
                "reuse_sections": ["carrier", "time", "imports", "satisfied_field_coverage"],
                "coverage_additions": [
                    {
                        "coverage_target": "metric_input.denominator",
                        "field_ref": "field.denominator",
                    }
                ],
            },
        )

        self.assertEqual(derive_resp.status_code, 422, derive_resp.text)
        detail = derive_resp.json()["detail"]
        self.assertEqual(detail["code"], "typed_binding_revision_derive_disabled")
        self.assertIn("legacy metric binding", detail["message"])

    @pytest.mark.slow
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

    def test_metric_binding_with_import_rejects_legacy_metric_scope(self) -> None:
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
        self.assertEqual(binding_resp.status_code, 422, binding_resp.text)
        detail = binding_resp.json()["detail"]
        self.assertEqual(detail["code"], "typed_binding_scope_not_authorable")
        self.assertIn("legacy metric", detail["message"])

    @pytest.mark.slow
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
            detail = resp.json()["detail"]
            message = detail["message"] if isinstance(detail, dict) else detail
            self.assertIn("cannot activate from status=published", message)

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
                code="activate_state_error",
                category="state",
                message_substring="expected draft",
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

    @pytest.mark.slow
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

    @pytest.mark.slow
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
            payload["guidance"]["schema_url"], "/openapi/schemas/TypedEntityCreateRequest?depth=6"
        )
        self.assertEqual(
            payload["guidance"]["contract_url"],
            "/openapi/paths/L3NlbWFudGljL2VudGl0aWVz?operation=post&expand=request,schemas&depth=6",
        )
        self.assertGreaterEqual(len(payload["guidance"]["examples"]), 1)
        self.assertIn("next_action", payload["guidance"])

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
        self.assertEqual(len(payload["guidance"]["examples"]), 1)
        self.assertNotIn("authoring_model", payload["guidance"])
        self.assertEqual(
            payload["guidance"]["examples"][0]["payload"]["header"]["binding_scope"],
            "entity",
        )

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

        resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.legacy_contract_error",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.account",
                    "observation_grain_ref": "grain.account",
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
                        "name": "accounts",
                        "semantics": "distinct accounts",
                        "aggregation": "count_distinct",
                    },
                },
                "field_bindings": [],
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        payload = resp.json()
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertEqual(payload["guidance"]["authoring_model"], "entity_first")
        self.assertIn("field_bindings", payload["guidance"]["legacy_physical_binding_fields"])
        self.assertIn(
            "entity.<entity>.field.<field>", payload["guidance"]["entity_first_next_action"]
        )

        resp = self.client.post(
            "/semantic/relationships",
            json={
                "relationship_ref": "invalid_relationship_ref",
                "left_entity_ref": "entity.left",
                "right_entity_ref": "entity.right",
                "key_alignment": {
                    "left_field_ref": "entity.left.field.id",
                    "right_field_ref": "entity.right.field.id",
                },
                "cardinality": "one_to_one",
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        payload = resp.json()
        self.assertIsInstance(payload["detail"], list)
        self.assertEqual(payload["error"]["code"], "request_validation_error")
        self.assertEqual(payload["guidance"]["docs_url"], "docs/api/semantic.md")

    @pytest.mark.slow
    def test_detail_reads_accept_canonical_refs_for_all_typed_object_families(self) -> None:
        suffix = uuid4().hex[:8]

        time_ref, _ = self._create_time(f"time.signup_time_{suffix}", publish=True)
        enum_ref, _ = self._create_enum_set(f"enum.signup_country_{suffix}", publish=True)
        dimension_ref, _ = self._create_dimension(
            f"dimension.signup_country_{suffix}",
            enum_set_ref=enum_ref,
            publish=True,
        )
        entity_ref, _ = self._create_entity(
            f"entity.account_{suffix}",
            key_refs=[f"key.account_id_{suffix}"],
            publish=True,
        )
        process_ref, _ = self._create_process_object(
            f"process.new_user_cohort_{suffix}",
            anchor_time_ref=time_ref,
            exported_dimension_refs=[dimension_ref],
            publish=True,
        )

        binding_source_fqn = f"warehouse.account_{suffix}"
        self._insert_source_object(fqn=binding_source_fqn)
        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.account_{suffix}",
                    "display_name": "Account Binding",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": binding_source_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.account_id", "physical_name": "account_id"}
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": f"key.account_id_{suffix}",
                            },
                            "semantic_ref": f"key.account_id_{suffix}",
                            "surface_ref": "field.account_id",
                        }
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        binding_ref = binding_resp.json()["header"]["binding_ref"]
        binding_id = binding_resp.json()["binding_id"]
        self.assertEqual(
            self.client.post(f"/semantic/bindings/{binding_id}/publish").status_code, 200
        )

        metric_ref, _ = self._create_metric(
            f"metric.account_count_{suffix}", entity_ref, publish=True
        )

        profile_ref, _ = self._create_compatibility_profile(
            f"compiler_profile.account_count_{suffix}",
            subject_kind="metric",
            subject_ref=metric_ref,
            requirement={"entity_refs": [entity_ref]},
            publish=True,
        )

        detail_reads = [
            (f"/semantic/process-objects/{process_ref}", "header", "process_ref", process_ref),
            (f"/semantic/dimensions/{dimension_ref}", "header", "dimension_ref", dimension_ref),
            (f"/semantic/time/{time_ref}", "header", "time_ref", time_ref),
            (f"/semantic/enum-sets/{enum_ref}", "header", "enum_set_ref", enum_ref),
            (f"/semantic/bindings/{binding_ref}", "header", "binding_ref", binding_ref),
            (f"/compiler/compatibility-profiles/{profile_ref}", None, "profile_ref", profile_ref),
        ]
        for path, parent_key, field_name, expected in detail_reads:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, resp.text)
            payload = resp.json()
            target = payload if parent_key is None else payload[parent_key]
            self.assertEqual(target[field_name], expected)

    def test_compatibility_profile_discovers_by_entity_pair_and_required_relationship(self) -> None:
        suffix = uuid4().hex[:8]
        left_ref, _ = self._create_entity(
            f"entity.profile_left_{suffix}",
            fields=[
                {"field_ref": "field.user_id", "value_type": "string", "physical_column": "user_id"}
            ],
            publish=True,
        )
        right_ref, _ = self._create_entity(
            f"entity.profile_right_{suffix}",
            fields=[
                {"field_ref": "field.user_id", "value_type": "string", "physical_column": "user_id"}
            ],
            publish=True,
        )
        relationship_resp = self.client.post(
            "/semantic/relationships",
            json={
                "relationship_ref": f"relationship.profile_pair_{suffix}",
                "left_entity_ref": left_ref,
                "right_entity_ref": right_ref,
                "key_alignment": {
                    "left_field_ref": f"{left_ref}.field.user_id",
                    "right_field_ref": f"{right_ref}.field.user_id",
                },
                "cardinality": "many_to_many",
            },
        )
        self.assertEqual(relationship_resp.status_code, 200, relationship_resp.text)
        relationship_id = relationship_resp.json()["relationship_id"]
        self.assertEqual(
            self.client.post(f"/semantic/relationships/{relationship_id}/publish").status_code,
            200,
        )

        metric_ref, _ = self._create_metric(
            f"metric.profile_cross_entity_{suffix}",
            left_ref,
            publish=True,
        )
        profile_resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": f"compiler_profile.profile_cross_entity_{suffix}",
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": metric_ref,
                "requirement": {
                    "entity_refs": [left_ref, right_ref],
                    "required_relationship_refs": [relationship_resp.json()["relationship_ref"]],
                    "grain_compatibility": {"required_grain_refs": ["grain.user_day"]},
                    "time_compatibility": {"alignment_basis": "event_time"},
                    "aggregation_compatibility": {"allowed_methods": ["sum", "count"]},
                    "field_profile_requirements": [
                        {
                            "field_ref": f"{right_ref}.field.user_id",
                            "required_value_type": "string",
                        }
                    ],
                    "governance_preflight": {
                        "required_checks": ["sensitivity_tags"],
                    },
                },
            },
        )
        self.assertEqual(profile_resp.status_code, 200, profile_resp.text)
        profile_id = profile_resp.json()["profile_id"]

        validate_resp = self.client.post(f"/compiler/compatibility-profiles/{profile_id}/validate")
        self.assertEqual(validate_resp.status_code, 200, validate_resp.text)
        self.assertTrue(validate_resp.json()["ok"])

        published_resp = self.client.post(f"/compiler/compatibility-profiles/{profile_id}/publish")
        self.assertEqual(published_resp.status_code, 200, published_resp.text)

        pair_resp = self.client.get(
            "/compiler/compatibility-profiles",
            params={
                "left_entity_ref": left_ref,
                "right_entity_ref": right_ref,
                "detail": "true",
            },
        )
        self.assertEqual(pair_resp.status_code, 200, pair_resp.text)
        self.assertEqual(pair_resp.json()["total"], 1)
        self.assertEqual(pair_resp.json()["items"][0]["profile_id"], profile_id)

    def test_metric_binding_rejects_legacy_metric_scope_before_measure_guidance(self) -> None:
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.account_measure_target",
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
        self.assertEqual(
            self.client.post(f"/semantic/entities/{entity_id}/publish").status_code, 200
        )

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.account_spend",
                    "display_name": "Account Spend",
                    "metric_family": "sum_metric",
                    "observed_entity_ref": "entity.account_measure_target",
                    "observation_grain_ref": "grain.account",
                    "sample_kind": "numeric",
                    "value_semantics": "sum",
                    "additivity_constraints": {
                        "dimension_policy": "all",
                        "time_axis_policy": "additive",
                    },
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "sum_metric",
                    "measure": {
                        "name": "spend",
                        "semantics": "total spend",
                        "aggregation": "sum",
                        "measure_ref": "measure.spend",
                    },
                },
            },
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        metric_id = metric_resp.json()["metric_contract_id"]
        self.assertEqual(
            self.client.post(f"/semantic/metrics/{metric_id}/publish").status_code, 200
        )

        resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "binding.account_spend_invalid",
                    "display_name": "Account Spend Invalid",
                    "binding_scope": "metric",
                    "bound_object_ref": "metric.account_spend",
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": "analytics.account_spend",
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.amount", "physical_name": "amount"}
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "metric_input", "target_key": "measure"},
                            "semantic_ref": "metric_input.measure",
                            "surface_ref": "field.amount",
                        }
                    ],
                },
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "typed_binding_scope_not_authorable")
        self.assertIn("legacy metric", detail["message"])

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
                    "additivity_constraints": {
                        "dimension_policy": "none",
                        "time_axis_policy": "non_additive",
                    },
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
                    "additivity_constraints": {
                        "dimension_policy": "all",
                        "time_axis_policy": "additive",
                    },
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
        self.assertEqual(binding_detail_resp.json()["readiness_status"], "stale")
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

        entity_detail_resp = self.client.get(f"/semantic/entities/{entity_id}")
        self.assertEqual(entity_detail_resp.status_code, 200, entity_detail_resp.text)
        self.assertIn(binding_ref, entity_detail_resp.json()["dependent_refs"])
        self.assertIn(
            metric_resp.json()["header"]["metric_ref"], entity_detail_resp.json()["dependent_refs"]
        )

        profile_detail_resp = self.client.get(f"/compiler/compatibility-profiles/{profile_id}")
        self.assertEqual(profile_detail_resp.status_code, 200, profile_detail_resp.text)
        self.assertEqual(profile_detail_resp.json()["readiness_status"], "stale")
        self.assertEqual(
            profile_detail_resp.json()["dependency_refs"],
            [
                metric_resp.json()["header"]["metric_ref"],
                entity_resp.json()["header"]["entity_ref"],
            ],
        )
        self.assertIn(
            "PROFILE_SUBJECT_REVISION_MISMATCH",
            {item["code"] for item in profile_detail_resp.json()["blocking_requirements"]},
        )

        metric_detail_resp = self.client.get(f"/semantic/metrics/{metric_id}")
        self.assertEqual(metric_detail_resp.status_code, 200, metric_detail_resp.text)
        self.assertIn(
            f"compiler_profile.readiness_case_{suffix}",
            metric_detail_resp.json()["dependent_refs"],
        )

        binding_list_resp = self.client.get("/semantic/bindings?status=published")
        self.assertEqual(binding_list_resp.status_code, 200, binding_list_resp.text)
        binding_summary_item = next(
            item for item in binding_list_resp.json()["items"] if item["binding_id"] == binding_id
        )
        self.assertEqual(binding_summary_item["readiness_status"], "stale")
        self.assertEqual(
            binding_summary_item["blocker_count"],
            len(binding_detail_resp.json()["blocking_requirements"]),
        )
        self.assertNotIn("interface_contract", binding_summary_item)

        binding_list_detail_resp = self.client.get(
            "/semantic/bindings?status=published&detail=true"
        )
        self.assertEqual(binding_list_detail_resp.status_code, 200, binding_list_detail_resp.text)
        binding_item = next(
            item
            for item in binding_list_detail_resp.json()["items"]
            if item["binding_id"] == binding_id
        )
        self.assertIn("interface_contract", binding_item)
        self.assertIn("dependent_refs", binding_item)

        binding_list_active_resp = self.client.get("/semantic/bindings?status=active&detail=true")
        self.assertEqual(binding_list_active_resp.status_code, 422, binding_list_active_resp.text)
        self.assertIn("Unsupported status filter", binding_list_active_resp.text)

        profile_list_resp = self.client.get("/compiler/compatibility-profiles?status=published")
        self.assertEqual(profile_list_resp.status_code, 200, profile_list_resp.text)
        self.assertNotIn("requirement", profile_list_resp.json()["items"][0])

        profile_list_detail_resp = self.client.get(
            "/compiler/compatibility-profiles?status=published&detail=true"
        )
        self.assertEqual(profile_list_detail_resp.status_code, 200, profile_list_detail_resp.text)
        profile_item = next(
            item
            for item in profile_list_detail_resp.json()["items"]
            if item["profile_id"] == profile_id
        )
        self.assertIn("requirement", profile_item)
        self.assertIn("dependent_refs", profile_item)

        profile_list_active_resp = self.client.get(
            "/compiler/compatibility-profiles?status=active&detail=true"
        )
        self.assertEqual(profile_list_active_resp.status_code, 422, profile_list_active_resp.text)
        self.assertIn("Unsupported status filter", profile_list_active_resp.text)

    def test_revalidate_compatibility_profile_updates_subject_revision(self) -> None:
        suffix = uuid4().hex[:8]
        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.profile_revalidate_{suffix}",
                    "display_name": "Profile Revalidate Entity",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.profile_revalidate_id_{suffix}"],
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

        metric_ref = f"metric.profile_revalidate_{suffix}"
        metric_payload = {
            "header": {
                "metric_ref": metric_ref,
                "display_name": "Profile Revalidate Metric",
                "metric_family": "count_metric",
                "observed_entity_ref": entity_resp.json()["header"]["entity_ref"],
                "observation_grain_ref": "grain.account",
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
                    "name": "rows",
                    "semantics": "row count",
                    "aggregation": "count",
                },
            },
        }
        metric_resp = self.client.post("/semantic/metrics", json=metric_payload)
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        metric_id = metric_resp.json()["metric_contract_id"]
        publish_metric_resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(publish_metric_resp.status_code, 200, publish_metric_resp.text)

        profile_ref = f"compiler_profile.profile_revalidate_{suffix}"
        profile_resp = self.client.post(
            "/compiler/compatibility-profiles",
            json={
                "profile_ref": profile_ref,
                "profile_kind": "requirement",
                "subject_kind": "metric",
                "subject_ref": metric_ref,
                "requirement": {"entity_refs": [entity_resp.json()["header"]["entity_ref"]]},
            },
        )
        self.assertEqual(profile_resp.status_code, 200, profile_resp.text)
        profile_id = profile_resp.json()["profile_id"]
        publish_profile_resp = self.client.post(
            f"/compiler/compatibility-profiles/{profile_id}/publish"
        )
        self.assertEqual(publish_profile_resp.status_code, 200, publish_profile_resp.text)
        self.assertEqual(publish_profile_resp.json()["subject_revision"], 1)

        revision_payload = json.loads(json.dumps(metric_payload))
        revision_payload["header"]["display_name"] = "Profile Revalidate Metric V2"
        create_revision_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions",
            json={
                "base_revision": 1,
                "change_summary": "Update display name",
                "expected_change_scope": "display_metadata",
                "replacement": revision_payload,
            },
        )
        self.assertEqual(create_revision_resp.status_code, 200, create_revision_resp.text)
        self.assertEqual(create_revision_resp.json()["revision"], 2)
        self.assertEqual(create_revision_resp.json()["classified_compatibility"], "compatible")

        activate_revision_resp = self.client.post(
            f"/semantic/metrics/{metric_ref}/revisions/2/activate"
        )
        self.assertEqual(activate_revision_resp.status_code, 200, activate_revision_resp.text)

        stale_profile_resp = self.client.get(f"/compiler/compatibility-profiles/{profile_ref}")
        self.assertEqual(stale_profile_resp.status_code, 200, stale_profile_resp.text)
        self.assertEqual(stale_profile_resp.json()["subject_revision"], 1)

        revalidate_resp = self.client.post(
            f"/compiler/compatibility-profiles/{profile_ref}/revalidate",
            json={"subject_revision": 2},
        )
        self.assertEqual(revalidate_resp.status_code, 200, revalidate_resp.text)
        self.assertEqual(revalidate_resp.json()["subject_revision"], 2)
        self.assertEqual(revalidate_resp.json()["readiness_status"], "ready")

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

    def test_semantic_batch_dry_run_returns_per_item_diagnostics(self) -> None:
        resp = self.client.post(
            "/semantic/batch",
            json={
                "mode": "dry_run",
                "lifecycle": "create_only",
                "continue_on_error": True,
                "items": [
                    {
                        "op_key": "bad_time",
                        "kind": "time",
                        "action": "create",
                        "payload": {},
                    },
                    {
                        "op_key": "time.batch_event_date",
                        "kind": "time",
                        "action": "create",
                        "payload": {
                            "header": {
                                "time_ref": "time.batch_event_date",
                                "display_name": "Batch Event Date",
                                "semantic_roles": ["measurement"],
                                "time_contract_version": "time.v1",
                            }
                        },
                    },
                ],
            },
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["summary"], {"total": 2, "succeeded": 1, "failed": 1, "skipped": 0}
        )
        self.assertEqual(payload["items"][0]["status"], "failed")
        self.assertEqual(payload["items"][0]["error"]["code"], "request_validation_error")
        self.assertEqual(payload["items"][1]["status"], "succeeded")
        self.assertTrue(payload["items"][1]["result"]["would_create"])

    def test_semantic_batch_rejects_legacy_metric_binding_item(self) -> None:
        suffix = uuid4().hex[:8]
        source_object_id = self._insert_source_object(
            fqn="main.analytics.watch_events",
            native_name="watch_events",
        )
        resp = self.client.post(
            "/semantic/batch",
            json={
                "mode": "apply",
                "lifecycle": "create_validate_activate",
                "continue_on_error": True,
                "defaults": {
                    "carrier_bindings": {
                        "watch_events_primary": {
                            "binding_key": "primary",
                            "source_object_ref": source_object_id,
                            "carrier_kind": "table",
                            "carrier_locator": {
                                "catalog": "main",
                                "schema": "analytics",
                                "table": "watch_events",
                            },
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.user_id", "physical_name": "user_id"}
                            ],
                        }
                    }
                },
                "items": [
                    {
                        "op_key": f"entity.batch_account_{suffix}",
                        "kind": "entity",
                        "action": "create",
                        "payload": {
                            "header": {
                                "entity_ref": f"entity.batch_account_{suffix}",
                                "display_name": "Batch Account",
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
                    },
                    {
                        "op_key": f"metric.batch_query_count_{suffix}",
                        "kind": "metric",
                        "action": "create",
                        "payload": {
                            "header": {
                                "metric_ref": f"metric.batch_query_count_{suffix}",
                                "display_name": "Batch Query Count",
                                "metric_family": "count_metric",
                                "observed_entity_ref": f"entity.batch_account_{suffix}",
                                "observation_grain_ref": "grain.account",
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
                                    "name": "rows",
                                    "semantics": "row count",
                                    "aggregation": "count",
                                },
                            },
                        },
                    },
                    {
                        "op_key": f"binding.batch_query_count_{suffix}",
                        "kind": "binding",
                        "action": "create",
                        "payload": {
                            "header": {
                                "binding_ref": f"binding.batch_query_count_{suffix}",
                                "display_name": "Batch Query Count Binding",
                                "binding_scope": "metric",
                                "bound_object_ref": f"metric.batch_query_count_{suffix}",
                                "binding_contract_version": "binding.v1",
                            },
                            "interface_contract": {
                                "carrier_binding_refs": ["watch_events_primary"],
                                "field_bindings": [
                                    {
                                        "carrier_binding_key": "primary",
                                        "target": {
                                            "target_kind": "metric_input",
                                            "target_key": "count_target",
                                        },
                                        "semantic_ref": "metric_input.count_target",
                                        "surface_ref": "field.user_id",
                                    }
                                ],
                            },
                        },
                    },
                ],
            },
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["summary"]["failed"], 1)
        binding_item = next(item for item in payload["items"] if item["kind"] == "binding")
        self.assertEqual(binding_item["status"], "failed")
        self.assertEqual(binding_item["error"]["code"], "typed_binding_scope_not_authorable")
        metric_item = next(item for item in payload["items"] if item["kind"] == "metric")
        self.assertEqual(metric_item["result"]["readiness_status"], "not_ready")
        self.assertEqual(payload["readiness_summary"]["counts"]["ready"], 1)
        self.assertEqual(
            payload["readiness_summary"]["final_metrics"][0]["readiness_status"], "not_ready"
        )

    def test_semantic_batch_dry_run_rejects_legacy_metric_binding_item(self) -> None:
        suffix = uuid4().hex[:8]
        resp = self.client.post(
            "/semantic/batch",
            json={
                "mode": "dry_run",
                "lifecycle": "create_only",
                "continue_on_error": True,
                "items": [
                    {
                        "op_key": f"binding.batch_metric_dry_run_{suffix}",
                        "kind": "binding",
                        "action": "create",
                        "payload": {
                            "header": {
                                "binding_ref": f"binding.batch_metric_dry_run_{suffix}",
                                "display_name": "Batch Metric Dry Run Binding",
                                "binding_scope": "metric",
                                "bound_object_ref": f"metric.batch_metric_dry_run_{suffix}",
                                "binding_contract_version": "binding.v1",
                            },
                            "interface_contract": {
                                "carrier_bindings": [
                                    {
                                        "binding_key": "primary",
                                        "carrier_kind": "table",
                                        "carrier_locator": "warehouse.batch_metric_dry_run",
                                        "binding_role": "primary",
                                        "field_surfaces": [
                                            {
                                                "surface_ref": "field.user_id",
                                                "physical_name": "user_id",
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
                                        "surface_ref": "field.user_id",
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["summary"]["failed"], 1)
        binding_item = payload["items"][0]
        self.assertEqual(binding_item["status"], "failed")
        self.assertEqual(binding_item["error"]["code"], "typed_binding_scope_not_authorable")

    def test_list_grains_exposes_metric_header_refs(self) -> None:
        suffix = uuid4().hex[:8]
        entity_ref, _entity_id = self._create_entity(f"entity.grain_account_{suffix}")
        metric_ref, _metric_id = self._create_metric(
            f"metric.grain_query_count_{suffix}", entity_ref
        )

        resp = self.client.get("/semantic/grains")

        self.assertEqual(resp.status_code, 200, resp.text)
        matching = [
            item
            for item in resp.json()["items"]
            if item["grain_ref"] == "grain.account" and item["source_ref"] == metric_ref
        ]
        self.assertEqual(matching[0]["source_kind"], "metric_observation")
