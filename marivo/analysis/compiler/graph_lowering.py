"""Lower a governed Query plan over its bound source relations."""

from __future__ import annotations

from dataclasses import dataclass, replace

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import PredicateExpression
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.graph_plan import (
    AttributeRead,
    NativeRead,
    QueryPlan,
    SourceRead,
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
from marivo.semantic._numeric_lowering import NumericCheck


@dataclass(frozen=True, slots=True, eq=False)
class LoweredRead:
    """An Ibis payload associated with the authoritative plan stage by ID."""

    stage_id: str
    expression: ir.Table
    numeric_checks: tuple[tuple[str, NumericCheck], ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class LoweredQuery:
    plan: QueryPlan
    reads: tuple[LoweredRead, ...]

    def read(self, stage_id: str) -> LoweredRead:
        for item in self.reads:
            if item.stage_id == stage_id:
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
    effective_stages: dict[str, SourceRead] = {}
    for stage in plan.stages:
        if not isinstance(stage, SourceRead):
            continue
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
        conditions: list[ir.BooleanValue] = []
        if stage.period is not None and read_attribute(stage) is None:
            from marivo.analysis.compiler.observation import temporal_bound

            time = table[stage.period.column]
            if stage.period.start is not None:
                conditions.append(
                    time >= temporal_bound(time, stage.period.start, stage.period.timezone)
                )
            conditions.append(time < temporal_bound(time, stage.period.end, stage.period.timezone))
        filtered = table.filter(*conditions) if conditions else table
        if read_attribute(stage) is not None:
            from marivo.analysis.compiler.attribute_inputs import (
                attribute_preparation_key,
                lower_attribute_inputs,
            )

            selected = tuple(_query_predicate(node, filtered, columns) for node in stage.predicates)
            scoped = filtered.filter(*selected) if selected else filtered
            expression, checks, check_scopes = lower_attribute_inputs(
                require_attribute(stage),
                scoped,
                columns,
                relations[attribute_preparation_key(stage.stage_id)],
                stage.preparation_checks,
            )
            from marivo.analysis.core.data_types import DurationDataType
            from marivo.analysis.core.value_types import data_type

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
            layout = require_attribute(stage)
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
            stage = replace(
                stage,
                columns=layout.columns,
                program=AttributeRead(layout, require_reduction(stage), read_members(stage)),
            )
            effective_stages[stage.stage_id] = stage
            lowered.append(LoweredRead(stage.stage_id, expression, checks))
            continue
        if read_members(stage):
            from marivo.analysis.compiler.original import lower_common_components

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
            lowered.append(LoweredRead(stage.stage_id, expression, checks))
            continue
        if read_reduction(stage) is not None:
            from marivo.analysis.compiler.observation import lower_observation

            if read_components(stage) is not None:
                from marivo.analysis.compiler.original import lower_components

                expression, checks = lower_components(
                    require_components(stage), filtered, columns, stage.predicates
                )
            else:
                expression, checks = lower_observation(
                    require_reduction(stage), filtered, columns, stage.predicates
                )
            expression, checks = _attach_preparation_checks(
                expression, table, checks, stage.preparation_checks
            )
            lowered.append(LoweredRead(stage.stage_id, expression, checks))
        else:
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
            finite_checks = tuple(
                (
                    f"__field_nonfinite_{index}",
                    NumericCheck(
                        value.notnull() & (value.isnan() | value.isinf()),
                        "finite numeric values or NULL",
                        f"nonfinite source field {name}",
                    ),
                )
                for index, name in enumerate(expression.columns)
                if isinstance((value := expression[name]), ir.FloatingValue)
            )
            if finite_checks:
                expression = expression.mutate(
                    **{name: check.violation for name, check in finite_checks}
                )
            expression, checks = _attach_preparation_checks(
                expression, table, finite_checks, stage.preparation_checks
            )
            lowered.append(LoweredRead(stage.stage_id, expression, checks))
    return LoweredQuery(
        replace(
            plan, stages=tuple(effective_stages.get(stage.stage_id, stage) for stage in plan.stages)
        ),
        tuple(lowered),
    )
