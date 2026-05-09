from __future__ import annotations

import unittest

from marivo.analysis_core import (
    CompositeStepTemplate,
    CompositeWorkflowRuntime,
    CompositeWorkflowSpec,
)


class CompositeWorkflowRuntimeTests(unittest.TestCase):
    def test_unknown_workflow_raises_key_error(self) -> None:
        runtime = CompositeWorkflowRuntime()

        with self.assertRaises(KeyError):
            runtime.expand_workflow("nonexistent_workflow")

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

    def test_custom_workflow_spec_expands(self) -> None:
        runtime = CompositeWorkflowRuntime(
            {
                "custom_analysis": CompositeWorkflowSpec(
                    name="custom_analysis",
                    steps=[
                        CompositeStepTemplate(
                            "metric_query",
                            params={
                                "metric_name": "watch_time",
                                "table_name": "analytics.watch_events",
                            },
                        ),
                        CompositeStepTemplate(
                            "sample_rows",
                            params={"table_name": "analytics.watch_events", "limit": 5},
                            dependencies=[0],
                        ),
                    ],
                )
            }
        )

        steps = runtime.expand_workflow("custom_analysis")

        self.assertEqual(
            [step.step_type for step in steps],
            ["metric_query", "sample_rows"],
        )
        self.assertEqual(steps[-1].dependencies, [0])


if __name__ == "__main__":
    unittest.main()
