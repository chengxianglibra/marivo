"""Lower a governed Query plan over its bound source relations."""

from __future__ import annotations

from dataclasses import dataclass

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import PredicateExpression
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.graph_plan import QueryPlan, SourceRead
from marivo.semantic._numeric_lowering import NumericCheck


@dataclass(frozen=True, slots=True, eq=False)
class LoweredRead:
    """One SourceRead and the exact Ibis table for its source ID."""

    stage: SourceRead
    expression: ir.Table
    numeric_checks: tuple[tuple[str, NumericCheck], ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class LoweredQuery:
    plan: QueryPlan
    reads: tuple[LoweredRead, ...]

    def read(self, stage_id: str) -> LoweredRead:
        for item in self.reads:
            if item.stage.stage_id == stage_id:
                return item
        raise compilation_error(f"a lowered read for {stage_id}", "missing lowered stage")


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
    from marivo.analysis.compiler.predicate import lower_condition

    value = lower_condition(node, {path: table[column] for path, column in columns.items()}).value
    if not isinstance(value, ir.BooleanValue):
        raise compilation_error("a boolean predicate", str(value.type()))
    return value


def lower_query(plan: QueryPlan, relations: dict[str, ir.Table]) -> LoweredQuery:
    """Lower every SourceRead over its bound relation, keeping its exact source ID."""
    lowered: list[LoweredRead] = []
    for stage in plan.stages:
        if not isinstance(stage, SourceRead):
            continue
        table = relations.get(stage.stage_id)
        if table is None:
            table = relations.get(stage.source_id)
        if table is None:
            raise compilation_error(f"a bound relation for {stage.source_id}", "unbound source")
        columns = dict(stage.field_columns)
        conditions: list[ir.BooleanValue] = []
        if stage.period is not None and stage.attribute_layout is None:
            from marivo.analysis.compiler.observation import temporal_bound

            time = table[stage.period.column]
            if stage.period.start is not None:
                conditions.append(
                    time >= temporal_bound(time, stage.period.start, stage.period.timezone)
                )
            conditions.append(time < temporal_bound(time, stage.period.end, stage.period.timezone))
        filtered = table.filter(*conditions) if conditions else table
        if stage.attribute_layout is not None:
            from marivo.analysis.compiler.attribute_inputs import (
                attribute_preparation_key,
                lower_attribute_inputs,
            )

            selected = tuple(_query_predicate(node, filtered, columns) for node in stage.predicates)
            scoped = filtered.filter(*selected) if selected else filtered
            expression, checks, check_scopes = lower_attribute_inputs(
                stage.attribute_layout,
                scoped,
                columns,
                relations[attribute_preparation_key(stage.stage_id)],
                stage.preparation_checks,
            )
            from dataclasses import replace

            from marivo.analysis.core.data_types import DurationDataType
            from marivo.analysis.core.value_types import data_type

            effective = {
                column.slot_id: replace(
                    column,
                    nullable=column.nullable
                    or len({leaf.fiber for leaf in stage.attribute_layout.leaves}) > 1,
                    dtype=column.dtype
                    if isinstance(column.dtype, DurationDataType)
                    and column.slot_id
                    in {
                        leaf.read.reduction.value_slot
                        for leaf in stage.attribute_layout.leaves
                        if leaf.read.reduction is not None
                    }
                    else data_type(expression[column.slot_id].type()),
                )
                for column in stage.columns
            }
            layout = stage.attribute_layout
            layout = replace(
                layout,
                columns=tuple(effective.values()),
                checks=check_scopes,
                leaves=tuple(
                    replace(
                        leaf,
                        roles=tuple(
                            (role, effective[column.slot_id]) for role, column in leaf.roles
                        ),
                        support=effective[leaf.support.slot_id],
                        effective=effective[leaf.effective.slot_id],
                    )
                    for leaf in layout.leaves
                ),
            )
            stage = replace(stage, columns=layout.columns, attribute_layout=layout)
            lowered.append(LoweredRead(stage, expression, checks))
            continue
        if stage.common_reads:
            from marivo.analysis.compiler.original import lower_common_components

            expression, checks = lower_common_components(
                tuple(read.reduction for read in stage.common_reads if read.reduction is not None),
                tuple(read.component_layout for read in stage.common_reads),
                filtered,
                columns,
                stage.predicates,
            )
            lowered.append(LoweredRead(stage, expression, checks))
            continue
        if stage.reduction is not None:
            from marivo.analysis.compiler.observation import lower_observation

            if stage.component_layout is not None:
                from marivo.analysis.compiler.original import lower_components

                expression, checks = lower_components(
                    stage.component_layout, filtered, columns, stage.predicates
                )
            else:
                expression, checks = lower_observation(
                    stage.reduction, filtered, columns, stage.predicates
                )
            expression, checks = _attach_preparation_checks(
                expression, table, checks, stage.preparation_checks
            )
            lowered.append(LoweredRead(stage, expression, checks))
        else:
            conditions = [_query_predicate(p, filtered, columns) for p in stage.predicates]
            filtered = filtered.filter(*conditions) if conditions else filtered
            expression = filtered.select(
                **{slot: filtered[column] for slot, column in stage.projections},
                **(
                    {"__numeric_present": filtered[stage.presence].fill_null(False)}
                    if stage.presence
                    else {}
                ),
            )
            expression, checks = _attach_preparation_checks(
                expression, table, (), stage.preparation_checks
            )
            lowered.append(LoweredRead(stage, expression, checks))
    return LoweredQuery(plan, tuple(lowered))
