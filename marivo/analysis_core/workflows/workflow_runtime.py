from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from marivo.analysis_core.composites import CompositeWorkflowSpec
from marivo.analysis_core.ir import AnalysisStepIR, step_ir_from_mapping
from marivo.analysis_core.workflows.catalog import WORKFLOW_SPECS


class CompositeWorkflowRuntime:
    """Expand data-driven workflow specs into executable step IR."""

    def __init__(
        self,
        workflow_specs: Mapping[str, CompositeWorkflowSpec] | None = None,
    ) -> None:
        self._workflow_specs = dict(workflow_specs or WORKFLOW_SPECS)

    def get_spec(self, workflow_name: str) -> CompositeWorkflowSpec:
        normalized = workflow_name.strip().lower().replace("-", "_")
        if normalized not in self._workflow_specs:
            raise KeyError(f"Unknown composite workflow: {workflow_name}")
        return self._workflow_specs[normalized]

    def expand_workflow(
        self,
        workflow_name: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[AnalysisStepIR]:
        spec = self.get_spec(workflow_name)
        context = dict(params or {})
        steps: list[AnalysisStepIR] = []
        for index, template in enumerate(spec.steps):
            rendered_params = _render_mapping(template.params, context)
            step = step_ir_from_mapping(
                index,
                {
                    "step_type": template.step_type,
                    "params": rendered_params,
                    "dependencies": list(template.dependencies),
                },
            )
            if template.execution_hints:
                step.execution_hints = {
                    **step.execution_hints,
                    **_render_mapping(template.execution_hints, context),
                }
            if template.evidence_hints:
                step.evidence_hints = {
                    **step.evidence_hints,
                    **_render_mapping(template.evidence_hints, context),
                }
            steps.append(step)
        return steps

    def materialize_runtime_step(
        self,
        step: AnalysisStepIR | Mapping[str, Any],
        *,
        index: int,
    ) -> AnalysisStepIR:
        if isinstance(step, AnalysisStepIR):
            step.index = index
            return step
        return step_ir_from_mapping(index, step)

    def materialize_runtime_steps(
        self,
        steps: Sequence[AnalysisStepIR | Mapping[str, Any]],
        *,
        start_index: int,
    ) -> list[AnalysisStepIR]:
        return [
            self.materialize_runtime_step(step, index=start_index + offset)
            for offset, step in enumerate(steps)
        ]

    @staticmethod
    def next_step_index(steps: Sequence[AnalysisStepIR]) -> int:
        if not steps:
            return 0
        return max(step.index for step in steps) + 1


def _render_mapping(values: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _render_value(value, context) for key, value in values.items()}


def _render_value(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        if (
            value.startswith("{")
            and value.endswith("}")
            and value.count("{") == 1
            and value.count("}") == 1
        ):
            key = value[1:-1]
            if key in context:
                return context[key]
        try:
            return value.format_map(context)
        except KeyError:
            return value
    if isinstance(value, list):
        return [_render_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_value(item, context) for key, item in value.items()}
    return value
