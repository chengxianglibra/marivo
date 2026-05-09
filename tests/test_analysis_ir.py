from __future__ import annotations

import unittest

from marivo.analysis_core import (
    AnalysisStepIR,
    ArtifactExpectation,
    ExecutionPlanIR,
    SemanticIntent,
    request_from_session_payload,
    step_ir_from_mapping,
)


class AnalysisIRTests(unittest.TestCase):
    def test_from_typed_metric_query_infers_semantic_and_artifact_contract(self) -> None:
        step = step_ir_from_mapping(
            2,
            {
                "step_type": "metric_query",
                "params": {
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "dimensions": ["platform", "app_version"],
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-01", "end": "2026-03-08"},
                    },
                    "observation_type": "metric_observation",
                    "limit": 5,
                },
                "dependencies": [0, 1],
            },
        )

        self.assertEqual(step.step_category, "primitive")
        self.assertEqual(step.dependencies, [0, 1])
        self.assertEqual(step.table_name(), "analytics.watch_events")
        self.assertEqual(step.routing_table_name(), "watch_events")
        self.assertEqual(step.primary_metric_name(), "watch_time")
        self.assertIsInstance(step.semantic_intent, SemanticIntent)
        self.assertEqual(step.semantic_intent.dimensions, ["platform", "app_version"])
        self.assertIsNone(step.semantic_intent.date_column)
        self.assertIsInstance(step.artifact_expectation, ArtifactExpectation)
        self.assertEqual(step.artifact_expectation.artifact_key, "watch_time_metric_query")
        self.assertEqual(step.observation_types(), ["metric_observation"])
        self.assertEqual(step.execution_hints["limit"], 5)
        self.assertTrue(step.execution_hints["requires_period_context"])

    def test_manual_step_keeps_helper_behavior_without_inferred_contracts(self) -> None:
        step = AnalysisStepIR(
            index=0,
            step_type="sample_rows",
            params={"table_name": "analytics.watch_events", "limit": 10},
        )

        self.assertEqual(step.table_name(), "analytics.watch_events")
        self.assertEqual(step.routing_table_name(), "watch_events")
        self.assertIsNone(step.primary_metric_name())
        self.assertEqual(step.observation_types(), [])
        self.assertFalse(step.is_optional())

    def test_request_from_session_payload_aggregates_requested_metrics_and_tables(self) -> None:
        steps = [
            step_ir_from_mapping(
                0,
                {
                    "step_type": "metric_query",
                    "params": {
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "time_scope": {
                            "mode": "single_window",
                            "grain": "day",
                            "current": {"start": "2026-03-01", "end": "2026-03-08"},
                        },
                    },
                },
            ),
            step_ir_from_mapping(
                1,
                {
                    "step_type": "sample_rows",
                    "params": {"table_name": "analytics.watch_events", "limit": 5},
                },
            ),
        ]

        request = request_from_session_payload(
            {
                "session_id": "sess_123",
                "goal": "Investigate watch time",
                "constraints": {"region": "us"},
                "budget": {"max_rows_scanned": 1000},
                "policy": {"aggregate_only": True},
            },
            plan_id="plan_123",
            steps=steps,
        )

        self.assertEqual(request.session_id, "sess_123")
        self.assertEqual(request.plan_id, "plan_123")
        self.assertEqual(request.requested_step_types, ["metric_query", "sample_rows"])
        self.assertEqual(request.requested_metrics, ["watch_time"])
        self.assertEqual(request.requested_tables, ["analytics.watch_events"])

    def test_typed_aggregate_query_populates_requested_tables(self) -> None:
        steps = [
            step_ir_from_mapping(
                0,
                {
                    "step_type": "aggregate_query",
                    "params": {
                        "table": "analytics.watch_events",
                        "group_by": ["platform"],
                        "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                        "time_scope": {
                            "mode": "single_window",
                            "grain": "day",
                            "current": {"start": "2026-03-01", "end": "2026-03-08"},
                        },
                    },
                },
            ),
        ]

        request = request_from_session_payload(
            {
                "session_id": "sess_123",
                "goal": "Aggregate",
                "constraints": {},
                "budget": {},
                "policy": {},
            },
            plan_id="plan_123",
            steps=steps,
        )

        self.assertEqual(request.requested_tables, ["analytics.watch_events"])

    def test_step_ir_from_mapping_handles_sparse_payloads(self) -> None:
        step = step_ir_from_mapping(4, {"step_type": "sample_rows"})

        self.assertEqual(step.index, 4)
        self.assertEqual(step.step_type, "sample_rows")
        self.assertEqual(step.params, {})

    def test_execution_plan_ir_lookup_helpers(self) -> None:
        plan_ir = ExecutionPlanIR(plan_id="plan_123")

        self.assertIsNone(plan_ir.semantic_resolution_for_step(0))
        self.assertIsNone(plan_ir.execution_target_for_step(0))


if __name__ == "__main__":
    unittest.main()
