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
from tests.semantic_test_helpers import (
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
)
from tests.shared_fixtures import get_seeded_duckdb_path


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
        self.assertGreaterEqual(len(snapshot["compile_context"]["ir_plan_ids"]), 1)
