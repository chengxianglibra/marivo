"""Small correlation contracts and independent numerical reference helpers."""

from __future__ import annotations

import math
from itertools import combinations
from statistics import correlation
from typing import Literal

from marivo._temporal import builtin_grain
from marivo.analysis import time_scope
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.association_contracts import (
    CorrelatePayload,
    CorrelateSpecV1,
    CorrelationMethod,
)
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources


def association_spec(
    method: CorrelationMethod,
    *,
    shape: Literal["entity", "dimension", "time", "dimension-time"] = "entity",
    lags: range | None = None,
) -> CorrelateSpecV1:
    source = make_sources()
    metrics = source.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.order_count")],
        time_scope=time_scope(start="2026-02-01", end="2026-02-12") if "time" in shape else None,
    )
    if "dimension" in shape:
        metrics = metrics.with_dimensions(ref.dimension("sales.orders.channel"))
    if "time" in shape:
        metrics = metrics.with_time_axis(
            ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day")
        )
    if shape != "entity":
        metrics = metrics.aggregate()
    value = metrics.correlate(method=method, lag_range=lags)
    assert isinstance(value._root, LogicalRootHandle) and isinstance(
        value._root.payload, CorrelatePayload
    )
    return value._root.payload.spec


def reference(x: list[float], y: list[float], method: CorrelationMethod) -> float:
    if method == "pearson":
        return correlation(x, y)
    if method == "spearman":

        def ranks(values: list[float]) -> list[float]:
            ordered = sorted(values)
            return [
                sum(i + 1 for i, v in enumerate(ordered) if v == value) / ordered.count(value)
                for value in values
            ]

        return correlation(ranks(x), ranks(y))
    concordant = discordant = tied_x = tied_y = 0
    for i, j in combinations(range(len(x)), 2):
        dx = (x[i] > x[j]) - (x[i] < x[j])
        dy = (y[i] > y[j]) - (y[i] < y[j])
        concordant += int(dx * dy > 0)
        discordant += int(dx * dy < 0)
        tied_x += int(dx == 0 and dy != 0)
        tied_y += int(dy == 0 and dx != 0)
    return (concordant - discordant) / math.sqrt(
        (concordant + discordant + tied_x) * (concordant + discordant + tied_y)
    )
