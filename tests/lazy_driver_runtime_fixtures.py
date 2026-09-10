"""Small exact additive partitions shared by driver Runtime acceptance owners."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import duckdb

from marivo._temporal import builtin_grain, time_scope
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_adapter_fixtures import AdapterFixture
from tests.lazy_retained_fixtures import setup_retained

CHANNEL = ref.dimension("sales.orders.channel")
REGION = ref.dimension("sales.customers.region")


def setup_driver(project: Path, kind: Literal["local", "engine"] = "local") -> AdapterFixture:
    fixture = setup_retained(project, kind)
    with duckdb.connect(str(fixture.database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM orders")
        rows: list[tuple[int, int, float, str, str]] = []
        for month in (1, 2):
            for day in (2, 3):
                for customer in (1, 2, 3, 4):
                    values = (
                        (10.0, 10.0, 10.0)
                        if month == 1 or customer >= 3
                        else ((16.0, 6.0, 10.0) if customer == 1 else (11.0, 11.0, 10.0))
                    )
                    for channel, amount in zip(("a", "b", "c"), values, strict=True):
                        rows.append(
                            (len(rows) + 1, customer, amount, channel, f"2026-{month:02}-{day:02}")
                        )
        connection.executemany(
            "INSERT INTO orders (id, customer_id, amount, channel, day) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    return fixture


def driver_metric(
    sources: LazySources,
    *,
    baseline: bool = False,
    axes: bool = True,
    temporal: bool = False,
    region: bool = False,
    entity: bool = False,
) -> LogicalMetricDataset:
    month = "01" if baseline else "02"
    window = time_scope(start=f"2026-{month}-01", end=f"2026-{month}-05")
    metric = sources.observe(
        ref.metric("sales.revenue"),
        time_scope=window,
        population=sources.population(ref.entity("sales.customers")) if entity else None,
    )
    if axes:
        metric = metric.with_dimensions(CHANNEL)
    if region:
        metric = metric.with_dimensions(REGION)
    if temporal:
        metric = metric.with_time_axis(
            ref.time_dimension("sales.orders.order_time"), grain=builtin_grain("day")
        )
    return metric if entity else metric.aggregate()
