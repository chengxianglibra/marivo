"""
Pattern: declare a dataset on top of a registered datasource.
When to use: you have a datasource and want to expose one of its tables as a typed dataset.
Output shape: project.describe of the dataset showing it bound to tiny_orders.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic_py as ms

# In a model file inside the semantic project:
#   ms.model(name="sales")
#   warehouse = ms.datasource(name="tiny_orders", backend_type="duckdb")
#
#   @ms.dataset(name="orders", datasource=warehouse, primary_key=["order_id"])
#   def orders(backend):
#       return backend.table("orders")

# --- executable demo ---
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / ".marivo" / "semantic" / "sales"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "_model.py").write_text("import marivo.semantic_py as ms\nms.model(name='sales')\n")
    (root / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "warehouse = ms.datasource(name='tiny_orders', backend_type='duckdb')\n"
        "\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
    )
    project = ms.SemanticProject(root=str(Path(tmp) / ".marivo" / "semantic"))
    project.load()
    ds = project.describe("sales.orders")
    print(ds)
