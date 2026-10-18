"""Complete independent source leaves for plain Calendar regrouping."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import ibis

from marivo._definition.expressions import SourceAggregate
from marivo._definition.query import RollupValues
from marivo._definition.types import CalendarCoordinate
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead
from marivo.analysis.compiler.graph_plan import LocalCompute, QueryStage, SourceRead
from marivo.analysis.compiler.observation import FormulaInput, FormulaProgram
from marivo.analysis.compiler.optimization import RuleDecision, required_slots
from marivo.analysis.core.data_types import DataColumn, ScalarDataType

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_source_execution import PreparedSources


def _compact_leaf(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    read: SourceRead,
    binding: FormulaInput,
    program: FormulaProgram,
    *,
    parent: LocalCompute,
    final: LocalCompute,
    calendar: CalendarCoordinate | None,
    target: bool,
) -> tuple[LoweredRead, FormulaInput] | str:
    from marivo.analysis.materialization.source_original import _NativeOriginal, _Relation

    # A fresh lowering context keeps every source check on its actual owner.
    native = _NativeOriginal(prepared, lowered)
    fiber = native.prepare_fiber((read,), sampled=True)
    if isinstance(fiber, str):
        return fiber
    raw = fiber.raw
    assert read.reduction is not None
    reduction = read.reduction
    if target:
        owner = prepared.sources.get(read.source_id, prepared.source)
        relation = _Relation(raw, raw, fiber.axes, program, (read,), owner, (fiber,))
        axes = (("__calendar_time", reduction.time_column),) if reduction.time_column else ()
        raw = native.deduplicate(relation, axes, fiber)
        reduction = replace(
            reduction,
            calendar=calendar,
            calendars=((final.keys[0], calendar),) if calendar is not None else (),
        )
    updated = replace(read, reduction=reduction)
    completed = native.complete_leaf(updated, raw, reduction.axes, retain_checks=True)
    if isinstance(completed, str):
        return completed
    expression, _, checks = completed
    keys = final.keys if target else binding.keys
    columns = (
        *(
            column
            for column in (final.columns if target else read.columns)
            if column.slot_id in keys
        ),
        binding.column,
        *(DataColumn(name, name, ScalarDataType("boolean"), False) for name, _ in checks),
    )
    expression = expression.mutate(__numeric_present=ibis.literal(True))
    for summary in native.summaries:
        expression = summary.left_join(expression, ibis.literal(True)).select(
            *summary.columns, *expression.columns
        )
    stage_id = read.stage_id + (":original-target" if target else ":original-calendar")
    stage = SourceRead(
        stage_id,
        read.source_id,
        read.occurrence,
        columns,
        keys,
        (),
        (),
        read.predicates,
        read.period,
        inputs=(parent.stage_id,) if target else (),
        binding_id=read.binding_id,
        native_reductions=(reduction,),
        native_reads=(read,),
    )
    return LoweredRead(stage, expression, (*native.checks, *checks)), replace(
        binding,
        stage_id=stage_id,
        keys=keys,
        key_groups=(keys,) if target and keys else () if target else binding.key_groups,
        checks=tuple((name, check.expected, check.received) for name, check in checks),
    )


def fuse_calendar_original(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery | None:
    """Regroup plain Calendar totals at each owner and keep the full host formula."""
    plan = lowered.plan
    stages = {stage.stage_id: stage for stage in plan.stages}
    final = stages[plan.publish]
    if (
        plan.checks
        or not isinstance(final, LocalCompute)
        or final.result is None
        or final.fused_steps
        or not isinstance(final.result.operation, RollupValues)
        or len(final.inputs) != 1
    ):
        return None
    parent = stages[final.inputs[0]]
    if not isinstance(parent, LocalCompute) or parent.program is None or parent.fused_steps:
        return None
    program = parent.program
    if (
        program.calendar is None
        or parent.keys != (program.calendar_key,)
        or not program.values
        or program.populations
        or program.output_population is not None
        or program.members is not None
    ):
        return None
    operation = final.result.operation
    calendar = None
    if final.keys:
        layout = final.result.output_layout
        if (
            operation.positions != (0,)
            or len(operation.calendar_maps) != 1
            or operation.calendar_maps[0][0] != 0
            or final.keys != parent.keys
            or layout is None
            or len(layout.axes) != 1
            or not isinstance(layout.axes[0], CalendarCoordinate)
        ):
            return None
        calendar = layout.axes[0]
    elif operation.positions or operation.calendar_maps:
        return None
    reads = []
    for binding in program.values:
        read = stages.get(binding.stage_id)
        if (
            not isinstance(read, SourceRead)
            or read.component_layout is None
            or read.common_reads
            or read.inputs
            or read.parameters
            or read.reduction is None
            or read.reduction.axes
            or read.reduction.calendar != program.calendar
            or read.reduction.calendars != ((program.calendar_key, program.calendar),)
            or read.reduction.window_key is not None
            or read.reduction.sampled
            or read.reduction.preserve_sample_presence
            or read.reduction.duration is not None
            or binding.coverage
            or not isinstance(read.reduction.node, SourceAggregate)
            or read.reduction.node.reducer != "sum"
        ):
            return None
        reads.append(read)
    if set(stages) != {parent.stage_id, final.stage_id, *(read.stage_id for read in reads)}:
        return None
    requests: list[LoweredRead] = []
    rewritten: list[QueryStage] = []
    for target in (False, True):
        bindings = []
        phase_reads = []
        for binding, read in zip(program.values, reads, strict=True):
            result = _compact_leaf(
                prepared,
                lowered,
                read,
                binding,
                program,
                parent=parent,
                final=final,
                calendar=calendar,
                target=target,
            )
            if isinstance(result, str):
                return None
            request, value = result
            requests.append(request)
            phase_reads.append(request.stage)
            bindings.append(value)
        rewritten.extend(phase_reads)
        rewritten.append(
            LocalCompute(
                final.stage_id if target else parent.stage_id,
                tuple(read.stage_id for read in phase_reads),
                "query.combine@v1",
                final.columns if target else parent.columns,
                final.keys if target else parent.keys,
                program=replace(
                    program,
                    values=tuple(bindings),
                    calendar=calendar if target else program.calendar,
                ),
            )
        )
    result_plan = replace(
        plan,
        stages=tuple(rewritten),
        consumers=tuple(
            (
                stage.stage_id,
                tuple(item.stage_id for item in rewritten if stage.stage_id in item.inputs),
            )
            for stage in rewritten
        ),
        original=None,
        fusion_groups=(),
        optimization_decisions=(
            *plan.optimization_decisions,
            RuleDecision(
                "source_original_calendar",
                "adopted",
                tuple(stages),
                "Each owner returns its own grouped leaves; host formulas check the Calendar parent before target leaves are read and combined.",
            ),
        ),
    )
    return LoweredQuery(
        replace(result_plan, required_slots=required_slots(result_plan)), tuple(requests)
    )
