"""Source evidence collection and entity preparation.

Shows: prepare an entity brief with datasource evidence and ScanScope,
then verify the authored object.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis

import marivo.datasource as md
import marivo.semantic as ms

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "orders.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, dt DATE, amount DOUBLE, paid BOOLEAN)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, true), (2, DATE '2026-07-01', 20.0, true), "
        "(3, DATE '2026-07-02', 5.0, false)"
    )
    con.disconnect()

    semantic_dir = root / "marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(
        'import marivo.semantic as ms\nms.domain(name="sales")\n'
    )

    previous = Path.cwd()
    try:
        os.chdir(root)
        md.register(md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)))
        from marivo.semantic.reader import SemanticProject

        project = SemanticProject(root=root / "marivo" / "semantic")
        project.load()

        # prepare_entity resolves datasource backends internally
        brief = project.prepare_entity(
            datasource="warehouse",
            source=ms.table("orders"),
            domain="sales",
            scope=md.ScanScope(),
        )
        print("brief status:", brief.status)
        print("schema columns:", [col.name for col in brief.table.columns])
        print("issues:", [issue.kind for issue in brief.issues])

        # Author the entity
        (semantic_dir / "_domain.py").write_text(
            "import marivo.datasource as md\n"
            "import marivo.semantic as ms\n"
            'ms.domain(name="sales")\n'
            'warehouse = md.ref("warehouse")\n'
            "orders = ms.entity(\n"
            '    name="orders",\n'
            "    datasource=warehouse,\n"
            '    source=ms.table("orders"),\n'
            '    primary_key=["order_id"],\n'
            ")\n"
        )

        # Load and verify
        project.load()
        verify = project.verify_object("sales.orders")
        print("verify status:", verify.status)
    finally:
        os.chdir(previous)
