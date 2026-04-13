from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileUpdateRequest,
)
from app.api.models.entity import TypedEntityCreateRequest, TypedEntityUpdateRequest
from app.api.models.metric import TypedMetricCreateRequest, TypedMetricUpdateRequest
from app.semantic import SemanticService
from app.semantic_service import (
    CompatibilityProfileService,
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

    def test_typed_entity_legacy_active_status_requires_migration(self) -> None:
        entity = self.service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
                    "header": {
                        "entity_ref": "entity.legacy_user",
                        "display_name": "Legacy User",
                        "entity_contract_version": "entity.v1",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.legacy_user_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        }
                    },
                }
            )
        )
        with sqlite3.connect(str(self.metadata.db_path)) as con:
            con.execute("PRAGMA ignore_check_constraints = true")
            con.execute(
                "UPDATE semantic_entity_contracts SET status = ? WHERE entity_contract_id = ?",
                ["active", entity["entity_contract_id"]],
            )
            con.commit()

        with self.assertRaises(ValueError) as ctx:
            self.service.list_typed_entities()
        self.assertIn("Unknown storage status", str(ctx.exception))

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

    def test_compatibility_profile_update_requires_draft_and_increments_revision(self) -> None:
        entity = self.service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
                    "header": {
                        "entity_ref": "entity.profile_subject",
                        "display_name": "Profile Subject",
                        "entity_contract_version": "entity.v1",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.profile_subject_id"],
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
                        "metric_ref": "metric.profile_requirement",
                        "display_name": "Profile Requirement Metric",
                        "metric_family": "count_metric",
                        "observed_entity_ref": "entity.profile_subject",
                        "observation_grain_ref": "grain.profile_subject",
                        "sample_kind": "numeric",
                        "value_semantics": "count",
                        "additivity": "additive",
                        "metric_contract_version": "metric.v1",
                    },
                    "payload": {
                        "metric_family": "count_metric",
                        "count_target": {
                            "name": "subject_count",
                            "semantics": "distinct subjects",
                            "aggregation": "count_distinct",
                        },
                    },
                }
            )
        )

        profile = self.service.create_compatibility_profile(
            CompatibilityProfileCreateRequest.model_validate(
                {
                    "profile_ref": "compiler_profile.profile_requirement",
                    "profile_kind": "requirement",
                    "subject_kind": "metric",
                    "subject_ref": "metric.profile_requirement",
                    "requirement": {"entity_refs": ["entity.profile_subject"]},
                }
            )
        )
        updated = self.service.update_compatibility_profile(
            profile["profile_id"],
            CompatibilityProfileUpdateRequest.model_validate(
                {"requirement": {"entity_refs": ["entity.profile_subject"]}}
            ),
        )
        self.assertEqual(updated["revision"], 2)
        self.assertIsNone(updated["subject_revision"])

        with self.assertRaises(ValueError):
            self.service.publish_compatibility_profile(profile["profile_id"])

        self.service.publish_typed_metric(metric["metric_contract_id"])
        published = self.service.publish_compatibility_profile(profile["profile_id"])
        self.assertEqual(published["revision"], 3)
        self.assertEqual(published["subject_revision"], 2)

        with self.assertRaises(ValueError):
            self.service.update_compatibility_profile(
                profile["profile_id"],
                CompatibilityProfileUpdateRequest.model_validate(
                    {"requirement": {"entity_refs": ["entity.profile_subject"]}}
                ),
            )
