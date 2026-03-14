from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.analysis_core.ir import from_legacy_step
from app.planner.replanning import ReplanningService
from app.runtime_contracts import CostEstimate
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class ReplanningServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.replanner = ReplanningService()

    def test_build_feedback_marks_insufficient_evidence(self) -> None:
        feedback = self.replanner.build_feedback(
            from_legacy_step(0, {
                "step_type": "compare_metric",
                "params": {"metric_name": "ad_fill_rate", "table_name": "analytics.ad_events"},
            }),
            {"summary": "No issue found", "observations": [], "claims": [], "recommendations": []},
            12.0,
            estimate=CostEstimate(subject="step:0", confidence="medium", engine_locality="bound_engine"),
        )

        self.assertEqual(feedback.code, "insufficient_evidence")
        self.assertTrue(feedback.replan_candidate)

    def test_decide_after_step_inserts_profile_step(self) -> None:
        step = from_legacy_step(0, {
            "step_type": "compare_metric",
            "params": {"metric_name": "ad_fill_rate", "table_name": "analytics.ad_events"},
        })
        estimate = CostEstimate(subject="step:0", confidence="medium", engine_locality="bound_engine")
        feedback = self.replanner.build_feedback(
            step,
            {"summary": "No issue found", "observations": [], "claims": [], "recommendations": []},
            12.0,
            estimate=estimate,
        )

        decision = self.replanner.decide_after_step(
            step,
            {"summary": "No issue found", "observations": [], "claims": [], "recommendations": []},
            estimate,
            feedback,
        )

        self.assertEqual(decision.action, "insert_steps")
        self.assertEqual(decision.detail["insert_steps"][0]["step_type"], "profile_table")
        self.assertEqual(
            decision.detail["insert_steps"][0]["params"]["table_name"],
            "analytics.ad_events",
        )

    def test_decide_before_step_replaces_risky_sample_rows(self) -> None:
        step = from_legacy_step(
            0,
            {"step_type": "sample_rows", "params": {"table_name": "analytics.watch_events"}},
        )
        estimate = CostEstimate(
            subject="step:0",
            confidence="low",
            engine_locality="default_engine_fallback",
        )

        decision = self.replanner.decide_before_step(step, estimate)

        self.assertEqual(decision.action, "replace_step")
        self.assertEqual(decision.detail["replacement_step"]["step_type"], "profile_table")

    def test_decide_before_step_continues_for_non_optional_step(self) -> None:
        """Non-optional steps under low confidence continue (no skip)."""
        step = from_legacy_step(0, {
            "step_type": "compare_metric",
            "params": {"metric_name": "watch_time", "table_name": "analytics.watch_events"},
        })
        estimate = CostEstimate(
            subject="step:0",
            confidence="low",
            engine_locality="default_engine_fallback",
        )

        decision = self.replanner.decide_before_step(step, estimate)

        self.assertEqual(decision.action, "continue")

    def test_decide_on_error_replaces_with_profile_step(self) -> None:
        step = from_legacy_step(0, {
            "step_type": "compare_metric",
            "params": {"metric_name": "qoe_metric", "table_name": "analytics.player_qoe"},
        })
        decision = self.replanner.decide_on_error(
            step,
            ValueError("compile failed for step"),
            estimate=CostEstimate(subject="step:0", confidence="low"),
        )

        self.assertEqual(decision.action, "replace_step")
        self.assertEqual(
            decision.detail["replacement_step"]["params"]["table_name"],
            "analytics.player_qoe",
        )


class AttachReplanningProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "replan.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "replan.duckdb"
        cls.metadata = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.metadata.initialize()
        cls.analytics.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_attach_replanning_provenance_updates_step_record(self) -> None:
        service = SemanticLayerService(self.metadata, self.analytics)
        session = service.create_session("Replanning provenance", {}, {}, {})
        service.run_step(
            session["session_id"],
            "profile_table",
            {"table_name": "analytics.watch_events"},
        )

        service._attach_replanning_provenance(
            session["session_id"],
            "profile_table",
            [{"action": "insert_steps", "reason": "test"}],
        )

        evidence = service.get_evidence_graph(session["session_id"])
        profile_step = next(
            step for step in evidence["steps"] if step["step_type"] == "profile_table"
        )
        self.assertEqual(profile_step["provenance"]["replanning"][0]["action"], "insert_steps")


if __name__ == "__main__":
    unittest.main()
