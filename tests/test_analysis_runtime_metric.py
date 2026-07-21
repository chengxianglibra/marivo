from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import marivo.analysis as mv
from marivo.analysis.runtime_metric import (
    FrozenSliceMap,
    RuntimeAggregateExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
    from_replay_payload,
    replay_payload,
)
from marivo.refs import ref as ref_factory


def test_runtime_metric_namespace_exposes_only_closed_constructors() -> None:
    assert mv.runtime_metric.__all__ == [
        "FrozenSliceMap",
        "RuntimeAggregateExpr",
        "RuntimeMetricExpr",
        "RuntimeRatioExpr",
        "RuntimeSliceExpr",
        "RuntimeWeightedMeanExpr",
        "aggregate",
        "ratio",
        "slice",
        "weighted_mean",
    ]
    assert not hasattr(mv, "observe")
    assert not hasattr(mv.Session, "compose")


def test_runtime_aggregate_aligns_authoring_fold_and_freezes_slice_copy() -> None:
    measure = ref_factory.measure("sales.orders.amount")
    country = ref_factory.dimension("sales.orders.country")
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
    metric = ref_factory.metric("sales.revenue")
    day = ref_factory.time_dimension("sales.orders.created_at")

    expression = mv.runtime_metric.slice(
        metric,
        by={day: {"op": ">=", "value": "2026-01-01"}},
        label="recent revenue",
    )

    assert isinstance(expression, RuntimeSliceExpr)
    assert expression.metric is metric
    assert expression.by[day] == {"op": ">=", "value": "2026-01-01"}


def test_runtime_ratio_is_recursive_and_label_is_not_value_equality() -> None:
    measure = ref_factory.measure("sales.orders.amount")
    total = mv.runtime_metric.aggregate(measure, agg="sum", label="total")
    count = mv.runtime_metric.aggregate(measure, agg="count", label="count")
    inner = mv.runtime_metric.ratio(total, count, label="average")
    first = mv.runtime_metric.ratio(inner, ref_factory.metric("sales.baseline"), label="first")
    second = mv.runtime_metric.ratio(inner, ref_factory.metric("sales.baseline"), label="second")

    assert isinstance(first, RuntimeRatioExpr)
    assert isinstance(first.numerator, RuntimeRatioExpr)
    assert first == second
    assert hash(first) == hash(second)


def test_runtime_weighted_mean_freezes_slice_and_round_trips_replay() -> None:
    value = ref_factory.measure("sales.orders.latency")
    weight = ref_factory.measure("sales.orders.requests")
    region = ref_factory.dimension("sales.orders.region")
    source = {region: ["CN", "US"]}

    expression = mv.runtime_metric.weighted_mean(
        value,
        weight,
        slice_by=source,
        label="  Observed latency  ",
    )
    source[region].append("DE")

    assert isinstance(expression, RuntimeWeightedMeanExpr)
    assert expression.kind == "weighted_mean"
    assert expression.value is value
    assert expression.weight is weight
    assert expression.slice_by[region] == ["CN", "US"]
    assert expression.label == "Observed latency"
    assert from_replay_payload(replay_payload(expression)) == expression


@pytest.mark.parametrize("bad_label", [None, ""])
def test_runtime_replay_rejects_missing_or_empty_labels(bad_label: object) -> None:
    expression = mv.runtime_metric.aggregate(
        ref_factory.measure("sales.orders.amount"),
        agg="sum",
        label="total",
    )
    payload = replay_payload(expression)
    payload["label"] = bad_label

    with pytest.raises(ValueError, match="label"):
        from_replay_payload(payload)


@pytest.mark.parametrize("bad", ["sum_all", ("percentile", 0.0), ("percentile", True)])
def test_runtime_aggregate_rejects_invalid_closed_agg(bad) -> None:
    with pytest.raises(ValueError):
        mv.runtime_metric.aggregate(
            ref_factory.measure("sales.orders.amount"),
            agg=bad,
            label="invalid aggregate",
        )


@pytest.mark.parametrize("bad", ["auto", ("percentile", 1.0), ("percentile", False)])
def test_runtime_aggregate_rejects_invalid_shared_fold(bad) -> None:
    with pytest.raises(Exception):
        mv.runtime_metric.aggregate(
            ref_factory.measure("sales.orders.amount"),
            agg="sum",
            fold=bad,
            label="invalid fold",
        )


