from __future__ import annotations

import unittest

from app.analysis_core import CompositeStepTemplate, CompositeWorkflowRuntime, CompositeWorkflowSpec
from app.execution.orchestrator import WorkflowOrchestrator
from app.runtime_contracts import CostEstimate, ExecutionFeedback, ReplanDecision


class WorkflowOrchestratorTests(unittest.TestCase):
    def test_execute_workflow_preserves_payload_shape(self) -> None:
        runtime = CompositeWorkflowRuntime(
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
        executor = FakeStepExecutor(
            {
                "compare_watch_time": {
                    "step_type": "compare_watch_time",
                    "summary": "comparison ready",
                    "observations": [{"observation_id": "obs_1"}],
                    "claims": [],
                    "recommendations": [],
                },
                "synthesize_findings": {
                    "step_type": "synthesize_findings",
                    "summary": "workflow summary",
                    "claims": [{"claim_id": "claim_1"}],
                    "recommendations": [{"rec_id": "rec_1"}],
                },
            }
        )
        approvals = FakeApprovalService()
        orchestrator = WorkflowOrchestrator(
            runtime,
            FakeReplanner(),
            analytics_engine=object(),
            query_router=None,
            step_executor=executor,
            approval_service=approvals,
        )

        result = orchestrator.execute_workflow("sess_demo", "watch_time_drop")

        self.assertEqual(result["workflow"], "watch_time_drop")
        self.assertEqual(result["final_summary"], "workflow summary")
        self.assertEqual(result["replanning"]["final_plan"], ["compare_watch_time", "synthesize_findings"])
        self.assertEqual(result["replanning"]["executed_step_types"], ["compare_watch_time", "synthesize_findings"])
        self.assertEqual([step["step_type"] for step in result["steps"]], ["compare_watch_time", "synthesize_findings"])
        self.assertEqual(approvals.calls, [("sess_demo", "P0")])

    def test_execute_workflow_inserts_supplementary_steps(self) -> None:
        runtime = CompositeWorkflowRuntime(
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
        executor = FakeStepExecutor(
            {
                "compare_watch_time": {
                    "step_type": "compare_watch_time",
                    "summary": "comparison ready",
                    "observations": [],
                    "claims": [],
                    "recommendations": [],
                },
                "profile_table": {
                    "step_type": "profile_table",
                    "summary": "profile ready",
                    "profile": {"row_count": 10},
                },
                "synthesize_findings": {
                    "step_type": "synthesize_findings",
                    "summary": "workflow summary",
                    "claims": [],
                    "recommendations": [],
                },
            }
        )
        orchestrator = WorkflowOrchestrator(
            runtime,
            FakeReplanner(
                after={
                    "compare_watch_time": ReplanDecision(
                        action="insert_steps",
                        reason="Need profiling",
                        detail={
                            "insert_steps": [
                                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}}
                            ]
                        },
                    )
                }
            ),
            analytics_engine=object(),
            step_executor=executor,
        )

        result = orchestrator.execute_workflow("sess_demo", "watch_time_drop")

        self.assertEqual(
            result["replanning"]["executed_step_types"],
            ["compare_watch_time", "profile_table", "synthesize_findings"],
        )
        self.assertIn("profile_table", result["replanning"]["final_plan"])
        self.assertEqual(executor.provenance_updates[0][1], "compare_watch_time")

    def test_execute_workflow_replaces_failed_step(self) -> None:
        runtime = CompositeWorkflowRuntime(
            {
                "watch_time_drop": CompositeWorkflowSpec(
                    name="watch_time_drop",
                    steps=[
                        CompositeStepTemplate("analyze_qoe"),
                        CompositeStepTemplate("synthesize_findings", dependencies=[0]),
                    ],
                )
            }
        )
        executor = FakeStepExecutor(
            {
                "profile_table": {
                    "step_type": "profile_table",
                    "summary": "profile ready",
                    "profile": {"row_count": 10},
                },
                "synthesize_findings": {
                    "step_type": "synthesize_findings",
                    "summary": "workflow summary",
                    "claims": [],
                    "recommendations": [],
                },
            },
            errors={"analyze_qoe": ValueError("compile failed")},
        )
        orchestrator = WorkflowOrchestrator(
            runtime,
            FakeReplanner(
                on_error={
                    "analyze_qoe": ReplanDecision(
                        action="replace_step",
                        reason="Replace failed step",
                        detail={
                            "replacement_step": {
                                "step_type": "profile_table",
                                "params": {"table_name": "analytics.player_qoe"},
                            }
                        },
                    )
                }
            ),
            analytics_engine=object(),
            step_executor=executor,
        )

        result = orchestrator.execute_workflow("sess_demo", "watch_time_drop")

        self.assertEqual(
            result["replanning"]["executed_step_types"],
            ["profile_table", "synthesize_findings"],
        )
        self.assertEqual(result["replanning"]["final_plan"], ["profile_table", "synthesize_findings"])

    def test_execute_workflow_skips_optional_step(self) -> None:
        runtime = CompositeWorkflowRuntime(
            {
                "watch_time_drop": CompositeWorkflowSpec(
                    name="watch_time_drop",
                    steps=[
                        CompositeStepTemplate("compare_watch_time"),
                        CompositeStepTemplate("analyze_recommendation"),
                        CompositeStepTemplate("synthesize_findings", dependencies=[0, 1]),
                    ],
                )
            }
        )
        executor = FakeStepExecutor(
            {
                "compare_watch_time": {
                    "step_type": "compare_watch_time",
                    "summary": "comparison ready",
                    "observations": [{"observation_id": "obs_1"}],
                    "claims": [],
                    "recommendations": [],
                },
                "synthesize_findings": {
                    "step_type": "synthesize_findings",
                    "summary": "workflow summary",
                    "claims": [],
                    "recommendations": [],
                },
            }
        )
        orchestrator = WorkflowOrchestrator(
            runtime,
            FakeReplanner(
                before={
                    "analyze_recommendation": ReplanDecision(
                        action="skip_step",
                        reason="Skip optional step",
                        detail={"skipped_step_type": "analyze_recommendation"},
                    )
                }
            ),
            analytics_engine=object(),
            step_executor=executor,
        )

        result = orchestrator.execute_workflow("sess_demo", "watch_time_drop")

        self.assertEqual(
            result["replanning"]["executed_step_types"],
            ["compare_watch_time", "synthesize_findings"],
        )
        self.assertEqual(
            result["replanning"]["final_plan"],
            ["compare_watch_time", "analyze_recommendation", "synthesize_findings"],
        )


