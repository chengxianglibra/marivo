"""Shared test fixtures — provides a pre-seeded DuckDB template so that each
test class can copy the file instead of re-running _seed_demo_data() (~35 s).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app.storage.duckdb_analytics import DuckDBAnalyticsEngine

_TEMPLATE_DIR: tempfile.TemporaryDirectory | None = None
_TEMPLATE_PATH: Path | None = None


def get_seeded_duckdb_path(dest: Path) -> Path:
    """Return *dest* populated with the standard demo data.

    On the first call the DuckDB is built from scratch (~35 s).  Every
    subsequent call copies the cached template file (<0.1 s).
    """
    global _TEMPLATE_DIR, _TEMPLATE_PATH

    if _TEMPLATE_PATH is None or not _TEMPLATE_PATH.exists():
        _TEMPLATE_DIR = tempfile.TemporaryDirectory(prefix="omnidb_tpl_")
        tpl = Path(_TEMPLATE_DIR.name) / "template.duckdb"
        engine = DuckDBAnalyticsEngine(tpl)
        engine.initialize()
        _TEMPLATE_PATH = tpl

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_TEMPLATE_PATH, dest)
    return dest
