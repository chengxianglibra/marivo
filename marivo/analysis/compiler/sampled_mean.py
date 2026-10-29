"""Qualified transport of a completed mean over native time sample means."""

from __future__ import annotations

from collections.abc import Mapping
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
    """Finish extrema over represented completed Float64 means."""
    assert prepared.mean_fold is not None
    sample_key, nulls, reducer = prepared.mean_fold
    assert reducer in ("min", "max")
    outer = tuple(name for name in result.columns if name.startswith("key.") and name != sample_key)
    # Predicate summaries can retain groups with no selected contributions.
    present = (
        result["__sample_present"].fill_null(False)
        if "__sample_present" in result.columns
        else result["__numeric_present"].fill_null(False)
        if "__numeric_present" in result.columns
        else ibis.literal(True)
    )
    demanded = present
    value = result[prepared.value_slot]
    numeric = demanded.ifelse(value, _null_like(value))
    metrics: dict[str, ir.Value] = {}
    for name in result.columns:
        column = result[name]
        if name in outer:
            continue
        elif name == prepared.value_slot:
            metrics[name] = numeric.min() if reducer == "min" else numeric.max()
        elif name in check_names or column.type().is_boolean():
            metrics[name] = column.any().fill_null(False)
    if nulls == "error":
        metrics["sample_check.null"] = (demanded & value.isnull()).any().fill_null(False)
    folded = result.group_by(*outer).aggregate(**metrics) if outer else result.aggregate(**metrics)
    return folded.select(
        **{
            name: folded[name] if name in folded.columns else _null_like(result[name])
            for name in result.columns
        },
        **({"sample_check.null": folded["sample_check.null"]} if nulls == "error" else {}),
        __mean_folded=ibis.literal(True),
    )
