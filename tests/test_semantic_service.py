from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.api.models.compatibility_profile import (
    CompatibilityProfileCreateRequest,
    CompatibilityProfileUpdateRequest,
)
from app.api.models.dimension import DimensionCreateRequest
from app.api.models.entity import TypedEntityCreateRequest, TypedEntityUpdateRequest
from app.api.models.metric import (
    MetricRevisionCreateRequest,
    TypedMetricCreateRequest,
    TypedMetricUpdateRequest,
)
from app.api.models.predicate import PredicateCreateRequest
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

    def test_compatibility_profile_list_includes_builtin_calendar_policies(self) -> None:
        listed = self.service.list_compatibility_profiles(readiness_status="ready")

        holiday = next(
            item
            for item in listed["items"]
            if item["profile_ref"] == "calendar_policy.calendar_yoy"
        )
        self.assertEqual(holiday["status"], "published")
        self.assertEqual(holiday["lifecycle_status"], "active")
        self.assertEqual(holiday["readiness_status"], "ready")
        self.assertTrue(holiday["system_managed"])
        self.assertEqual(holiday["catalog_source"], "builtin_calendar_policy")

    def test_builtin_calendar_policy_profile_is_read_only(self) -> None:
        detail = self.service.read_compatibility_profile("calendar_policy.calendar_yoy")
        self.assertEqual(detail["profile_ref"], "calendar_policy.calendar_yoy")
        self.assertTrue(detail["system_managed"])
        self.assertEqual(detail["semantic"]["resolved_alignment_mode"], "calendar_aware")

        with self.assertRaisesRegex(ValueError, "system-managed builtin calendar policy"):
            self.service.update_compatibility_profile(
                "calendar_policy.calendar_yoy",
                CompatibilityProfileUpdateRequest.model_validate(
                    {"capability": {"inferential_ready": True}}
                ),
            )

        with self.assertRaisesRegex(ValueError, "system-managed builtin calendar policy"):
            self.service.publish_compatibility_profile("calendar_policy.calendar_yoy")

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
                        "additivity_constraints": {
                            "dimension_policy": "all",
                            "time_axis_policy": "additive",
                        },
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
        self.assertEqual(published["revision"], 1)
        self.assertEqual(published["is_latest_active"], True)

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

    def test_metric_revision_create_and_activate_preserves_old_revision(self) -> None:
        entity = self.service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
                    "header": {
                        "entity_ref": "entity.metric_revision_order",
                        "display_name": "Order",
                        "entity_contract_version": "entity.v1",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.metric_revision_order_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        }
                    },
                }
            )
        )
        self.service.publish_typed_entity(entity["entity_contract_id"])
        create_payload = {
            "header": {
                "metric_ref": "metric.revision_orders",
                "display_name": "Orders",
                "description": "Orders in seconds",
                "metric_family": "count_metric",
                "observed_entity_ref": "entity.metric_revision_order",
                "observation_grain_ref": "grain.order",
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
                    "name": "orders",
                    "semantics": "order count",
                    "aggregation": "count",
                },
            },
        }
        metric = self.service.create_typed_metric(
            TypedMetricCreateRequest.model_validate(create_payload)
        )
        published = self.service.publish_typed_metric(metric["metric_contract_id"])
        self.assertEqual(published["revision"], 1)

        replacement_payload = dict(create_payload)
        replacement_payload["header"] = dict(create_payload["header"])
        replacement_payload["header"]["description"] = "Orders in milliseconds"
        revision = self.service.create_metric_revision(
            "metric.revision_orders",
            MetricRevisionCreateRequest.model_validate(
                {
                    "base_revision": 1,
                    "change_summary": "Fix unit label",
                    "expected_change_scope": "display_metadata",
                    "replacement": replacement_payload,
                }
            ),
        )
        self.assertEqual(revision["revision"], 2)
        self.assertEqual(revision["status"], "draft")
        self.assertEqual(revision["base_revision"], 1)

        self.service.validate_metric_revision("metric.revision_orders", 2)
        default_before = self.service.read_typed_metric("metric.revision_orders")
        self.assertEqual(default_before["revision"], 1)

        activated = self.service.activate_metric_revision("metric.revision_orders", 2)
        self.assertEqual(activated["revision"], 2)
        self.assertEqual(activated["is_latest_active"], True)
        old = self.service.read_metric_revision("metric.revision_orders", 1)
        self.assertEqual(old["revision"], 1)
        self.assertEqual(old["is_latest_active"], False)
        default_after = self.service.read_typed_metric("metric.revision_orders")
        self.assertEqual(default_after["revision"], 2)

    def test_metric_update_persists_catalog_metadata(self) -> None:
        entity = self.service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
                    "header": {
                        "entity_ref": "entity.metric_catalog_domain",
                        "display_name": "Catalog Domain Entity",
                        "entity_contract_version": "entity.v1",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.metric_catalog_domain_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        }
                    },
                }
            )
        )
        metric = self.service.create_typed_metric(
            TypedMetricCreateRequest.model_validate(
                {
                    "header": {
                        "metric_ref": "metric.catalog_domain_update",
                        "display_name": "Catalog Domain Update",
                        "metric_family": "count_metric",
                        "observed_entity_ref": entity["header"]["entity_ref"],
                        "observation_grain_ref": "grain.metric_catalog_domain",
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
                            "name": "metric_catalog_domain_id",
                            "semantics": "catalog domain id",
                            "aggregation": "count_distinct",
                        },
                    },
                }
            )
        )

        updated = self.service.update_typed_metric(
            metric["metric_contract_id"],
            TypedMetricUpdateRequest.model_validate(
                {
                    "catalog_metadata": {
                        "domain_ref": "domain.growth",
                        "related_domain_refs": ["domain.core"],
                        "aliases": ["Catalog Domain"],
                    }
                }
            ),
        )

        self.assertEqual(
            updated["catalog_metadata"],
            {
                "domain_ref": "domain.growth",
                "related_domain_refs": ["domain.core"],
                "aliases": ["Catalog Domain"],
            },
        )

    def test_list_dimensions_detail_uses_list_context_for_dependents(self) -> None:
        dimension = self.service.create_dimension(
            DimensionCreateRequest.model_validate(
                {
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
                }
            )
        )
        self.service.publish_dimension(dimension["dimension_contract_id"])

        original = self.service.typed_objects._dependent_refs_for_ref

        def _fail_if_called(ref: str) -> list[str]:
            raise AssertionError(f"unexpected per-item dependent scan for {ref}")

        self.service.typed_objects._dependent_refs_for_ref = _fail_if_called
        try:
            listed = self.service.list_dimensions(
                lifecycle_status="active",
                readiness_status="ready",
                detail=True,
            )
        finally:
            self.service.typed_objects._dependent_refs_for_ref = original

        self.assertEqual(listed["total"], 1)
        self.assertEqual(
            listed["items"][0]["header"]["dimension_ref"],
            "dimension.discovery_channel",
        )
        self.assertIn("dependent_refs", listed["items"][0])

    def test_field_dependents_use_structured_refs_only(self) -> None:
        entity = self.service.create_typed_entity(
            TypedEntityCreateRequest.model_validate(
                {
                    "header": {
                        "entity_ref": "entity.field_structured_only",
                        "display_name": "Field Structured Only",
                        "entity_contract_version": "entity.v4",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.field_structured_only_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        },
                        "fields": [
                            {"field_ref": "field.country", "physical_column": "country"},
                        ],
                    },
                }
            )
        )
        predicate = self.service.create_predicate(
            PredicateCreateRequest.model_validate(
                {
                    "header": {
                        "predicate_ref": "predicate.field_structured_only",
                        "display_name": "Field Structured Only",
                        "description": "mentions field.country but is not a dependency",
                        "subject_ref": "entity.field_structured_only",
                        "predicate_contract_version": "predicate.v1",
                    },
                    "interface_contract": {
                        "expression": {
                            "op": "eq",
                            "target_ref": "entity.field_structured_only.field.country",
                            "value": "US",
                        },
                        "allowed_usage": ["metric_qualifier"],
                    },
                }
            )
        )

        dependents = self.service.field_dependents_for_entity_field(
            "entity.field_structured_only",
            "entity.field_structured_only.field.country",
        )

        self.assertEqual(
            dependents,
            [
                {
                    "object_kind": "predicate",
                    "ref": predicate["header"]["predicate_ref"],
                    "usage_paths": ["interface_contract.expression.target_ref"],
                    "usage_count": 1,
                }
            ],
        )
        detail = self.service.read_typed_entity(entity["entity_contract_id"])
        self.assertEqual(detail["field_dependency_graph"]["field.country"], dependents)

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
                        "additivity_constraints": {
                            "dimension_policy": "none",
                            "time_axis_policy": "non_additive",
                        },
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
                    "catalog_metadata": {
                        "domain_ref": "domain.growth",
                        "related_domain_refs": ["domain.core"],
                        "aliases": ["Profile Requirement"],
                    },
                }
            )
        )
        self.assertEqual(
            profile["catalog_metadata"],
            {
                "domain_ref": "domain.growth",
                "related_domain_refs": ["domain.core"],
                "aliases": ["Profile Requirement"],
            },
        )
        updated = self.service.update_compatibility_profile(
            profile["profile_id"],
            CompatibilityProfileUpdateRequest.model_validate(
                {
                    "requirement": {"entity_refs": ["entity.profile_subject"]},
                    "catalog_metadata": {
                        "domain_ref": "domain.core",
                        "related_domain_refs": ["domain.growth"],
                        "aliases": ["Updated Requirement"],
                    },
                }
            ),
        )
        self.assertEqual(updated["revision"], 2)
        self.assertIsNone(updated["subject_revision"])
        self.assertEqual(
            updated["catalog_metadata"],
            {
                "domain_ref": "domain.core",
                "related_domain_refs": ["domain.growth"],
                "aliases": ["Updated Requirement"],
            },
        )

        with self.assertRaises(ValueError):
            self.service.publish_compatibility_profile(profile["profile_id"])

        self.service.publish_typed_metric(metric["metric_contract_id"])
        published = self.service.publish_compatibility_profile(profile["profile_id"])
        self.assertEqual(published["revision"], 3)
        self.assertEqual(published["subject_revision"], 1)

        with self.assertRaises(ValueError):
            self.service.update_compatibility_profile(
                profile["profile_id"],
                CompatibilityProfileUpdateRequest.model_validate(
                    {"requirement": {"entity_refs": ["entity.profile_subject"]}}
                ),
            )
