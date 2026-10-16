"""Translate ordinary observation bindings to the pure nullable numeric kernel."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldPredicate,
    MeasureAssertion,
    MetricAdd,
    MetricConstant,
    MetricDivide,
    MetricMultiply,
    MetricNegate,
    MetricNode,
    MetricSubtract,
    RowAdd,
    RowConstant,
    RowDivide,
    RowField,
    RowMultiply,
    RowNegate,
    RowNode,
    RowSubtract,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.types import CalendarCoordinate, ExactRef, FieldRead, field_read_key
from marivo.analysis.binding.observe import ContributionBinding
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.semantic.metric_graph_lowering import (
    NumericCheck,
    NumericValue,
    lower_field_predicate,
    lower_metric_formula,
    lower_row,
    lower_source_reduction,
    numeric_binary,
    numeric_failure,
)


@dataclass(frozen=True, slots=True)
class ReductionInput:
    """Closed physical input to a source reduction; no Session or Signature."""

    node: SourceAggregate | SourceWeightedMean | EntityCount
    axes: tuple[tuple[str, str], ...]
    value_slot: str
    calendar: CalendarCoordinate | None = None
    time_column: str | None = None
    duration: DurationDataType | None = None


@dataclass(frozen=True, slots=True)
class FormulaInput:
    """One completed reduction slot, including its missing-coordinate value."""

    occurrence: tuple[int, ...]
    stage_id: str
    column: DataColumn
    empty_zero: bool


@dataclass(frozen=True, slots=True)
class FormulaProgram:
    outputs: tuple[tuple[str, MetricNode], ...]
    declared: tuple[tuple[ExactRef, MetricNode], ...]
    values: tuple[FormulaInput, ...]
    calendar: CalendarCoordinate | None = None


def ibis_type(dtype: DataType) -> dt.DataType:
    if isinstance(dtype, ScalarDataType):
        return dt.dtype(dtype.name)
    if isinstance(dtype, DecimalDataType):
        return dt.Decimal(dtype.precision, dtype.scale)
    if isinstance(dtype, TimestampDataType):
        return dt.Timestamp(
            timezone=dtype.timezone, scale={"s": 0, "ms": 3, "us": 6, "ns": 9}[dtype.unit]
        )
    return dt.int64


def data_type(dtype: dt.DataType) -> DataType:
    if dtype.is_signed_integer():
        return ScalarDataType("int64")
    if dtype.is_floating():
        return ScalarDataType("float64")
    if isinstance(dtype, dt.Decimal):
        return DecimalDataType(dtype.precision or 38, dtype.scale or 0)
    if isinstance(dtype, dt.Timestamp):
        units: dict[int, Literal["s", "ms", "us", "ns"]] = {0: "s", 3: "ms", 6: "us", 9: "ns"}
        unit = units.get(dtype.scale or 0)
        if unit in ("s", "ms", "us", "ns"):
            return TimestampDataType(unit, dtype.timezone)
    if dtype.is_date():
        return ScalarDataType("date")
    if dtype.is_boolean():
        return ScalarDataType("boolean")
    if dtype.is_string():
        return ScalarDataType("string")
    if isinstance(dtype, dt.Interval) and dtype.unit.short in ("s", "ms", "us", "ns"):
        return DurationDataType(dtype.unit.short)
    numeric_failure("a lossless nullable numeric output type", str(dtype))


def reduction_input(
    contribution: ContributionBinding, slot: str, calendar: CalendarCoordinate | None
) -> ReductionInput:
    node = contribution.definition
    if not isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
        numeric_failure("an ordinary contribution reduction", type(node).__name__)
    axes = tuple(
        (f"key.{i + (calendar is not None)}", field_read_key(read))
        for i, read in enumerate(contribution.coordinates)
    )
    return ReductionInput(
        node,
        axes,
        slot,
        calendar,
        field_read_key(
            FieldRead(
                contribution.time.field,
                contribution.root,
                contribution.time.path,
                contribution.time.at,
            )
        )
        if contribution.time
        else None,
    )


def lower_observation(
    prepared: ReductionInput,
    table: ir.Table,
    columns: Mapping[str, str],
    predicates: tuple[FieldPredicate, ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Emit grouped nullable values and ephemeral check columns in one read."""
    fields = {path: table[name] for path, name in columns.items()}
    selected = [lower_field_predicate(node, fields) for node in predicates]
    # Predicate checks run over the same period-scoped input, before the predicate
    # can hide an invalid arithmetic row. Cross joining one scalar summary does
    # not multiply contribution rows.
    predicate_checks = tuple(check for item in selected for check in item.checks)
    predicate_summary = (
        table.aggregate(
            **{
                f"predicate_check.{i}": check.violation.any().fill_null(False)
                for i, check in enumerate(predicate_checks)
            }
        )
        if predicate_checks
        else None
    )
    filtered = table.filter(*(item.value for item in selected)) if selected else table
    fields = {path: filtered[name] for path, name in columns.items()}
    axes = {slot: fields[path] for slot, path in prepared.axes}
    calendar = prepared.calendar
    if calendar is not None:
        assert prepared.time_column is not None
        time = fields[prepared.time_column]
        bucket: ir.Value = ibis.null().cast("int64")
        for i, (_, _, start, end) in reversed(tuple(enumerate(calendar.cells))):
            bucket = (
                (time >= temporal_bound(time, start, calendar.timezone))
                & (time < temporal_bound(time, end, calendar.timezone))
            ).ifelse(i, bucket)
        axes = {"key.0": bucket, **axes}
    item = lower_source_reduction(prepared.node, filtered, fields)
    metrics = {
        prepared.value_slot: ops.Cast(item.value, to=item.value.type()).to_expr(),
        **{f"numeric_check.{i}": check.violation for i, check in enumerate(item.checks)},
    }
    if prepared.duration is not None and isinstance(prepared.node, SourceWeightedMean):
        ticks = lower_row(prepared.node.value, fields).value
        weight = lower_row(prepared.node.weight, fields).value
        pair = ticks.notnull() & weight.notnull()
        product = numeric_binary("multiply", NumericValue(ticks), NumericValue(weight)).value
        numerator = pair.ifelse(product, ibis.null().cast(product.type())).sum().fill_null(0)
        denominator = pair.ifelse(weight, ibis.null().cast(weight.type())).sum().fill_null(0)
        metrics[prepared.value_slot] = ops.Cast(numerator, to=numerator.type()).to_expr()
        metrics["__duration_weight_sum"] = ops.Cast(denominator, to=denominator.type()).to_expr()
    if prepared.duration is not None and isinstance(prepared.node, SourceAggregate):
        node = prepared.node
        ticks = lower_row(node.operand, fields).value
        if node.reducer == "mean":
            metrics[prepared.value_slot] = ops.Cast(ticks.sum().fill_null(0), to=dt.int64).to_expr()
            metrics["__duration_count"] = ticks.count()
        elif node.reducer in ("median", "quantile"):
            q = 0.5 if node.reducer == "median" else node.q
            assert q is not None
            filtered = filtered.mutate(__duration_ticks=ticks, **axes)
            window = ibis.window(group_by=[filtered[name] for name in axes])
            ticks = filtered["__duration_ticks"]
            filtered = filtered.mutate(
                __duration_rank=ibis.row_number().over(
                    ibis.window(
                        group_by=[filtered[name] for name in axes],
                        order_by=ticks.asc(nulls_first=False),
                    )
                ),
                __duration_n=ticks.count().over(window),
            )
            ticks = filtered["__duration_ticks"]
            position = (filtered["__duration_n"] - 1) * q
            rank = filtered["__duration_rank"]
            metrics[prepared.value_slot] = ticks.max(where=rank == position.floor())
            metrics["__duration_upper"] = ticks.max(where=rank == position.ceil())
            metrics["__duration_count"] = filtered["__duration_n"].max().fill_null(0)
            axes = {name: filtered[name] for name in axes}
    result = (
        filtered.group_by(**axes).aggregate(**metrics) if axes else filtered.aggregate(**metrics)
    )
    checks = [(f"numeric_check.{i}", check) for i, check in enumerate(item.checks)]
    if predicate_summary is not None:
        result = predicate_summary.left_join(
            result.mutate(__numeric_present=ibis.literal(True)), ibis.literal(True)
        )
        checks.extend((f"predicate_check.{i}", check) for i, check in enumerate(predicate_checks))
    return result, tuple(checks)


