"""pytest configuration for the Factum test suite.

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
"""

from __future__ import annotations

import os

os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import (
    get_seeded_duckdb_path,
    get_seeded_metadata_path,
    is_managed_template_path,
)

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
        _original_metadata_initialize(self)
        return

    get_seeded_metadata_path(self.db_path)


SQLiteMetadataStore.initialize = _fast_metadata_initialize
