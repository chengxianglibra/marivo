"""Fuse qualified consumers sharing one native Query-key relation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.observation import common_slot
from marivo.analysis.compiler.optimization import required_slots
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    QueryPlan,
    RuleDecision,
    SourceReadStage,
    common_program,
    read_components,
    read_finish,
    read_members,
    read_reduction,
    require_reduction,
)
from marivo.analysis.materialization.native_query_checks import (
    NativeQueryRelation,
    _attach_relation_checks,
)
from marivo.analysis.materialization.query_parameters import inline_key_relation
from marivo.analysis.materialization.source_query_policy import (
    qualify_query_source,
)
from marivo.analysis.model.data_types import (
    DataColumn,
    ScalarDataType,
)
from marivo.semantic.spi import NumericCheck

if TYPE_CHECKING:
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.datasource.spi import SourceSession


from marivo.analysis.materialization.native_query_relation import (
    _physical_owner,
    native_query_relation,
)
from marivo.analysis.materialization.query_key_values import _outer_join_value, _same_key
from marivo.analysis.materialization.query_plan_transport import (
    _consumers,
    _logical_reads,
    _prune_transport,
)


def qualified_parameter_consumer(
    prepared: PreparedSources, stage: SourceReadStage, read: LoweredRead, owner: SourceSession
) -> bool:
    if (
        prepared.sources.get(stage.source_id, prepared.source) is not owner
        or read.population_checks
    ):
        # Deferred checks can carry parameters outside the consumer's data relation.
        return False
    placeholders = {parameter.placeholder for parameter in stage.parameters}
    allowed = frozenset(
        node for node in read.expression.op().find(ops.UnboundTable) if node.name in placeholders
    )
    if not _physical_owner(read.expression, owner, prepared, allowed_parameters=allowed):
        return False
    return owner.provider.query_policy.key_fusion == "local" or (
        owner.provider.query_policy.uses_identity_alignment_domain
        and qualify_query_source(owner, stage, read, frozenset()) is None
    )


def fuse_shared_query_keys(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Keep shared keys inside one statement for qualified compatible consumer grids."""
    producers = tuple(
        dict.fromkeys(
            parameter.stage_id
            for read in lowered.reads
            for parameter in lowered.plan.source_read_stage(read.stage_id).parameters
        )
    )
    for producer in producers:
        candidate, pruned = _prune_transport(lowered.plan, producer)
        final = next(stage for stage in candidate.stages if stage.stage_id == candidate.publish)
        if not isinstance(final, LocalCompute) or not isinstance(final.program, FormulaProgram):
            continue
        reads = tuple(
            read
            for read in lowered.reads
            if any(
                parameter.stage_id == producer
                for parameter in lowered.plan.source_read_stage(read.stage_id).parameters
            )
        )
        if len(reads) < 2 or set(candidate.consumers_of(producer)) != {
            read.stage_id for read in reads
        }:
            continue
        owner = _shared_grid_owner(
            prepared, lowered.plan, candidate, producer, final.stage_id, reads
        )
        if owner is None:
            continue
        native = native_query_relation(
            prepared, replace(lowered, plan=candidate), producer, owner, shared=True
        )
        if isinstance(native, str):
            continue
        lowered = _install_shared_grid(
            prepared, lowered, candidate, producer, pruned, final, reads, native, owner
        )
    return lowered


def _shared_grid_owner(
    prepared: PreparedSources,
    plan: QueryPlan,
    candidate: QueryPlan,
    producer: str,
    final_id: str,
    reads: tuple[LoweredRead, ...],
) -> SourceSession | None:
    first = reads[0]
    first_stage = plan.source_read_stage(first.stage_id)
    first_reduction = read_reduction(first_stage)
    if first_reduction is None:
        return None
    owner = prepared.sources.get(first_stage.source_id, prepared.source)
    first_keys = tuple(
        column for column in first_stage.columns if column.slot_id in first_stage.keys
    )
    for read in reads:
        stage = plan.source_read_stage(read.stage_id)
        reduction = read_reduction(stage)
        if (
            not qualified_parameter_consumer(prepared, stage, read, owner)
            or stage.keys != first_stage.keys
            or tuple(column for column in stage.columns if column.slot_id in stage.keys)
            != first_keys
            or reduction is None
            or reduction.axes != first_reduction.axes
            or read_members(stage)
            or read_finish(stage) is not None
            or read_components(stage) is not None
            or reduction.duration is not None
            or reduction.calendar is not None
            or reduction.calendars
            or reduction.window_key is not None
            or reduction.preserve_sample_presence
            or stage.inputs != (producer,)
            or len(stage.parameters) != 1
            or candidate.consumers_of(read.stage_id) != (final_id,)
        ):
            return None
    return owner


