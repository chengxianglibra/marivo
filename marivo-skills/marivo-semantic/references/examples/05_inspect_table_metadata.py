"""Inspect table metadata before semantic authoring."""

from __future__ import annotations

import tempfile
from pathlib import Path

import ibis

import marivo.analysis as mv
import marivo.datasource as md

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db_path = root / "orders.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.raw_sql("CREATE TABLE orders (order_id INTEGER NOT NULL, amount DOUBLE)")
    con.raw_sql("COMMENT ON TABLE orders IS 'One row per order'")
    con.raw_sql("COMMENT ON COLUMN orders.amount IS 'Gross order amount in USD'")
    con.disconnect()

    previous = Path.cwd()
    try:
        import os

        os.chdir(root)
        mv.datasources.register(
            md.DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path))
        )
        metadata = mv.datasources.inspect_table("warehouse", table="orders")
        print(
            f"metadata: {metadata.table} columns={len(metadata.columns)} comment={metadata.comment}"
        )
    finally:
        os.chdir(previous)
