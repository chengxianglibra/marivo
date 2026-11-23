"""L1 over adjacent selections in the composed public Frame schedule."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.query import FilterValues, ValueBinary
from marivo.analysis.compiler.frame_plan import CompiledFramePlan, frame_program
from marivo.analysis.compiler.graph_plan import FrameCompute, FrameFilterFusion, QueryStage
from marivo.analysis.compiler.optimization import FusionGroup, RuleDecision, _consumers
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.frame_contracts import CheckedStageInputT
from marivo.analysis.model.local_laws import (
    query_selection_fusion_issue,
    selection_predicate_columns,
)


def fuse_frame_filters(
    compiled: CompiledFramePlan[CheckedStageInputT],
    *,
    protected_inputs: frozenset[int] = frozenset(),
) -> CompiledFramePlan[CheckedStageInputT]:
    """Remove one intermediate selection per disjoint qualified pair."""
    plan = compiled.lowered.plan
    protected = {
        plan.publish,
        *(identifier for identifier, _ in compiled.source_outputs),
        *(identifier for identifier, _ in compiled.checked_inputs),
        *(
            identifier
            for identifier, expression in compiled.frame_operations
            if id(expression) in protected_inputs
        ),
    }
    consumers = dict(plan.consumers)
    retained: list[QueryStage] = []
    removed: set[str] = set()
    groups: list[FusionGroup] = []
    decisions: list[RuleDecision] = []
    for outer in plan.stages:
        inner = retained[-1] if retained else None
        if (
            isinstance(inner, FrameCompute)
            and isinstance(outer, FrameCompute)
            and isinstance(inner.program, FrameProgram)
            and isinstance(outer.program, FrameProgram)
            and isinstance(first := inner.program.operation, FilterValues)
            and isinstance(second := outer.program.operation, FilterValues)
            and len(inner.inputs) == 1
            and outer.inputs == (inner.stage_id,)
            and inner.filter_fusion is None
            and outer.filter_fusion is None
        ):
            members = (inner.stage_id, outer.stage_id)
            input_schema = inner.program.input_schema
            midpoint = inner.program.output_schema
            output_schema = outer.program.output_schema
            issue = query_selection_fusion_issue(
                first,
                second,
                input_schema.values,
                input_schema.columns,
                inner.program.key_groups,
                single_consumer=consumers[inner.stage_id] == (outer.stage_id,),
                same_layout=(
                    midpoint == outer.program.input_schema
                    and input_schema.axes == midpoint.axes == output_schema.axes
                    and input_schema.columns == midpoint.columns == output_schema.columns
                    and input_schema.values == midpoint.values == output_schema.values
                ),
                original_boundary=plan.original is not None,
            )
            if inner.stage_id in protected:
                issue = ("an unreferenced intermediate selection", "output or key checkpoint")
            if issue is None:
                operation = FilterValues(ValueBinary("and", first.predicate, second.predicate))
                combined = frame_program(input_schema, operation, output_schema)
                if combined.output_schema == output_schema:
                    second_slots = selection_predicate_columns(
                        second.predicate,
                        input_schema.values,
                        input_schema.columns,
                        inner.program.key_groups,
                    )
                    assert second_slots is not None
                    retained.pop()
                    removed.add(inner.stage_id)
                    groups.append(FusionGroup("L1", members, outer.stage_id))
                    decisions.append(
                        RuleDecision(
                            "L1",
                            "adopted",
                            members,
                            "One selection over the same complete typed input, with the original steps retained for carrier changes.",
                        )
                    )
                    retained.append(
                        replace(
                            outer,
                            inputs=inner.inputs,
                            program=combined,
                            filter_fusion=FrameFilterFusion(
                                tuple(sorted(second_slots)), (inner, outer)
                            ),
                        )
                    )
                    continue
                issue = ("identical final Frame facts", "different schema after one selection")
            decisions.append(RuleDecision("L1", "refused", members, ": ".join(issue)))
        retained.append(outer)
    if not decisions:
        return compiled
    stages = tuple(retained)
    return replace(
        compiled,
        lowered=replace(
            compiled.lowered,
            plan=replace(
                plan,
                stages=stages,
                consumers=_consumers(stages),
                fusion_groups=(*plan.fusion_groups, *groups),
                optimization_decisions=(*plan.optimization_decisions, *decisions),
            ),
        ),
        frame_operations=tuple(
            item for item in compiled.frame_operations if item[0] not in removed
        ),
    )


def frame_filter_steps(stage: FrameCompute, actual: DataSchema | None) -> tuple[FrameCompute, ...]:
    """Recheck the proof against the normalized input, before evaluating any mask."""
    fusion = stage.filter_fusion
    if fusion is None:
        return (stage,)
    program = stage.program
    assert isinstance(program, FrameProgram) and isinstance(program.operation, FilterValues)
    return (stage,) if actual == program.input_schema else fusion.fallback
