"""pytest configuration for the Factum test suite.

Two optimizations applied here (no test logic changes):

1. LOG_LEVEL=WARNING — suppresses ~5000 INFO log writes that would otherwise
   flood stdout during every API call. Set LOG_LEVEL=DEBUG to re-enable.

2. DuckDBAnalyticsEngine.initialize() patch — redirects fresh DuckDB
   initialization to copy from the cached shared template instead of
   rebuilding the demo data from scratch (~35 s per call). When a test has
   already copied a named template into place, initialize becomes a no-op so
   app startup does not overwrite the prepared file.
"""

from __future__ import annotations

import os

os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from tests.shared_fixtures import get_seeded_duckdb_path, is_managed_template_path

_original_initialize = DuckDBAnalyticsEngine.initialize


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
