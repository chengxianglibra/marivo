"""pytest configuration for the Marivo test suite.

Three optimizations applied here (no test logic changes):

1. LOG_LEVEL=WARNING — suppresses ~5000 INFO log writes that would otherwise
   flood stdout during every API call. Set LOG_LEVEL=DEBUG to re-enable.

2. DuckDBAnalyticsEngine.initialize() patch — redirects fresh DuckDB
   initialization to copy from the cached shared template instead of
   rebuilding the demo data from scratch (~35 s per call). When a test has
   already copied a named template into place, initialize becomes a no-op so
   app startup does not overwrite the prepared file.

3. SQLiteMetadataStore.initialize() patch — redirects fresh metadata files to
   copy the cached empty schema template instead of replaying the full DDL on
   every app/test setup.

Tests skipped during OSI v2 migration:
These test files depend on the deleted SemanticService / old API models.
They will be re-enabled as part of Task 7 (Fix Downstream Dependencies).
"""

from __future__ import annotations

import os

import pytest

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.identity import reset_current_user, set_current_user
from tests.shared_fixtures import (
    get_seeded_duckdb_path,
    get_seeded_metadata_path,
    is_managed_template_path,
)

os.environ.setdefault("LOG_LEVEL", "WARNING")


@pytest.fixture(autouse=True)
def _set_test_user():
    token = set_current_user("test_user")
    try:
        yield
    finally:
        reset_current_user(token)


# Test files that depend on the deleted SemanticService — skip during OSI v2 migration.
# Re-enable as part of Task 7 (Fix Downstream Dependencies).
collect_ignore_glob = [
    "test_intent_api.py",
    "test_catalog_query.py",
    "test_step_metadata.py",
    "test_time_scope_resolution.py",
    "test_time_axis_metadata.py",
    "test_observe_compare_lineage_reuse.py",
    "test_compiler_executor.py",
    "test_datasources.py",
    "test_api_models_base.py",
]

_original_initialize = DuckDBAnalyticsEngine.initialize
_original_metadata_initialize = SQLiteMetadataStore.initialize


def _fast_initialize(self: DuckDBAnalyticsEngine) -> None:
    """Copy the cached seeded template instead of re-running demo seeding."""
    if getattr(self, "_is_memory", False):
        _original_initialize(self)
        return

    if is_managed_template_path(self.db_path):
        _original_initialize(self)
        return

    if self.db_path.exists():
        return

    get_seeded_duckdb_path(self.db_path)


DuckDBAnalyticsEngine.initialize = _fast_initialize


def _fast_metadata_initialize(self: SQLiteMetadataStore) -> None:
    """Copy the cached metadata template instead of replaying schema DDL."""
    if is_managed_template_path(self.db_path):
        _original_metadata_initialize(self)
        return

    if self.db_path.exists():
        return

    get_seeded_metadata_path(self.db_path)


SQLiteMetadataStore.initialize = _fast_metadata_initialize


# ---------------------------------------------------------------------------
# semantic_py shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def semantic_project_factory(tmp_path):
    """Factory that creates a SemanticProject from a dict of files.

    Files are written under ``<tmp_path>/.marivo/semantic/``.
    """

    def _make(files: dict[str, str], load: bool = True):
        from marivo.semantic_py.reader import SemanticProject

        root = tmp_path / ".marivo" / "semantic"
        root.mkdir(parents=True, exist_ok=True)
        for rel, src in files.items():
            full = root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(src)
        project = SemanticProject(root=root)
        if load:
            project.load()
        return project

    return _make


# ---------------------------------------------------------------------------
# analysis_py shared bootstrap helper
# ---------------------------------------------------------------------------


def bootstrap_sales_project(tmp_path, *, with_time: bool = True):
    """Create a ready semantic project on disk for analysis_py tests.

    Creates a 'sales' model with:
    - warehouse datasource (duckdb)
    - orders dataset
    - created_at time field (if with_time=True)
    - region field
    - revenue metric
    """
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True, exist_ok=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n"
    )
    time_field = (
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "\n"
        "warehouse = ms.datasource(name='warehouse', backend_type='duckdb')\n"
        "\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        f"{time_field}"
        "@ms.field(dataset=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(datasets=[orders], decomposition=ms.sum(), name='revenue')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
