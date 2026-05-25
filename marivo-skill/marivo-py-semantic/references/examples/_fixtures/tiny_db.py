"""Tiny in-memory DuckDB backend for marivo-py-semantic examples.

Provides:
  - ``connect()`` -- returns a fresh DuckDB ibis backend with one ``orders``
    table seeded with six rows.
  - ``new_project()`` -- returns a fresh ``SemanticProject`` plus
    a context manager that swaps the active registry to that project's
    registry; use as ``with new_project() as project: ...``.

Assumes ``marivo`` is installed (e.g. ``pip install marivo``); no
``sys.path`` manipulation is performed. Examples run with cwd=<examples
dir>, so ``_fixtures.tiny_db`` resolves automatically.
"""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import ibis

from marivo.semantic_py.registry import SemanticProject, use_registry


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


@contextmanager
def new_project(root: str = ":example:") -> Iterator[SemanticProject]:
    """Yield a fresh SemanticProject with its registry active.

    Examples that need to register a clean semantic model wrap their
    decorator calls with this context so they never pollute the global
    default registry -- re-running the example must always succeed.
    """
    project = SemanticProject(root=root)
    with use_registry(project.registry):
        yield project
    project.registry.state = "ready"
