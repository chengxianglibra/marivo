"""
Pattern: declare a dataset on top of a registered datasource.
When to use: you have a datasource and want to expose one of its tables as a typed dataset.
Output shape: project.describe of the dataset showing it bound to tiny_orders.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic as ms

# In a model file inside the semantic project:
#   ms.model(name="sales")
#   @ms.dataset(name="orders", datasource="tiny_orders", primary_key=["order_id"])
#   def orders(backend):
#       return backend.table("orders")

# --- executable demo ---
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / ".marivo" / "semantic" / "sales"
    root.mkdir(parents=True)
    datasource_dir = Path(tmp) / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "tiny_orders.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='tiny_orders', backend_type='duckdb', path=':memory:')\n"
    )
    (root / "__init__.py").write_text("")
    (root / "_model.py").write_text("import marivo.semantic as ms\nms.model(name='sales')\n")
    (root / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "@ms.dataset(name='orders', datasource='tiny_orders')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
    )
    project = ms.SemanticProject(root=str(Path(tmp) / ".marivo" / "semantic"))
    project.load()
    ds = project.describe("sales.orders")
    print(ds)
