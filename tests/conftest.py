"""pytest configuration for the Factum test suite.

Two optimizations applied here (no test logic changes):

1. LOG_LEVEL=WARNING — suppresses ~5000 INFO log writes that would otherwise
   flood stdout during every API call.  Set LOG_LEVEL=DEBUG to re-enable.

2. DuckDBAnalyticsEngine.initialize() patch — redirects every fresh DuckDB
   initialization to copy from the persistent seeded template instead of
   rebuilding the demo data from scratch (~35 s per call).  The template itself
   is created on first use and reused across processes/workers via shared_fixtures.
   The patch is skipped for the template path itself to avoid infinite recursion.
"""
from __future__ import annotations

import os

os.environ.setdefault("LOG_LEVEL", "WARNING")

# ── Patch DuckDBAnalyticsEngine.initialize() ────────────────────────────────
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from tests.shared_fixtures import _PERSISTENT_TEMPLATE

_original_initialize = DuckDBAnalyticsEngine.initialize


def _fast_initialize(self: DuckDBAnalyticsEngine) -> None:
    """Copy seeded template instead of re-running _seed_demo_data (~35 s).

    Falls back to the original implementation when:
    - initializing the persistent template itself (avoids infinite recursion)
    """
    if self.db_path.resolve() == _PERSISTENT_TEMPLATE.resolve():
        _original_initialize(self)
        return
    from tests.shared_fixtures import get_seeded_duckdb_path
    get_seeded_duckdb_path(self.db_path)


DuckDBAnalyticsEngine.initialize = _fast_initialize
