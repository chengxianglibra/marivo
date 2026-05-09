from __future__ import annotations

from typing import Any, Protocol

from marivo.core.semantic.ir import AnalysisStepIR
from marivo.runtime.workflows.workflow_runtime import CompositeWorkflowRuntime


class WorkflowStepExecutor(Protocol):
    def execute_step(self, session_id: str, step_ir: AnalysisStepIR) -> dict[str, Any]: ...


class WorkflowOrchestrator:
    """Execute composite workflows step by step."""

    def __init__(
        self,
        workflow_runtime: CompositeWorkflowRuntime,
        step_executor: WorkflowStepExecutor,
    ) -> None:
        self.workflow_runtime = workflow_runtime
        self.step_executor = step_executor

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

        return {
            "session_id": session_id,
            "workflow": workflow_name,
            "steps": results,
            "final_summary": final_result["summary"],
            "claims": final_result.get("claims", []),
            "recommendations": final_result.get("recommendations", []),
        }
