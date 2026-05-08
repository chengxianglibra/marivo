"""Runtime-based WorkflowStepExecutor implementation.

Implements the WorkflowStepExecutor protocol from app.execution.orchestrator
using the semantic_ops.run_* step registry built with MarivoRuntime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.analysis_core.ir import AnalysisStepIR
from app.analysis_core.step_registry import StepRunnerRegistry
from app.analysis_core.step_runners import build_service_step_registry

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime


class RuntimeWorkflowStepExecutor:
    """Implements WorkflowStepExecutor using MarivoRuntime and semantic_ops run_* functions.

    Replaces the legacy _ServiceWorkflowStepExecutor that depended on
    SemanticLayerService. This executor builds a step registry from the
    runtime and delegates step execution to the registered runners.
    """

    def __init__(self, runtime: MarivoRuntime) -> None:
        self._runtime = runtime
        self._registry: StepRunnerRegistry = build_service_step_registry(runtime)

    def execute_step(self, session_id: str, step_ir: AnalysisStepIR) -> dict[str, Any]:
        try:
            runner = self._registry.get(step_ir.step_type)
        except KeyError:
            raise ValueError(
                f"No step runner registered for step_type={step_ir.step_type!r}"
            ) from None
        return runner(session_id, step_ir.params if step_ir.params else None)
