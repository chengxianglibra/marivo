"""
Pattern: declare a dataset on top of a registered datasource.
When to use: you have a datasource and want to expose one of its tables as a typed dataset.
Output shape: ms.describe of the dataset showing it bound to tiny_orders.
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

    @ms.dataset(name="orders", datasource=tiny_orders, primary_key=["order_id"])
    def orders(backend: Any) -> Any:
        return backend.table("orders")


print(ms.describe("sales.orders", project=project))

# Expected output:
# {'kind': 'dataset', 'model': 'sales', 'name': 'orders', 'datasource': 'tiny_orders'
