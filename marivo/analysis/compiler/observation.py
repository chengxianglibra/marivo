"""Translate ordinary observation bindings to the pure nullable numeric kernel."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
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
    IntervalCoordinate,
    coordinate_read_keys,
    field_read_key,
)
from marivo.analysis.binding.observe import ContributionBinding
from marivo.analysis.compiler.predicate import lower_condition
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DurationDataType,
)
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.semantic._numeric_duration import recipe_duration
from marivo.semantic._numeric_lowering import (
    NumericCheck,
    NumericValue,
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
    calendar: CalendarCoordinate | IntervalCoordinate | None = None
    time_column: str | None = None
    duration: DurationDataType | None = None
    window_key: int | datetime | None = None
    preserve_sample_presence: bool = False
    calendars: tuple[tuple[str, CalendarCoordinate | IntervalCoordinate], ...] = ()
    sampled: bool = False
    provider: str = "duckdb"


@dataclass(frozen=True, slots=True)
class FormulaInput:
    """One completed reduction slot, including its missing-coordinate value."""

    occurrence: tuple[int, ...]
    stage_id: str
    column: DataColumn
    zero_when_absent: bool
    checks: tuple[tuple[str, str, str], ...] = ()
    keys: tuple[str, ...] = ()
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
    definition: PopulationDependency
    population: PopulationDependency
    keys: tuple[str, ...]
    axis_mapping: tuple[tuple[ComparisonAxis, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class FormulaProgram:
    outputs: tuple[tuple[str, MetricNode], ...]
    declared: tuple[tuple[ExactRef, MetricNode], ...]
    values: tuple[FormulaInput, ...]
    calendar: CalendarCoordinate | IntervalCoordinate | None = None
    populations: tuple[PopulationInput, ...] = ()
    output_population: PopulationDependency | None = None
    calendar_key: str = "key.0"
    counts: tuple[MemberInput, ...] = ()
    axes: tuple[ComparisonAxis, ...] = ()
    complete_calendar: bool = True
    parent_scope: PopulationDependency | None = None
    parent_slots: tuple[str, ...] = ()
    population_checked: bool = False
    calendar_samples: tuple[tuple[tuple[int, ...], CalendarCoordinate], ...] = ()


def calendar_field(slot: str) -> str:
    """Identify a private prepared calendar membership carrier."""
    return f"__calendar_{slot}"


def reduction_input(
    contribution: ContributionBinding,
    slot: str,
    calendar: CalendarCoordinate | IntervalCoordinate | None,
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
        calendars=calendars,
        sampled=bool(contribution.sample_axes),
    )


def bind_reduction_calendars(
    prepared: ReductionInput, table: ir.Table, columns: Mapping[str, str]
) -> tuple[ir.Table, Mapping[str, str]]:
    from marivo.analysis.compiler.calendar import bind_calendar

    bound = dict(columns)
    calendars = prepared.calendars or (
        (("key.0", prepared.calendar),) if prepared.calendar is not None else ()
    )
    for slot, calendar in calendars:
        field = calendar_field(slot)
        if field in bound or (prepared.window_key is not None and calendar == prepared.calendar):
            continue
        assert prepared.time_column is not None
        name = "__bound_calendar_" + slot.replace(".", "_")
        table = bind_calendar(table, table[bound[prepared.time_column]], calendar, name)
        bound[field] = name
    return table, bound


def _reduction_axes(
    prepared: ReductionInput, available: Mapping[str, ir.Value], window_slot: str | None = None
) -> dict[str, ir.Value]:
    """Bind the exact ordered grouping, including captured calendar buckets."""
    result = {slot: available[path] for slot, path in prepared.axes}
    calendars = prepared.calendars or (
        (("key.0", prepared.calendar),) if prepared.calendar is not None else ()
    )
    for slot, calendar in calendars:
        if prepared.window_key is not None and calendar == prepared.calendar:
            assert window_slot is not None
            result[slot] = available[window_slot]
            continue
        result[slot] = available[calendar_field(slot)]
    return dict(sorted(result.items(), key=lambda item: int(item[0].split(".")[1])))


def lower_observation(
    prepared: ReductionInput,
    table: ir.Table,
    columns: Mapping[str, str],
    predicates: tuple[PredicateExpression, ...],
    row_checks: tuple[tuple[str, str, str], ...] = (),
    *,
    frequency: bool = False,
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Emit grouped nullable values and ephemeral check columns in one read."""
    table, columns = bind_reduction_calendars(prepared, table, columns)
    window_slot: str | None = None
    if prepared.window_key is not None:
        window_slot = "__marivo_window_key"
        while window_slot in table.columns:
            window_slot += "_"
        # Group a projected coordinate, preserving empty-input grouping and
        # avoiding SQL dialects interpreting an integer literal as a position.
        table = table.mutate(
            **{
                window_slot: ibis.literal(
                    prepared.window_key,
                    type="timestamp('UTC', 6)"
                    if isinstance(prepared.window_key, datetime)
                    else "int64",
                )
            }
        ).view()
    fields = {path: table[name] for path, name in columns.items()}
    if window_slot is not None:
        fields[window_slot] = table[window_slot]
    selected = [lower_condition(node, fields) for node in predicates]
    predicate_checks = (
        *tuple(check for item in selected for check in item.checks),
        *(NumericCheck(table[name], expected, received) for name, expected, received in row_checks),
    )

    predicate_axes = _reduction_axes(prepared, fields, window_slot)
    predicate_metrics = {
        f"predicate_check.{i}": check.violation.any().fill_null(False)
        for i, check in enumerate(predicate_checks)
    }
    assignments = {
        int(path.removeprefix("__sample_assigned_")): table[name]
        for path, name in columns.items()
        if path.startswith("__sample_assigned_")
    }
    if assignments:
        # Each recursive reducer consumes one physical key prefix. A deeper
        # unmatched sample must not erase the coordinates already received.
        for width in range(len(predicate_axes) + 1):
            scope_predicates = (
                *(item.value for item in selected),
                *(value for position, value in assignments.items() if position < width),
            )
            predicate_metrics[f"__sample_scope_{width}"] = (
                ibis.and_(*scope_predicates).any().fill_null(False)
                if scope_predicates
                else table.count() > 0
            )
    if prepared.preserve_sample_presence:
        predicate_metrics["__sample_present"] = (
            ibis.and_(*(item.value for item in selected)).any().fill_null(False)
            if selected
            else table.count() > 0
        )
    inline_presence = (
        prepared.preserve_sample_presence
        and not selected
        and not assignments
        and not predicate_checks
    )
    predicate_summary = (
        (
            table.group_by(**predicate_axes).aggregate(**predicate_metrics)
            if predicate_axes
            else table.aggregate(**predicate_metrics)
        )
        if predicate_metrics and not inline_presence
        else None
    )
    value_predicates = (*(item.value for item in selected), *assignments.values())
    filtered = table.filter(*value_predicates) if value_predicates else table
    fields = {path: filtered[name] for path, name in columns.items()}
    if window_slot is not None:
        fields[window_slot] = filtered[window_slot]
    axes = _reduction_axes(prepared, fields, window_slot)
    from marivo.analysis.compiler.source_aggregates import admit_source_aggregate

    admit_source_aggregate(prepared, fields)
    if frequency:
        from marivo.analysis.errors import AnalysisStageUnavailableError

        assert isinstance(prepared.node, SourceAggregate)
        dtype = lower_row(prepared.node.operand, fields).value.type()
        distinct = prepared.node.reducer == "count_distinct"
        if not dtype.is_numeric() or (distinct and not (dtype.is_integer() or dtype.is_decimal())):
            raise AnalysisStageUnavailableError(
                stage="attribute_state_binding",
                expected="native numeric quantile values or integer/Decimal distinct membership with exact equality",
                received=f"{prepared.provider}: {prepared.node.reducer}({dtype})",
                repair="Use a qualified numeric state input. String collation and floating special-value equality have no qualified membership carrier; inspect the original observations before changing the business definition. No business row was read.",
                target="analysis_frame.attribute",
            )
    item = lower_source_reduction(prepared.node, filtered, fields)
    metrics = {
        prepared.value_slot: ibis.null().cast(item.value.type()) if frequency else item.value,
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
        if node.reducer == "sum":
            metrics[prepared.value_slot] = ops.Cast(item.value, to=dt.int64).to_expr()
        if node.reducer == "mean":
            metrics[prepared.value_slot] = ops.Cast(ticks.sum().fill_null(0), to=dt.int64).to_expr()
            metrics["__duration_count"] = ticks.count()

    if inline_presence:
        # Its numeric groups already contain precisely the presence domain.
        metrics["__sample_present"] = filtered.count() > 0
        metrics["__numeric_present"] = ibis.literal(True)
    grouped_axes = axes
    if frequency:
        assert isinstance(prepared.node, SourceAggregate)
        grouped_axes = {
            **axes,
            "__attribute_frequency_value": lower_row(prepared.node.operand, fields).value,
        }
        metrics["__attribute_frequency"] = filtered.count()
    result = (
        filtered.group_by(**grouped_axes).aggregate(**metrics)
        if grouped_axes
        else filtered.aggregate(**metrics)
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
    predicates: tuple[PredicateExpression, ...],
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]]:
    """Emit independent reductions and their checks in one native aggregate."""
    table, columns = bind_reduction_calendars(reductions[0], table, columns)
    fields = {path: table[name] for path, name in columns.items()}
    selected = tuple(lower_condition(node, fields) for node in predicates)
    predicate_checks = tuple(check for item in selected for check in item.checks)
    first = reductions[0]
    predicate_axes = _reduction_axes(first, fields)
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
    axes = _reduction_axes(first, fields)
    metrics: dict[str, ir.Value] = {}
    checks: list[tuple[str, NumericCheck]] = []
    from marivo.analysis.compiler.source_aggregates import admit_source_aggregate

    for index, reduction in enumerate(reductions):
        admit_source_aggregate(reduction, fields)
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

        return ibis.literal(
            boundary.astimezone(time_zone(zone)).replace(tzinfo=None), type="timestamp(6)"
        )
    return ibis.literal(boundary, type="timestamp('UTC', 6)")


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
        return recipe_duration(node.recipe, tuple(parts))
    active = [part for part in parts if part is not None]
    if not active:
        return None
    if len(set(active)) != 1:
        numeric_failure("matching fixed Duration units", repr(active))
    return active[0]
