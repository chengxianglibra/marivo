"""Lower a governed Query plan over its bound source relations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import PredicateExpression, SourceAggregate, SourceWeightedMean
from marivo.analysis.compiler import attribute_input_lowering
from marivo.analysis.compiler.attribute_frequencies import frequency_finishers
from marivo.analysis.compiler.attribute_inputs import (
    attribute_preparation_key,
)
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead, PopulationCheck
from marivo.analysis.compiler.observation import lower_observation
from marivo.analysis.compiler.original import lower_common_components, lower_components
from marivo.analysis.compiler.predicate import lower_condition
from marivo.analysis.compiler.query_stages import (
    ActualWindowRead,
    AttributeCoalitionRead,
    AttributeFrequencyRead,
    AttributeRead,
    CheckedOutput,
    NativeRead,
    ProjectionRead,
    QueryPlan,
    SampleWindowRead,
    SourceReadStage,
    read_attribute,
    read_components,
    read_members,
    read_presence,
    read_projections,
    read_reduction,
    require_attribute,
    require_components,
    require_reduction,
)
from marivo.analysis.compiler.source_time import temporal_bound
from marivo.analysis.compiler.unique_keys import relation_unique_keys
from marivo.analysis.core.value_types import data_type
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.analysis.model.data_types import DurationDataType
from marivo.analysis.model.unique_keys import UniqueKeys
from marivo.semantic.spi import NumericCheck, lower_row


def _attach_preparation_checks(
    expression: ir.Table,
    table: ir.Table,
    checks: tuple[tuple[str, NumericCheck], ...],
    preparation_checks: tuple[tuple[str, str, str], ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Attach source-wide preparation facts to a lowered source expression."""
    if not preparation_checks:
        return expression, checks
    summary = table.aggregate(
        **{name: table[name].any().fill_null(False) for name, _, _ in preparation_checks}
    )
    if "__numeric_present" not in expression.columns:
        expression = expression.mutate(__numeric_present=ibis.literal(True))
    expression = summary.left_join(expression, ibis.literal(True))
    return expression, (
        *checks,
        *(
            (name, NumericCheck(summary[name], expected, received))
            for name, expected, received in preparation_checks
        ),
    )


def _query_predicate(
    node: PredicateExpression, table: ir.Table, columns: dict[str, str]
) -> ir.BooleanValue:

    value = lower_condition(node, {path: table[column] for path, column in columns.items()}).value
    if not isinstance(value, ir.BooleanValue):
        raise compilation_error("a boolean predicate", str(value.type()))
    return value


def lower_query(
    plan: QueryPlan,
    relations: dict[str, ir.Table],
    population_checks: Mapping[str, tuple[PopulationCheck, ...]] | None = None,
) -> LoweredQuery:
    """Lower every SourceReadStage over its bound relation, keeping its exact source ID."""
    lowered: list[LoweredRead] = []
    effective_stages: dict[str, SourceReadStage] = {}
    for stage in plan.stages:
        if not isinstance(stage, SourceReadStage):
            continue

        if isinstance(
            stage.program,
            (ActualWindowRead, SampleWindowRead, AttributeCoalitionRead, CheckedOutput),
        ):
            continue
        stage, read = _lower_source(stage, relations, population_checks)
        effective_stages[stage.stage_id] = stage
        lowered.append(read)
    # Facts describe the data relation, before diagnostic rows are separated by
    # execution. Raw component and Attribute unions cannot use aggregate facts.
    for read in lowered:
        stage = effective_stages.get(read.stage_id, plan.source_read_stage(read.stage_id))
        source = relations.get(stage.stage_id)
        if source is None:
            source = relations[stage.source_id]
        seeds = {}
        if isinstance(stage.program, ProjectionRead) and stage.unique_keys.proves(stage.keys):
            physical = dict(stage.program.projections)
            seeds[source.op()] = UniqueKeys(
                tuple(source.columns), (tuple(physical[key] for key in stage.keys),)
            )
        facts = relation_unique_keys(read.expression, seeds).project(
            tuple(c.slot_id for c in stage.columns)
        )
        mixed = (
            read_components(stage) is not None
            or read_attribute(stage) is not None
            or any(read_components(member) is not None for member in read_members(stage))
        )
        if not mixed:
            for key in stage.unique_keys.keys:
                facts = facts.add(key)
        effective_stages[stage.stage_id] = replace(stage, unique_keys=facts)
    return LoweredQuery(
        replace(
            plan, stages=tuple(effective_stages.get(stage.stage_id, stage) for stage in plan.stages)
        ),
        tuple(lowered),
    )


