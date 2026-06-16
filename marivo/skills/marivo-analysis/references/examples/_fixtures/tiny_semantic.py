"""Tiny in-memory semantic domain for analysis examples.

Provides:
  - one ``orders`` table with a few rows in a fresh DuckDB instance
  - one ``tiny_orders`` datasource
  - one ``orders`` entity
  - one ``revenue`` metric (sum of amount)
  - one optional ``region`` segment axis

``ensure_loaded()`` is idempotent: calling it twice within one process reuses
the registered semantic domain and keeps the ``examples`` session attached.

This fixture creates a temporary project on disk under models/semantic/
and uses the standard loader pipeline to build the semantic domain.
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

import marivo.analysis as mv

DOMAIN_NAME = "sales"
METRIC_ID = f"{DOMAIN_NAME}.revenue"
DERIVED_RATIO_METRIC_ID = f"{DOMAIN_NAME}.failure_rate"
SESSION_NAME = "examples"
DATASOURCE_NAME = "tiny_orders"

_CON: Any | None = None
_SESSION_ROOT: Path | None = None


def _seed_connection() -> Any:
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER, state VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2025-07-01', 10.0, 'north', 100, 'FAILED'),"
        "(2, DATE '2025-08-01', 20.0, 'south', 200, 'SUCCEEDED'),"
        "(3, DATE '2025-09-01', 30.0, 'north', 300, 'SUCCEEDED'),"
        "(4, DATE '2026-07-01', 12.0, 'north', 100, 'FAILED'),"
        "(5, DATE '2026-08-01', 24.0, 'south', 200, 'FAILED'),"
        "(6, DATE '2026-09-01', 60.0, 'north', 300, 'SUCCEEDED')"
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
        _SESSION_ROOT = Path(tempfile.mkdtemp(prefix="marivo-analysis-examples-"))
    return _SESSION_ROOT


def _bootstrap_semantic_layer(root: Path) -> None:
    """Write a minimal semantic project to disk so the loader can find it."""
    (root / "marivo.toml").write_text('[project]\nname = "examples"\n')
    datasource_dir = root / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / f"{DATASOURCE_NAME}.py").write_text(
        "import marivo.datasource as md\n"
        f"{DATASOURCE_NAME} = md.DatasourceSpec(name='{DATASOURCE_NAME}', backend_type='duckdb', path=':memory:')\n"
        f"md.datasource({DATASOURCE_NAME})\n"
    )
    semantic_dir = root / "models" / "semantic" / DOMAIN_NAME
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales')\n"
    )
    (semantic_dir / "definitions.py").write_text(
        "import marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        f"{DATASOURCE_NAME} = md.ref('{DATASOURCE_NAME}')\n"
        "\n"
        f"orders = ms.entity(name='orders', datasource={DATASOURCE_NAME}, source=ms.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, data_type='date', granularity='day')\n"
        "def created_at(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region\n"
        "\n"
        "@ms.simple_metric(entities=[orders], additivity='additive', name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "\n"
        "@ms.simple_metric(entities=[orders], additivity='additive')\n"
        "def failed_count(orders):\n"
        "    return (orders.state == 'FAILED').cast('int64').sum()\n"
        "\n"
        "@ms.simple_metric(entities=[orders], additivity='additive')\n"
        "def total_count(orders):\n"
        "    return orders.count()\n"
        "\n"
        "failure_rate = ms.ratio(\n"
        '    name="failure_rate",\n'
        "    numerator='sales.failed_count',\n"
        "    denominator='sales.total_count',\n"
        ")\n"
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


def ensure_loaded(*, default_calendar: str | None = None) -> Any:
    """Register the tiny semantic domain and attach a writable examples session."""
    root = _session_root()
    _bootstrap_semantic_layer(root)
    with _temporary_cwd(root):
        return mv.session.get_or_create(
            name=SESSION_NAME,
            default_calendar=default_calendar,
            backends=_backends(),
        )
