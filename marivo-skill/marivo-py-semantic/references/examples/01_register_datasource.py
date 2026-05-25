"""
Pattern: register a single datasource.
When to use: you are starting a new semantic model and need one backend factory the rest of the model can hang off.
Output shape: list of registered datasource ids (one entry, qualified by model).
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


print(ms.list_datasources(project))

# Expected output:
# ['sales.tiny_orders']
