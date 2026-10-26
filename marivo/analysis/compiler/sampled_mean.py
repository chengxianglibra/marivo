"""Qualified transport of a completed mean over native time sample means."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    MetricNode,
    PerReduce,
    SourceAggregate,
)
from marivo._definition.observations import SampleCoverage
from marivo._definition.types import ExactRef

if TYPE_CHECKING:
    from marivo.analysis.compiler.observation import ReductionInput


def _null_like(value: ir.Value) -> ir.Value:
    """Keep the physical type while making synthesized NULLs explicitly nullable."""
    return ibis.null().cast(value.type().copy(nullable=True))


def mean_occurrences(
    outputs: tuple[tuple[str, MetricNode], ...], declared: Mapping[ExactRef, MetricNode]
) -> dict[tuple[int, ...], tuple[tuple[int, ...], str, str]]:
    """Admit direct mean/min/max of one source mean, never conditional demand."""
    result: dict[tuple[int, ...], tuple[tuple[int, ...], str, str]] = {}
    for index, (_, node) in enumerate(outputs):
        occurrence: tuple[int, ...] = (index,)
        while isinstance(node, (DeclaredMetricRef, ContributionFilter)):
            node = declared[node.ref] if isinstance(node, DeclaredMetricRef) else node.operand
            occurrence = (*occurrence, 0)
        if not isinstance(node, PerReduce) or node.reducer not in ("mean", "min", "max"):
            continue
        if not isinstance(node.samples, tuple) or len(node.samples) != 1:
            continue
        outer, nulls, reducer = occurrence, node.nulls, node.reducer
        node = node.operand
        occurrence = (*occurrence, 0)
        while isinstance(node, (DeclaredMetricRef, ContributionFilter)):
            node = declared[node.ref] if isinstance(node, DeclaredMetricRef) else node.operand
            occurrence = (*occurrence, 0)
        if isinstance(node, SourceAggregate) and node.reducer == "mean" and node.time is not None:
            result[occurrence] = (outer, nulls, reducer)
    return result


def expected_wall_points(points: tuple[datetime, ...], zone: str) -> tuple[datetime, ...]:
    """Use the same private UTC-labelled wall clock as local coverage completion."""
    from marivo.analysis.core.time_authority import time_zone

    return tuple(point.astimezone(time_zone(zone)).replace(tzinfo=timezone.utc) for point in points)


def coverage_selection(
    coverage: tuple[SampleCoverage, ...],
    clocks: Mapping[str, ir.Value],
    axes: Mapping[str, ir.Value],
) -> ir.BooleanValue:
    """Select only raw clocks that can satisfy a captured coverage requirement."""
    return ibis.or_(
        *(
            clocks["__coverage_" + item.key].isin(
                expected_wall_points(item.points, item.wall_timezone)
            )
            & (
                axes[item.output_slot] == item.output_key
                if item.output_key is not None
                else ibis.literal(True)
            )
            for item in coverage
            if item.wall_timezone is not None
        )
    )


def compact_mean(
    prepared: ReductionInput, result: ir.Table, check_names: tuple[str, ...]
) -> ir.Table:
    """Finish native AVG and retain strict checks on demanded completed samples."""
    from marivo.semantic._numeric_lowering import native_mean

    assert prepared.mean_fold is not None
    sample_key, nulls, _ = prepared.mean_fold
    outer = tuple(name for name in result.columns if name.startswith("key.") and name != sample_key)
    value = result[prepared.value_slot]
    metrics: dict[str, ir.Value] = {prepared.value_slot: native_mean(value)}
    for name in result.columns:
        if name in check_names or result[name].type().is_boolean():
            metrics[name] = result[name].any().fill_null(False)
    if nulls == "error":
        present = (
            result["__numeric_present"].fill_null(False)
            if "__numeric_present" in result.columns
            else ibis.literal(True)
        )
        metrics["sample_check.null"] = (present & value.isnull()).any().fill_null(False)
    folded = result.group_by(*outer).aggregate(**metrics) if outer else result.aggregate(**metrics)
    return folded.select(
        **{
            name: folded[name] if name in folded.columns else _null_like(result[name])
            for name in result.columns
        },
        **({"sample_check.null": folded["sample_check.null"]} if nulls == "error" else {}),
        __mean_folded=ibis.literal(True),
    )


def compact_extrema(
    prepared: ReductionInput, result: ir.Table, check_names: tuple[str, ...]
) -> ir.Table:
    """Finish extrema over completed Float64 means with bounded retained checks.

    Each partition is one complete output key. Scheduled raw wall clocks remain
    separate from numeric samples, and distinct clocks prove that entire finite
    clock domain is covered within that output cell.
    """
    assert prepared.mean_fold is not None
    sample_key, nulls, reducer = prepared.mean_fold
    assert reducer in ("min", "max")
    outer = tuple(name for name in result.columns if name.startswith("key.") and name != sample_key)
    coverage_row = (
        result["__coverage_row"] if "__coverage_row" in result.columns else ibis.literal(False)
    )
    # Predicate summaries can retain groups with no selected contributions,
    # including when no cadence requested a separate sample-presence column.
    present = (
        result["__sample_present"].fill_null(False)
        if "__sample_present" in result.columns
        else result["__numeric_present"].fill_null(False)
        if "__numeric_present" in result.columns
        else ibis.literal(True)
    )
    demanded = ~coverage_row & present
    value = result[prepared.value_slot]
    numeric = demanded.ifelse(value, _null_like(value))
    missing: ir.BooleanValue = ibis.literal(False)
    for coverage in prepared.coverage:
        clock = "__coverage_" + coverage.key if coverage.wall_timezone else coverage.key
        points = (
            expected_wall_points(coverage.points, coverage.wall_timezone)
            if coverage.wall_timezone
            else coverage.points
        )
        selected = result[clock].isin(points)
        in_output = (
            result[coverage.output_slot] == coverage.output_key
            if coverage.output_key is not None
            else ibis.literal(True)
        )
        # Predicate summaries retain rejected keys; they cannot prove coverage.
        represented = coverage_row if coverage.wall_timezone else demanded
        observed = (selected & in_output & represented).ifelse(
            result[clock], _null_like(result[clock])
        )
        missing |= (observed.nunique() != len(set(points))) & (
            demanded & in_output
        ).any().fill_null(False)
    metrics: dict[str, ir.Value] = {}
    for name in result.columns:
        column = result[name]
        if name in outer:
            continue
        elif name == prepared.value_slot:
            metrics[name] = numeric.min() if reducer == "min" else numeric.max()
        elif name.startswith("__coverage_"):
            continue
        elif name in check_names or column.type().is_boolean():
            metrics[name] = column.any().fill_null(False)
    if nulls == "error":
        metrics["sample_check.null"] = (demanded & value.isnull()).any().fill_null(False)
    metrics["sample_check.coverage"] = missing
    folded = result.group_by(*outer).aggregate(**metrics) if outer else result.aggregate(**metrics)
    folded = _coverage_diagnostics(prepared, result, folded, outer, demanded)
    return folded.select(
        **{
            name: folded[name]
            if name in folded.columns
            else ibis.literal(False)
            if name == "__coverage_row"
            else _null_like(result[name])
            for name in result.columns
        },
        **({"sample_check.null": folded["sample_check.null"]} if nulls == "error" else {}),
        **{"sample_check.coverage": folded["sample_check.coverage"]},
        **{name: folded[name] for name in folded.columns if name.startswith("__sampling_")},
        __mean_folded=ibis.literal(True),
    )


def _coverage_diagnostics(
    prepared: ReductionInput,
    samples: ir.Table,
    folded: ir.Table,
    outer: tuple[str, ...],
    demanded: ir.BooleanValue,
) -> ir.Table:
    """Find bounded missing clocks inside the same native aggregate statement.

    Literal clocks are SQL constants, not uploaded business keys. Only the first
    six failed groups join the expected clock domain; successful reads keep one
    aggregate row per output coordinate.
    """
    if not prepared.coverage:
        return folded
    failure_count = folded["sample_check.coverage"].ifelse(1, 0).sum().over()
    folded = folded.mutate(__sampling_group_count=failure_count)
    failed = folded.filter(folded["sample_check.coverage"])
    if outer:
        failed = failed.order_by(*(failed[key].asc(nulls_first=True) for key in outer))
    failed = failed.limit(6)
    result = folded
    for index, coverage in enumerate(prepared.coverage):
        if not coverage.points:
            continue
        clock = "__coverage_" + coverage.key if coverage.wall_timezone else coverage.key
        points = (
            expected_wall_points(coverage.points, coverage.wall_timezone)
            if coverage.wall_timezone
            else coverage.points
        )
        constants = [
            ibis.literal(point, type=samples[clock].type()).name("__missing_clock").as_table()
            for point in points
        ]
        expected = constants[0].union(*constants[1:]) if len(constants) > 1 else constants[0]
        selected = failed
        if coverage.output_key is not None:
            selected = selected.filter(selected[coverage.output_slot] == coverage.output_key)
        requested = selected.select(*outer).cross_join(expected)
        represented = samples.filter(
            samples["__coverage_row"] if coverage.wall_timezone else demanded
        )
        observed = represented.select(
            *outer, represented[clock].name("__observed_clock")
        ).distinct()
        missing = requested.anti_join(
            observed,
            [
                *(requested[key].identical_to(observed[key]) for key in outer),
                requested["__missing_clock"] == observed["__observed_clock"],
            ],
        )
        missing = missing.mutate(
            __missing_rank=ibis.row_number().over(
                group_by=[missing[key] for key in outer],
                order_by=missing["__missing_clock"],
            )
        )
        prefix = f"__sampling_{index}_"
        metrics = {prefix + "count": missing.count()}
        for position in range(3):
            selected_point = missing["__missing_rank"] == position
            metrics[prefix + str(position)] = selected_point.any().ifelse(
                missing["__missing_clock"].max(where=selected_point),
                _null_like(missing["__missing_clock"]),
            )
        witnesses = (
            missing.group_by(*outer).aggregate(**metrics) if outer else missing.aggregate(**metrics)
        )
        joined = result.left_join(
            witnesses,
            [result[key].identical_to(witnesses[key]) for key in outer]
            if outer
            else ibis.literal(True),
        )
        result = joined.select(*result.columns, *(witnesses[name] for name in metrics))
    return result
