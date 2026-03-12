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
            from_legacy_step(0, {"step_type": "analyze_ads"}),
            {"summary": "No issue found", "observations": [], "claims": [], "recommendations": []},
            12.0,
            estimate=CostEstimate(subject="step:0", confidence="medium", engine_locality="bound_engine"),
        )

        self.assertEqual(feedback.code, "insufficient_evidence")
        self.assertTrue(feedback.replan_candidate)

    def test_decide_after_step_inserts_profile_step(self) -> None:
        step = from_legacy_step(0, {"step_type": "analyze_ads"})
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

    def test_decide_before_step_skips_optional_high_risk_step(self) -> None:
        step = from_legacy_step(0, {"step_type": "analyze_recommendation"})
        estimate = CostEstimate(
            subject="step:0",
            confidence="low",
            engine_locality="default_engine_fallback",
        )

        decision = self.replanner.decide_before_step(step, estimate)

        self.assertEqual(decision.action, "skip_step")

    def test_decide_on_error_replaces_with_profile_step(self) -> None:
        step = from_legacy_step(0, {"step_type": "analyze_qoe"})
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


class WorkflowReplanningTests(unittest.TestCase):
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

    def test_workflow_inserts_supplementary_steps(self) -> None:
        service = SemanticLayerService(self.metadata, self.analytics)
        session = service.create_session("Replanning test", {}, {}, {})
        original_run_step = service.run_step

        def fake_run_step(session_id: str, step_type: str, params: dict | None = None) -> dict:
            if step_type == "compare_watch_time":
                return {
                    "step_type": step_type,
                    "summary": "No strong signal yet",
                    "observations": [],
                    "claims": [],
                    "recommendations": [],
                }
            if step_type == "profile_table":
                return {
                    "step_type": step_type,
                    "summary": "Profile generated",
                    "profile": {"row_count": 10},
                }
            if step_type == "synthesize_findings":
                return {
                    "step_type": step_type,
                    "summary": "Final summary",
                    "claims": [{"text": "claim"}],
                    "recommendations": [{"text": "rec"}],
                }
            return {
                "step_type": step_type,
                "summary": f"{step_type} ok",
                "observations": [{"id": 1}],
                "claims": [],
                "recommendations": [],
            }

        service.run_step = fake_run_step  # type: ignore[method-assign]
        try:
            payload = service.run_watch_time_drop_workflow(session["session_id"])
        finally:
            service.run_step = original_run_step  # type: ignore[method-assign]

        executed_step_types = [step["step_type"] for step in payload["steps"]]
        self.assertIn("profile_table", executed_step_types)
        self.assertTrue(
            any(decision["action"] == "insert_steps" for decision in payload["replanning"]["decisions"])
        )

    def test_attach_replanning_provenance_updates_step_record(self) -> None:
        service = SemanticLayerService(self.metadata, self.analytics)
        session = service.create_session("Replanning provenance", {}, {}, {})
        service.run_step(session["session_id"], "compare_watch_time")

        service._attach_replanning_provenance(
            session["session_id"],
            "compare_watch_time",
            [{"action": "insert_steps", "reason": "test"}],
        )

        evidence = service.get_evidence_graph(session["session_id"])
        compare_step = next(
            step for step in evidence["steps"] if step["step_type"] == "compare_watch_time"
        )
        self.assertEqual(compare_step["provenance"]["replanning"][0]["action"], "insert_steps")


if __name__ == "__main__":
    unittest.main()
