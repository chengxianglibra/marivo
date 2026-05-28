"""
Pitfall: declaring a dataset whose datasource= argument has no project datasource.
When triggered: the loader reports a MISSING_DATASET_REF error because the
datasource name has no matching .marivo/datasource/*.py declaration.

Expected output:
    [missing_dataset_ref] Dataset 'sales.orders' references unknown datasource 'tiny_orders'.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic as ms

# In a model directory, if you define:
#   # _model.py
#   import marivo.semantic as ms
#   ms.model(name="sales")
#
#   # datasets.py  (BUG: no .marivo/datasource/tiny_orders.py exists!)
#   import marivo.semantic as ms
#
#   @ms.dataset(name="orders", datasource="tiny_orders")
#   def orders(backend):
#       return backend.table("orders")
#
# The loader will produce a MISSING_DATASET_REF error because
# 'tiny_orders' has no matching project datasource declaration.
#
# Fix: create .marivo/datasource/tiny_orders.py, then reference it by name:
#   @ms.dataset(datasource="tiny_orders")
#   def orders(backend):
#       return backend.table("orders")

# --- executable demo ---
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / ".marivo" / "semantic" / "sales"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "_model.py").write_text("import marivo.semantic as ms\nms.model(name='sales')\n")
    (root / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='tiny_orders')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
    )
    project = ms.SemanticProject(root=str(Path(tmp) / ".marivo" / "semantic"))
    result = project.load()
    for error in result.errors:
        print(f"[{error.kind}] {error.message}")
        if error.hint:
            print(f"  hint: {error.hint}")
