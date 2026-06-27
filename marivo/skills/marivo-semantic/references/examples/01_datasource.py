"""Datasource-first discovery flow for semantic authoring."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ibis

import marivo.datasource as md

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql(
        "CREATE TABLE orders ("
        "order_id INTEGER, customer_id INTEGER, order_date DATE, "
        "region VARCHAR, status VARCHAR, amount DOUBLE)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, 10, DATE '2026-06-01', 'US', 'paid', 100.0), "
        "(2, 20, DATE '2026-06-02', 'CA', 'paid', 50.0), "
        "(3, 10, DATE '2026-06-03', 'US', 'refunded', 25.0)"
    )
    con.disconnect()

    datasource_dir = root / "models" / "datasources"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(db_path)!r})\n"
    )

    previous = Path.cwd()
    try:
        os.chdir(root)

        md.help("discover_entity", print=False)
        test_result = md.test("warehouse")
        print("datasource test:", test_result.ok)

        warehouse = md.ref("warehouse")
        orders = md.table("orders")
        scope = md.unpruned(max_rows=100)

        entity_evidence = md.discover_entity(warehouse, orders, scope=scope)
        print("entity table:", entity_evidence.table)

        dimension_evidence = md.discover_dimensions(
            warehouse,
            orders,
            columns=("region", "status"),
            scope=scope,
        )
        print("dimension columns:", [column.column for column in dimension_evidence.columns])

        time_evidence = md.discover_time_dimensions(
            warehouse,
            orders,
            columns=("order_date",),
            scope=scope,
        )
        print("time columns:", [column.column for column in time_evidence.columns])

        measure_evidence = md.discover_measures(
            warehouse,
            orders,
            columns=("amount",),
            scope=scope,
        )
        print("measure columns:", [column.column for column in measure_evidence.columns])

        values = md.discover_dimension_values(
            warehouse,
            orders,
            column="status",
            limit=5,
            scope=scope,
        )
        print("status values:", [fact.value for fact in values.values])
    finally:
        os.chdir(previous)
