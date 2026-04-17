from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
from fastapi.testclient import TestClient

from app.analysis_core.compiler import compile_step
from app.analysis_core.ir import AnalysisStepIR
from app.evidence_engine.ref_boundary import assert_no_canonical_refs_in_semantic_payload
from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


def _seed_val_events_table(db_path: Path) -> None:
    get_seeded_duckdb_path(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.val_events (
                event_date DATE NOT NULL,
                value DOUBLE NOT NULL
            )
            """
        )
        con.executemany(
            "INSERT INTO analytics.val_events VALUES (?, ?)",
            [
                ("2024-01-01", 95.0),
                ("2024-01-02", 98.0),
                ("2024-01-03", 100.0),
                ("2024-01-04", 102.0),
                ("2024-01-05", 105.0),
            ],
        )
    finally:
        con.close()


class TypedSemanticEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "typed_semantic_e2e.duckdb"
        _seed_val_events_table(db_path)
        cls.client = TestClient(create_app(db_path))
        cls.service = cls.client.app.state.service
        cls.metadata = cls.client.app.state.metadata_store
        now = datetime.now(UTC).isoformat()
        cls.val_events_object_id = "obj_typed_semantic_e2e"
        cls.val_events_fqn = "analytics.val_events"
        cls.metadata.execute(
            """
            INSERT OR IGNORE INTO sources (
                source_id, source_type, display_name, connection_json, capabilities_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "src_typed_semantic_e2e",
                "duckdb",
                "Typed Semantic E2E Source",
                "{}",
                "{}",
                now,
                now,
            ],
        )
        cls.metadata.execute(
            """
            INSERT OR IGNORE INTO source_objects (
                object_id, source_id, object_type, native_name, fqn, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                cls.val_events_object_id,
                "src_typed_semantic_e2e",
                "table",
                "val_events",
                cls.val_events_fqn,
                now,
                now,
            ],
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_typed_object_binding_publish_compile_and_step_metadata_closure(self) -> None:
        suffix = uuid4().hex[:8]
        metric_key = f"s7_03_metric_{suffix}"
        metric_ref = f"metric.{metric_key}"
        time_ref = f"time.s7_03_event_date_{suffix}"
        entity_ref = f"entity.s7_03_subject_{suffix}"
        binding_ref = f"binding.s7_03_metric_{suffix}"

        time_resp = self.client.post(
            "/semantic/time",
            json={
                "header": {
                    "time_ref": time_ref,
                    "display_name": "S7-03 Event Date",
                    "semantic_roles": ["measurement"],
                    "time_contract_version": "time.v1",
                }
            },
        )
        self.assertEqual(time_resp.status_code, 200, time_resp.text)
        time_contract_id = time_resp.json()["time_contract_id"]
        self.assertEqual(
            self.client.post(f"/semantic/time/{time_contract_id}/publish").status_code, 200
        )

        entity_resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": entity_ref,
                    "display_name": "S7-03 Subject",
                    "entity_contract_version": "entity.v1",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": [f"key.s7_03_subject_id_{suffix}"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    },
                    "primary_time_ref": time_ref,
                },
            },
        )
        self.assertEqual(entity_resp.status_code, 200, entity_resp.text)
        entity_contract_id = entity_resp.json()["entity_contract_id"]
        self.assertEqual(
            self.client.post(f"/semantic/entities/{entity_contract_id}/publish").status_code,
            200,
        )

        metric_resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": metric_ref,
                    "display_name": "S7-03 Metric",
                    "metric_family": "sum_metric",
                    "observed_entity_ref": entity_ref,
                    "observation_grain_ref": "grain.day",
                    "sample_kind": "numeric",
                    "value_semantics": "sum",
                    "aggregation_scope": "window",
                    "primary_time_ref": time_ref,
                    "additivity": "additive",
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "sum_metric",
                    "measure": {
                        "name": "value",
                        "semantics": "Windowed value sum",
                        "aggregation": "sum",
                        "measure_ref": "measure.value",
                    },
                },
            },
        )
        self.assertEqual(metric_resp.status_code, 200, metric_resp.text)
        metric_contract_id = metric_resp.json()["metric_contract_id"]
        self.assertEqual(
            self.client.post(f"/semantic/metrics/{metric_contract_id}/publish").status_code,
            200,
        )

        binding_resp = self.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": binding_ref,
                    "display_name": "S7-03 Metric Binding",
                    "binding_scope": "metric",
                    "bound_object_ref": metric_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": self.val_events_object_id,
                            "carrier_kind": "table",
                            "carrier_locator": self.val_events_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {
                                    "surface_ref": "field.event_date",
                                    "physical_name": "event_date",
                                },
                                {
                                    "surface_ref": "field.value",
                                    "physical_name": "value",
                                },
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": time_ref,
                            },
                            "semantic_ref": time_ref,
                            "surface_ref": "field.event_date",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "metric_input",
                                "target_key": "measure",
                            },
                            "semantic_ref": "metric_input.measure",
                            "surface_ref": "field.value",
                        },
                    ],
                },
            },
        )
        self.assertEqual(binding_resp.status_code, 200, binding_resp.text)
        binding_id = binding_resp.json()["binding_id"]
        self.assertEqual(
            self.client.post(f"/semantic/bindings/{binding_id}/publish").status_code, 200
        )

        resolve_resp = self.client.get(f"/semantic/resolve/{metric_ref}")
        self.assertEqual(resolve_resp.status_code, 200, resolve_resp.text)
        self.assertEqual(resolve_resp.json()["object_kind"], "metric")

        session = self.service.create_session("S7-03 typed semantic closure", {}, {}, {})
        planner_resp = self.client.get(f"/sessions/{session['session_id']}/planner-context")
        self.assertEqual(planner_resp.status_code, 200, planner_resp.text)
        planner_metrics = planner_resp.json()["metrics"]
        self.assertTrue(
            any(metric["header"]["metric_ref"] == metric_ref for metric in planner_metrics)
        )

        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": metric_ref,
                    "table": "analytics.val_events",
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2024-01-01", "end": "2024-01-06"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "value",
                "dimensions": [],
                "semantic_repository": self.service.semantic_repository,
                "binding_reader": self.service._published_bindings_for_object_ref,
                "compatibility_profile_reader": (
                    self.service._published_compatibility_profiles_for_subject_ref
                ),
            },
        )

        self.assertIsNotNone(compiled.ir_bundle)
        assert compiled.ir_bundle is not None
        self.assertEqual(compiled.metadata["resolved_metric_ref"], metric_ref)
        self.assertEqual(compiled.metadata["resolved_binding_refs"], [binding_ref])
        self.assertEqual(
            compiled.ir_bundle["plan"]["inputs"]["metric_ref"],
            metric_ref,
        )
        self.assertEqual(
            compiled.ir_bundle["plan"]["inputs"]["resolved_bindings"][0]["binding_ref"],
            binding_ref,
        )
        assert_no_canonical_refs_in_semantic_payload(
            compiled.ir_bundle, surface="compiler_ir_bundle"
        )
        assert_no_canonical_refs_in_semantic_payload(compiled.metadata, surface="compiler_metadata")

        semantic_metadata = self.service.build_step_semantic_metadata(compiled)
        self.assertIsNotNone(semantic_metadata)
        assert semantic_metadata is not None
        self.assertEqual(semantic_metadata["typed_inputs"]["metric_ref"], metric_ref)
        self.assertEqual(semantic_metadata["binding_refs"], [binding_ref])
        assert_no_canonical_refs_in_semantic_payload(
            semantic_metadata,
            surface="step_semantic_metadata",
        )

        step_id = f"step_s703_{suffix}"
        self.service._insert_step(
            step_id,
            session["session_id"],
            "metric_query",
            "S7-03 typed semantic metric_query",
            {"artifact_id": f"art_{suffix}"},
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

        persisted_snapshot = json.loads(row["semantic_snapshot_json"])
        self.assertEqual(persisted_snapshot["typed_inputs"]["metric_ref"], metric_ref)
        self.assertEqual(persisted_snapshot["binding_refs"], [binding_ref])
        self.assertIn(
            compiled.metadata["ir_plan_id"],
            persisted_snapshot["compile_context"]["ir_plan_ids"],
        )
        self.assertIsNone(persisted_snapshot["compile_context"]["calendar_policy_binding"])
        assert_no_canonical_refs_in_semantic_payload(
            persisted_snapshot,
            surface="persisted_step_semantic_metadata",
        )
