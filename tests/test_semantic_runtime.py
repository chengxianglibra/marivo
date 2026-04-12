from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.semantic_runtime import (
    CatalogRuntimeService,
    SemanticRuntimeInvalidRefError,
    SemanticRuntimeNotReadyError,
    SemanticRuntimeUnpublishedError,
)
from tests.semantic_test_helpers import (
    create_typed_entity,
    create_typed_metric,
    create_typed_metric_binding,
    publish_typed_entity,
    publish_typed_metric,
)
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

        entity = create_typed_entity(
            cls.client,
            name="user",
            display_name="User",
            description="A platform user",
            keys=["user_id"],
        )
        publish_typed_entity(cls.client, entity["entity_contract_id"])
        cls.entity_id = entity["entity_contract_id"]

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
        publish_typed_metric(cls.client, metric["metric_contract_id"])
        cls.metric_id = metric["metric_contract_id"]

        source = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Semantic Runtime Source",
                "connection": {"path": str(db_path)},
            },
        ).json()
        cls.source_id = source["source_id"]
        cls.client.post(f"/sources/{cls.source_id}/sync")

        table_objects = {
            table["native_name"]: table
            for table in cls.client.get(f"/sources/{cls.source_id}/objects?type=table").json()
        }
        cls.watch_events_object_id = table_objects["watch_events"]["object_id"]
        cls.watch_events_fqn = str(table_objects["watch_events"]["fqn"])

        create_typed_metric_binding(
            cls.client,
            metric_ref="metric.watch_time",
            object_id=cls.watch_events_object_id,
            carrier_locator=cls.watch_events_fqn,
            metric_input_target_keys=["numerator"],
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_semantic_resolver_hides_not_ready_metric(self) -> None:
        service = self.client.app.state.service

        resolved = service.semantic_resolver.resolve_metric("watch_time")
        self.assertIsNone(resolved)

    def test_semantic_repository_resolves_runtime_objects(self) -> None:
        repository = self.client.app.state.service.semantic_repository

        resolved_metric = repository.resolve_metric("watch_time")
        resolved_entity = repository.resolve_entity("user")

        self.assertIsNone(resolved_metric)
        self.assertIsNotNone(resolved_entity)
        assert resolved_entity is not None
        self.assertEqual(resolved_entity.entity_ref, "entity.user")
        self.assertEqual(resolved_entity.key_refs, ["key.user_id"])

    def test_semantic_repository_resolves_typed_refs(self) -> None:
        suffix = uuid4().hex[:8]

        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": f"time.runtime_anchor_{suffix}",
                    "display_name": "Runtime Anchor Time",
                    "semantic_roles": ["business_anchor", "measurement"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_contract_id = time_resp.json()["time_contract_id"]
        time_ref = time_resp.json()["header"]["time_ref"]
        self.assertEqual(
            self.client.post(f"/semantic/time/{time_contract_id}/publish").status_code, 200
        )

        enum_resp = self.client.post(
            "/semantic/enum-sets",
            json={
                "header": {
                    "enum_set_ref": f"enum.runtime_country_{suffix}",
                    "value_type": "string",
                },
                "display_name": "Runtime Countries",
                "versions": [
                    {
                        "enum_version": "v1",
                        "values": [
                            {"value_key": "CN", "raw_value": "CN", "label": "China"},
                            {"value_key": "US", "raw_value": "US", "label": "United States"},
                        ],
                    }
                ],
            },
        )
        self.assertEqual(enum_resp.status_code, 200, enum_resp.text)
        enum_contract_id = enum_resp.json()["enum_set_contract_id"]
        enum_ref = enum_resp.json()["header"]["enum_set_ref"]
        self.assertEqual(
            self.client.post(f"/semantic/enum-sets/{enum_contract_id}/publish").status_code,
            200,
        )

        dimension_resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": f"dimension.runtime_country_{suffix}",
                    "display_name": "Runtime Country",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                        "enum_set_ref": enum_ref,
                        "enum_version": "v1",
                    },
                    "grouping": {"supports_grouping": True},
                },
            },
        )
        self.assertEqual(dimension_resp.status_code, 200, dimension_resp.text)
        dimension_contract_id = dimension_resp.json()["dimension_contract_id"]
        dimension_ref = dimension_resp.json()["header"]["dimension_ref"]
        self.assertEqual(
            self.client.post(f"/semantic/dimensions/{dimension_contract_id}/publish").status_code,
            200,
        )

        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.runtime_user_{suffix}",
                    "display_name": "Runtime User",
                    "entity_contract_version": "entity.v1",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.runtime_user_id_{suffix}"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "primary_time_ref": time_ref,
                    "stable_descriptors": [{"dimension_ref": dimension_ref, "cardinality": "one"}],
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

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": f"metric.runtime_watch_time_{suffix}",
                    "display_name": "Runtime Watch Time",
                    "metric_family": "sum_metric",
                    "observed_entity_ref": entity_ref,
                    "observation_grain_ref": "grain.session",
                    "sample_kind": "numeric",
                    "value_semantics": "sum",
                    "aggregation_scope": "session",
                    "primary_time_ref": time_ref,
                    "additivity": "additive",
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "sum_metric",
                    "measure": {
                        "name": "watch_time_seconds",
                        "semantics": "Watch time in seconds",
                        "aggregation": "sum",
                        "measure_ref": "measure.watch_time_seconds",
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

        process_resp = self.client.post(
            "/semantic/process-objects",
            json={
                "header": {
                    "process_ref": f"process.runtime_session_{suffix}",
                    "display_name": "Runtime Session",
                    "process_type": "session_contract",
                    "process_contract_version": "process.v1",
                },
                "interface_contract": {
                    "contract_mode": "entity_stream",
                    "population_subject_ref": "subject.user",
                    "entity_ref": entity_ref,
                    "emitted_grain_ref": "grain.session",
                    "subject_cardinality": "many",
                    "anchor_time_ref": time_ref,
                    "exported_dimension_refs": [dimension_ref],
                },
                "payload": {
                    "process_type": "session_contract",
                    "session_key": f"runtime_session_{suffix}",
                    "event_stream_ref": "event_stream.watch_events",
                },
            },
        )
        self.assertEqual(process_resp.status_code, 200, process_resp.text)
        process_contract_id = process_resp.json()["process_contract_id"]
        process_ref = process_resp.json()["header"]["process_ref"]
        self.assertEqual(
            self.client.post(
                f"/semantic/process-objects/{process_contract_id}/publish"
            ).status_code,
            200,
        )

        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.runtime_entity_{suffix}",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": self.watch_events_object_id,
                            "carrier_kind": "table",
                            "carrier_locator": self.watch_events_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {
                                    "surface_ref": f"field.runtime_user_id_{suffix}",
                                    "physical_name": "user_id",
                                },
                                {
                                    "surface_ref": f"field.runtime_country_{suffix}",
                                    "physical_name": "country",
                                },
                                {
                                    "surface_ref": f"field.runtime_event_date_{suffix}",
                                    "physical_name": "event_date",
                                },
                            ],
                            "time_surfaces": [
                                {
                                    "surface_ref": f"time_surface.runtime_event_{suffix}",
                                    "physical_name": "event_date",
                                    "time_granularity": "day",
                                }
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": f"key.runtime_user_id_{suffix}",
                            },
                            "semantic_ref": f"key.runtime_user_id_{suffix}",
                            "surface_ref": f"field.runtime_user_id_{suffix}",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "primary_time", "target_key": time_ref},
                            "semantic_ref": time_ref,
                            "surface_ref": f"field.runtime_event_date_{suffix}",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "stable_descriptor",
                                "target_key": dimension_ref,
                            },
                            "semantic_ref": dimension_ref,
                            "surface_ref": f"field.runtime_country_{suffix}",
                        },
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        binding_id = binding_resp.json()["binding_id"]
        binding_ref = binding_resp.json()["header"]["binding_ref"]
        publish_binding_resp = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(publish_binding_resp.status_code, 200, publish_binding_resp.text)

        repository = self.client.app.state.service.semantic_repository

        metric_availability = repository.inspect_ref(metric_ref)
        self.assertEqual(metric_availability.lifecycle_status, "active")
        self.assertEqual(metric_availability.readiness_status, "not_ready")
        with self.assertRaises(SemanticRuntimeNotReadyError):
            repository.resolve_metric_ref(metric_ref)

        resolved_entity = repository.resolve_entity_ref(entity_ref)
        self.assertEqual(resolved_entity.object_kind, "entity")
        self.assertEqual(
            resolved_entity.semantic_object["interface_contract"]["primary_time_ref"],
            time_ref,
        )

        process_availability = repository.inspect_ref(process_ref)
        self.assertEqual(process_availability.lifecycle_status, "active")
        self.assertEqual(process_availability.readiness_status, "ready")
        resolved_process = repository.resolve_process_ref(process_ref)
        self.assertEqual(resolved_process.object_kind, "process")
        self.assertEqual(
            resolved_process.semantic_object["interface_contract"]["exported_dimension_refs"],
            [dimension_ref],
        )

        resolved_dimension = repository.resolve_dimension_ref(dimension_ref)
        self.assertEqual(resolved_dimension.object_kind, "dimension")
        self.assertEqual(
            resolved_dimension.semantic_object["interface_contract"]["value_domain"][
                "enum_set_ref"
            ],
            enum_ref,
        )

        resolved_time = repository.resolve_time_ref(time_ref)
        self.assertEqual(resolved_time.object_kind, "time")
        self.assertEqual(
            resolved_time.semantic_object["header"]["semantic_roles"],
            ["business_anchor", "measurement"],
        )

        resolved_binding = repository.resolve_binding_ref(binding_ref)
        self.assertEqual(resolved_binding.object_kind, "binding")
        self.assertEqual(
            resolved_binding.semantic_object["interface_contract"]["carrier_bindings"][0][
                "carrier_locator"
            ],
            self.watch_events_fqn,
        )
        with self.assertRaises(SemanticRuntimeNotReadyError):
            repository.resolve_ref(metric_ref)

        runtime = CatalogRuntimeService(
            self.metadata_store,
            self.binding_service,
            semantic_repository=repository,
        )
        for object_type, expected_ref in (
            ("process", process_ref),
            ("dimension", dimension_ref),
            ("time", time_ref),
            ("binding", binding_ref),
        ):
            search_results = runtime.search(suffix, object_type=object_type)
            self.assertTrue(any(result["ref"] == expected_ref for result in search_results))
        resolved_process_detail = runtime.resolve(process_ref)
        self.assertEqual(resolved_process_detail["object_kind"], "process")
        self.assertEqual(resolved_process_detail["ref"], process_ref)

    def test_typed_repository_rejects_invalid_and_unpublished_refs(self) -> None:
        repository = self.client.app.state.service.semantic_repository

        with self.assertRaises(SemanticRuntimeInvalidRefError):
            repository.resolve_ref("profile.not_supported")

        draft_suffix = uuid4().hex[:8]
        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": f"time.runtime_draft_{draft_suffix}",
                    "display_name": "Draft Runtime Time",
                    "semantic_roles": ["measurement"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        draft_time_ref = time_resp.json()["header"]["time_ref"]

        with self.assertRaises(SemanticRuntimeUnpublishedError):
            repository.resolve_time_ref(draft_time_ref)

    def test_typed_repository_rejects_unpublished_non_time_refs(self) -> None:
        repository = self.client.app.state.service.semantic_repository
        suffix = uuid4().hex[:8]

        enum_resp = self.client.post(
            "/semantic/enum-sets",
            json={
                "header": {
                    "enum_set_ref": f"enum.runtime_unpublished_country_{suffix}",
                    "value_type": "string",
                },
                "display_name": "Draft Runtime Countries",
                "versions": [
                    {
                        "enum_version": "v1",
                        "values": [
                            {"value_key": "CN", "raw_value": "CN", "label": "China"},
                        ],
                    }
                ],
            },
        )
        self.assertEqual(enum_resp.status_code, 200, enum_resp.text)
        enum_ref = enum_resp.json()["header"]["enum_set_ref"]
        enum_contract_id = enum_resp.json()["enum_set_contract_id"]
        self.assertEqual(
            self.client.post(f"/semantic/enum-sets/{enum_contract_id}/publish").status_code,
            200,
        )

        dimension_resp = self.client.post(
            "/semantic/dimensions",
            json={
                "header": {
                    "dimension_ref": f"dimension.runtime_unpublished_country_{suffix}",
                    "display_name": "Draft Runtime Country",
                    "dimension_contract_version": "dimension.v1",
                },
                "interface_contract": {
                    "value_domain": {
                        "structure_kind": "flat",
                        "semantic_role": "category",
                        "value_type": "string",
                        "domain_kind": "enumerated",
                        "enum_set_ref": enum_ref,
                        "enum_version": "v1",
                    },
                    "grouping": {"supports_grouping": True},
                },
            },
        )
        self.assertEqual(dimension_resp.status_code, 200, dimension_resp.text)
        draft_dimension_ref = dimension_resp.json()["header"]["dimension_ref"]

        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.runtime_unpublished_user_{suffix}",
                    "display_name": "Draft Runtime User",
                    "entity_contract_version": "entity.v1",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.runtime_unpublished_user_id_{suffix}"],
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

        process_resp = self.client.post(
            "/semantic/process-objects",
            json={
                "header": {
                    "process_ref": f"process.runtime_unpublished_session_{suffix}",
                    "display_name": "Draft Runtime Session",
                    "process_type": "session_contract",
                    "process_contract_version": "process.v1",
                },
                "interface_contract": {
                    "contract_mode": "entity_stream",
                    "population_subject_ref": "subject.user",
                    "entity_ref": entity_ref,
                    "emitted_grain_ref": "grain.session",
                    "subject_cardinality": "many",
                    "exported_dimension_refs": [draft_dimension_ref],
                },
                "payload": {
                    "process_type": "session_contract",
                    "session_key": f"runtime_unpublished_session_{suffix}",
                    "event_stream_ref": "event_stream.watch_events",
                },
            },
        )
        self.assertEqual(process_resp.status_code, 200, process_resp.text)
        draft_process_ref = process_resp.json()["header"]["process_ref"]

        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.runtime_unpublished_entity_{suffix}",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "carrier_kind": "table",
                            "carrier_locator": self.watch_events_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {
                                    "surface_ref": f"field.runtime_unpublished_user_id_{suffix}",
                                    "physical_name": "user_id",
                                }
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "identity_key",
                                "target_key": f"key.runtime_unpublished_user_id_{suffix}",
                            },
                            "semantic_ref": f"key.runtime_unpublished_user_id_{suffix}",
                            "surface_ref": f"field.runtime_unpublished_user_id_{suffix}",
                        }
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        draft_binding_ref = binding_resp.json()["header"]["binding_ref"]

        with self.assertRaises(SemanticRuntimeUnpublishedError):
            repository.resolve_dimension_ref(draft_dimension_ref)
        with self.assertRaises(SemanticRuntimeUnpublishedError):
            repository.resolve_process_ref(draft_process_ref)
        with self.assertRaises(SemanticRuntimeUnpublishedError):
            repository.resolve_binding_ref(draft_binding_ref)

    def test_planner_context_provider_includes_session_details(self) -> None:
        service = self.client.app.state.service
        session = service.create_session("Semantic runtime test", {}, {}, {})

        context = service.planner_context_provider.build_planner_context(session["session_id"])

        self.assertIn("session", context)
        self.assertEqual(context["session"]["session_id"], session["session_id"])
        self.assertEqual(context["session"]["goal"], "Semantic runtime test")
        self.assertFalse(
            any(
                metric["header"]["metric_ref"] == "metric.watch_time"
                for metric in context["metrics"]
            )
        )
        entity = next(
            entity
            for entity in context["entities"]
            if entity["header"]["entity_ref"] == "entity.user"
        )
        self.assertEqual(
            entity["interface_contract"]["identity"]["key_refs"],
            ["key.user_id"],
        )
        self.assertIsNone(entity["interface_contract"]["primary_time_ref"])
        self.assertNotIn("legacy", entity)

    def test_semantic_repository_builds_planner_context(self) -> None:
        repository = self.client.app.state.service.semantic_repository
        session = self.client.app.state.service.create_session(
            "Repository planner context", {}, {}, {}
        )

        context = repository.build_planner_context(session["session_id"])

        self.assertEqual(context["session"]["session_id"], session["session_id"])
        self.assertFalse(
            any(
                metric["header"]["metric_ref"] == "metric.watch_time"
                for metric in context["metrics"]
            )
        )

    def test_semantic_resolver_resolves_published_entity(self) -> None:
        service = self.client.app.state.service

        resolved = service.semantic_resolver.resolve_entity("user")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.entity_ref, "entity.user")
        self.assertEqual(resolved.key_refs, ["key.user_id"])
        self.assertEqual(resolved.stable_descriptors, [])

    def test_contract_tables_are_published_for_typed_semantic_objects(self) -> None:
        metric_contract = self.metadata_store.query_one(
            "SELECT * FROM semantic_metric_contracts WHERE metric_contract_id = ?",
            [self.metric_id],
        )
        entity_contract = self.metadata_store.query_one(
            "SELECT * FROM semantic_entity_contracts WHERE entity_contract_id = ?",
            [self.entity_id],
        )

        self.assertIsNotNone(metric_contract)
        self.assertIsNotNone(entity_contract)
        assert metric_contract is not None
        assert entity_contract is not None
        self.assertEqual(metric_contract["metric_ref"], "metric.watch_time")
        self.assertEqual(metric_contract["observed_entity_ref"], "entity.user")
        self.assertEqual(metric_contract["observation_grain_ref"], "grain.session")
        self.assertEqual(entity_contract["entity_ref"], "entity.user")
        self.assertEqual(entity_contract["entity_contract_version"], "entity.v1")
        key_refs = self.metadata_store.query_rows(
            "SELECT key_ref FROM semantic_entity_key_refs WHERE entity_contract_id = ? ORDER BY position",
            [self.entity_id],
        )
        self.assertEqual([row["key_ref"] for row in key_refs], ["key.user_id"])

    def test_catalog_runtime_search_finds_published_metric(self) -> None:
        runtime = CatalogRuntimeService(self.metadata_store, self.binding_service)

        results = runtime.search("watch", object_type="metric")
        self.assertFalse(any(result["name"] == "watch_time" for result in results))

        results = runtime.search("watch", object_type="metric", readiness="not_ready")
        metric = next(result for result in results if result["name"] == "watch_time")
        self.assertEqual(metric["object_kind"], "metric")
        self.assertEqual(metric["ref"], "metric.watch_time")
        self.assertEqual(metric["object_id"], self.metric_id)
        self.assertEqual(metric["lifecycle_status"], "active")
        self.assertEqual(metric["readiness_status"], "not_ready")
        self.assertEqual(metric["blocker_count"], 1)
        self.assertEqual(
            metric["blocking_requirements_preview"][0]["code"],
            "METRIC_INPUT_COVERAGE_MISSING",
        )
        self.assertFalse(metric["capabilities_summary"]["supports_validate"])

    def test_catalog_runtime_resolve_raises_not_ready_for_non_ready_metric(self) -> None:
        runtime = CatalogRuntimeService(self.metadata_store, self.binding_service)

        with self.assertRaises(SemanticRuntimeNotReadyError) as ctx:
            runtime.resolve("metric.watch_time")

        error = ctx.exception
        self.assertEqual(error.semantic_ref, "metric.watch_time")
        self.assertEqual(error.lifecycle_status, "active")
        self.assertEqual(error.readiness_status, "not_ready")
        self.assertEqual(error.blocking_requirements[0]["code"], "METRIC_INPUT_COVERAGE_MISSING")
        self.assertIn("entity.user", error.dependency_refs)
        detail = error.detail_payload()
        self.assertEqual(detail["code"], "semantic_not_ready")
        self.assertEqual(detail["category"], "readiness")
        self.assertEqual(detail["subject_ref"], "metric.watch_time")
        self.assertEqual(detail["object_kind"], "metric")
        self.assertEqual(detail["lifecycle_status"], "active")
        self.assertEqual(detail["readiness_status"], "not_ready")
        self.assertEqual(
            detail["blocking_requirements"][0]["code"], "METRIC_INPUT_COVERAGE_MISSING"
        )
        self.assertIn("entity.user", detail["dependency_refs"])

    def test_catalog_runtime_resolve_requires_explicit_typed_refs(self) -> None:
        runtime = CatalogRuntimeService(self.metadata_store, self.binding_service)

        entity_resolved = runtime.resolve("entity.user")

        self.assertEqual(entity_resolved["ref"], "entity.user")
        with self.assertRaises(SemanticRuntimeNotReadyError):
            runtime.resolve("metric.watch_time")
        with self.assertRaisesRegex(KeyError, "requires an explicit typed ref"):
            runtime.resolve("runtime_session")

    def test_catalog_runtime_search_rejects_invalid_type_filter(self) -> None:
        runtime = CatalogRuntimeService(self.metadata_store, self.binding_service)

        with self.assertRaises(ValueError):
            runtime.search("watch", object_type="profile")

    def test_catalog_runtime_planner_context_formats_runtime_payload(self) -> None:
        runtime = CatalogRuntimeService(
            self.metadata_store,
            self.binding_service,
            semantic_repository=self.client.app.state.service.semantic_repository,
        )
        session = self.client.app.state.service.create_session(
            "Catalog runtime planner context", {}, {}, {}
        )

        context = runtime.planner_context(session["session_id"])

        self.assertEqual(context["session_id"], session["session_id"])
        self.assertIn("metric_query", context["available_step_types"])
        self.assertFalse(
            any(
                metric["header"]["metric_ref"] == "metric.watch_time"
                for metric in context["metrics"]
            )
        )
        entity = next(
            entity
            for entity in context["entities"]
            if entity["header"]["entity_ref"] == "entity.user"
        )
        self.assertEqual(
            entity["interface_contract"]["identity"]["key_refs"],
            ["key.user_id"],
        )
        self.assertNotIn("legacy", entity)

    def test_runtime_hides_objects_without_published_typed_contracts(self) -> None:
        runtime = CatalogRuntimeService(
            self.metadata_store,
            self.binding_service,
            semantic_repository=self.client.app.state.service.semantic_repository,
        )
        session = self.client.app.state.service.create_session("Typed visibility gate", {}, {}, {})
        self.metadata_store.execute(
            "UPDATE semantic_metric_contracts SET status = 'draft' WHERE metric_contract_id = ?",
            [self.metric_id],
        )
        try:
            self.assertFalse(any(item["name"] == "watch_time" for item in runtime.search("watch")))
            with self.assertRaises(SemanticRuntimeUnpublishedError):
                runtime.resolve("metric.watch_time")
            context = runtime.planner_context(session["session_id"])
            self.assertFalse(
                any(
                    metric.get("header", {}).get("metric_ref") == "metric.watch_time"
                    for metric in context["metrics"]
                )
            )
            self.assertIsNone(
                self.client.app.state.service.semantic_repository.resolve_metric("watch_time")
            )
        finally:
            self.metadata_store.execute(
                "UPDATE semantic_metric_contracts SET status = 'published' WHERE metric_contract_id = ?",
                [self.metric_id],
            )

    def test_catalog_runtime_graph_traverses_metric_mapping(self) -> None:
        runtime = CatalogRuntimeService(self.metadata_store, self.binding_service)

        graph = runtime.graph(self.metric_id, depth=2)

        self.assertEqual(graph["root"], self.metric_id)
        self.assertTrue(any(node["id"] == self.metric_id for node in graph["nodes"]))
        self.assertTrue(any(edge["edge_type"] == "maps_to" for edge in graph["edges"]))


if __name__ == "__main__":
    unittest.main()
