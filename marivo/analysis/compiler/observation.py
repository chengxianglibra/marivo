"""Translate ordinary observation bindings to the pure nullable numeric kernel."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.common.deferred import Resolver

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
    PerWeightedMean,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.relations import PopulationDependency
from marivo._definition.types import (
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    coordinate_read_keys,
    field_read_key,
)
from marivo.analysis.binding.observe import ContributionBinding
from marivo.analysis.binding.sampling import SampleCoverage
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.semantic._numeric_lowering import (
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
    window_key: int | None = None
    preserve_sample_presence: bool = False
    calendars: tuple[tuple[str, CalendarCoordinate], ...] = ()
    sampled: bool = False
    retain_quantile_bounds: bool = False
    native_quantile_value: bool = False


@dataclass(frozen=True, slots=True)
class FormulaInput:
    """One completed reduction slot, including its missing-coordinate value."""

    occurrence: tuple[int, ...]
    stage_id: str
    column: DataColumn
    empty_zero: bool
    checks: tuple[tuple[str, str, str], ...] = ()
    keys: tuple[str, ...] = ()
    coverage: tuple[SampleCoverage, ...] = ()
    key_groups: tuple[tuple[str, ...], ...] = ()
    presence: str | None = None


@dataclass(frozen=True, slots=True)
class PopulationInput:
    """One independently selected source-backed internal sample domain."""

    definition: PopulationDependency
    stage_id: str
    keys: tuple[str, ...]
    key_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class MemberInput:
    population: PopulationDependency
    keys: tuple[str, ...]
    output_mapping: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class FormulaProgram:
    outputs: tuple[tuple[str, MetricNode], ...]
    declared: tuple[tuple[ExactRef, MetricNode], ...]
    values: tuple[FormulaInput, ...]
    calendar: CalendarCoordinate | None = None
    populations: tuple[PopulationInput, ...] = ()
    output_population: PopulationDependency | None = None
    calendar_key: str = "key.0"
    members: MemberInput | None = None


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
    numeric_failure("a supported nullable output type", str(dtype))


def reduction_input(
    contribution: ContributionBinding, slot: str, calendar: CalendarCoordinate | None
) -> ReductionInput:
    node = contribution.definition
    if not isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
        numeric_failure("an ordinary contribution reduction", type(node).__name__)
    calendars = contribution.calendars or ((("key.0", calendar),) if calendar is not None else ())
    calendar_slots = {slot for slot, _ in calendars}
    axes_list: list[tuple[str, str]] = []
    offset = 0
    for read in contribution.coordinates:
        for path in coordinate_read_keys(read):
            while f"key.{offset}" in calendar_slots:
                offset += 1
            axes_list.append((f"key.{offset}", path))
            offset += 1
    axes = tuple(axes_list)
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
        window_key=contribution.window_key,
        preserve_sample_presence=bool(contribution.coverage),
        calendars=calendars,
        sampled=bool(contribution.sample_axes),
    )


def lower_observation(
    prepared: ReductionInput,
    table: ir.Table,
    columns: Mapping[str, str],
    predicates: tuple[FieldPredicate, ...],
    row_checks: tuple[tuple[str, str, str], ...] = (),
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Emit grouped nullable values and ephemeral check columns in one read."""
    window_slot: str | None = None
    if prepared.window_key is not None:
        window_slot = "__marivo_window_key"
        while window_slot in table.columns:
            window_slot += "_"
        # Group a projected coordinate, preserving empty-input grouping and
        # avoiding SQL dialects interpreting an integer literal as a position.
        table = table.mutate(**{window_slot: ibis.literal(prepared.window_key)}).view()
    fields = {path: table[name] for path, name in columns.items()}
    if window_slot is not None:
        fields[window_slot] = table[window_slot]
    selected = [lower_field_predicate(node, fields) for node in predicates]
    predicate_checks = (
        *tuple(check for item in selected for check in item.checks),
        *(NumericCheck(table[name], expected, received) for name, expected, received in row_checks),
    )

    def output_axes(available: Mapping[str, ir.Value]) -> dict[str, ir.Value]:
        result = {slot: available[path] for slot, path in prepared.axes}
        calendars = prepared.calendars or (
            (("key.0", prepared.calendar),) if prepared.calendar is not None else ()
        )
        for slot, calendar in calendars:
            if prepared.window_key is not None and calendar == prepared.calendar:
                assert window_slot is not None
                result[slot] = available[window_slot]
                continue
            assert prepared.time_column is not None
            time = available[prepared.time_column]
            bucket: ir.Value = ibis.null().cast("int64")
            for i, (_, _, start, end) in reversed(tuple(enumerate(calendar.cells))):
                bucket = (
                    (time >= temporal_bound(time, start, calendar.timezone))
                    & (time < temporal_bound(time, end, calendar.timezone))
                ).ifelse(i, bucket)
            result[slot] = bucket
        return dict(sorted(result.items(), key=lambda item: int(item[0].split(".")[1])))

    predicate_axes = output_axes(fields)
    predicate_metrics = {
        f"predicate_check.{i}": check.violation.any().fill_null(False)
        for i, check in enumerate(predicate_checks)
    }
    if prepared.preserve_sample_presence:
        predicate_metrics["__sample_present"] = table.count() > 0
    predicate_summary = (
        (
            table.group_by(**predicate_axes).aggregate(**predicate_metrics)
            if predicate_axes
            else table.aggregate(**predicate_metrics)
        )
        if predicate_metrics
        else None
    )
    filtered = table.filter(*(item.value for item in selected)) if selected else table
    fields = {path: filtered[name] for path, name in columns.items()}
    if window_slot is not None:
        fields[window_slot] = filtered[window_slot]
    axes = output_axes(fields)
    item = lower_source_reduction(prepared.node, filtered, fields)
    metrics = {
        prepared.value_slot: item.value,
        **{f"numeric_check.{i}": check.violation for i, check in enumerate(item.checks)},
    }
    if prepared.duration is not None and isinstance(prepared.node, SourceWeightedMean):
        ticks = lower_row(prepared.node.value, fields).value
        weight = lower_row(prepared.node.weight, fields).value
        pair = ticks.notnull() & weight.notnull()
        product = numeric_binary("multiply", NumericValue(ticks), NumericValue(weight)).value
        numerator = pair.ifelse(product, ibis.null().cast(product.type())).sum().fill_null(0)
        denominator = pair.ifelse(weight, ibis.null().cast(weight.type())).sum().fill_null(0)
        metrics[prepared.value_slot] = numerator
        metrics["__duration_weight_sum"] = denominator
    if prepared.duration is not None and isinstance(prepared.node, SourceAggregate):
        node = prepared.node
        ticks = lower_row(node.operand, fields).value
        if node.reducer == "mean":
            metrics[prepared.value_slot] = ops.Cast(ticks.sum().fill_null(0), to=dt.int64).to_expr()
            metrics["__duration_count"] = ticks.count()
    if isinstance(prepared.node, SourceAggregate) and prepared.node.reducer in (
        "median",
        "quantile",
    ):
        node = prepared.node
        value = lower_row(node.operand, fields).value
        if (
            prepared.duration is not None
            or (value.type().is_decimal() and not prepared.native_quantile_value)
            or prepared.retain_quantile_bounds
        ):
            q = 0.5 if node.reducer == "median" else node.q
            assert q is not None
            probability = Fraction(str(q))
            prefix = "__duration" if prepared.duration is not None else "__quantile"
            filtered = filtered.mutate(__quantile_value=value, **axes)
            window = ibis.window(group_by=[filtered[name] for name in axes])
            value = filtered["__quantile_value"]
            filtered = filtered.mutate(
                __quantile_rank=ibis.row_number().over(
                    ibis.window(
                        group_by=[filtered[name] for name in axes],
                        order_by=value.asc(nulls_first=False),
                    )
                ),
                __quantile_n=value.count().over(window),
            )
            value = filtered["__quantile_value"]
            rank = filtered["__quantile_rank"]
            if probability.denominator >= 10**38:
                # Every positive such probability has h < 1 for int64 counts.
                lower = rank == 0
                upper = rank >= (filtered["__quantile_n"] > 1).cast("int64")
            else:
                # Clamp multiplication to a proven int64-count upper bound for
                # h. This keeps even tiny decimal probabilities within Decimal38
                # without rounding an integer rank or using floating division.
                bound = -(-((2**63 - 2) * probability.numerator) // probability.denominator)
                scaled = rank.clip(upper=bound).cast("decimal(38,0)") * ibis.literal(
                    str(probability.denominator)
                ).cast("decimal(38,0)")
                position = (filtered["__quantile_n"] - 1).cast(
                    "decimal(38,0)"
                ) * probability.numerator
                lower = (rank <= bound) & (scaled <= position)
                upper = (rank > bound) | (scaled >= position)
            metrics[prepared.value_slot] = value.max(where=lower)
            metrics[f"{prefix}_upper"] = value.min(where=upper)
            metrics[f"{prefix}_count"] = filtered["__quantile_n"].max().fill_null(0)
            axes = {name: filtered[name] for name in axes}
    result = (
        filtered.group_by(**axes).aggregate(**metrics) if axes else filtered.aggregate(**metrics)
    )
    checks = [(f"numeric_check.{i}", check) for i, check in enumerate(item.checks)]
    if predicate_summary is not None:
        result = predicate_summary.left_join(
            result.mutate(__numeric_present=ibis.literal(True)),
            [predicate_summary[name].identical_to(result[name]) for name in axes]
            if axes
            else ibis.literal(True),
        )
        result = result.select(
            *predicate_summary.columns,
            *(
                name
                for name in result.columns
                if name not in predicate_summary.columns
                and name not in {f"{key}_right" for key in axes}
            ),
        )
        checks.extend((f"predicate_check.{i}", check) for i, check in enumerate(predicate_checks))
    return result, tuple(checks)


def common_slot(index: int, slot: str) -> str:
    """Namespace one logical occurrence's ephemeral checks or raw projections."""
    return f"{slot}.block{index}"


def lower_common_observations(
    reductions: tuple[ReductionInput, ...],
    table: ir.Table,
    columns: Mapping[str, str],
    predicates: tuple[FieldPredicate, ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Emit admitted independent exact reductions in one native aggregate."""
    fields = {path: table[name] for path, name in columns.items()}
    selected = tuple(lower_field_predicate(node, fields) for node in predicates)
    predicate_checks = tuple(check for item in selected for check in item.checks)
    first = reductions[0]
    predicate_axes = {slot: fields[path] for slot, path in first.axes}
    predicate_metrics = {
        common_slot(index, f"predicate_check.{position}"): check.violation.any().fill_null(False)
        for index in range(len(reductions))
        for position, check in enumerate(predicate_checks)
    }
    summary = (
        (
            table.group_by(**predicate_axes).aggregate(**predicate_metrics)
            if predicate_axes
            else table.aggregate(**predicate_metrics)
        )
        if predicate_metrics
        else None
    )
    filtered = table.filter(*(item.value for item in selected)) if selected else table
    fields = {path: filtered[name] for path, name in columns.items()}
    axes = {slot: fields[path] for slot, path in first.axes}
    metrics: dict[str, ir.Value] = {}
    checks: list[tuple[str, NumericCheck]] = []
    for index, reduction in enumerate(reductions):
        item = lower_source_reduction(reduction.node, filtered, fields)
        metrics[reduction.value_slot] = ops.Cast(item.value, to=item.value.type()).to_expr()
        for position, check in enumerate(item.checks):
            name = common_slot(index, f"numeric_check.{position}")
            metrics[name] = check.violation
            checks.append((name, check))
        checks.extend(
            (common_slot(index, f"predicate_check.{position}"), check)
            for position, check in enumerate(predicate_checks)
        )
    result = (
        filtered.group_by(**axes).aggregate(**metrics) if axes else filtered.aggregate(**metrics)
    )
    if summary is not None:
        # Every occurrence has the same predicate domain, but retains its own
        # check columns for demand-aware formula completion.
        result = summary.left_join(
            result.mutate(__numeric_present=ibis.literal(True)),
            [summary[name].identical_to(result[name]) for name in axes]
            if axes
            else ibis.literal(True),
        )
        result = result.select(
            *summary.columns,
            *(
                name
                for name in result.columns
                if name not in summary.columns and name not in {f"{key}_right" for key in axes}
            ),
        )
        result = result.mutate(
            **{
                common_slot(index, "__numeric_present"): result["__numeric_present"]
                for index in range(len(reductions))
            }
        ).drop("__numeric_present")
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
        dtype = fields[field_read_key(node.read) if node.read is not None else node.ref.ref.path]
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
    if isinstance(node, (EntityCount, MemberCount)) or (
        isinstance(node, (SourceAggregate, PerReduce))
        and node.reducer in ("count", "count_distinct", "approx_nunique")
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
        if edge.role in ("operand", "value", "weight")
        and isinstance(
            edge.child,
            (
                RowExpression,
                MetricExpression,
                MetricWindow,
                PerReduce,
                PerWeightedMean,
                RowField,
                MeasureAssertion,
                SourceAggregate,
                SourceWeightedMean,
                EntityCount,
                MemberCount,
                DeclaredMetricRef,
                ContributionFilter,
            ),
        )
    ]
    if isinstance(node, (RowExpression, MetricExpression)):
        return _recipe_duration(node.recipe, tuple(parts))
    active = [part for part in parts if part is not None]
    if not active:
        return None
    if len(set(active)) != 1:
        numeric_failure("matching fixed Duration units", repr(active))
    return active[0]


def _recipe_duration(
    recipe: Resolver, inputs: tuple[DurationDataType | None, ...]
) -> DurationDataType | None:
    """Propagate fixed tick units through the admitted native recipe structure."""
    import operator

    from ibis.common.deferred import (
        Attr,
        BinaryOperator,
        Call,
        Just,
        Sequence,
        UnaryOperator,
        Variable,
    )

    def combine(parts: tuple[DurationDataType | None, ...]) -> DurationDataType | None:
        present = tuple(part for part in parts if part is not None)
        if len(set(present)) > 1:
            numeric_failure("matching fixed Duration units", repr(present))
        return present[0] if present else None

    def visit(item: Resolver) -> DurationDataType | None:
        if type(item) is Variable:
            return inputs[int(item.name.removeprefix("slot_"))]
        if type(item) is Just:
            return None
        if type(item) is Sequence:
            return combine(tuple(visit(child) for child in item.values))
        if type(item) is UnaryOperator:
            return None if item.func is operator.invert else visit(item.arg)
        if type(item) is BinaryOperator:
            left, right = visit(item.left), visit(item.right)
            if item.func in (
                operator.eq,
                operator.ne,
                operator.lt,
                operator.le,
                operator.gt,
                operator.ge,
                operator.and_,
                operator.or_,
                operator.xor,
            ):
                return None
            return arithmetic(item.func is operator.truediv, item.func is operator.mul, left, right)
        if type(item) is Call:
            if type(item.func) is Attr:
                name = attribute_name(item.func)
                parts = (visit(item.func.obj), *(visit(arg) for arg in item.args))
            else:
                name = item.func.value.__name__
                parts = tuple(visit(arg) for arg in item.args)
            if name in ("isnull", "notnull"):
                return None
            if name == "divide":
                return arithmetic(True, False, parts[0], parts[1])
            if name in ("sqrt", "log", "ln", "exp") and any(part is not None for part in parts):
                numeric_failure("a dimensionless input to nonlinear numeric functions", "Duration")
            return combine((*parts, *(visit(value) for value in item.kwargs.values())))
        numeric_failure("an admitted Duration recipe", type(item).__name__)

    def arithmetic(
        divide: bool, multiply: bool, left: DurationDataType | None, right: DurationDataType | None
    ) -> DurationDataType | None:
        common = combine((left, right))
        if divide and right is not None:
            if left is None:
                numeric_failure("a numeric or fixed Duration quotient", "inverse Duration")
            return None
        if multiply and left is not None and right is not None:
            numeric_failure("one Duration operand in multiplication", "two Duration operands")
        return common

    return visit(recipe)
