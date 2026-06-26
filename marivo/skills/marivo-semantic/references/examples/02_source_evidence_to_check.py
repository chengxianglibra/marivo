"""Source evidence collection and entity preparation.

Shows: prepare an entity brief with datasource evidence and a scope helper,
then verify the authored object.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.authoring import _DuckDBSpec

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

    semantic_dir = root / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(
        'import marivo.semantic as ms\nms.domain(name="sales")\n'
    )

    previous = Path.cwd()
    try:
        os.chdir(root)
        md.register(_DuckDBSpec(name="warehouse", path=str(db_path)))

        warehouse = md.ref("warehouse")
        orders_source = md.table("orders")
        scope = md.latest_partition()

        # Discovery-first evidence: gather bounded datasource evidence before
        # authoring. Each call below returns a result object with .show().
        entity_evidence = md.discover_entity(warehouse, orders_source, scope=scope)
        entity_evidence.show()

        dimension_evidence = md.discover_dimensions(
            warehouse,
            orders_source,
            columns=("paid",),
            scope=scope,
        )
        dimension_evidence.show()

        time_evidence = md.discover_time_dimensions(
            warehouse,
            orders_source,
            columns=("dt",),
            scope=scope,
        )
        time_evidence.show()

        measure_evidence = md.discover_measures(
            warehouse,
            orders_source,
            columns=("amount",),
            scope=scope,
        )
        measure_evidence.show()

        value_evidence = md.discover_dimension_values(
            warehouse,
            orders_source,
            column="paid",
            limit=10,
            scope=scope,
        )
        value_evidence.show()

        diagnostic = md.raw_sql(
            warehouse,
            "SELECT paid, COUNT(*) AS n FROM orders GROUP BY paid",
            reason="confirm current paid flag distribution before asking for business meaning",
        )
        diagnostic.show()

        # prepare_entity resolves datasource backends internally
        brief = ms.prepare_entity(
            datasource="warehouse",
            source=ms.table("orders"),
            domain="sales",
            scope=md.latest_partition(),
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

        # Verify — ms.verify_object reloads the project automatically
        verify = ms.verify_object("sales.orders")
        print("verify status:", verify.status)
    finally:
        os.chdir(previous)
