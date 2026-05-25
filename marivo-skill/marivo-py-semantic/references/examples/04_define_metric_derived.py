"""
Pattern: define a derived metric as a ratio of two registered metrics.
When to use: you have two metrics already defined and need their ratio (e.g. average order value).
Output shape: list of metric ids -- base metrics plus the derived one.
"""

from __future__ import annotations

from typing import Any

from _fixtures.tiny_db import connect, new_project

import marivo.semantic_py as ms

with new_project() as project:
    ms.model(name="sales")

    @ms.datasource(name="tiny_orders", backend_type="duckdb")
    def tiny_orders() -> Any:
        return connect()

    @ms.dataset(name="orders", datasource=tiny_orders)
    def orders(backend: Any) -> Any:
        return backend.table("orders")

    @ms.time_field(dataset="orders", data_type="date", granularity="day")
    def created_at(orders: Any) -> Any:
        return orders.created_at.cast("date")

    @ms.metric(decomposition=ms.sum(), name="revenue")
    def revenue(orders: Any) -> Any:
        return orders.amount.sum()

    @ms.metric(decomposition=ms.sum(), name="orders_count")
    def orders_count(orders: Any) -> Any:
        return orders.count()

    @ms.metric(
        decomposition=ms.ratio(
            numerator=ms.ref("metric.revenue"),
            denominator=ms.ref("metric.orders_count"),
        ),
        name="aov",
    )
    def aov(orders: Any) -> Any:
        return revenue(orders) / orders_count(orders)


print(sorted(ms.list_metrics(project=project)))

# Expected output:
# ['sales.aov', 'sales.orders_count', 'sales.revenue']
