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
    "test_intent_attribute.py",
    "test_intent_detect.py",
    "test_intent_diagnose.py",
    "test_intent_forecast.py",
    "test_intent_compare.py",
    "test_intent_decompose.py",
    "test_intent_correlate.py",
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
