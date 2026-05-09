from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

from app.analysis_core.compiler import CompiledQuery
from app.api.app_factory import create_app
from app.evidence_engine.ref_boundary import assert_no_canonical_refs_in_semantic_payload
from app.runtime.runtime import MarivoRuntime
from app.runtime.semantic_ops import build_step_semantic_metadata
from app.storage.step_metadata_repository import StepMetadataRepository
from tests.semantic_test_helpers import (
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
    seed_duckdb_source_object,
)
from tests.shared_fixtures import get_seeded_duckdb_path

# Stub name for deleted model type -- no longer functional; see Task 7.
MetricRevisionCreateRequest = None  # type: ignore[assignment,misc]

_VALID_SOURCE_LINEAGE = {
    "table_fqn": "calendar",
    "calendar_version": "cn_2026q2_v1",
}


def _make_metadata_only_service() -> MarivoRuntime:
    from unittest.mock import MagicMock

    from app.core.engine import CoreEngine
    from app.runtime.ports import RuntimePorts

    ports = MagicMock(spec=RuntimePorts)
    core = CoreEngine()
    runtime = MarivoRuntime(ports, core)
    return runtime


class StepMetadataPersistenceTests(unittest.TestCase):
    temp_dir: ClassVar[tempfile.TemporaryDirectory[str]]
    service: ClassVar[Any]
    semantic_service: ClassVar[Any]
    metadata: ClassVar[Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "step_metadata.duckdb"
        get_seeded_duckdb_path(db_path)
        app = create_app(db_path)
        cls.service = cast("Any", app.state.services.runtime)
        cls.semantic_service = cast("Any", app.state.semantic_v2_service)
        cls.metadata = cls.service.metadata
        now = datetime.now(UTC).isoformat()
        seed_duckdb_source_object(
            cls.metadata,
            source_id="src_step_metadata",
            object_id="obj_step_metadata",
            display_name="Step Metadata Source",
            table_name="watch_events",
            table_fqn="analytics.watch_events",
            now=now,
            connection={"path": str(db_path), "catalog": "main"},
        )
        ensure_published_typed_metric(
            cls.metadata,
            metric_name="dau",
            display_name="Daily Active Users",
            grain="day",
            dimensions=["event_date"],
            definition_sql="COUNT(DISTINCT user_id)",
        )
        ensure_published_typed_metric_binding(
            cls.metadata,
            metric_name="dau",
            carrier_locator="analytics.watch_events",
            source_object_ref="obj_step_metadata",
        )
        ensure_published_typed_metric(
            cls.metadata,
            metric_name="revision_snapshot_dau",
            display_name="Revision Snapshot DAU",
        )
        ensure_published_typed_metric_binding(
            cls.metadata,
            metric_name="revision_snapshot_dau",
            carrier_locator="analytics.watch_events",
            source_object_ref="obj_step_metadata",
            surface_name="user_id",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_insert_step_persists_typed_semantic_step_metadata(self) -> None:
        session = self.service.create_session("step metadata test")
        step_id = "step_semantic_metadata_test"
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "step_type": "metric_query",
                "ir_plan_id": "plan_test_001",
                "normalized_request_class": "root_metric_process",
                "resolved_metric_ref": "metric.dau",
                "resolved_metric_revision": 2,
                "resolved_metric_object_id": "metc_dau_v2",
                "resolved_process_ref": None,
                "resolved_filter_time_ref": "time.event_date",
                "resolved_dimension_refs": ["dimension.country"],
                "resolved_binding_refs": ["binding.watch_events"],
                "resolved_entity_field_refs": ["entity.user.field.country"],
                "resolved_entity_field_sources": [
                    {
                        "field_ref": "entity.user.field.country",
                        "entity_ref": "entity.user",
                        "local_field_ref": "field.country",
                        "entity_revision": 3,
                        "value_type": "string",
                        "nullable": True,
                        "source_object_fqn": "analytics.user",
                        "carrier_kind": "table",
                        "physical_column": "country",
                        "usage_paths": ["dimension.country.source_field_ref"],
                    }
                ],
                "resolved_relationship_refs": ["relationship.user_account"],
                "resolved_relationship_sources": [
                    {
                        "relationship_ref": "relationship.user_account",
                        "left_entity_ref": "entity.user",
                        "right_entity_ref": "entity.account",
                        "revision": 5,
                        "key_alignment": {
                            "left_field_ref": "entity.user.field.user_id",
                            "right_field_ref": "entity.account.field.user_id",
                            "alignment_kind": "equality",
                        },
                        "cardinality": "one_to_many",
                        "grain_compatibility": {
                            "left_grain_ref": "grain.user_day",
                            "right_grain_ref": "grain.user_day",
                            "compatibility": "same_grain",
                        },
                    }
                ],
                "metric_entity_anchor_ref": "entity.user",
                "resolved_imported_dimensions": [
                    {
                        "dimension_ref": "dimension.cluster",
                        "source_binding_ref": "binding.entity_user",
                        "source_entity_ref": "entity.user",
                        "import_key": "entity_bridge",
                    }
                ],
                "imported_dimension_conflicts": {},
                "resolved_imported_dimension_sources": [
                    {
                        "dimension_ref": "dimension.cluster",
                        "source_binding_ref": "binding.entity_user",
                        "source_entity_ref": "entity.user",
                        "import_key": "entity_bridge",
                        "carrier_binding_key": "primary",
                        "carrier_locator": "analytics.entity_events",
                        "surface_ref": "field.cluster",
                        "physical_name": "cluster",
                    }
                ],
                "compiler_summary": {
                    "passed_gate_count": 2,
                    "warning_count": 0,
                    "validated_dimension_refs": ["dimension.country"],
                    "resolved_filter_time_ref": "time.event_date",
                },
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "resolved_baseline_generation_rule": {
                        "strategy": "offset",
                        "offset_value": 1,
                        "offset_unit": "year",
                        "fixed_start": None,
                        "fixed_end": None,
                        "named_window_ref": None,
                    },
                    "current_window": {"start": "2026-04-01", "end": "2026-05-01"},
                    "baseline_window": {"start": "2025-04-01", "end": "2025-05-01"},
                    "bucket_pairing": [],
                    "rollup_safe": True,
                    "coverage_summary": {
                        "aligned_bucket_count": 30,
                        "unpaired_bucket_count": 0,
                        "aligned_ratio": 1.0,
                    },
                    "comparability_warnings": [],
                    "source_lineage": {
                        "table_fqn": "calendar",
                        "calendar_version": "cn_2026q2_v1",
                    },
                },
            },
        )
        semantic_metadata = build_step_semantic_metadata(self.service, compiled)
        self.assertIsNotNone(semantic_metadata)
        self.service.insert_step(
            step_id,
            session.session_id,
            "metric_query",
            "metric query test",
            {"artifact_id": "art_test"},
            provenance={"engine": "duckdb"},
            semantic_metadata=semantic_metadata,
        )

        row = self.metadata.query_one(
            """
            SELECT metadata_kind, semantic_snapshot_json
            FROM step_metadata
            WHERE step_id = ?
            """,
            [step_id],
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["metadata_kind"], "typed_semantic_snapshot")

        snapshot = json.loads(row["semantic_snapshot_json"])
        assert_no_canonical_refs_in_semantic_payload(snapshot, surface="step_semantic_metadata")
        self.assertEqual(snapshot["typed_inputs"]["metric_ref"], "metric.dau")
        self.assertEqual(snapshot["typed_inputs"]["resolved_metric_revision"], 2)
        self.assertEqual(snapshot["typed_inputs"]["resolved_metric_object_id"], "metc_dau_v2")
        self.assertEqual(snapshot["typed_inputs"]["metric_entity_anchor_ref"], "entity.user")
        self.assertEqual(
            snapshot["compile_context"]["imported_dimension_lineage"][0]["dimension_ref"],
            "dimension.cluster",
        )
        self.assertEqual(
            snapshot["compile_context"]["imported_dimension_sources"][0]["carrier_locator"],
            "analytics.entity_events",
        )
        self.assertEqual(snapshot["entity_field_refs"], ["entity.user.field.country"])
        self.assertEqual(
            snapshot["compile_context"]["entity_field_sources"][0]["physical_column"],
            "country",
        )
        self.assertEqual(
            snapshot["resolved_refs"]["entity.user.field.country"]["entity_revision"],
            3,
        )
        self.assertEqual(
            snapshot["resolved_refs"]["entity.user.field.country"]["source_object_fqn"],
            "analytics.user",
        )
        self.assertEqual(snapshot["relationship_refs"], ["relationship.user_account"])
        self.assertEqual(
            snapshot["compile_context"]["relationship_sources"][0]["revision"],
            5,
        )
        self.assertEqual(
            snapshot["resolved_refs"]["relationship.user_account"]["revision"],
            5,
        )
        self.assertEqual(
            snapshot["compile_context"]["calendar_policy_binding"]["policy_ref"],
            "calendar_policy.calendar_yoy",
        )
        self.assertEqual(
            snapshot["compile_context"]["calendar_policy_binding"]["resolved_calendar_version"],
            "calendar_data_cn_2026q2_v1",
        )
        self.assertEqual(
            snapshot["compile_context"]["calendar_policy_binding"]["source_lineage"][
                "calendar_version"
            ],
            "cn_2026q2_v1",
        )
        self.assertGreaterEqual(len(snapshot["compile_context"]["ir_plan_ids"]), 1)

    def test_step_metadata_semantic_snapshot_freezes_metric_revision_after_activation(
        self,
    ) -> None:
        metric_ref = "metric.revision_snapshot_dau"
        session = self.service.create_session("step metadata revision freeze test")
        result = self.service.observe(
            session.session_id,
            {
                "metric": metric_ref,
                "time_scope": {
                    "kind": "range",
                    "start": "2024-01-01",
                    "end": "2024-01-08",
                },
            },
        )
        step_id = result["step_ref"]["step_id"]
        current = self.semantic_service.read_typed_metric(metric_ref)
        replacement = {
            "header": current["header"],
            "payload": current["payload"],
        }
        revision = self.semantic_service.create_metric_revision(
            metric_ref,
            MetricRevisionCreateRequest.model_validate(
                {
                    "base_revision": 1,
                    "change_summary": "Create equivalent revision for snapshot regression",
                    "replacement": replacement,
                }
            ),
        )
        self.assertEqual(revision["revision"], 2)
        self.semantic_service.activate_metric_revision(metric_ref, 2)

        step_metadata = StepMetadataRepository(self.metadata).get(step_id)
        self.assertIsNotNone(step_metadata)
        assert step_metadata is not None
        snapshot = step_metadata["semantic_snapshot"]
        resolved = snapshot["resolved_refs"][metric_ref]
        self.assertEqual(resolved["ref"], metric_ref)
        self.assertEqual(resolved["revision"], 1)
        self.assertIn("object_id", resolved)

    def test_build_step_semantic_metadata_omits_calendar_policy_binding_without_alignment(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "step_type": "metric_query",
                "ir_plan_id": "plan_test_002",
                "normalized_request_class": "root_metric_process",
                "resolved_metric_ref": "metric.dau",
            },
        )

        semantic_metadata = build_step_semantic_metadata(self.service, compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        self.assertIsNone(semantic_metadata["compile_context"]["calendar_policy_binding"])

    def test_build_step_semantic_metadata_rejects_conflicting_calendar_policy_bindings(
        self,
    ) -> None:
        compiled_queries = [
            CompiledQuery(
                "SELECT 1",
                metadata={
                    "resolved_calendar_alignment": {
                        "policy_ref": "calendar_policy.calendar_yoy",
                        "comparison_basis": "yoy",
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "source_lineage": _VALID_SOURCE_LINEAGE,
                    }
                },
            ),
            CompiledQuery(
                "SELECT 1",
                metadata={
                    "resolved_calendar_alignment": {
                        "policy_ref": "calendar_policy.weekday_yoy",
                        "comparison_basis": "yoy",
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "source_lineage": _VALID_SOURCE_LINEAGE,
                    }
                },
            ),
        ]

        with self.assertRaisesRegex(
            ValueError, "conflicting calendar policy bindings in compiled step metadata"
        ):
            build_step_semantic_metadata(self.service, compiled_queries)

    def test_build_step_semantic_metadata_rejects_missing_calendar_policy_binding_field(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": None,
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": _VALID_SOURCE_LINEAGE,
                }
            },
        )

        with self.assertRaisesRegex(ValueError, "resolved_calendar_alignment missing policy_ref"):
            build_step_semantic_metadata(self.service, compiled)

    def test_build_step_semantic_metadata_rejects_empty_calendar_source_lineage(self) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {},
                }
            },
        )

        with self.assertRaisesRegex(
            ValueError, "resolved_calendar_alignment missing source_lineage metadata"
        ):
            build_step_semantic_metadata(self.service, compiled)

    def test_build_step_semantic_metadata_rejects_invalid_calendar_source_lineage(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {"table_fqn": "calendar"},
                }
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "resolved_calendar_alignment source_lineage missing calendar_version",
        ):
            build_step_semantic_metadata(self.service, compiled)

    def test_build_step_semantic_metadata_allows_identical_calendar_policy_bindings(
        self,
    ) -> None:
        alignment = {
            "policy_ref": "calendar_policy.calendar_yoy",
            "comparison_basis": "yoy",
            "resolved_calendar_source": "calendar_data_cn_assembled",
            "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
            "source_lineage": _VALID_SOURCE_LINEAGE,
        }
        compiled_queries = [
            CompiledQuery("SELECT 1", metadata={"resolved_calendar_alignment": dict(alignment)}),
            CompiledQuery("SELECT 2", metadata={"resolved_calendar_alignment": dict(alignment)}),
        ]

        semantic_metadata = build_step_semantic_metadata(self.service, compiled_queries)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        self.assertEqual(
            semantic_metadata["compile_context"]["calendar_policy_binding"]["policy_ref"],
            "calendar_policy.calendar_yoy",
        )

    def test_build_step_semantic_metadata_uses_calendar_policy_binding_from_aligned_query(
        self,
    ) -> None:
        compiled_queries = [
            CompiledQuery(
                "SELECT 1",
                metadata={
                    "ir_plan_id": "plan_without_alignment",
                    "resolved_metric_ref": "metric.dau",
                },
            ),
            CompiledQuery(
                "SELECT 2",
                metadata={
                    "resolved_calendar_alignment": {
                        "policy_ref": "calendar_policy.calendar_yoy",
                        "comparison_basis": "yoy",
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "source_lineage": _VALID_SOURCE_LINEAGE,
                    }
                },
            ),
        ]

        semantic_metadata = build_step_semantic_metadata(self.service, compiled_queries)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        self.assertEqual(
            semantic_metadata["compile_context"]["calendar_policy_binding"][
                "resolved_calendar_version"
            ],
            "calendar_data_cn_2026q2_v1",
        )


class StepMetadataCalendarPolicyBindingUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _make_metadata_only_service()

    def test_build_step_semantic_metadata_accepts_valid_flat_source_lineage(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": _VALID_SOURCE_LINEAGE,
                }
            },
        )

        semantic_metadata = build_step_semantic_metadata(self.service, compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        binding = semantic_metadata["compile_context"]["calendar_policy_binding"]
        self.assertEqual(binding["source_lineage"], _VALID_SOURCE_LINEAGE)

    def test_build_step_semantic_metadata_rejects_missing_table_fqn(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        "calendar_version": "cn_2026q2_v1",
                    },
                }
            },
        )

        with self.assertRaisesRegex(
            ValueError, "resolved_calendar_alignment source_lineage missing table_fqn"
        ):
            build_step_semantic_metadata(self.service, compiled)

    def test_build_step_semantic_metadata_rejects_missing_calendar_version(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        "table_fqn": "calendar",
                    },
                }
            },
        )

        with self.assertRaisesRegex(
            ValueError, "resolved_calendar_alignment source_lineage missing calendar_version"
        ):
            build_step_semantic_metadata(self.service, compiled)

    def test_build_step_semantic_metadata_normalizes_source_lineage_to_required_fields(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.calendar_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        "table_fqn": "calendar",
                        "calendar_version": "cn_2026q2_v1",
                        "extra_key": "ignored",
                    },
                }
            },
        )

        semantic_metadata = build_step_semantic_metadata(self.service, compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        binding = semantic_metadata["compile_context"]["calendar_policy_binding"]
        self.assertEqual(binding["source_lineage"], _VALID_SOURCE_LINEAGE)
        self.assertNotIn("extra_key", binding["source_lineage"])
