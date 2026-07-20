from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import marivo.analysis as mv
from marivo.analysis.runtime_metric import (
    RuntimeAggregateExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
)
from marivo.refs import Ref


def test_runtime_metric_namespace_exposes_only_closed_constructors() -> None:
    assert mv.runtime_metric.__all__ == [
        "FrozenSliceMap",
        "RuntimeAggregateExpr",
        "RuntimeMetricExpr",
        "RuntimeRatioExpr",
        "RuntimeSliceExpr",
        "aggregate",
        "ratio",
        "slice",
    ]
    assert not hasattr(mv, "observe")
    assert not hasattr(mv.Session, "compose")


def test_runtime_aggregate_aligns_authoring_fold_and_freezes_slice_copy() -> None:
    measure = Ref.measure("sales.orders.amount")
    country = Ref.dimension("sales.orders.country")
    source = {country: ["CN", "US"]}

    expression = mv.runtime_metric.aggregate(
        measure,
        agg=("percentile", 0.95),
        fold=("percentile", 0.5),
        slice_by=source,
        label="  p95 amount  ",
    )
    source[country].append("DE")

    assert isinstance(expression, RuntimeAggregateExpr)
    assert expression.kind == "aggregate"
    assert expression.agg == ("percentile", 0.95)
    assert expression.fold == ("percentile", 0.5)
    assert expression.slice_by[country] == ["CN", "US"]
    assert expression.label == "p95 amount"
    with pytest.raises(FrozenInstanceError):
        expression.label = "changed"  # type: ignore[misc]


def test_runtime_slice_accepts_metric_ref_and_time_dimension() -> None:
    metric = Ref.metric("sales.revenue")
    day = Ref.time_dimension("sales.orders.created_at")

    expression = mv.runtime_metric.slice(metric, by={day: {"op": ">=", "value": "2026-01-01"}})

    assert isinstance(expression, RuntimeSliceExpr)
    assert expression.metric is metric
    assert expression.by[day] == {"op": ">=", "value": "2026-01-01"}


def test_runtime_ratio_is_recursive_and_label_is_not_value_equality() -> None:
    measure = Ref.measure("sales.orders.amount")
    total = mv.runtime_metric.aggregate(measure, agg="sum")
    count = mv.runtime_metric.aggregate(measure, agg="count")
    inner = mv.runtime_metric.ratio(total, count)
    first = mv.runtime_metric.ratio(inner, Ref.metric("sales.baseline"), label="first")
    second = mv.runtime_metric.ratio(inner, Ref.metric("sales.baseline"), label="second")

    assert isinstance(first, RuntimeRatioExpr)
    assert isinstance(first.numerator, RuntimeRatioExpr)
    assert first == second
    assert hash(first) == hash(second)


@pytest.mark.parametrize("bad", ["sum_all", ("percentile", 0.0), ("percentile", True)])
def test_runtime_aggregate_rejects_invalid_closed_agg(bad) -> None:
    with pytest.raises(ValueError):
        mv.runtime_metric.aggregate(Ref.measure("sales.orders.amount"), agg=bad)


@pytest.mark.parametrize("bad", ["auto", ("percentile", 1.0), ("percentile", False)])
def test_runtime_aggregate_rejects_invalid_shared_fold(bad) -> None:
    with pytest.raises(Exception):
        mv.runtime_metric.aggregate(Ref.measure("sales.orders.amount"), agg="sum", fold=bad)


def test_runtime_constructors_reject_wrong_ref_and_operand_kinds() -> None:
    with pytest.raises(TypeError, match=r"exact Ref\[measure\]"):
        mv.runtime_metric.aggregate(Ref.metric("sales.revenue"), agg="sum")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"exact Ref\[metric\]"):
        mv.runtime_metric.ratio(Ref.measure("sales.orders.amount"), Ref.metric("sales.total"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"exact Ref\[dimension"):
        mv.runtime_metric.slice(Ref.metric("sales.revenue"), by={"country": "CN"})  # type: ignore[dict-item]


def test_runtime_slice_requires_nonempty_mapping() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        mv.runtime_metric.slice(Ref.metric("sales.revenue"), by={})
