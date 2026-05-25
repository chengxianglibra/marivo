"""
Pattern: define an aggregate metric on a dataset.
When to use: you want a metric whose value comes from a single reducer over the dataset.
Output shape: list of metric ids ending in the new metric.
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


print(ms.list_metrics(project=project))

# Expected output:
# ['sales.revenue']
