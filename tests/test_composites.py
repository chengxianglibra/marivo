from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.analysis_core import CompositeStepTemplate, CompositeWorkflowRuntime, CompositeWorkflowSpec
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class CompositeWorkflowRuntimeTests(unittest.TestCase):
    def test_watch_time_workflow_expands_to_ir_steps(self) -> None:
        runtime = CompositeWorkflowRuntime()

        steps = runtime.expand_workflow("watch_time_drop")

        self.assertEqual(
            [step.step_type for step in steps],
            [
                "compare_watch_time",
                "analyze_qoe",
                "analyze_ads",
                "analyze_recommendation",
                "synthesize_findings",
            ],
        )
        self.assertEqual(steps[-1].dependencies, [0, 1, 2, 3])
        self.assertEqual(steps[0].step_category, "composite")

    def test_runtime_renders_parameterized_workflow_params(self) -> None:
        runtime = CompositeWorkflowRuntime(
            {
                "demo_workflow": CompositeWorkflowSpec(
                    name="demo_workflow",
                    steps=[
                        CompositeStepTemplate(
                            "sample_rows",
                            params={"table_name": "{table_name}", "limit": "{limit}"},
                        )
                    ],
                )
            }
        )

        steps = runtime.expand_workflow(
            "demo_workflow",
            params={"table_name": "analytics.watch_events", "limit": 7},
        )

        self.assertEqual(steps[0].params["table_name"], "analytics.watch_events")
        self.assertEqual(steps[0].params["limit"], 7)


class CompositeWorkflowServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "composite.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "composite.duckdb"
        cls.metadata = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.metadata.initialize()
        cls.analytics.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_service_uses_workflow_runtime_spec(self) -> None:
        service = SemanticLayerService(self.metadata, self.analytics)
        service.workflow_runtime = CompositeWorkflowRuntime(
            {
                "watch_time_drop": CompositeWorkflowSpec(
                    name="watch_time_drop",
                    steps=[
                        CompositeStepTemplate("compare_watch_time"),
                        CompositeStepTemplate("synthesize_findings", dependencies=[0]),
                    ],
                )
            }
        )
        session = service.create_session("Composite runtime test", {}, {}, {})
        original_run_step = service.run_step

        def fake_run_step(session_id: str, step_type: str, params: dict | None = None) -> dict:
            del session_id, params
            if step_type == "compare_watch_time":
                return {
                    "step_type": step_type,
                    "summary": "Watch-time comparison ready",
                    "observations": [{"id": "obs_1"}],
                    "claims": [],
                    "recommendations": [],
                }
            return {
                "step_type": step_type,
                "summary": "Workflow synthesis complete",
                "claims": [{"text": "claim"}],
                "recommendations": [{"text": "rec"}],
            }

        service.run_step = fake_run_step  # type: ignore[method-assign]
        try:
            payload = service.run_watch_time_drop_workflow(session["session_id"])
        finally:
            service.run_step = original_run_step  # type: ignore[method-assign]

        self.assertEqual(payload["replanning"]["final_plan"], ["compare_watch_time", "synthesize_findings"])
        self.assertEqual([step["step_type"] for step in payload["steps"]], ["compare_watch_time", "synthesize_findings"])


if __name__ == "__main__":
    unittest.main()
