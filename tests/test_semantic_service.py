from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.api.models.entity import TypedEntityCreateRequest, TypedEntityUpdateRequest
from app.api.models.metric import TypedMetricCreateRequest, TypedMetricUpdateRequest
from app.semantic import SemanticService
from app.semantic_service import (
    CompatibilityProfileService,
    LegacySemanticService,
    TypedBindingService,
    TypedObjectService,
)
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticServiceFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_semantic_service.duckdb"
        get_seeded_duckdb_path(self.db_path)
        self.metadata = SQLiteMetadataStore(self.db_path.with_suffix(".meta.sqlite"))
        self.metadata.initialize()
        self.service = SemanticService(self.metadata)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_facade_exposes_split_subservices(self) -> None:
        self.assertIsInstance(self.service.legacy, LegacySemanticService)
        self.assertIsInstance(self.service.typed_objects, TypedObjectService)
        self.assertIsInstance(self.service.bindings, TypedBindingService)
        self.assertIsInstance(
            self.service.compatibility_profiles,
            CompatibilityProfileService,
        )

    def test_facade_delegates_typed_entity_operations(self) -> None:
        entity = self.service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
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
                }
            )
        )
        self.assertEqual(entity["header"]["entity_ref"], "entity.user")
        listed = self.service.list_typed_entities()
        self.assertEqual(listed["total"], 1)

    def test_typed_entity_revision_increments_on_update_and_publish(self) -> None:
        entity = self.service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
                    "header": {
                        "entity_ref": "entity.account",
                        "display_name": "Account",
                        "entity_contract_version": "entity.v1",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.account_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        }
                    },
                }
            )
        )

        updated = self.service.update_typed_entity(
            entity["entity_contract_id"],
            TypedEntityUpdateRequest.model_validate({"description": "Customer account"}),
        )
        self.assertEqual(updated["revision"], 2)

        published = self.service.publish_typed_entity(entity["entity_contract_id"])
        self.assertEqual(published["revision"], 3)

        with self.assertRaises(ValueError):
            self.service.publish_typed_entity(entity["entity_contract_id"])

        with self.assertRaises(ValueError):
            self.service.update_typed_entity(
                entity["entity_contract_id"],
                TypedEntityUpdateRequest.model_validate({"description": "Should fail"}),
            )

    def test_typed_metric_published_contract_is_frozen(self) -> None:
        entity = self.service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
                    "header": {
                        "entity_ref": "entity.order",
                        "display_name": "Order",
                        "entity_contract_version": "entity.v1",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.order_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        }
                    },
                }
            )
        )
        self.service.publish_typed_entity(entity["entity_contract_id"])

        metric = self.service.create_typed_metric(
            TypedMetricCreateRequest.model_validate(
                {
                    "header": {
                        "metric_ref": "metric.orders",
                        "display_name": "Orders",
                        "metric_family": "count_metric",
                        "observed_entity_ref": "entity.order",
                        "observation_grain_ref": "grain.order",
                        "sample_kind": "numeric",
                        "value_semantics": "count",
                        "additivity": "additive",
                        "metric_contract_version": "metric.v1",
                    },
                    "payload": {
                        "metric_family": "count_metric",
                        "count_target": {
                            "name": "orders",
                            "semantics": "order count",
                            "aggregation": "count",
                        },
                    },
                }
            )
        )
        published = self.service.publish_typed_metric(metric["metric_contract_id"])
        self.assertEqual(published["revision"], 2)

        with self.assertRaises(ValueError):
            self.service.update_typed_metric(
                metric["metric_contract_id"],
                TypedMetricUpdateRequest.model_validate(
                    {
                        "payload": {
                            "metric_family": "count_metric",
                            "count_target": {
                                "name": "published_orders",
                                "semantics": "published order count",
                                "aggregation": "count",
                            },
                        }
                    }
                ),
            )

    def test_facade_delegates_legacy_mapping_operations(self) -> None:
        entity = self.service.create_entity(name="user", display_name="User", keys=["user_id"])
        mapping = self.service.create_mapping(
            semantic_type="entity",
            semantic_id=entity["entity_id"],
            object_id="obj_test",
            mapping_type="primary_source",
        )
        self.assertEqual(mapping["semantic_type"], "entity")
        listed = self.service.list_mappings(semantic_id=entity["entity_id"])
        self.assertEqual(len(listed), 1)
