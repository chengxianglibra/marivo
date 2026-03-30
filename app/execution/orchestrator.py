from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from app.analysis_core.ir import AnalysisStepIR
from app.analysis_core.workflows.workflow_runtime import CompositeWorkflowRuntime

if TYPE_CHECKING:
    from app.approvals import ApprovalService


class WorkflowStepExecutor(Protocol):
    def execute_step(self, session_id: str, step_ir: AnalysisStepIR) -> dict[str, Any]: ...


class WorkflowOrchestrator:
    """Execute composite workflows step by step."""

    def __init__(
        self,
        workflow_runtime: CompositeWorkflowRuntime,
        step_executor: WorkflowStepExecutor,
        *,
        approval_service: ApprovalService | None = None,
        auto_flag: bool = False,
    ) -> None:
        self.workflow_runtime = workflow_runtime
        self.step_executor = step_executor
        self.approval_service = approval_service
        self._auto_flag = auto_flag

    def execute_workflow(
        self,
        session_id: str,
        workflow_name: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow_plan = self.workflow_runtime.expand_workflow(workflow_name, params=params)
        results: list[dict[str, Any]] = []

        for step_ir in workflow_plan:
            result = self.step_executor.execute_step(session_id, step_ir)
            results.append(result)

        final_result = results[-1]

        if self.approval_service and self._auto_flag:
            self.approval_service.auto_flag_recommendations(session_id, risk_threshold="P0")

        return {
            "session_id": session_id,
            "workflow": workflow_name,
            "steps": results,
            "final_summary": final_result["summary"],
            "claims": final_result.get("claims", []),
            "recommendations": final_result.get("recommendations", []),
        }
