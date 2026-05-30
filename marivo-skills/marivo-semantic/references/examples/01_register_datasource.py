"""
Pattern: register a single datasource.
When to use: you are starting a new semantic model and need one backend factory the rest of the model can hang off.
Output shape: list of registered global datasource ids.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic as ms

# In .marivo/datasource/tiny_orders.py:
#   import marivo.datasource as md
#   tiny_orders = md.DatasourceSpec(name="tiny_orders", backend_type="duckdb", path=":memory:")
#   md.datasource(tiny_orders)

# --- executable demo ---
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / ".marivo" / "semantic" / "sales"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "_model.py").write_text("import marivo.semantic as ms\nms.model(name='sales')\n")
    (Path(tmp) / ".marivo" / "datasource").mkdir(parents=True)
    (Path(tmp) / ".marivo" / "datasource" / "tiny_orders.py").write_text(
        "import marivo.datasource as md\n"
        "tiny_orders = md.DatasourceSpec(name='tiny_orders', backend_type='duckdb', path=':memory:')\n"
        "md.datasource(tiny_orders)\n"
    )
    project = ms.SemanticProject(root=str(Path(tmp) / ".marivo" / "semantic"))
    project.load()
    datasources = project.list_datasources()
    print([d.semantic_id for d in datasources])
