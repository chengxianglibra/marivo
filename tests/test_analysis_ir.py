from __future__ import annotations

import unittest

from app.analysis_core import AnalysisStepIR, ArtifactExpectation, SemanticIntent, from_legacy_step


class AnalysisIRTests(unittest.TestCase):
    def test_from_legacy_compare_metric_infers_semantic_and_artifact_contract(self) -> None:
        step = from_legacy_step(
            2,
            {
                "step_type": "compare_metric",
                "params": {
                    "metric_name": "watch_time",
                    "table_name": "analytics.watch_events",
                    "dimensions": ["platform", "app_version"],
                    "observation_type": "metric_change",
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
        self.assertIsInstance(step.artifact_expectation, ArtifactExpectation)
        self.assertEqual(step.artifact_expectation.artifact_key, "watch_time_comparison")
        self.assertEqual(step.observation_types(), ["metric_change"])
        self.assertEqual(step.execution_hints["limit"], 5)
        self.assertTrue(step.execution_hints["requires_period_context"])

    def test_from_legacy_domain_step_infers_default_table_and_optional_hint(self) -> None:
        step = from_legacy_step(0, {"step_type": "analyze_ads"})

        self.assertEqual(step.step_category, "composite")
        self.assertEqual(step.table_name(), "analytics.ad_events")
        self.assertEqual(step.routing_table_name(), "ad_events")
        self.assertEqual(step.primary_metric_name(), "preroll_timeout_rate")
        self.assertTrue(step.is_optional())
        self.assertEqual(step.observation_types(), ["ad_regression"])
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


if __name__ == "__main__":
    unittest.main()
