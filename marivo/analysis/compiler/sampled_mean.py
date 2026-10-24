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
    """Finish the equal-weight mean of completed samples with native AVG semantics."""
    assert prepared.mean_fold is not None
    sample_key, _, _ = prepared.mean_fold
    outer = tuple(name for name in result.columns if name.startswith("key.") and name != sample_key)
    value = result[prepared.value_slot].mean()
    if value.type().is_decimal():
        from marivo.semantic._numeric_lowering import force_cast

        value = force_cast(value, value.type())
    metrics: dict[str, ir.Value] = {prepared.value_slot: value}
    for name in result.columns:
        if name in check_names or result[name].type().is_boolean():
            metrics[name] = result[name].any().fill_null(False)
    folded = result.group_by(*outer).aggregate(**metrics) if outer else result.aggregate(**metrics)
    return folded.select(
        **{
            name: folded[name] if name in folded.columns else _null_like(result[name])
            for name in result.columns
        },
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
        observed = (selected & in_output).ifelse(result[clock], _null_like(result[clock]))
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
    metrics["sample_check.null"] = (
        (demanded & value.isnull()).any().fill_null(False)
        if nulls == "error"
        else ibis.literal(False)
    )
    metrics["sample_check.coverage"] = missing
    folded = result.group_by(*outer).aggregate(**metrics) if outer else result.aggregate(**metrics)
    return folded.select(
        **{
            name: folded[name]
            if name in folded.columns
            else ibis.literal(False)
            if name == "__coverage_row"
            else _null_like(result[name])
            for name in result.columns
        },
        **{name: folded[name] for name in ("sample_check.null", "sample_check.coverage")},
        __mean_folded=ibis.literal(True),
    )