def test_runtime_constructors_require_nonempty_labels() -> None:
    measure = ref_factory.measure("sales.orders.amount")
    metric = ref_factory.metric("sales.revenue")
    dimension = ref_factory.dimension("sales.orders.country")

    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        mv.runtime_metric.aggregate(measure, agg="sum")  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        mv.runtime_metric.weighted_mean(measure, measure)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        mv.runtime_metric.slice(metric, by={dimension: "CN"})  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        mv.runtime_metric.ratio(metric, metric)  # type: ignore[call-arg]

    for constructor in (
        lambda: mv.runtime_metric.aggregate(measure, agg="sum", label=""),
        lambda: mv.runtime_metric.weighted_mean(measure, measure, label=" "),
        lambda: mv.runtime_metric.slice(metric, by={dimension: "CN"}, label=""),
        lambda: mv.runtime_metric.ratio(metric, metric, label=" "),
    ):
        with pytest.raises(ValueError, match="label must not be empty"):
            constructor()


def test_public_runtime_descriptor_classes_enforce_label_invariant() -> None:
    measure = ref_factory.measure("sales.orders.amount")
    metric = ref_factory.metric("sales.revenue")
    dimension = ref_factory.dimension("sales.orders.country")
    empty_slice = FrozenSliceMap(())
    country_slice = FrozenSliceMap(((dimension, "CN"),))
    constructors = (
        lambda label: RuntimeAggregateExpr(
            kind="aggregate",
            measure=measure,
            agg="sum",
            fold=None,
            slice_by=empty_slice,
            label=label,
        ),
        lambda label: RuntimeWeightedMeanExpr(
            kind="weighted_mean",
            value=measure,
            weight=measure,
            slice_by=empty_slice,
            label=label,
        ),
        lambda label: RuntimeSliceExpr(
            kind="slice",
            metric=metric,
            by=country_slice,
            label=label,
        ),
        lambda label: RuntimeRatioExpr(
            kind="ratio",
            numerator=metric,
            denominator=metric,
            zero_division="null",
            label=label,
        ),
    )

    for constructor in constructors:
        with pytest.raises(TypeError, match="label must be str"):
            constructor(None)
        with pytest.raises(ValueError, match="label must not be empty"):
            constructor(" ")

        expression = constructor("  stable column  ")
        assert expression.label == "stable column"
        assert from_replay_payload(replay_payload(expression)) == expression


def test_runtime_constructors_reject_wrong_ref_and_operand_kinds() -> None:
    with pytest.raises(TypeError, match=r"exact Ref\[measure\]"):
        mv.runtime_metric.aggregate(  # type: ignore[arg-type]
            ref_factory.metric("sales.revenue"),
            agg="sum",
            label="wrong aggregate",
        )
    with pytest.raises(TypeError, match=r"exact Ref\[metric\]"):
        mv.runtime_metric.ratio(
            ref_factory.measure("sales.orders.amount"),
            ref_factory.metric("sales.total"),
            label="wrong ratio",
        )  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=r"exact Ref\[dimension"):
        mv.runtime_metric.slice(
            ref_factory.metric("sales.revenue"),
            by={"country": "CN"},  # type: ignore[dict-item]
            label="wrong slice",
        )
    with pytest.raises(TypeError, match=r"weighted_mean value requires exact Ref\[measure\]"):
        mv.runtime_metric.weighted_mean(  # type: ignore[arg-type]
            ref_factory.metric("sales.revenue"),
            ref_factory.measure("sales.orders.requests"),
            label="wrong weighted mean",
        )
    with pytest.raises(TypeError, match=r"weighted_mean weight requires exact Ref\[measure\]"):
        mv.runtime_metric.weighted_mean(  # type: ignore[arg-type]
            ref_factory.measure("sales.orders.latency"),
            ref_factory.metric("sales.requests"),
            label="wrong weighted mean",
        )


def test_runtime_slice_requires_nonempty_mapping() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        mv.runtime_metric.slice(
            ref_factory.metric("sales.revenue"),
            by={},
            label="empty slice",
        )
