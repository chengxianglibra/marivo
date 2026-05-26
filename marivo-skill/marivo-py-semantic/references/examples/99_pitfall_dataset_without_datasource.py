"""
Pitfall: declaring a dataset whose datasource= argument was never registered.
When triggered: the loader reports a MISSING_DATASET_REF error because the
datasource semantic_id has no matching ms.datasource() declaration.

Expected output:
    [missing_dataset_ref] Dataset 'sales.orders' references unknown datasource 'sales.tiny_orders'.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic_py as ms

# In a model directory, if you define:
#   # _model.py
#   import marivo.semantic_py as ms
#   ms.model(name="sales")
#
#   # datasets.py  (BUG: no ms.datasource declared!)
#   import marivo.semantic_py as ms
#
#   @ms.dataset(name="orders", datasource="sales.tiny_orders")
#   def orders(backend):
#       return backend.table("orders")
#
# The loader will produce a MISSING_DATASET_REF error because
# 'sales.tiny_orders' has no matching ms.datasource() declaration.
#
# Fix: declare the datasource first:
#   warehouse = ms.datasource(name="tiny_orders", backend_type="duckdb")
#
#   @ms.dataset(datasource=warehouse)
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
        "\n"
        "@ms.dataset(name='orders', datasource='sales.tiny_orders')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
    )
    project = ms.SemanticProject(root=str(Path(tmp) / ".marivo" / "semantic"))
    result = project.load()
    for error in result.errors:
        print(f"[{error.kind}] {error.message}")
        if error.hint:
            print(f"  hint: {error.hint}")
