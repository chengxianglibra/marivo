from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol

from app.analysis_core.ir import AnalysisStepIR
from app.analysis_core.workflows.workflow_runtime import CompositeWorkflowRuntime
from app.planner.replanning import ReplanningService

if TYPE_CHECKING:
    from app.approvals import ApprovalService
    from app.routing import QueryRouter
    from app.storage.analytics import AnalyticsEngine


class WorkflowStepExecutor(Protocol):
    def execute_step(self, session_id: str, step_ir: AnalysisStepIR) -> dict[str, Any]:
        ...

    def attach_replanning_provenance(
        self,
        session_id: str,
        step_type: str,
        decisions: list[dict[str, Any]],
    ) -> None:
        ...


class WorkflowOrchestrator:
    """Execute composite workflows through a compatibility-preserving replanning loop."""

    def __init__(
        self,
        workflow_runtime: CompositeWorkflowRuntime,
        replanner: ReplanningService,
        analytics_engine: AnalyticsEngine,
        step_executor: WorkflowStepExecutor,
        *,
        query_router: QueryRouter | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self.workflow_runtime = workflow_runtime
        self.replanner = replanner
        self.analytics_engine = analytics_engine
        self.query_router = query_router
        self.step_executor = step_executor
        self.approval_service = approval_service

    def execute_workflow(
        self,
        session_id: str,
        workflow_name: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow_plan = self.workflow_runtime.expand_workflow(workflow_name, params=params)
        results: list[dict[str, Any]] = []
        replan_decisions: list[dict[str, Any]] = []
        executed_step_types: list[str] = []
        plan_cursor = 0

        while plan_cursor < len(workflow_plan):
            step_ir = workflow_plan[plan_cursor]
            estimate = self.replanner.estimate_step(
                step_ir,
                analytics_engine=self.analytics_engine,
                query_router=self.query_router,
            )
            applied_decisions: list[dict[str, Any]] = []

            before_decision = self.replanner.decide_before_step(step_ir, estimate)
            if before_decision.action == "replace_step":
                replacement_step = before_decision.detail.get("replacement_step")
                if isinstance(replacement_step, dict):
                    step_ir = self.workflow_runtime.materialize_runtime_step(
                        replacement_step,
                        index=step_ir.index,
                    )
                    workflow_plan[plan_cursor] = step_ir
                    estimate = self.replanner.estimate_step(
                        step_ir,
                        analytics_engine=self.analytics_engine,
                        query_router=self.query_router,
                    )
                    applied_decisions.append(before_decision.to_dict())
                    replan_decisions.append(before_decision.to_dict())
            elif before_decision.action == "skip_step":
                replan_decisions.append(before_decision.to_dict())
                plan_cursor += 1
                continue

            started = time.perf_counter()
            try:
                result = self.step_executor.execute_step(session_id, step_ir)
            except Exception as error:
                failure_decision = self.replanner.decide_on_error(step_ir, error, estimate=estimate)
                replan_decisions.append(failure_decision.to_dict())
                if failure_decision.action == "replace_step":
                    replacement_step = failure_decision.detail.get("replacement_step")
                    if isinstance(replacement_step, dict):
                        workflow_plan[plan_cursor] = self.workflow_runtime.materialize_runtime_step(
                            replacement_step,
                            index=step_ir.index,
                        )
                        continue
                if failure_decision.action == "skip_step":
                    plan_cursor += 1
                    continue
                raise

            duration_ms = (time.perf_counter() - started) * 1000
            feedback = self.replanner.build_feedback(step_ir, result, duration_ms, estimate=estimate)
            after_decision = self.replanner.decide_after_step(step_ir, result, estimate, feedback)
            if after_decision.action == "insert_steps":
                insert_steps = after_decision.detail.get("insert_steps", [])
                if isinstance(insert_steps, list) and insert_steps:
                    workflow_plan[plan_cursor + 1:plan_cursor + 1] = self.workflow_runtime.materialize_runtime_steps(
                        insert_steps,
                        start_index=self.workflow_runtime.next_step_index(workflow_plan),
                    )
                    applied_decisions.append(after_decision.to_dict())
                    replan_decisions.append(after_decision.to_dict())
            elif after_decision.action == "skip_step":
                applied_decisions.append(after_decision.to_dict())
                replan_decisions.append(after_decision.to_dict())

            result["execution_feedback"] = feedback.to_dict()
            if applied_decisions:
                result["replanning"] = applied_decisions
                self.step_executor.attach_replanning_provenance(session_id, step_ir.step_type, applied_decisions)

            results.append(result)
            executed_step_types.append(step_ir.step_type)
            plan_cursor += 1

        final_result = results[-1]

        if self.approval_service:
            self.approval_service.auto_flag_recommendations(session_id, risk_threshold="P0")

        return {
            "session_id": session_id,
            "workflow": workflow_name,
            "steps": results,
            "final_summary": final_result["summary"],
            "claims": final_result.get("claims", []),
            "recommendations": final_result.get("recommendations", []),
            "replanning": {
                "decisions": replan_decisions,
                "executed_step_types": executed_step_types,
                "final_plan": [step.step_type for step in workflow_plan],
            },
        }
