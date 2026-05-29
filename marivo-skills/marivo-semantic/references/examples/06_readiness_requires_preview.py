"""Readiness blocks when required raw preview evidence is missing."""

from __future__ import annotations

import tempfile
from pathlib import Path

import ibis

import marivo.analysis as mv
import marivo.semantic as ms

MODEL = """
import marivo.semantic as ms
ms.model(name="sales", default=True)
"""

OBJECTS = """
import marivo.semantic as ms

@ms.dataset(datasource="warehouse", description="Orders")
def orders(backend):
    return backend.table("orders")

@ms.metric(datasets=[orders], decomposition=ms.sum(), declared_status="python_native")
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

        def backend_factory(name: str) -> object:
            return mv.datasources.build_backend(name)

        report = project.readiness(
            require_preview=True,
            backend_factory=backend_factory,
            raw_previews=(),
        )
        print(f"readiness: {report.status}")
        print(f"blocker: {report.blockers[0].kind}")
    finally:
        os.chdir(previous)
