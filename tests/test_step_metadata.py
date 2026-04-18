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
from app.service import SemanticLayerService
from tests.semantic_test_helpers import (
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
)
from tests.shared_fixtures import get_seeded_duckdb_path

_VALID_SOURCE_LINEAGE = {
    "holiday_source": {
        "source_id": "src_holiday",
        "source_name": "holiday_source",
        "table_fqn": "calendar.public_holiday",
        "calendar_version": "cn_public_holiday_2026_v1",
    }
}


def _make_metadata_only_service() -> SemanticLayerService:
    return cast("SemanticLayerService", object.__new__(SemanticLayerService))


class StepMetadataPersistenceTests(unittest.TestCase):
    temp_dir: ClassVar[tempfile.TemporaryDirectory[str]]
    service: ClassVar[Any]
    metadata: ClassVar[Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "step_metadata.duckdb"
        get_seeded_duckdb_path(db_path)
        app = create_app(db_path)
        cls.service = cast("Any", app.state.service)
        cls.metadata = cls.service.metadata
        now = datetime.now(UTC).isoformat()
        cls.metadata.execute(
            "INSERT OR IGNORE INTO sources "
            "(source_id, source_type, display_name, connection_json, capabilities_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["src_step_metadata", "duckdb", "Step Metadata Source", "{}", "{}", now, now],
        )
        cls.metadata.execute(
            "INSERT OR IGNORE INTO source_objects "
            "(object_id, source_id, object_type, native_name, fqn, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "obj_step_metadata",
                "src_step_metadata",
                "table",
                "watch_events",
                "analytics.watch_events",
                now,
                now,
            ],
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

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_insert_step_persists_typed_semantic_step_metadata(self) -> None:
        session = self.service.create_session("step metadata test", {}, {}, {})
        step_id = "step_semantic_metadata_test"
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "step_type": "metric_query",
                "ir_plan_id": "plan_test_001",
                "normalized_request_class": "root_metric_process",
                "resolved_metric_ref": "metric.dau",
                "resolved_process_ref": None,
                "resolved_filter_time_ref": "time.event_date",
                "resolved_dimension_refs": ["dimension.country"],
                "resolved_binding_refs": ["binding.watch_events"],
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
                    "policy_ref": "calendar_policy.holiday_yoy",
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
                    "coverage_summary": {
                        "aligned_bucket_count": 30,
                        "unpaired_bucket_count": 0,
                        "aligned_ratio": 1.0,
                    },
                    "comparability_warnings": [],
                    "source_lineage": {
                        "holiday_source": {
                            "source_id": "src_holiday",
                            "source_name": "holiday_source",
                            "table_fqn": "calendar.public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                        "event_source": {
                            "source_id": "src_event",
                            "source_name": "event_source",
                            "table_fqn": "calendar.business_event",
                            "calendar_version": "campaign_calendar_2026_q2_v3",
                        },
                    },
                },
            },
        )
        semantic_metadata = self.service.build_step_semantic_metadata(compiled)
        self.assertIsNotNone(semantic_metadata)
        self.service._insert_step(
            step_id,
            session["session_id"],
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
        self.assertEqual(snapshot["typed_inputs"]["metric_entity_anchor_ref"], "entity.user")
        self.assertEqual(
            snapshot["compile_context"]["imported_dimension_lineage"][0]["dimension_ref"],
            "dimension.cluster",
        )
        self.assertEqual(
            snapshot["compile_context"]["imported_dimension_sources"][0]["carrier_locator"],
            "analytics.entity_events",
        )
        self.assertEqual(
            snapshot["compile_context"]["calendar_policy_binding"]["policy_ref"],
            "calendar_policy.holiday_yoy",
        )
        self.assertEqual(
            snapshot["compile_context"]["calendar_policy_binding"]["resolved_calendar_version"],
            "calendar_data_cn_2026q2_v1",
        )
        self.assertEqual(
            snapshot["compile_context"]["calendar_policy_binding"]["source_lineage"][
                "holiday_source"
            ]["calendar_version"],
            "cn_public_holiday_2026_v1",
        )
        self.assertGreaterEqual(len(snapshot["compile_context"]["ir_plan_ids"]), 1)

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

        semantic_metadata = self.service.build_step_semantic_metadata(compiled)
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
                        "policy_ref": "calendar_policy.holiday_yoy",
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
            self.service.build_step_semantic_metadata(compiled_queries)

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
            self.service.build_step_semantic_metadata(compiled)

    def test_build_step_semantic_metadata_rejects_empty_calendar_source_lineage(self) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.holiday_yoy",
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
            self.service.build_step_semantic_metadata(compiled)

    def test_build_step_semantic_metadata_rejects_invalid_calendar_source_lineage(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.holiday_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {"holiday_source": {"source_id": "src_holiday"}},
                }
            },
        )

        with self.assertRaisesRegex(
            ValueError,
            "resolved_calendar_alignment source_lineage.holiday_source missing source_name",
        ):
            self.service.build_step_semantic_metadata(compiled)

    def test_build_step_semantic_metadata_allows_identical_calendar_policy_bindings(
        self,
    ) -> None:
        alignment = {
            "policy_ref": "calendar_policy.holiday_yoy",
            "comparison_basis": "yoy",
            "resolved_calendar_source": "calendar_data_cn_assembled",
            "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
            "source_lineage": _VALID_SOURCE_LINEAGE,
        }
        compiled_queries = [
            CompiledQuery("SELECT 1", metadata={"resolved_calendar_alignment": dict(alignment)}),
            CompiledQuery("SELECT 2", metadata={"resolved_calendar_alignment": dict(alignment)}),
        ]

        semantic_metadata = self.service.build_step_semantic_metadata(compiled_queries)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        self.assertEqual(
            semantic_metadata["compile_context"]["calendar_policy_binding"]["policy_ref"],
            "calendar_policy.holiday_yoy",
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
                        "policy_ref": "calendar_policy.holiday_yoy",
                        "comparison_basis": "yoy",
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "source_lineage": _VALID_SOURCE_LINEAGE,
                    }
                },
            ),
        ]

        semantic_metadata = self.service.build_step_semantic_metadata(compiled_queries)
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

    def test_build_step_semantic_metadata_accepts_holiday_only_calendar_source_lineage(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.holiday_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": _VALID_SOURCE_LINEAGE,
                }
            },
        )

        semantic_metadata = self.service.build_step_semantic_metadata(compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        binding = semantic_metadata["compile_context"]["calendar_policy_binding"]
        self.assertEqual(binding["source_lineage"], _VALID_SOURCE_LINEAGE)
        self.assertNotIn("event_source", binding["source_lineage"])

    def test_build_step_semantic_metadata_rejects_missing_holiday_calendar_source_lineage(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.holiday_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        "event_source": {
                            "source_id": "src_event",
                            "source_name": "event_source",
                            "table_fqn": "calendar.business_event",
                            "calendar_version": "campaign_calendar_2026_q2_v3",
                        }
                    },
                }
            },
        )

        with self.assertRaisesRegex(
            ValueError, "resolved_calendar_alignment missing source_lineage.holiday_source"
        ):
            self.service.build_step_semantic_metadata(compiled)

    def test_build_step_semantic_metadata_omits_incomplete_optional_event_source_lineage(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.holiday_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        **_VALID_SOURCE_LINEAGE,
                        "event_source": {
                            "source_name": "event_source",
                            "table_fqn": "calendar.business_event",
                            "calendar_version": "campaign_calendar_2026_q2_v3",
                        },
                    },
                }
            },
        )

        semantic_metadata = self.service.build_step_semantic_metadata(compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        binding = semantic_metadata["compile_context"]["calendar_policy_binding"]
        self.assertEqual(binding["source_lineage"], _VALID_SOURCE_LINEAGE)
        self.assertNotIn("event_source", binding["source_lineage"])

    def test_build_step_semantic_metadata_omits_empty_optional_event_source_lineage(
        self,
    ) -> None:
        compiled = CompiledQuery(
            "SELECT 1",
            metadata={
                "resolved_calendar_alignment": {
                    "policy_ref": "calendar_policy.holiday_yoy",
                    "comparison_basis": "yoy",
                    "resolved_calendar_source": "calendar_data_cn_assembled",
                    "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                    "source_lineage": {
                        **_VALID_SOURCE_LINEAGE,
                        "event_source": {},
                    },
                }
            },
        )

        semantic_metadata = self.service.build_step_semantic_metadata(compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        binding = semantic_metadata["compile_context"]["calendar_policy_binding"]
        self.assertEqual(binding["source_lineage"], _VALID_SOURCE_LINEAGE)
        self.assertNotIn("event_source", binding["source_lineage"])
