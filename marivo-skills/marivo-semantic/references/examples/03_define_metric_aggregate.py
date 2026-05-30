"""
Pattern: define an aggregate metric on a dataset.
When to use: you want a metric whose value comes from a single reducer over the dataset.
Output shape: list of metric ids ending in the new metric.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic as ms

# In a model file inside the semantic project:
#   ms.model(name="sales")
#   tiny_orders = md.ref("tiny_orders")
#   @ms.dataset(name="orders", datasource=tiny_orders)
#   def orders(backend):
#       return backend.table("orders")
#
#   @ms.time_field(dataset=orders, data_type="date", granularity="day")
#   def created_at(table):
#       return table.created_at.cast("date")
#
#   @ms.metric(datasets=[orders], decomposition=ms.sum(), name="revenue")
#   def revenue(table):
#       return table.amount.sum()

# --- executable demo ---
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / ".marivo" / "semantic" / "sales"
    root.mkdir(parents=True)
    datasource_dir = Path(tmp) / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "tiny_orders.py").write_text(
        "import marivo.datasource as md\n"
        "tiny_orders = md.DatasourceSpec(name='tiny_orders', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(tiny_orders)\n"
    )
    (root / "__init__.py").write_text("")
    (root / "_model.py").write_text("import marivo.semantic as ms\nms.model(name='sales')\n")
    (root / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "tiny_orders = md.ref('tiny_orders')\n"
        "@ms.dataset(name='orders', datasource=tiny_orders)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
    project = ms.SemanticProject(root=str(Path(tmp) / ".marivo" / "semantic"))
    project.load()
    metrics = project.list_metrics()
    print([m.semantic_id for m in metrics])
