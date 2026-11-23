"""L1 over adjacent selections in the composed public Frame schedule."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.query import FilterValues
from marivo.analysis.compiler.compiled_frame import CompiledFramePlan
from marivo.analysis.compiler.optimization import _consumers
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.compiler.query_stages import (
    FilterFusionStep,
    FrameCompute,
    FrameFilterFusion,
    FusionGroup,
    QueryStage,
    RuleDecision,
)
from marivo.analysis.core.frame_operations import FixFrameValues
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.frame_contracts import CheckedStageInputT
from marivo.analysis.model.local_laws import (
    selection_predicate_columns,
    short_circuit_selection_issue,
)


def _selection_step(program: FrameProgram) -> FilterFusionStep | None:
    operation = program.operation
    schema = program.input_schema
    if isinstance(operation, FixFrameValues):
        columns = program.key_groups[operation.position]
    elif isinstance(operation, FilterValues):
        consumed = selection_predicate_columns(
            operation.predicate,
            schema.values,
            schema.columns,
            program.key_groups,
            presence=tuple(zip(schema.presence_paths, schema.presence, strict=True)),
        )
        if consumed is None:
            return None
        columns = tuple(sorted(consumed))
    else:
        return None
    return FilterFusionStep(program, columns)


def fuse_frame_filters(
    compiled: CompiledFramePlan[CheckedStageInputT],
    *,
    protected_inputs: frozenset[int] = frozenset(),
) -> CompiledFramePlan[CheckedStageInputT]:
    """Retain maximal ordered selection chains on unshared typed inputs."""
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
    decisions: list[RuleDecision] = []
    pending: dict[str, FusionGroup] = {}
    for outer in plan.stages:
        inner = retained[-1] if retained else None
        if not (
            isinstance(inner, FrameCompute)
            and isinstance(outer, FrameCompute)
            and isinstance(inner.program, FrameProgram)
            and isinstance(outer.program, FrameProgram)
            and isinstance(inner.program.operation, (FilterValues, FixFrameValues))
            and isinstance(outer.program.operation, (FilterValues, FixFrameValues))
            and len(inner.inputs) == 1
            and outer.inputs == (inner.stage_id,)
            and outer.filter_fusion is None
        ):
            retained.append(outer)
            continue
        fusion = inner.filter_fusion
        first = _selection_step(inner.program) if fusion is None else fusion.steps[0]
        last = _selection_step(outer.program)
        original = (inner,) if fusion is None else fusion.fallback
        members = (*(stage.stage_id for stage in original), outer.stage_id)
        issue = short_circuit_selection_issue(
            single_consumer=consumers[inner.stage_id] == (outer.stage_id,),
            same_layout=inner.program.output_schema == outer.program.input_schema,
            original_boundary=plan.original is not None,
        )
        if inner.stage_id in protected:
            issue = ("an unreferenced intermediate selection", "output or key checkpoint")
        if first is None or last is None:
            issue = ("qualified row-local selection masks", "unqualified predicate")
        if issue is not None:
            decisions.append(RuleDecision("L1", "refused", members, ": ".join(issue)))
            retained.append(outer)
            continue
        assert first is not None and last is not None
        steps = (first,) if fusion is None else fusion.steps
        retained[-1] = replace(
            outer,
            inputs=inner.inputs,
            program=replace(outer.program, input_schema=first.program.input_schema),
            filter_fusion=FrameFilterFusion((*steps, last), (*original, outer)),
        )
        removed.add(inner.stage_id)
        pending.pop(inner.stage_id, None)
        pending[outer.stage_id] = FusionGroup("L1", members, outer.stage_id)
    groups = list(pending.values())
    decisions.extend(
        RuleDecision(
            "L1",
            "adopted",
            group.members,
            "Ordered masks run on surviving rows and retain the original typed replay.",
        )
        for group in groups
    )
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
    assert isinstance(program, FrameProgram)
    return (stage,) if actual == program.input_schema else fusion.fallback
