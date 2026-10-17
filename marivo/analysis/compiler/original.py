"""Schema-only admission of invocation-local original Metric inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

import ibis
import ibis.expr.types as ir

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.deferred import attribute_name
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldPredicate,
    MeasureAssertion,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
    SumPermission,
)
from marivo.analysis.compiler.observation import (
    FormulaProgram,
    ReductionInput,
    common_slot,
    data_type,
    lower_common_observations,
    lower_observation,
    temporal_bound,
)
from marivo.analysis.core.data_types import DataColumn
from marivo.semantic.metric_graph_lowering import (
    NumericCheck,
    lower_field_predicate,
    lower_row,
    numeric_failure,
)


@dataclass(frozen=True, slots=True)
class AggregatePayload:
    value: DataColumn


@dataclass(frozen=True, slots=True)
class WeightedPayload:
    value: DataColumn
    weight: DataColumn


@dataclass(frozen=True, slots=True)
class CountPayload:
    """Entity count needs complete contribution identities, without a value."""


ComponentPayload: TypeAlias = AggregatePayload | WeightedPayload | CountPayload


@dataclass(frozen=True, slots=True)
class ComponentLayout:
    """One admitted source occurrence's fine result and sufficient raw inputs.

    Original inputs exist only within this invocation. The source statement
    completes and checks its original fine grid before any regrouping.
    """

    reduction: ReductionInput
    columns: tuple[DataColumn, ...]
    identity: tuple[tuple[DataColumn, str], ...]
    payload: ComponentPayload
    present: str = "__original_present"

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(column.slot_id for column in self.columns if column.slot_id.startswith("key."))

    @property
    def raw_columns(self) -> tuple[DataColumn, ...]:
        payload = (
            (self.payload.value, self.payload.weight)
            if isinstance(self.payload, WeightedPayload)
            else (self.payload.value,)
            if isinstance(self.payload, AggregatePayload)
            else ()
        )
        return (
            *(column for column in self.columns if column.slot_id in self.keys),
            *(column for column, _ in self.identity),
            *payload,
        )


@dataclass(frozen=True, slots=True)
class DrillSchema:
    """One schema-bound new-attribute read over selected original identities."""

    source_id: str
    value_slot: str
    root: ExactRef
    identity: tuple[tuple[DataColumn, str], ...]
    columns: tuple[DataColumn, ...]
    keys: tuple[str, ...]
    projections: tuple[tuple[str, str], ...]
    preparation_checks: tuple[tuple[str, str, str], ...]
    parameter: str


def contribution_root(
    node: SourceAggregate | SourceWeightedMean | EntityCount,
) -> ExactRef:
    """Resolve the physical contribution root without a Catalog or source read."""
    if isinstance(node, EntityCount):
        return node.entity

    def row_root(row: RowNode) -> ExactRef:
        if isinstance(row, RowField):
            return row.root
        if isinstance(row, MeasureAssertion):
            return row_root(row.operand)
        return row_root(row.inputs[0])

    return row_root(node.value if isinstance(node, SourceWeightedMean) else node.operand)


def component_layout(
    reduction: ReductionInput,
    columns: tuple[DataColumn, ...],
    identity: tuple[tuple[DataColumn, str], ...],
    table: ir.Table,
    field_columns: Mapping[str, str],
) -> ComponentLayout:
    """Admit exact full-input recomputation independently of source aggregation."""
    node = reduction.node
    if not identity:
        numeric_failure("complete contribution identity for original recomputation", "no identity")
    if isinstance(node, SourceAggregate) and node.reducer.startswith("approx_"):
        numeric_failure(
            "a qualified original-input approximate reduction route",
            f"{node.reducer}: no qualified pandas original-input kernel",
        )
    fields = {path: table[name] for path, name in field_columns.items()}
    payload: ComponentPayload
    if isinstance(node, EntityCount):
        payload = CountPayload()
    else:
        value = lower_row(
            node.value if isinstance(node, SourceWeightedMean) else node.operand, fields
        )
        column = DataColumn(
            "__original_value",
            "original value",
            reduction.duration or data_type(value.value.type()),
            True,
        )
        if isinstance(node, SourceWeightedMean):
            weight = lower_row(node.weight, fields)
            payload = WeightedPayload(
                column,
                DataColumn(
                    "__original_weight", "original weight", data_type(weight.value.type()), True
                ),
            )
        else:
            payload = AggregatePayload(column)
    return ComponentLayout(reduction, columns, identity, payload)


def require_original_formula(node: MetricNode, declared: Mapping[ExactRef, MetricNode]) -> None:
    """Reject semantic boundaries without an admitted full-input route."""
    from marivo._definition.graph import children

    if isinstance(node, DeclaredMetricRef):
        require_original_formula(declared[node.ref], declared)
        return
    if isinstance(node, (SourceAggregate, PerReduce)) and node.reducer.startswith("approx_"):
        numeric_failure(
            "a qualified approximate original Metric route",
            f"{node.reducer}: full input does not establish approximation equivalence",
        )
    for edge in children(node):
        if isinstance(edge.child, MetricNode):
            require_original_formula(edge.child, declared)


def _permission(node: RowNode) -> SumPermission | None:
    if isinstance(node, RowField):
        return node.permission
    if isinstance(node, MeasureAssertion):
        return node.permission if node.permission is not None else _permission(node.operand)
    from marivo.semantic.expressions import _intersect

    permissions = tuple(_permission(item) for item in node.inputs)
    if not permissions or any(item is None for item in permissions):
        return None
    permission = permissions[0]
    assert permission is not None
    for other in permissions[1:]:
        assert other is not None
        permission = _intersect(permission, other)
    return permission


def _allows(
    permission: SumPermission | None,
    axes: tuple[ComparisonAxis, ...] | None,
    node: SourceAggregate,
) -> bool:
    if isinstance(permission, SumAll):
        return True
    if permission is None or isinstance(permission, SumNone) or axes is None:
        return False
    references: list[ExactRef] = []
    for axis in axes:
        if isinstance(axis, AxisBinding):
            references.append(axis.field if axis.field is not None else axis.entity)
        elif isinstance(axis, CalendarCoordinate) and node.time is not None:
            references.append(node.time.field)
        else:
            return False
    if isinstance(permission, SumAxes):
        return all(
            any(axis.ref == allowed.ref for allowed in permission.axes) for axis in references
        )
    assert isinstance(permission, SumAllExcept)
    return all(not any(axis.ref == fixed.ref for fixed in permission.axes) for axis in references)


def require_rollup(
    program: FormulaProgram,
    source_axes: tuple[ComparisonAxis, ...],
    target_axes: tuple[ComparisonAxis, ...],
) -> None:
    """Check source sum permissions on the original axes actually merged."""
    from marivo._definition.graph import children

    changed = tuple(axis for axis in source_axes if axis not in target_axes)
    declared = dict(program.declared)

    def visit(node: MetricNode) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref])
            return
        if (
            isinstance(node, SourceAggregate)
            and node.reducer == "sum"
            and not _allows(_permission(node.operand), changed, node)
        ):
            numeric_failure(
                "sum authorization for every merged original coordinate axis",
                f"source sum permission {_permission(node.operand)!r}; merged axes={changed!r}",
            )
        for edge in children(node):
            if isinstance(edge.child, MetricNode):
                visit(edge.child)

    for _, node in program.outputs:
        require_original_formula(node, declared)
        visit(node)


def share_additive(
    node: MetricNode,
    declared: Mapping[ExactRef, MetricNode],
    merged_axes: tuple[ComparisonAxis, ...] | None = None,
) -> bool:
    """Prove contribution additivity; rollup eligibility alone grants no share."""
    import operator

    from ibis.common.deferred import Attr, BinaryOperator, Call, Just, UnaryOperator, Variable

    if isinstance(node, DeclaredMetricRef):
        return share_additive(declared[node.ref], declared, merged_axes)
    if isinstance(node, (ContributionFilter, MetricWindow)):
        return share_additive(node.operand, declared, merged_axes)
    if isinstance(node, (EntityCount, MemberCount)):
        return True
    if isinstance(node, SourceAggregate):
        return node.reducer in ("sum", "count") and (
            node.reducer == "count" or _allows(_permission(node.operand), merged_axes, node)
        )
    if not isinstance(node, MetricExpression):
        return False

    def homogeneous(recipe: object) -> bool:
        if isinstance(recipe, Just):
            return isinstance(recipe.value, (int, float)) and bool(recipe.value == 0)
        if isinstance(recipe, Variable):
            return share_additive(
                node.operands[int(recipe.name.removeprefix("slot_"))], declared, merged_axes
            )
        if isinstance(recipe, UnaryOperator):
            return recipe.func is operator.neg and homogeneous(recipe.arg)
        if isinstance(recipe, BinaryOperator):
            if recipe.func in (operator.add, operator.sub):
                return homogeneous(recipe.left) and homogeneous(recipe.right)
            if recipe.func is operator.mul:
                return (isinstance(recipe.left, Just) and homogeneous(recipe.right)) or (
                    homogeneous(recipe.left) and isinstance(recipe.right, Just)
                )
            if recipe.func is operator.truediv:
                return homogeneous(recipe.left) and isinstance(recipe.right, Just)
        if isinstance(recipe, Call) and isinstance(recipe.func, Attr):
            name = attribute_name(recipe.func)
            receiver = recipe.func.obj
            if name == "name":
                return homogeneous(receiver)
            if len(recipe.args) == 1:
                argument = recipe.args[0]
                if name in ("add", "subtract"):
                    return homogeneous(receiver) and homogeneous(argument)
                if name == "multiply":
                    return (isinstance(receiver, Just) and homogeneous(argument)) or (
                        homogeneous(receiver) and isinstance(argument, Just)
                    )
                if name == "divide":
                    return homogeneous(receiver) and isinstance(argument, Just)
        return False

    return homogeneous(node.recipe)


def lower_components(
    layout: ComponentLayout,
    table: ir.Table,
    field_columns: Mapping[str, str],
    predicates: tuple[FieldPredicate, ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Complete the fine grid and transport its raw inputs in one source read.

    A single statement retains the owning source reducer and its checks. The
    joined raw rows are used only by admitted later original Metric consumers.
    """
    reduction = layout.reduction
    completed, checks = lower_observation(reduction, table, field_columns, predicates)
    fields = {path: table[name] for path, name in field_columns.items()}
    selected = tuple(lower_field_predicate(node, fields).value for node in predicates)
    filtered = table.filter(*selected) if selected else table
    fields = {path: filtered[name] for path, name in field_columns.items()}
    axes: dict[str, ir.Value] = {slot: fields[path] for slot, path in reduction.axes}
    calendars = reduction.calendars or (
        (("key.0", reduction.calendar),) if reduction.calendar is not None else ()
    )

    for slot, calendar in calendars:
        if reduction.window_key is not None and calendar == reduction.calendar:
            axes[slot] = ibis.literal(reduction.window_key)
            continue
        assert reduction.time_column is not None
        time = fields[reduction.time_column]
        bucket: ir.Value = ibis.null().cast("int64")
        for index, (_, _, start, end) in reversed(tuple(enumerate(calendar.cells))):
            bucket = (
                (time >= temporal_bound(time, start, calendar.timezone))
                & (time < temporal_bound(time, end, calendar.timezone))
            ).ifelse(index, bucket)
        axes[slot] = bucket
    projected = {
        **{f"__original_{slot}": expression for slot, expression in axes.items()},
        **{column.slot_id: filtered[name] for column, name in layout.identity},
        layout.present: ibis.literal(True),
    }
    node = reduction.node
    if not isinstance(node, EntityCount):
        assert isinstance(layout.payload, (AggregatePayload, WeightedPayload))
        projected[layout.payload.value.slot_id] = lower_row(
            node.value if isinstance(node, SourceWeightedMean) else node.operand, fields
        ).value
        if isinstance(node, SourceWeightedMean):
            assert isinstance(layout.payload, WeightedPayload)
            projected[layout.payload.weight.slot_id] = lower_row(node.weight, fields).value
    raw = filtered.select(**projected)
    joined = completed.left_join(
        raw,
        [completed[key].identical_to(raw[f"__original_{key}"]) for key in layout.keys]
        if layout.keys
        else ibis.literal(True),
    )
    return (
        joined.select(
            *completed.columns,
            *(column.slot_id for column, _ in layout.identity),
            *(
                (layout.payload.value.slot_id, layout.payload.weight.slot_id)
                if isinstance(layout.payload, WeightedPayload)
                else (layout.payload.value.slot_id,)
                if isinstance(layout.payload, AggregatePayload)
                else ()
            ),
            layout.present,
        ),
        checks,
    )


