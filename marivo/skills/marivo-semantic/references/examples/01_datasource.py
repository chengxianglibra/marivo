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

    previous = Path.cwd()
    try:
        os.chdir(root)

        spec = md.duckdb(name="warehouse", path=str(db_path))
        md.register(spec)

        md.help_text("discover_entity")
        test_result = md.test(spec.ref)
        print("datasource test:", test_result.ok)

        warehouse = spec.ref
        orders = md.table("orders")
        scope = md.unpruned(max_rows=100)

        entity_evidence = md.discover_entity(warehouse, orders, scope=scope)
        entity_evidence.show()

        dimension_evidence = md.discover_dimensions(
            warehouse,
            orders,
            columns=("region", "status"),
            scope=scope,
        )
        dimension_evidence.show()

        time_evidence = md.discover_time_dimensions(
            warehouse,
            orders,
            columns=("order_date",),
            scope=scope,
        )
        time_evidence.show()

        measure_evidence = md.discover_measures(
            warehouse,
            orders,
            columns=("amount",),
            scope=scope,
        )
        measure_evidence.show()

        values = md.discover_dimension_values(
            warehouse,
            orders,
            column="status",
            limit=5,
            scope=scope,
        )
        values.show()
    finally:
        os.chdir(previous)