def _build_shared_grids(
    prepared: PreparedSources,
    plan: QueryPlan,
    reads: tuple[LoweredRead, ...],
    native: NativeQueryRelation,
    producer_keys: tuple[str, ...],
    owner: SourceSession,
) -> tuple[list[ir.Table], list[SourceReadStage], list[tuple[str, NumericCheck]], list[DataColumn]]:
    expressions: list[ir.Table] = []
    members: list[SourceReadStage] = []
    checks: list[tuple[str, NumericCheck]] = []
    columns: list[DataColumn] = []
    for index, read in enumerate(reads):
        stage = plan.source_read_stage(read.stage_id)
        parameter = stage.parameters[0]
        expression = inline_key_relation(
            read.expression, parameter, native.expression, producer_keys, owner.provider.name
        )
        preparation = stage.preparation_checks
        numeric = read.numeric_checks
        if index == 0:
            expression, preparation, numeric = _attach_relation_checks(
                expression, native, plan, preparation, numeric, prepared=prepared
            )
        if "__numeric_present" not in expression.columns:
            expression = expression.mutate(__numeric_present=ibis.literal(True))
        member = replace(
            stage,
            inputs=(),
            parameters=(),
            preparation_checks=preparation,
            columns=stage.columns
            if any(column.slot_id == "__numeric_present" for column in stage.columns)
            else (
                *stage.columns,
                DataColumn(
                    "__numeric_present", "__numeric_present", ScalarDataType("boolean"), False
                ),
            ),
        )
        members.append(member)
        assert read_reduction(member) is not None
        value_slot = require_reduction(member).value_slot
        names = {
            name: name if name in member.keys or name == value_slot else common_slot(index, name)
            for name in expression.columns
        }
        expressions.append(
            expression.select(
                **{
                    alias: _outer_join_value(expression[name], owner.provider.name)
                    for name, alias in names.items()
                }
            )
        )
        columns.extend(
            replace(column, slot_id=names[column.slot_id])
            for column in member.columns
            if index == 0 or column.slot_id not in member.keys
        )
        checks.extend((names[name], check) for name, check in numeric)
    return expressions, members, checks, columns


def _align_shared_grids(
    expressions: list[ir.Table], keys: tuple[str, ...], engine: str
) -> ir.Table:
    expression = expressions[0]
    for other in expressions[1:]:
        if keys:
            left, right = expression.view(), other.view()
            joined = left.join(
                right,
                [_same_key(left[key], right[key], engine) for key in keys],
                how="outer",
            )
            expression = joined.select(
                **{
                    **{key: ibis.coalesce(left[key], right[key]) for key in keys},
                    **{name: left[name] for name in left.columns if name not in keys},
                    **{name: right[name] for name in right.columns if name not in keys},
                }
            )
        else:
            expression = expression.cross_join(other)
    return expression


def _install_shared_grid(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    candidate: QueryPlan,
    producer: str,
    pruned: tuple[str, ...],
    final: LocalCompute,
    reads: tuple[LoweredRead, ...],
    native: NativeQueryRelation,
    owner: SourceSession,
) -> LoweredQuery:
    producer_stage = next(stage for stage in candidate.stages if stage.stage_id == producer)
    expressions, members, checks, columns = _build_shared_grids(
        prepared, lowered.plan, reads, native, producer_stage.keys, owner
    )
    assert isinstance(final.program, FormulaProgram)
    expression = _align_shared_grids(expressions, members[0].keys, owner.provider.name)
    head = replace(
        members[0],
        columns=tuple(columns),
        preparation_checks=(),
        native_reductions=(*members[0].native_reductions, *native.reductions(lowered.plan)),
        native_reads=_logical_reads(
            (
                *members,
                *(lowered.plan.source_read_stage(read.stage_id) for read in native.sources),
            )
        ),
        program=common_program(members[0], tuple(members)),
    )
    indices = {member.stage_id: index for index, member in enumerate(members)}

    def remap(program: FormulaProgram, indexes: dict[str, int] = indices) -> FormulaProgram:
        return replace(
            program,
            values=tuple(
                replace(
                    binding,
                    presence=common_slot(indexes[binding.stage_id], "__numeric_present"),
                    checks=tuple(
                        (common_slot(indexes[binding.stage_id], name), expected, received)
                        for name, expected, received in binding.checks
                    ),
                )
                if binding.stage_id in indexes
                else binding
                for binding in program.values
            ),
        )

    final = replace(
        final,
        inputs=tuple(head.stage_id if value in indices else value for value in final.inputs),
        program=remap(final.program),
    )
    removed = native.stages | frozenset(pruned) | (frozenset(indices) - {head.stage_id})
    stages = tuple(
        head
        if stage.stage_id == head.stage_id
        else final
        if stage.stage_id == final.stage_id
        else stage
        for stage in candidate.stages
        if stage.stage_id not in removed
    )
    plan = replace(
        candidate,
        stages=stages,
        consumers=_consumers(stages),
        original=remap(candidate.original) if candidate.original is not None else None,
        fusion_groups=tuple(
            group for group in candidate.fusion_groups if group.output not in removed
        ),
        optimization_decisions=(
            *candidate.optimization_decisions,
            RuleDecision(
                "source_shared_key_fusion",
                "adopted",
                (producer, *indices),
                "One same-owner source statement shares the native key relation across qualified compatible scalar or grouped consumers; independent grids, presence and checks are retained.",
            ),
        ),
    )
    plan = replace(plan, required_slots=required_slots(plan))
    return LoweredQuery(
        plan,
        tuple(
            LoweredRead(head.stage_id, expression, tuple(checks))
            if read.stage_id == head.stage_id
            else read
            for read in lowered.reads
            if read.stage_id not in removed
        ),
    )