class FakeReplanner:
    def __init__(
        self,
        *,
        before: dict[str, ReplanDecision] | None = None,
        after: dict[str, ReplanDecision] | None = None,
        on_error: dict[str, ReplanDecision] | None = None,
    ) -> None:
        self.before = before or {}
        self.after = after or {}
        self.on_error = on_error or {}

    def estimate_step(self, step, analytics_engine=None, query_router=None):  # noqa: ANN001
        del analytics_engine, query_router
        return CostEstimate(subject=f"step:{step.index}", confidence="medium", engine_locality="bound_engine")

    def build_feedback(self, step, result, duration_ms, estimate=None):  # noqa: ANN001
        del step, result, duration_ms, estimate
        return ExecutionFeedback(
            code="step_completed",
            category="execution",
            message="ok",
            detail={},
        )

    def decide_before_step(self, step, estimate):  # noqa: ANN001
        del estimate
        return self.before.get(step.step_type, ReplanDecision(action="continue", reason="No-op"))

    def decide_after_step(self, step, result, estimate, feedback):  # noqa: ANN001
        del result, estimate, feedback
        return self.after.get(step.step_type, ReplanDecision(action="continue", reason="No-op"))

    def decide_on_error(self, step, error, estimate=None):  # noqa: ANN001
        del error, estimate
        return self.on_error.get(step.step_type, ReplanDecision(action="raise", reason="Unhandled"))


class FakeStepExecutor:
    def __init__(
        self,
        results: dict[str, dict],
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.results = results
        self.errors = errors or {}
        self.calls: list[tuple[str, dict | None]] = []
        self.provenance_updates: list[tuple[str, str, list[dict]]] = []

    def execute_step(self, session_id: str, step_ir) -> dict:  # noqa: ANN001
        self.calls.append((step_ir.step_type, step_ir.params))
        if step_ir.step_type in self.errors:
            raise self.errors[step_ir.step_type]
        result = dict(self.results[step_ir.step_type])
        result.setdefault("step_type", step_ir.step_type)
        result.setdefault("summary", f"{step_ir.step_type} complete")
        return result

    def attach_replanning_provenance(
        self,
        session_id: str,
        step_type: str,
        decisions: list[dict],
    ) -> None:
        self.provenance_updates.append((session_id, step_type, decisions))


class FakeApprovalService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def auto_flag_recommendations(self, session_id: str, risk_threshold: str = "P0") -> None:
        self.calls.append((session_id, risk_threshold))


if __name__ == "__main__":
    unittest.main()
