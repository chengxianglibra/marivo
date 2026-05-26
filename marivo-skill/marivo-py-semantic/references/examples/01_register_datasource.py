"""
Pattern: register a single datasource.
When to use: you are starting a new semantic model and need one backend factory the rest of the model can hang off.
Output shape: list of registered datasource ids (one entry, qualified by model).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import marivo.semantic_py as ms

# In a _model.py file inside the semantic project:
#   ms.model(name="sales")
# In a sibling .py file:
#   warehouse = ms.datasource(name="tiny_orders", backend_type="duckdb")

# --- executable demo ---
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / ".marivo" / "semantic" / "sales"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "_model.py").write_text("import marivo.semantic_py as ms\nms.model(name='sales')\n")
    (root / "datasources.py").write_text(
        "import marivo.semantic_py as ms\n"
        "warehouse = ms.datasource(name='tiny_orders', backend_type='duckdb')\n"
    )
    project = ms.SemanticProject(root=str(Path(tmp) / ".marivo" / "semantic"))
    project.load()
    datasources = project.list_datasources()
    print([d.semantic_id for d in datasources])
