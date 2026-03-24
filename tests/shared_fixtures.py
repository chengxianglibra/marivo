"""Shared test fixtures — provides a pre-seeded DuckDB template so that each
test class can copy the file instead of re-running _seed_demo_data() (~35 s).
"""

from __future__ import annotations

import fcntl
import shutil
from pathlib import Path

from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

# Bump suffix when _seed_demo_data() or _ANALYTICS_DDL changes to force rebuild.
_PERSISTENT_TEMPLATE = Path("/tmp/factum_test_tpl_v1.duckdb")
_LOCK_FILE = Path("/tmp/factum_test_tpl_v1.lock")

# In-process flag: skip lock on repeated calls within the same worker.
_TEMPLATE_READY: bool = False


def get_seeded_duckdb_path(dest: Path) -> Path:
    """Return *dest* populated with the standard demo data.

    On the very first call across all processes, builds DuckDB (~35 s) and
    saves to a fixed /tmp path.  Every subsequent call copies the cached file
    (<0.1 s).  A POSIX advisory lock prevents simultaneous builds by parallel
    pytest-xdist workers.
    """
    global _TEMPLATE_READY

    if not _TEMPLATE_READY:
        with open(_LOCK_FILE, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                if not _PERSISTENT_TEMPLATE.exists():
                    engine = DuckDBAnalyticsEngine(_PERSISTENT_TEMPLATE)
                    engine.initialize()
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
        _TEMPLATE_READY = True

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_PERSISTENT_TEMPLATE, dest)
    return dest
