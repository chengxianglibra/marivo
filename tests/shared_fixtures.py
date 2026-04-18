"""Shared test fixtures for seeded DuckDB templates.

The default template caches the standard demo data. Named templates extend that
baseline with deterministic test-specific tables so repeated test classes can
copy a prepared DuckDB file instead of rebuilding it.
"""

from __future__ import annotations

import fcntl
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from app.storage.duckdb_analytics import DuckDBAnalyticsEngine


@dataclass(frozen=True)
class _TemplateSpec:
    version: str
    builder: Callable[[Path], None]


def _build_default_template(db_path: Path) -> None:
    engine = DuckDBAnalyticsEngine(db_path)
    engine.initialize()


def _build_regression_8_5_template(db_path: Path) -> None:
    _build_default_template(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.reg_events (
                event_date DATE NOT NULL,
                region VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                value DOUBLE NOT NULL,
                numerator DOUBLE NOT NULL,
                denominator DOUBLE NOT NULL
            )
            """
        )
        rows: list[tuple[str, str, str, float, float, float]] = []
        base = datetime(2026, 3, 1).date()
        for day_offset in range(7):
            event_day = (base + timedelta(days=day_offset)).isoformat()
            rows.append((event_day, "us", f"us_{day_offset}", float(100 + day_offset * 10), 1.0, 1.0))
            rows.append((event_day, "eu", f"eu_{day_offset}", float(80 + day_offset * 5), 0.0, 1.0))
        con.executemany("INSERT INTO analytics.reg_events VALUES (?, ?, ?, ?, ?, ?)", rows)
    finally:
        con.close()


def _build_metric_dimension_resolution_template(db_path: Path) -> None:
    _build_default_template(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.metric_dimension_events (
                event_date DATE NOT NULL,
                user_id VARCHAR NOT NULL,
                country VARCHAR NOT NULL,
                plan VARCHAR NOT NULL,
                value DOUBLE NOT NULL
            )
            """
        )
        con.executemany(
            "INSERT INTO analytics.metric_dimension_events VALUES (?, ?, ?, ?, ?)",
            [
                ("2026-04-01", "u1", "US", "free", 10.0),
                ("2026-04-01", "u2", "CA", "pro", 20.0),
            ],
        )
    finally:
        con.close()


_TEMPLATE_SPECS: dict[str, _TemplateSpec] = {
    "default": _TemplateSpec(version="default_v1", builder=_build_default_template),
    "regression_8_5": _TemplateSpec(
        version="regression_8_5_v1",
        builder=_build_regression_8_5_template,
    ),
    "metric_dimension_resolution": _TemplateSpec(
        version="metric_dimension_resolution_v1",
        builder=_build_metric_dimension_resolution_template,
    ),
}


def _template_db_path(template_name: str) -> Path:
    spec = _TEMPLATE_SPECS[template_name]
    return Path(f"/tmp/factum_test_tpl_{spec.version}.duckdb")


def _template_lock_path(template_name: str) -> Path:
    spec = _TEMPLATE_SPECS[template_name]
    return Path(f"/tmp/factum_test_tpl_{spec.version}.lock")


# Backward-compatible aliases used by tests/conftest.py.
_PERSISTENT_TEMPLATE = _template_db_path("default")
_LOCK_FILE = _template_lock_path("default")

# In-process flags: skip lock on repeated calls within the same worker.
_TEMPLATE_READY: set[str] = set()


def is_managed_template_path(path: str | Path) -> bool:
    """Return whether *path* points at one of the shared template files."""
    if str(path) == ":memory:":
        return False
    resolved = Path(path).resolve()
    return any(resolved == _template_db_path(name).resolve() for name in _TEMPLATE_SPECS)


def get_seeded_duckdb_path(dest: Path) -> Path:
    """Return *dest* populated with the standard demo data."""
    return get_named_seeded_duckdb_path(dest, "default")


def get_named_seeded_duckdb_path(dest: Path, template_name: str) -> Path:
    """Return *dest* populated with a cached named DuckDB template.

    The first caller across all processes builds the template under `/tmp`.
    Subsequent callers copy the cached file into *dest*.
    """
    if template_name not in _TEMPLATE_SPECS:
        available = ", ".join(sorted(_TEMPLATE_SPECS))
        raise KeyError(f"Unknown DuckDB template {template_name!r}; available: {available}")

    template_path = _template_db_path(template_name)
    lock_path = _template_lock_path(template_name)

    if template_name not in _TEMPLATE_READY:
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                if not template_path.exists():
                    _TEMPLATE_SPECS[template_name].builder(template_path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
        _TEMPLATE_READY.add(template_name)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, dest)
    return dest
