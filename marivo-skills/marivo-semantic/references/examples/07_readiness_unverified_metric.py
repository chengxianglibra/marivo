"""Unverified metrics appear in readiness."""

from __future__ import annotations

import tempfile
from pathlib import Path

import ibis

import marivo.analysis as mv
import marivo.semantic as ms

MODEL = "import marivo.semantic as ms\nms.model(name='sales', default=True)\n"
OBJECTS = """
import marivo.semantic as ms

@ms.dataset(datasource="warehouse", description="Orders")
def orders(backend):
    return backend.table("orders")

@ms.metric(datasets=[orders], decomposition=ms.sum())
def revenue(table):
    return table.amount.sum()
"""


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "orders.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql("CREATE TABLE orders (amount DOUBLE)")
    con.raw_sql("INSERT INTO orders VALUES (10.0)")
    con.disconnect()

    (root / ".marivo" / "semantic" / "sales").mkdir(parents=True)
    (root / ".marivo" / "semantic" / "sales" / "_model.py").write_text(MODEL)
    (root / ".marivo" / "semantic" / "sales" / "objects.py").write_text(OBJECTS)

    previous = Path.cwd()
    try:
        import os

        os.chdir(root)
        mv.datasources.register("warehouse", backend_type="duckdb", path=str(db_path))
        project = ms.SemanticProject(root=root / ".marivo" / "semantic")
        project.load()
        report = project.readiness(require_preview=False, strict_provenance=True)
        print(f"readiness: {report.status}")
        print(f"unverified: {report.parity_summary.unverified_metrics[0]}")
    finally:
        os.chdir(previous)