def _lower_source(
    stage: SourceReadStage,
    relations: dict[str, ir.Table],
    population_checks: Mapping[str, tuple[PopulationCheck, ...]] | None,
) -> tuple[SourceReadStage, LoweredRead]:
    if isinstance(stage.program, NativeRead):
        raise compilation_error(
            "a native source payload supplied by its physical rewrite",
            "a native program sent through ordinary source lowering",
        )
    table = relations.get(stage.stage_id)
    if table is None:
        table = relations.get(stage.source_id)
    if table is None:
        raise compilation_error(f"a bound relation for {stage.source_id}", "unbound source")
    columns = dict(stage.field_columns)
    filtered = _filter_source_period(stage, table)
    if stage.attribute_state:
        stage = _type_attribute_state(stage, filtered, columns)
    if read_attribute(stage) is not None:
        return _lower_attribute_read(stage, filtered, columns, relations)
    if read_members(stage) and not isinstance(stage.program, AttributeFrequencyRead):
        expression, checks = lower_common_components(
            tuple(
                require_reduction(read)
                for read in read_members(stage)
                if read_reduction(read) is not None
            ),
            tuple(read_components(read) for read in read_members(stage)),
            filtered,
            columns,
            stage.predicates,
        )
        return stage, LoweredRead(stage.stage_id, expression, checks)
    if read_reduction(stage) is not None:
        return _lower_reduction_read(stage, table, filtered, columns)
    return stage, _lower_projection_read(stage, table, filtered, columns, population_checks)


def _filter_source_period(stage: SourceReadStage, table: ir.Table) -> ir.Table:
    conditions: list[ir.BooleanValue] = []
    if (
        stage.period is not None
        and not stage.period.predicate_prepared
        and read_attribute(stage) is None
    ):
        time = table[stage.period.column]
        if stage.period.start is not None:
            conditions.append(
                time >= temporal_bound(time, stage.period.start, stage.period.timezone)
            )
        conditions.append(time < temporal_bound(time, stage.period.end, stage.period.timezone))
    return table.filter(*conditions) if conditions else table


def _type_attribute_state(
    stage: SourceReadStage, filtered: ir.Table, columns: dict[str, str]
) -> SourceReadStage:
    fields = {path: filtered[name] for path, name in columns.items()}
    for member in read_members(stage) or (stage,):
        reduction = read_reduction(member)
        if reduction is None:
            continue
        node = reduction.node
        operands = (
            (node.operand,)
            if isinstance(node, SourceAggregate) and node.reducer not in ("count", "count_distinct")
            else (node.value, node.weight)
            if isinstance(node, SourceWeightedMean)
            else ()
        )
        operand_types = tuple(lower_row(operand, fields).value.type() for operand in operands)
        if any(not dtype.is_numeric() for dtype in operand_types):
            raise AnalysisStageUnavailableError(
                stage="attribute_state_binding",
                expected="numeric source states for a numeric replacement-game expression",
                received=f"{type(node).__name__}: nonnumeric reduction input",
                repair="Inspect the original observations or use a qualified numeric Metric. Counting rows or non-NULL values remains available; no business row was read.",
                target="analysis_frame.attribute",
            )
    input_types = tuple(
        (
            reduction.value_slot,
            tuple(
                data_type(lower_row(operand, fields).value.type())
                for operand in (
                    (reduction.node.operand,)
                    if isinstance(reduction.node, SourceAggregate)
                    else (reduction.node.value, reduction.node.weight)
                    if isinstance(reduction.node, SourceWeightedMean)
                    else ()
                )
            ),
        )
        for member in (read_members(stage) or (stage,))
        if (reduction := read_reduction(member)) is not None
    )
    stage = replace(stage, attribute_input_types=input_types)
    return stage


