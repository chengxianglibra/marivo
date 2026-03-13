from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.analysis_core.ir import from_legacy_step
from app.analysis_core.ir import ExecutionTargetIR
from app.execution.costing import CostModel
from app.main import create_app
from app.runtime_contracts import CostEstimate
from fastapi.testclient import TestClient
from tests.shared_fixtures import get_seeded_duckdb_path


class CostModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "cost_model.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        cls.cost_model = CostModel(
            analytics_engine=cls.client.app.state.analytics_engine,
            query_router=cls.client.app.state.query_router,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_estimate_step_uses_bound_route(self) -> None:
        estimate = self.cost_model.estimate_step(
            from_legacy_step(0, {"step_type": "compare_watch_time"})
        )

        self.assertGreater(estimate.estimated_rows or 0, 0)
        self.assertGreater(estimate.estimated_bytes or 0, 0)
        self.assertIn(
            estimate.engine_locality,
            {"bound_engine", "default_engine_fallback"},
        )
        self.assertEqual(estimate.join_fanout_risk, "low")

    def test_estimate_step_includes_cache_and_fallback_signals(self) -> None:
        estimate = self.cost_model.estimate_step(
            from_legacy_step(
                0,
                {
                    "step_type": "sample_rows",
                    "params": {"table_name": "analytics.watch_events", "limit": 10},
                },
            )
        )

        self.assertIn("limit_pushdown_candidate", estimate.cache_signals)
        self.assertIn("reduce_sample_limit", estimate.suggested_fallbacks)

    def test_estimate_step_with_execution_target_includes_engine_capabilities(self) -> None:
        engine = self.client.app.state.engine_service.list_engines()[0]
        estimate = self.cost_model.estimate_step(
            from_legacy_step(0, {"step_type": "compare_watch_time"}),
            execution_target=ExecutionTargetIR(
                step_index=0,
                table_names=["analytics.watch_events"],
                routing_table_names=["watch_events"],
                engine_id=engine["engine_id"],
                engine_type=engine["engine_type"],
                engine_locality="bound_engine",
            ),
        )

        self.assertEqual(estimate.detail["engine_capabilities"]["engine_type"], "duckdb")

    def test_budget_check_flags_unknown_estimates(self) -> None:
        unknown = self.cost_model.estimate_step(
            from_legacy_step(
                0,
                {"step_type": "profile_table", "params": {"table_name": "analytics.missing_table"}},
            )
        )

        result = self.cost_model.check_budget("plan_test", 100, [unknown])

        self.assertTrue(result.within_budget)
        self.assertEqual(result.risk_level, "medium")
        self.assertEqual(result.confidence, "low")
        self.assertEqual(result.unknown_subjects, ["step:0"])

    def test_build_actual_feedback_summarizes_execution(self) -> None:
        feedback = self.cost_model.build_actual_feedback(
            from_legacy_step(0, {"step_type": "compare_watch_time"}),
            {"summary": "ok", "observations": [1, 2], "claims": [1]},
            12.3456,
            estimate=CostEstimate(subject="step:0", confidence="medium"),
        )

        self.assertEqual(feedback["observation_count"], 2)
        self.assertEqual(feedback["claim_count"], 1)
        self.assertEqual(feedback["estimate_confidence"], "medium")


if __name__ == "__main__":
    unittest.main()
