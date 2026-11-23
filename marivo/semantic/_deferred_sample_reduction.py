"""Resolve a deferred reduction over a completed Metric's internal samples."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypeAlias

from ibis.common.deferred import Just, Resolver

from marivo._definition.expressions import (
    FieldCalendarSample,
    MetricNode,
    PerReduce,
    PerWeightedMean,
    Reducer,
)
from marivo._definition.types import AxisBinding, ExactRef, NullPolicy
from marivo._kernel.refs import SemanticKind
from marivo._kernel.temporal import Grain
from marivo.semantic._deferred_facts import MetricFacts, RowFacts, _Facts, _fail, _Samples
from marivo.semantic._deferred_time import _constant

_ReductionCall: TypeAlias = Reducer | Literal["weighted_mean"]


def _reduce_samples(
    receiver: _Samples,
    name: _ReductionCall,
    policy: NullPolicy,
    q: float | None,
    kwargs: dict[str, Resolver],
    consume: Callable[[Resolver], _Facts],
) -> MetricFacts:
    metric = receiver.metric
    if name == "weighted_mean":
        weight = consume(kwargs.get("weight", Just(None)))
        if not isinstance(weight, MetricFacts):
            _fail(
                "Sample weight must be a Metric.",
                expected="completed Metric",
                received=type(weight).__name__,
            )
        assert isinstance(weight, MetricFacts)
        node: MetricNode = PerWeightedMean(
            metric.node,
            weight.node,
            receiver.axes,
            nulls=policy,
        )
        return MetricFacts(
            node,
            tuple(dict.fromkeys((*metric.roots, *weight.roots))),
            metric.unit,
            tuple(dict((*metric._declared, *weight._declared)).items()),
        )
    order_by: ExactRef | None = None
    include_null = _constant(kwargs.get("include_null", Just(False)))
    if type(include_null) is not bool:
        _fail(
            "include_null must be boolean.",
            expected="True or False",
            received=str(include_null),
        )
    sample_axes = (
        ()
        if isinstance(receiver.axes, (Grain, FieldCalendarSample))
        else receiver.axes
        if isinstance(receiver.axes, tuple)
        else receiver.axes.axes
    )
    if name in {"first", "last"}:
        if isinstance(receiver.axes, (Grain, FieldCalendarSample)) and "order_by" in kwargs:
            _fail(
                "Calendar samples use their captured calendar order.",
                expected="calendar first/last without a field order_by",
                received="Grain samples with order_by",
                action="Omit order_by to use the calendar cell order.",
            )
        if "order_by" in kwargs:
            ordering = consume(kwargs["order_by"])
            if not isinstance(ordering, RowFacts) or ordering.field is None:
                _fail(
                    "order_by must name an internal time axis.",
                    expected="resolved TimeField",
                    received=type(ordering).__name__,
                )
            assert isinstance(ordering, RowFacts) and ordering.field is not None  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
            order_by = ordering.field.reference
        else:
            times = tuple(
                axis.field
                for axis in sample_axes
                if isinstance(axis, AxisBinding)
                and axis.field is not None
                and axis.field.ref.kind is SemanticKind.TIME_DIMENSION
            )
            if len(times) == 1:
                order_by = times[0]
        if not isinstance(receiver.axes, (Grain, FieldCalendarSample)) and (
            order_by is None
            or order_by.ref.kind is not SemanticKind.TIME_DIMENSION
            or order_by
            not in tuple(axis.field for axis in sample_axes if isinstance(axis, AxisBinding))
        ):
            _fail(
                "Ordered sample reduction requires one internal time axis.",
                expected="order_by naming an internal TimeField",
                received=str(order_by),
            )
    node = PerReduce(
        metric.node,
        receiver.axes,
        name,
        q=q,
        nulls=policy,
        order_by=order_by,
        include_null=include_null,
    )
    return MetricFacts(
        node,
        metric.roots,
        "1" if name in {"count", "count_distinct", "approx_nunique"} else metric.unit,
        metric._declared,
    )
