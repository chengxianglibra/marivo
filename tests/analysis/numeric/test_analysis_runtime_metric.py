from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import marivo.analysis as mv
import marivo.analysis.runtime_metric as runtime_descriptors
from marivo.analysis.runtime_metric import (
    FrozenSliceMap,
    RuntimeAggregateExpr,
    RuntimeLinearExpr,
    RuntimeMetricExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
    from_replay_payload,
    replay_payload,
)
from marivo.refs import FieldKind, MetricKind, Ref
from marivo.refs import ref as ref_factory
from marivo.semantic.runtime_metric import SliceScalar


def test_runtime_metric_namespace_exposes_only_closed_constructors() -> None:
    assert runtime_descriptors.__all__ == [
        "FrozenSliceMap",
        "RuntimeAggregateExpr",
        "RuntimeLinearExpr",
        "RuntimeMetricExpr",
        "RuntimeRatioExpr",
        "RuntimeSliceExpr",
        "RuntimeWeightedMeanExpr",
        "aggregate",
        "linear",
        "ratio",
        "slice",
        "weighted_mean",
    ]
    assert not hasattr(mv, "observe")
    assert not hasattr(mv.Session, "compose")


def test_runtime_aggregate_aligns_authoring_fold_and_freezes_slice_copy() -> None:
    measure = ref_factory.measure("sales.orders.amount")
    country = ref_factory.dimension("sales.orders.country")
    source: dict[Ref[FieldKind], list[SliceScalar]] = {country: ["CN", "US"]}

    expression = runtime_descriptors.aggregate(
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

    expression = runtime_descriptors.slice(
        metric,
        by={day: {"op": ">=", "value": "2026-01-01"}},
        label="recent revenue",
    )

    assert isinstance(expression, RuntimeSliceExpr)
    assert expression.metric is metric
    assert expression.by[day] == {"op": ">=", "value": "2026-01-01"}


def test_runtime_ratio_is_recursive_and_label_is_not_value_equality() -> None:
    measure = ref_factory.measure("sales.orders.amount")
    total = runtime_descriptors.aggregate(measure, agg="sum", label="total")
    count = runtime_descriptors.aggregate(measure, agg="count", label="count")
    inner = runtime_descriptors.ratio(total, count, label="average")
    first = runtime_descriptors.ratio(inner, ref_factory.metric("sales.baseline"), label="first")
    second = runtime_descriptors.ratio(inner, ref_factory.metric("sales.baseline"), label="second")

    assert isinstance(first, RuntimeRatioExpr)
    assert isinstance(first.numerator, RuntimeRatioExpr)
    assert first == second
    assert hash(first) == hash(second)


def test_runtime_linear_freezes_ordered_terms_and_round_trips_replay() -> None:
    revenue = ref_factory.metric("sales.revenue")
    cogs = ref_factory.metric("sales.cogs")
    discounts = ref_factory.metric("sales.discounts")
    add: list[Ref[MetricKind] | RuntimeMetricExpr] = [revenue]
    subtract: list[Ref[MetricKind] | RuntimeMetricExpr] = [cogs, discounts]

    expression = runtime_descriptors.linear(
        add=add,
        subtract=subtract,
        label="  Gross margin  ",
    )
    add.append(ref_factory.metric("sales.other_revenue"))
    subtract.reverse()

    assert isinstance(expression, RuntimeLinearExpr)
    assert expression.kind == "linear"
    assert expression.add == (revenue,)
    assert expression.subtract == (cogs, discounts)
    assert expression.label == "Gross margin"
    assert from_replay_payload(replay_payload(expression)) == expression


def test_runtime_linear_accepts_nested_runtime_expressions() -> None:
    revenue = ref_factory.metric("sales.revenue")
    discounts = ref_factory.metric("sales.discounts")
    net = runtime_descriptors.linear(
        add=[revenue],
        subtract=[discounts],
        label="Net revenue",
    )
    rate = runtime_descriptors.ratio(
        net,
        revenue,
        label="Net revenue rate",
    )

    assert isinstance(rate.numerator, RuntimeLinearExpr)
    assert rate.numerator == net


def test_runtime_weighted_mean_freezes_slice_and_round_trips_replay() -> None:
    value = ref_factory.measure("sales.orders.latency")
    weight = ref_factory.measure("sales.orders.requests")
    region = ref_factory.dimension("sales.orders.region")
    source: dict[Ref[FieldKind], list[SliceScalar]] = {region: ["CN", "US"]}

    expression = runtime_descriptors.weighted_mean(
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
    expression = runtime_descriptors.aggregate(
        ref_factory.measure("sales.orders.amount"),
        agg="sum",
        label="total",
    )
    payload = replay_payload(expression)
    payload["label"] = bad_label

    with pytest.raises(ValueError, match="label"):
        from_replay_payload(payload)


@pytest.mark.parametrize("bad", ["sum_all", ("percentile", 0.0), ("percentile", True)])
def test_runtime_aggregate_rejects_invalid_closed_agg(bad: str | tuple[str, float | bool]) -> None:
    with pytest.raises(ValueError):
        runtime_descriptors.aggregate(
            ref_factory.measure("sales.orders.amount"),
            agg=bad,  # type: ignore[arg-type]  # Exercise closed runtime reducer rejection.
            label="invalid aggregate",
        )


@pytest.mark.parametrize("bad", ["auto", ("percentile", 1.0), ("percentile", False)])
def test_runtime_aggregate_rejects_invalid_shared_fold(bad: str | tuple[str, float | bool]) -> None:
    with pytest.raises(Exception):
        runtime_descriptors.aggregate(
            ref_factory.measure("sales.orders.amount"),
            agg="sum",
            fold=bad,  # type: ignore[arg-type]  # Exercise closed runtime fold rejection.
            label="invalid fold",
        )


def test_runtime_constructors_require_nonempty_labels() -> None:
    measure = ref_factory.measure("sales.orders.amount")
    metric = ref_factory.metric("sales.revenue")
    dimension = ref_factory.dimension("sales.orders.country")

    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        runtime_descriptors.aggregate(measure, agg="sum")  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        runtime_descriptors.weighted_mean(measure, measure)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        runtime_descriptors.slice(metric, by={dimension: "CN"})  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        runtime_descriptors.ratio(metric, metric)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'label'"):
        runtime_descriptors.linear(add=[metric, metric])  # type: ignore[call-arg]

    for constructor in (
        lambda: runtime_descriptors.aggregate(measure, agg="sum", label=""),
        lambda: runtime_descriptors.weighted_mean(measure, measure, label=" "),
        lambda: runtime_descriptors.slice(metric, by={dimension: "CN"}, label=""),
        lambda: runtime_descriptors.ratio(metric, metric, label=" "),
        lambda: runtime_descriptors.linear(add=[metric, metric], label=" "),
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
        lambda label: RuntimeLinearExpr(
            kind="linear",
            add=(metric,),
            subtract=(metric,),
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
        runtime_descriptors.aggregate(
            ref_factory.metric("sales.revenue"),  # type: ignore[arg-type]  # Runtime wrong-kind rejection.
            agg="sum",
            label="wrong aggregate",
        )
    with pytest.raises(TypeError, match=r"exact Ref\[metric\]"):
        runtime_descriptors.ratio(
            ref_factory.measure("sales.orders.amount"),  # type: ignore[arg-type]  # Runtime wrong-kind rejection.
            ref_factory.metric("sales.total"),
            label="wrong ratio",
        )
    with pytest.raises(TypeError, match=r"exact Ref\[dimension"):
        runtime_descriptors.slice(
            ref_factory.metric("sales.revenue"),
            by={"country": "CN"},  # type: ignore[dict-item]
            label="wrong slice",
        )
    with pytest.raises(TypeError, match=r"weighted_mean value requires exact Ref\[measure\]"):
        runtime_descriptors.weighted_mean(
            ref_factory.metric("sales.revenue"),  # type: ignore[arg-type]  # Runtime wrong-kind rejection.
            ref_factory.measure("sales.orders.requests"),
            label="wrong weighted mean",
        )
    with pytest.raises(TypeError, match=r"weighted_mean weight requires exact Ref\[measure\]"):
        runtime_descriptors.weighted_mean(
            ref_factory.measure("sales.orders.latency"),
            ref_factory.metric("sales.requests"),  # type: ignore[arg-type]  # Runtime wrong-kind rejection.
            label="wrong weighted mean",
        )
    with pytest.raises(TypeError, match=r"exact Ref\[metric\]"):
        runtime_descriptors.linear(
            add=[
                ref_factory.metric("sales.revenue"),
                ref_factory.measure("sales.orders.amount"),  # type: ignore[list-item]  # Runtime wrong-kind rejection.
            ],
            label="wrong linear",
        )


@pytest.mark.parametrize(
    ("add", "subtract"),
    [([], []), ([ref_factory.metric("sales.revenue")], [])],
)
def test_runtime_linear_requires_at_least_two_terms(
    add: list[Ref[MetricKind] | RuntimeMetricExpr],
    subtract: list[Ref[MetricKind] | RuntimeMetricExpr],
) -> None:
    with pytest.raises(ValueError, match="at least two metric terms"):
        runtime_descriptors.linear(
            add=add,
            subtract=subtract,
            label="invalid linear",
        )


def test_runtime_linear_requires_list_or_tuple_inputs() -> None:
    revenue = ref_factory.metric("sales.revenue")

    with pytest.raises(TypeError, match="add requires list or tuple"):
        runtime_descriptors.linear(
            add={revenue},  # type: ignore[arg-type]
            subtract=[revenue],
            label="invalid linear",
        )


def test_runtime_slice_requires_nonempty_mapping() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        runtime_descriptors.slice(
            ref_factory.metric("sales.revenue"),
            by={},
            label="empty slice",
        )
