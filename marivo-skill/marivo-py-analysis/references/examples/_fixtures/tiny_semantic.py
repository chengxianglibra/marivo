"""Tiny in-memory semantic model for analysis examples.

Provides:
  - one ``orders`` table with a few rows in a fresh DuckDB instance
  - one ``tiny_orders`` datasource
  - one ``orders`` dataset
  - one ``revenue`` metric (sum of amount)
  - one optional ``region`` segment axis

``ensure_loaded()`` is idempotent: calling it twice within one process reuses
the registered semantic model and keeps the ``examples`` session attached.
"""
# mypy: disable-error-code=import-untyped

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ibis  # noqa: E402

import marivo.analysis_py as mv  # noqa: E402
import marivo.analysis_py.session.attach as session_attach  # noqa: E402
import marivo.semantic_py as ms  # noqa: E402
from marivo.analysis_py.errors import DuplicateSessionNameError  # noqa: E402
from marivo.semantic_py.registry import SemanticProject, use_registry  # noqa: E402

MODEL_NAME = "sales"
METRIC_ID = f"{MODEL_NAME}.revenue"
SESSION_NAME = "examples"
DATASOURCE_NAME = "tiny_orders"

_CON: Any | None = None
_PROJECT: SemanticProject | None = None
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


def _build_project() -> SemanticProject:
    project = SemanticProject(root=":tiny_semantic:")
    with use_registry(project.registry):
        ms.model(name=MODEL_NAME)

        @ms.datasource(name=DATASOURCE_NAME, backend_type="duckdb")
        def tiny_orders() -> None: ...

        @ms.dataset(name="orders", datasource=tiny_orders)
        def orders(backend: Any) -> Any:
            return backend.table("orders")

        @ms.time_field(dataset="orders", data_type="date", granularity="day")
        def created_at(orders: Any) -> Any:
            return orders.created_at.cast("date")

        @ms.field(dataset="orders")
        def region(orders: Any) -> Any:
            return orders.region

        @ms.metric(decomposition=ms.sum(), name="revenue")
        def revenue(orders: Any) -> Any:
            return orders.amount.sum()

    project.registry.state = "ready"
    return project


def _project() -> SemanticProject:
    global _PROJECT
    if _PROJECT is None:
        _PROJECT = _build_project()
    return _PROJECT


def _backends() -> dict[str, Any]:
    return {DATASOURCE_NAME: _connection}


def _session_root() -> Path:
    global _SESSION_ROOT
    if _SESSION_ROOT is None:
        _SESSION_ROOT = Path(tempfile.mkdtemp(prefix="marivo-py-analysis-examples-"))
    return _SESSION_ROOT


@contextmanager
def _temporary_cwd(path: Path) -> Iterator[None]:
    original = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


def ensure_loaded() -> None:
    """Register the tiny semantic model and attach a writable examples session."""
    project = _project()
    with _temporary_cwd(_session_root()):
        try:
            session = mv.session.create(name=SESSION_NAME, backends=_backends())
        except DuplicateSessionNameError:
            session = session_attach.attach(name=SESSION_NAME, backends=_backends())
    session.semantic_project = project