def _lower_attribute_read(
    stage: SourceReadStage,
    filtered: ir.Table,
    columns: dict[str, str],
    relations: dict[str, ir.Table],
) -> tuple[SourceReadStage, LoweredRead]:
    selected = tuple(_query_predicate(node, filtered, columns) for node in stage.predicates)
    scoped = filtered.filter(*selected) if selected else filtered
    layout = replace(require_attribute(stage), partition="__attribute_assigned" in scoped.columns)
    expression, checks, check_scopes = attribute_input_lowering.lower_attribute_inputs(
        layout,
        scoped,
        columns,
        relations[attribute_preparation_key(stage.stage_id)],
        stage.preparation_checks,
    )

    effective = {
        column.slot_id: replace(
            column,
            nullable=column.nullable
            or len({leaf.fiber for leaf in require_attribute(stage).leaves}) > 1,
            dtype=column.dtype
            if isinstance(column.dtype, DurationDataType)
            and column.slot_id
            in {
                require_reduction(leaf.read).value_slot
                for leaf in require_attribute(stage).leaves
                if read_reduction(leaf.read) is not None
            }
            else data_type(expression[column.slot_id].type()),
        )
        for column in stage.columns
    }
    layout = replace(
        layout,
        columns=tuple(effective.values()),
        checks=check_scopes,
        leaves=tuple(
            replace(
                leaf,
                roles=tuple((role, effective[column.slot_id]) for role, column in leaf.roles),
                support=effective[leaf.support.slot_id],
                effective=effective[leaf.effective.slot_id],
            )
            for leaf in layout.leaves
        ),
    )
    stage = replace(
        stage,
        columns=layout.columns,
        program=AttributeRead(layout, require_reduction(stage), read_members(stage)),
    )
    return stage, LoweredRead(stage.stage_id, expression, checks)


def _lower_reduction_read(
    stage: SourceReadStage, table: ir.Table, filtered: ir.Table, columns: dict[str, str]
) -> tuple[SourceReadStage, LoweredRead]:
    if read_components(stage) is not None:
        expression, checks = lower_components(
            require_components(stage), filtered, columns, stage.predicates
        )
    else:
        expression, checks = lower_observation(
            require_reduction(stage),
            filtered,
            columns,
            stage.predicates,
            frequency=isinstance(stage.program, AttributeFrequencyRead),
        )
    expression, checks = _attach_preparation_checks(
        expression, table, checks, stage.preparation_checks
    )
    if isinstance(stage.program, AttributeFrequencyRead):
        stage = replace(
            stage,
            program=replace(
                stage.program,
                input_type=data_type(expression["__attribute_frequency_value"].type()),
                finishers=frequency_finishers(
                    stage, {path: filtered[name] for path, name in columns.items()}
                ),
            ),
        )
    return stage, LoweredRead(stage.stage_id, expression, checks)


def _lower_projection_read(
    stage: SourceReadStage,
    table: ir.Table,
    filtered: ir.Table,
    columns: dict[str, str],
    population_checks: Mapping[str, tuple[PopulationCheck, ...]] | None,
) -> LoweredRead:
    conditions = [_query_predicate(p, filtered, columns) for p in stage.predicates]
    filtered = filtered.filter(*conditions) if conditions else filtered
    expression = filtered.select(
        **{slot: filtered[column] for slot, column in read_projections(stage)},
        **(
            {"__numeric_present": filtered[read_presence(stage)].fill_null(False)}
            if read_presence(stage)
            else {}
        ),
    )
    expression, checks = _attach_preparation_checks(expression, table, (), stage.preparation_checks)
    return LoweredRead(
        stage.stage_id, expression, checks, (population_checks or {}).get(stage.source_id, ())
    )
