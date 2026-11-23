"""L5 composition of adjacent row-local Frame projections."""

from __future__ import annotations

from dataclasses import replace

from marivo._definition.value_ir import (
    SelectValues,
    ValueBinary,
    ValueExpression,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.compiler.compiled_frame import CompiledFramePlan
from marivo.analysis.compiler.optimization import _consumers
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    FrameSelectFusion,
    FusionGroup,
    QueryStage,
    RuleDecision,
    SelectFusionStep,
)
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.frame_contracts import CheckedStageInputT
from marivo.analysis.model.local_laws import select_fusion_issue


def _select(stage: QueryStage) -> FrameProgram | None:
    if (
        isinstance(stage, FrameCompute)
        and isinstance(stage.program, FrameProgram)
        and isinstance(stage.program.operation, SelectValues)
        and stage.select_fusion is None
        and len(stage.inputs) == 1
    ):
        return stage.program
    return None


def _same_coordinate_contract(before: DataSchema, after: DataSchema) -> bool:
    return (
        before.axes == after.axes
        and before.presence == after.presence
        and before.presence_paths == after.presence_paths
        and before.projection_columns == after.projection_columns
        and before.comparisons == after.comparisons
    )


def _input_slots(expression: ValueExpression) -> frozenset[int]:
    if isinstance(expression, ValueSlot):
        return frozenset((expression.index,))
    if isinstance(expression, ValueUnary):
        return _input_slots(expression.operand)
    if isinstance(expression, ValueBinary):
        return _input_slots(expression.left) | _input_slots(expression.right)
    return frozenset()


def _is_exact_alias(program: FrameProgram, index: int, expression: ValueExpression) -> bool:
    if not isinstance(expression, ValueSlot):
        return False
    source, target = (
        program.input_schema.values[expression.index],
        program.output_schema.values[index],
    )
    return source.dtype == target.dtype and (target.nullable or not source.nullable)


def _select_fusion(stages: tuple[FrameCompute, ...]) -> FrameSelectFusion:
    final = _select(stages[-1])
    assert final is not None and isinstance(final.operation, SelectValues)
    needed: frozenset[int] = frozenset().union(
        *(_input_slots(expression) for _, expression in final.operation.values)
    )
    steps: list[SelectFusionStep] = []
    for stage in reversed(stages[:-1]):
        program = _select(stage)
        assert program is not None and isinstance(program.operation, SelectValues)
        values = tuple(enumerate(expression for _, expression in program.operation.values))
        live = tuple(item for item in values if item[0] in needed)
        checks = tuple(
            (index, expression)
            for index, expression in values
            if index not in needed and not _is_exact_alias(program, index, expression)
        )
        steps.append(SelectFusionStep(program, live, checks))
        needed = frozenset().union(*(_input_slots(expr) for _, expr in (*live, *checks)))
    return FrameSelectFusion(tuple(reversed(steps)), stages)


def _admission(
    first: FrameProgram,
    second: FrameProgram,
    *,
    single_consumer: bool,
    original_boundary: bool,
    protected_intermediate: bool,
) -> tuple[str, str] | None:
    assert isinstance(first.operation, SelectValues) and isinstance(second.operation, SelectValues)
    return select_fusion_issue(
        first.operation,
        second.operation,
        single_consumer=single_consumer,
        same_key_domain=(
            first.output_schema == second.input_schema
            and _same_coordinate_contract(first.input_schema, first.output_schema)
            and _same_coordinate_contract(second.input_schema, second.output_schema)
        ),
        original_boundary=original_boundary,
        protected_intermediate=protected_intermediate,
        # Original programs, ordered evaluation and exact typed bindings keep
        # finishing, normalization and demand at their original boundaries.
        value_policy_preserved=True,
        checks_preserved=True,
        demand_preserved=True,
    )


def fuse_frame_selects(
    compiled: CompiledFramePlan[CheckedStageInputT],
    *,
    protected_inputs: frozenset[int],
) -> CompiledFramePlan[CheckedStageInputT]:
    """Compose adjacent projections without materializing intermediate Frames."""
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
    chain: list[FrameCompute] = []
    removed: set[str] = set()
    groups: list[FusionGroup] = []
    decisions: list[RuleDecision] = []

    def finish_chain() -> None:
        if len(chain) < 2:
            retained.extend(chain)
        else:
            stages = tuple(chain)
            members = tuple(stage.stage_id for stage in stages)
            outer = stages[-1]
            retained.append(
                replace(outer, inputs=stages[0].inputs, select_fusion=_select_fusion(stages))
            )
            removed.update(members[:-1])
            groups.append(FusionGroup("L5", members, outer.stage_id))
            decisions.append(
                RuleDecision(
                    "L5_select_fusion",
                    "adopted",
                    members,
                    "Ordered typed bindings retain required projection checks.",
                )
            )
        chain.clear()

    for stage in plan.stages:
        program = _select(stage)
        if chain and program is not None and stage.inputs == (chain[-1].stage_id,):
            inner = chain[-1]
            first = _select(inner)
            assert first is not None and isinstance(stage, FrameCompute)
            issue = _admission(
                first,
                program,
                single_consumer=consumers[inner.stage_id] == (stage.stage_id,),
                original_boundary=plan.original is not None,
                protected_intermediate=inner.stage_id in protected,
            )
            if issue is None:
                chain.append(stage)
                continue
            decisions.append(
                RuleDecision(
                    "L5_select_fusion",
                    "refused",
                    (inner.stage_id, stage.stage_id),
                    ": ".join(issue),
                )
            )
        finish_chain()
        if program is None:
            retained.append(stage)
        else:
            assert isinstance(stage, FrameCompute)
            chain.append(stage)
    finish_chain()
    if not decisions:
        return compiled
    rewritten = tuple(retained)
    return replace(
        compiled,
        lowered=replace(
            compiled.lowered,
            plan=replace(
                plan,
                stages=rewritten,
                consumers=_consumers(rewritten),
                fusion_groups=(*plan.fusion_groups, *groups),
                optimization_decisions=(*plan.optimization_decisions, *decisions),
            ),
        ),
        frame_operations=tuple(
            item for item in compiled.frame_operations if item[0] not in removed
        ),
    )


def frame_select_steps(stage: FrameCompute, actual: DataSchema | None) -> tuple[FrameCompute, ...]:
    """Replay ordinary projections if their compiled operand carrier has changed."""
    fusion = stage.select_fusion
    if fusion is None or actual == fusion.steps[0].program.input_schema:
        return (stage,)
    return fusion.fallback
