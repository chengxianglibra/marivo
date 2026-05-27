"""Tiny in-memory semantic model for analysis examples.

Provides:
  - one ``orders`` table with a few rows in a fresh DuckDB instance
  - one ``tiny_orders`` datasource
  - one ``orders`` dataset
  - one ``revenue`` metric (sum of amount)
  - one optional ``region`` segment axis

``ensure_loaded()`` is idempotent: calling it twice within one process reuses
the registered semantic model and keeps the ``examples`` session attached.

This fixture creates a temporary project on disk under .marivo/semantic/
and uses the standard loader pipeline to build the semantic model.
"""
# mypy: disable-error-code=import-untyped

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import ibis

import marivo.analysis_py as mv

MODEL_NAME = "sales"
METRIC_ID = f"{MODEL_NAME}.revenue"
SESSION_NAME = "examples"
DATASOURCE_NAME = "tiny_orders"

_CON: Any | None = None
_SESSION_ROOT: Path | None = None


def _seed_connection() -> Any:
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


def _connection() -> Any:
    global _CON
    if _CON is None:
        _CON = _seed_connection()
    return _CON


def _session_root() -> Path:
    global _SESSION_ROOT
    if _SESSION_ROOT is None:
        _SESSION_ROOT = Path(tempfile.mkdtemp(prefix="marivo-py-analysis-examples-"))
    return _SESSION_ROOT


def _bootstrap_semantic_project(root: Path) -> None:
    """Write a minimal semantic project to disk so the loader can find it."""
    datasource_dir = root / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / f"{DATASOURCE_NAME}.py").write_text(
        "import marivo.datasource_py as md\n"
        f"md.datasource(name='{DATASOURCE_NAME}', backend_type='duckdb', path=':memory:')\n"
    )
    semantic_dir = root / ".marivo" / "semantic" / MODEL_NAME
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    (semantic_dir / "definitions.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        f"@ms.dataset(name='orders', datasource='{DATASOURCE_NAME}')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def created_at(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.field(dataset=orders)\n"
        "def region(orders):\n"
        "    return orders.region\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


@contextmanager
def _temporary_cwd(path: Path) -> Iterator[None]:
    original = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


def _backends() -> dict[str, Any]:
    return {DATASOURCE_NAME: _connection}


def ensure_loaded(*, tz: str = "UTC", default_calendar: str | None = None) -> Any:
    """Register the tiny semantic model and attach a writable examples session."""
    root = _session_root()
    _bootstrap_semantic_project(root)
    with _temporary_cwd(root):
        return mv.session.get_or_create(
            name=SESSION_NAME,
            tz=tz,
            default_calendar=default_calendar,
            backends=_backends(),
        )
