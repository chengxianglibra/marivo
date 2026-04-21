from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.semantic_runtime.dimensions import resolve_entity_binding_dimensions
from tests.semantic_test_helpers import (
    ensure_published_typed_dimension,
    ensure_published_typed_time,
)
from tests.shared_fixtures import get_named_seeded_duckdb_path


class TypedMetricDimensionResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "metric_dimension_resolution.duckdb"
        get_named_seeded_duckdb_path(cls.db_path, "metric_dimension_resolution")
        cls.client = TestClient(create_app(cls.db_path))
        cls.metadata_store = cls.client.app.state.metadata_store
        cls.service = cls.client.app.state.service
        source = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Metric Dimension Resolution Source",
                "connection": {"path": str(cls.db_path)},
            },
        ).json()
        cls.source_id = source["source_id"]
        cls.client.post(
            f"/sources/{cls.source_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "metric_dimension_events"},
                ]
            },
        )
        cls.client.post(f"/sources/{cls.source_id}/sync")
        source_object = cls.metadata_store.query_one(
            """
            SELECT object_id, fqn
            FROM source_objects
            WHERE source_id = ? AND object_type = 'table' AND fqn LIKE ?
            ORDER BY object_id
            """,
            [cls.source_id, "%.metric_dimension_events"],
        )
        assert source_object is not None
        cls.object_id = str(source_object["object_id"])
        cls.table_fqn = str(source_object["fqn"])
        ensure_published_typed_time(cls.metadata_store)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_entity(self, *, suffix: str, stable_descriptors: list[str]) -> str:
        for dimension_ref in stable_descriptors:
            ensure_published_typed_dimension(
                self.metadata_store,
                dimension_name=dimension_ref.removeprefix("dimension."),
            )
        response = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": f"entity.dimension_subject_{suffix}",
                    "display_name": f"Dimension Subject {suffix}",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "stable_descriptors": [
                        {"dimension_ref": dimension_ref} for dimension_ref in stable_descriptors
                    ],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        entity_id = response.json()["entity_contract_id"]
        entity_ref = response.json()["header"]["entity_ref"]
        publish_response = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(publish_response.status_code, 200, publish_response.text)
        return str(entity_ref)

    def _create_metric(self, *, suffix: str, entity_ref: str) -> str:
        response = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": f"metric.dimension_metric_{suffix}",
                    "display_name": f"Dimension Metric {suffix}",
                    "metric_family": "sum_metric",
                    "observed_entity_ref": entity_ref,
                    "observation_grain_ref": "grain.user",
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
                        "name": "value",
                        "semantics": "Metric value",
                        "aggregation": "sum",
                    },
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        metric_id = response.json()["metric_contract_id"]
        metric_ref = response.json()["header"]["metric_ref"]
        publish_response = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(publish_response.status_code, 200, publish_response.text)
        return str(metric_ref)

    def _create_entity_binding(
        self,
        *,
        suffix: str,
        entity_ref: str,
        binding_name: str,
        stable_descriptors: list[str],
    ) -> str:
        field_surfaces = [{"surface_ref": "field.user_id", "physical_name": "user_id"}]
        field_bindings: list[dict[str, object]] = [
            {
                "carrier_binding_key": "primary",
                "target": {
                    "target_kind": "identity_key",
                    "target_key": "key.user_id",
                },
                "semantic_ref": "key.user_id",
                "surface_ref": "field.user_id",
            }
        ]
        for dimension_ref in stable_descriptors:
            surface_ref = f"field.{dimension_ref.removeprefix('dimension.')}"
            field_surfaces.append(
                {
                    "surface_ref": surface_ref,
                    "physical_name": dimension_ref.removeprefix("dimension."),
                }
            )
            field_bindings.append(
                {
                    "carrier_binding_key": "primary",
                    "target": {
                        "target_kind": "stable_descriptor",
                        "target_key": dimension_ref,
                    },
                    "semantic_ref": dimension_ref,
                    "surface_ref": surface_ref,
                }
            )

        response = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.{binding_name}_{suffix}",
                    "display_name": f"{binding_name} {suffix}",
                    "binding_scope": "entity",
                    "bound_object_ref": entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": self.object_id,
                            "carrier_kind": "table",
                            "carrier_locator": self.table_fqn,
                            "binding_role": "primary",
                            "field_surfaces": field_surfaces,
                        }
                    ],
                    "field_bindings": field_bindings,
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        binding_id = response.json()["binding_id"]
        binding_ref = response.json()["header"]["binding_ref"]
        publish_response = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(publish_response.status_code, 200, publish_response.text)
        return str(binding_ref)

    def _create_metric_binding(self, *, suffix: str, metric_ref: str) -> str:
        response = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": f"binding.metric_binding_{suffix}",
                    "display_name": f"Metric Binding {suffix}",
                    "binding_scope": "metric",
                    "bound_object_ref": metric_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": self.object_id,
                            "carrier_kind": "table",
                            "carrier_locator": self.table_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.value", "physical_name": "value"},
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "metric_input",
                                "target_key": "measure",
                            },
                            "semantic_ref": "metric_input.measure",
                            "surface_ref": "field.value",
                        }
                    ],
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        binding_id = response.json()["binding_id"]
        binding_ref = response.json()["header"]["binding_ref"]
        publish_response = self.client.post(f"/semantic/bindings/{binding_id}/publish")
        self.assertEqual(publish_response.status_code, 200, publish_response.text)
        return str(binding_ref)

    @pytest.mark.slow
    def test_typed_metric_dimensions_fallback_to_entity_binding_stable_descriptors(self) -> None:
        suffix = uuid4().hex[:8]
        entity_ref = self._create_entity(
            suffix=suffix,
            stable_descriptors=["dimension.country", "dimension.plan"],
        )
        metric_ref = self._create_metric(suffix=suffix, entity_ref=entity_ref)
        self._create_entity_binding(
            suffix=suffix,
            entity_ref=entity_ref,
            binding_name="entity_binding_a",
            stable_descriptors=["dimension.country", "dimension.plan"],
        )
        self._create_entity_binding(
            suffix=suffix,
            entity_ref=entity_ref,
            binding_name="entity_binding_b",
            stable_descriptors=["dimension.country", "dimension.plan"],
        )
        self._create_metric_binding(suffix=suffix, metric_ref=metric_ref)

        self.assertEqual(
            self.service.resolve_metric_dimensions(metric_ref),
            ["dimension.country", "dimension.plan"],
        )
        self.assertEqual(
            self.service.semantic_repository.resolve_metric_dimensions(
                metric_ref.removeprefix("metric.")
            ),
            ["dimension.country", "dimension.plan"],
        )
        resolved_metric = self.service.semantic_repository.resolve_metric(
            metric_ref.removeprefix("metric.")
        )
        self.assertIsNotNone(resolved_metric)
        assert resolved_metric is not None
        self.assertEqual(resolved_metric.dimensions, ["dimension.country", "dimension.plan"])

    def test_entity_dimension_helper_ignores_invalid_semantic_refs(self) -> None:
        suffix = uuid4().hex[:8]
        entity_ref = self._create_entity(
            suffix=suffix,
            stable_descriptors=["dimension.country"],
        )
        binding_ref = self._create_entity_binding(
            suffix=suffix,
            entity_ref=entity_ref,
            binding_name="entity_binding_invalid",
            stable_descriptors=["dimension.country"],
        )
        binding_row = self.metadata_store.query_one(
            "SELECT binding_id FROM typed_bindings WHERE binding_ref = ?",
            [binding_ref],
        )
        assert binding_row is not None
        self.metadata_store.execute(
            """
            UPDATE field_bindings
            SET semantic_ref = 'country'
            WHERE binding_id = ? AND target_kind = 'stable_descriptor'
            """,
            [binding_row["binding_id"]],
        )

        self.assertEqual(resolve_entity_binding_dimensions(self.metadata_store, entity_ref), [])

    def test_explicit_metric_dimensions_override_entity_binding_fallback(self) -> None:
        suffix = uuid4().hex[:8]
        entity_ref = self._create_entity(
            suffix=suffix,
            stable_descriptors=["dimension.country", "dimension.plan"],
        )
        metric_ref = self._create_metric(suffix=suffix, entity_ref=entity_ref)
        self._create_entity_binding(
            suffix=suffix,
            entity_ref=entity_ref,
            binding_name="entity_binding_explicit",
            stable_descriptors=["dimension.country", "dimension.plan"],
        )
        self._create_metric_binding(suffix=suffix, metric_ref=metric_ref)

        metric_row = self.metadata_store.query_one(
            """
            SELECT metric_contract_id, family_payload_json
            FROM semantic_metric_contracts
            WHERE metric_ref = ?
            """,
            [metric_ref],
        )
        assert metric_row is not None
        ensure_published_typed_dimension(
            self.metadata_store,
            dimension_name="explicit_override",
        )
        family_payload = json.loads(metric_row["family_payload_json"] or "{}")
        family_payload["dimensions"] = ["dimension.explicit_override"]
        self.metadata_store.execute(
            """
            UPDATE semantic_metric_contracts
            SET family_payload_json = ?
            WHERE metric_contract_id = ?
            """,
            [json.dumps(family_payload), metric_row["metric_contract_id"]],
        )

        self.assertEqual(
            self.service.resolve_metric_dimensions(metric_ref),
            ["dimension.explicit_override"],
        )
        self.assertEqual(
            self.service.semantic_repository.resolve_metric_dimensions(
                metric_ref.removeprefix("metric.")
            ),
            ["dimension.explicit_override"],
        )