def lower_common_components(
    reductions: tuple[ReductionInput, ...],
    layouts: tuple[ComponentLayout | None, ...],
    table: ir.Table,
    field_columns: Mapping[str, str],
    predicates: tuple[FieldPredicate, ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """One checked aggregate and raw projection for a compiled source block."""
    completed, checks = lower_common_observations(reductions, table, field_columns, predicates)
    if not any(layout is not None for layout in layouts):
        return completed, checks
    fields = {path: table[name] for path, name in field_columns.items()}
    selected = tuple(lower_field_predicate(node, fields).value for node in predicates)
    filtered = table.filter(*selected) if selected else table
    fields = {path: filtered[name] for path, name in field_columns.items()}
    axes = {slot: fields[path] for slot, path in reductions[0].axes}
    projections: dict[str, ir.Value] = {f"__original_{slot}": value for slot, value in axes.items()}
    for index, layout in enumerate(layouts):
        if layout is None:
            continue
        projections.update(
            {common_slot(index, column.slot_id): filtered[name] for column, name in layout.identity}
        )
        projections[common_slot(index, layout.present)] = ibis.literal(True)
        node = layout.reduction.node
        if isinstance(node, SourceAggregate):
            assert isinstance(layout.payload, AggregatePayload)
            projections[common_slot(index, layout.payload.value.slot_id)] = lower_row(
                node.operand, fields
            ).value
    raw = filtered.select(**projections)
    joined = completed.left_join(
        raw,
        [completed[key].identical_to(raw[f"__original_{key}"]) for key in axes]
        if axes
        else ibis.literal(True),
    )
    return joined.select(
        *completed.columns,
        *(name for name in projections if not name.startswith("__original_key.")),
    ), checks
