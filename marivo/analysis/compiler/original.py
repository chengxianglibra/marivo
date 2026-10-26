"""Schema-only source component admission and ordinary source lowering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import (
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MetricNode,
    PerReduce,
    PredicateExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.types import (
    ExactRef,
)
from marivo.analysis.compiler.observation import (
    ReductionInput,
    _reduction_axes,
    calendar_field,
    common_slot,
    lower_common_observations,
    lower_observation,
    temporal_bound,
)
from marivo.analysis.compiler.predicate import lower_condition
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.core.value_types import data_type
from marivo.semantic._numeric_lowering import (
    NumericCheck,
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


def lower_components(
    layout: ComponentLayout,
    table: ir.Table,
    field_columns: Mapping[str, str],
    predicates: tuple[PredicateExpression, ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Complete the fine grid and transport its raw inputs in one source read.

    A single statement retains the owning source reducer and its checks. The
    joined raw rows are used only by admitted later original Metric consumers.
    """
    reduction = layout.reduction
    completed, checks = lower_observation(reduction, table, field_columns, predicates)
    fields = {path: table[name] for path, name in field_columns.items()}
    selected = tuple(lower_condition(node, fields).value for node in predicates)
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
        captured = fields.get(calendar_field(slot))
        if captured is not None:
            axes[slot] = captured
            continue
        assert reduction.time_column is not None
        time = fields[reduction.time_column]
        bucket: ir.Value = ibis.null().cast("int64")
        from marivo._definition.types import CalendarCoordinate

        cells = (
            calendar.cells
            if isinstance(calendar, CalendarCoordinate)
            else ((calendar.start, calendar.end, calendar.start, calendar.end),)
        )
        for index, (_, _, start, end) in reversed(tuple(enumerate(cells))):
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
    predicates: tuple[PredicateExpression, ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """One checked aggregate and raw projection for a compiled source block."""
    completed, checks = lower_common_observations(reductions, table, field_columns, predicates)
    if not any(layout is not None for layout in layouts):
        return completed, checks
    fields = {path: table[name] for path, name in field_columns.items()}
    selected = tuple(lower_condition(node, fields).value for node in predicates)
    filtered = table.filter(*selected) if selected else table
    fields = {path: filtered[name] for path, name in field_columns.items()}
    axes = _reduction_axes(reductions[0], fields)
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
