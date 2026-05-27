"""Tiny in-memory DuckDB backend for marivo-py-semantic examples.

Provides:
  - ``connect()`` -- returns a fresh DuckDB ibis backend with one ``orders``
    table seeded with six rows.

Assumes ``marivo`` is installed (e.g. ``pip install marivo``); no
``sys.path`` manipulation is performed. Examples run with cwd=<examples
dir>, so ``_fixtures.tiny_db`` resolves automatically.
"""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

from typing import Any

import ibis


def connect() -> Any:
    """Return a fresh DuckDB ibis backend with a seeded ``orders`` table."""
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2025-07-01', 10.0, 'north', 100),"
        "(2, DATE '2025-08-01', 20.0, 'south', 200),"
        "(3, DATE '2025-09-01', 30.0, 'north', 300),"
        "(4, DATE '2026-07-01', 12.0, 'north', 100),"
        "(5, DATE '2026-08-01', 24.0, 'south', 200),"
        "(6, DATE '2026-09-01', 60.0, 'north', 300)"
    )
    return con
