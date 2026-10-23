"""Qualified transport of a completed mean over native time sample means."""

from __future__ import annotations

import sys
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


def mean_occurrences(
    outputs: tuple[tuple[str, MetricNode], ...], declared: Mapping[ExactRef, MetricNode]
) -> dict[tuple[int, ...], tuple[tuple[int, ...], str]]:
    """Admit only an output's direct mean of one source mean, never conditional demand."""
    result: dict[tuple[int, ...], tuple[tuple[int, ...], str]] = {}
    for index, (_, node) in enumerate(outputs):
        occurrence: tuple[int, ...] = (index,)
        while isinstance(node, (DeclaredMetricRef, ContributionFilter)):
            node = declared[node.ref] if isinstance(node, DeclaredMetricRef) else node.operand
            occurrence = (*occurrence, 0)
        if not isinstance(node, PerReduce) or node.reducer != "mean":
            continue
        if not isinstance(node.samples, tuple) or len(node.samples) != 1:
            continue
        outer, nulls = occurrence, node.nulls
        node = node.operand
        occurrence = (*occurrence, 0)
        while isinstance(node, (DeclaredMetricRef, ContributionFilter)):
            node = declared[node.ref] if isinstance(node, DeclaredMetricRef) else node.operand
            occurrence = (*occurrence, 0)
        if isinstance(node, SourceAggregate) and node.reducer == "mean" and node.time is not None:
            result[occurrence] = (outer, nulls)
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
    """Finish safe float means in the source; retain original samples for unsafe arithmetic.

    A global guard keeps a read uniformly compact or sampled. In particular,
    cancellation and finite-sum overflow still use the existing local fsum path
    when the absolute-value bound cannot prove a finite sum in every order.
    """
    assert prepared.mean_fold is not None
    sample_key, nulls = prepared.mean_fold
    outer = tuple(name for name in result.columns if name.startswith("key.") and name != sample_key)
    coverage_row = (
        result["__coverage_row"] if "__coverage_row" in result.columns else ibis.literal(False)
    )
    normal = ~coverage_row
    value = result[prepared.value_slot]
    numeric_value = normal.ifelse(value, ibis.null().cast(value.type()))
    count = numeric_value.count()
    bound = numeric_value.abs().max().fill_null(0) < sys.float_info.max / count.clip(lower=1)
    safe = bound & ibis.and_(
        *(result[name].cast("int64").max().fill_null(0) == 0 for name in check_names)
    )
    for name in ("__sample_present", "__numeric_present"):
        if name in result.columns:
            safe &= (coverage_row | result[name].fill_null(False)).cast("int64").min().fill_null(
                1
            ) == 1
    if nulls == "error":
        safe &= (normal & value.isnull()).cast("int64").max().fill_null(0) == 0
    for coverage in prepared.coverage:
        clock = "__coverage_" + coverage.key if coverage.wall_timezone else coverage.key
        points = (
            expected_wall_points(coverage.points, coverage.wall_timezone)
            if coverage.wall_timezone
            else coverage.points
        )
        required = result[clock].isin(points)
        if coverage.output_key is not None:
            in_output = result[coverage.output_slot] == coverage.output_key
            # Other output cells impose no requirement for this coverage entry.
            selected = required & in_output
            safe &= (
                selected.ifelse(result[clock], ibis.null().cast(result[clock].type())).count()
                == len(set(points))
            ) | (in_output.cast("int64").max().fill_null(0) == 0)
        else:
            safe &= required.ifelse(
                result[clock], ibis.null().cast(result[clock].type())
            ).count() == len(set(points))
    # Window summaries share the original grouped relation. A scalar gate
    # joined back to the source would make Trino inline and rescan its CTEs.
    window = ibis.window(group_by=[result[key] for key in outer], rows=(None, None))
    folded = result.mutate(
        __mean_safe=safe.over(window),
        __mean_value=numeric_value.mean().over(window),
        __mean_rank=ibis.row_number().over(
            ibis.window(group_by=[result[key] for key in outer], order_by=result[sample_key])
        ),
    )
    # Numeric MIN avoids Ibis' boolean-window rewrite to a cumulative frame.
    folded = folded.mutate(
        __mean_folded=folded.__mean_safe.cast("int64").min().over(ibis.window(rows=(None, None)))
        == 1
    )
    folded = folded.filter(~folded.__mean_folded | (folded.__mean_rank == 0))
    compact = folded.__mean_folded
    projection: dict[str, ir.Value] = {}
    for name in result.columns:
        original = folded[name]
        if name in outer:
            projection[name] = original
        elif name == prepared.value_slot:
            projection[name] = compact.ifelse(folded.__mean_value, original)
        elif name == "__coverage_row" or name in check_names:
            projection[name] = compact.ifelse(False, original)
        elif result[name].type().is_boolean():
            projection[name] = compact.ifelse(True, original)
        else:
            projection[name] = compact.ifelse(ibis.null().cast(result[name].type()), original)
    return folded.select(**projection, __mean_folded=compact)