def formula_columns(program: FormulaProgram) -> tuple[DataColumn, ...]:
    values = {
        item.occurrence: NumericValue(ibis.literal(None).cast(ibis_type(item.column.dtype)))
        for item in program.values
    }
    return tuple(
        DataColumn(
            f"value.{i}",
            name,
            duration_type(
                node,
                {},
                values={v.occurrence: v.column.dtype for v in program.values},
                declared=dict(program.declared),
                occurrence=(i,),
            )
            or data_type(
                lower_metric_formula(node, (i,), values, dict(program.declared)).value.type()
            ),
            True,
        )
        for i, (name, node) in enumerate(program.outputs)
    )


def temporal_bound(value: ir.Value, boundary: datetime, zone: str) -> ir.Value:
    """Compare civil dates in the captured boundary zone, instants by exact instant."""
    if value.type().is_date():
        from marivo.analysis.core.time_authority import time_zone

        return ibis.literal(boundary.astimezone(time_zone(zone)).replace(tzinfo=None))
    return ibis.literal(boundary)


def duration_type(
    node: MetricNode | RowNode,
    fields: Mapping[str, DataType],
    *,
    values: Mapping[tuple[int, ...], DataType] | None = None,
    declared: Mapping[ExactRef, MetricNode] | None = None,
    occurrence: tuple[int, ...] = (),
) -> DurationDataType | None:
    """Preserve fixed tick units through admitted Row and Metric operations."""
    from marivo._definition.graph import children

    if values is not None and occurrence in values:
        dtype = values[occurrence]
        return dtype if isinstance(dtype, DurationDataType) else None
    if isinstance(node, RowField):
        dtype = fields[node.ref.ref.path]
        return dtype if isinstance(dtype, DurationDataType) else None
    if isinstance(node, DeclaredMetricRef):
        assert declared is not None
        return duration_type(
            declared[node.ref],
            fields,
            values=values,
            declared=declared,
            occurrence=(*occurrence, 0),
        )
    if isinstance(node, EntityCount) or (
        isinstance(node, SourceAggregate) and node.reducer in ("count", "count_distinct")
    ):
        return None
    if isinstance(node, SourceWeightedMean):
        return duration_type(node.value, fields)
    parts = [
        duration_type(
            edge.child,
            fields,
            values=values,
            declared=declared,
            occurrence=(*occurrence, edge.index),
        )
        for edge in children(node)
        if isinstance(
            edge.child,
            (
                RowField,
                RowConstant,
                RowNegate,
                RowAdd,
                RowSubtract,
                RowMultiply,
                RowDivide,
                MeasureAssertion,
                SourceAggregate,
                SourceWeightedMean,
                EntityCount,
                MetricAdd,
                MetricSubtract,
                MetricMultiply,
                MetricDivide,
                MetricNegate,
                MetricConstant,
                DeclaredMetricRef,
                ContributionFilter,
            ),
        )
    ]
    active = [part for part in parts if part is not None]
    if not active:
        return None
    if len(set(active)) != 1:
        numeric_failure("matching fixed Duration units", repr(active))
    if isinstance(node, (RowDivide, MetricDivide)) and len(parts) == 2 and parts[1] is not None:
        return None
    if isinstance(node, (RowMultiply, MetricMultiply)) and len(active) == 2:
        numeric_failure("one Duration operand in multiplication", "two Duration operands")
    return active[0]
