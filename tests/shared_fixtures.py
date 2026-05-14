"""Shared test fixtures for seeded DuckDB templates.

The default template caches the standard demo data. Named templates extend that
baseline with deterministic test-specific tables so repeated test classes can
copy a prepared DuckDB file instead of rebuilding it.
"""

from __future__ import annotations

import fcntl
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore

# Capture the original (unpatched) initialize before conftest.py monkeys it.
# This prevents _build_default_template from re-entering the template build
# path, which would deadlock on the fcntl file lock.
_unpatched_duckdb_initialize = DuckDBAnalyticsEngine.initialize


@dataclass(frozen=True)
class _TemplateSpec:
    version: str
    builder: Callable[[Path], None]
    validator: Callable[[Path], bool]


def _build_default_template(db_path: Path) -> None:
    engine = DuckDBAnalyticsEngine(db_path)
    # Use the original (unpatched) initialize to avoid re-entering the
    # template build path via the conftest monkey-patch, which would
    # deadlock on the fcntl file lock.
    _unpatched_duckdb_initialize(engine)


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
            rows.append(
                (event_day, "us", f"us_{day_offset}", float(100 + day_offset * 10), 1.0, 1.0)
            )
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


def _build_attribute_intent_template(db_path: Path) -> None:
    _build_default_template(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.attr_events (
                event_date DATE    NOT NULL,
                channel    VARCHAR NOT NULL,
                region     VARCHAR NOT NULL,
                value      DOUBLE  NOT NULL
            )
            """
        )
        rows: list[tuple[str, str, str, float]] = []
        for i in range(3):
            event_date = (datetime(2026, 3, 1).date() + timedelta(days=i)).isoformat()
            rows.extend(
                [
                    (event_date, "A", "X", 100.0),
                    (event_date, "B", "X", 80.0),
                    (event_date, "C", "X", 60.0),
                ]
            )
        for i in range(3):
            event_date = (datetime(2026, 2, 1).date() + timedelta(days=i)).isoformat()
            rows.extend(
                [
                    (event_date, "A", "X", 70.0),
                    (event_date, "B", "X", 60.0),
                    (event_date, "C", "X", 50.0),
                ]
            )
        con.executemany("INSERT INTO analytics.attr_events VALUES (?, ?, ?, ?)", rows)
    finally:
        con.close()


def _build_diagnose_intent_template(db_path: Path) -> None:
    _build_default_template(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.diag_events (
                event_date DATE    NOT NULL,
                channel    VARCHAR NOT NULL,
                value      DOUBLE  NOT NULL
            )
            """
        )
        rows: list[tuple[str, str, float]] = []
        base = datetime(2024, 3, 1).date()
        for i in range(10):
            event_date = (base + timedelta(days=i)).isoformat()
            for channel in ("A", "B", "C"):
                value = 700.0 if event_date == "2024-03-05" and channel == "A" else 100.0
                rows.append((event_date, channel, value))
        con.executemany("INSERT INTO analytics.diag_events VALUES (?, ?, ?)", rows)
    finally:
        con.close()


def _build_forecast_intent_template(db_path: Path) -> None:
    _build_default_template(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.forecast_events (
                event_date DATE   NOT NULL,
                value      DOUBLE NOT NULL
            )
            """
        )
        rows = [
            ((datetime(2026, 1, 1).date() + timedelta(days=i)).isoformat(), 100.0 + i * 10.0)
            for i in range(14)
        ]
        con.executemany("INSERT INTO analytics.forecast_events VALUES (?, ?)", rows)
    finally:
        con.close()


def _build_detect_intent_template(db_path: Path) -> None:
    """Pre-seeded detect_events with spike and uniform_events for detect tests."""
    _build_default_template(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        # detect_events: 14 days with spike on day 7 (500 rows vs 100 rows)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.detect_events (
                event_date DATE NOT NULL,
                cluster    VARCHAR NOT NULL,
                value      DOUBLE NOT NULL
            )
            """
        )
        rows: list[tuple[str, str, float]] = []
        base_date = datetime(2026, 1, 1).date()
        for i in range(14):
            d = (base_date + timedelta(days=i)).isoformat()
            count = 500 if i == 7 else 100
            for _ in range(count):
                rows.append((d, "alpha", 1.0))
            for _ in range(100):
                rows.append((d, "beta", 1.0))
        con.executemany("INSERT INTO analytics.detect_events VALUES (?, ?, ?)", rows)

        # uniform_events: 14 days uniform (100 rows each day)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.uniform_events (
                event_date DATE NOT NULL,
                cluster    VARCHAR NOT NULL,
                value      DOUBLE NOT NULL
            )
            """
        )
        rows = []
        for i in range(14):
            d = (base_date + timedelta(days=i)).isoformat()
            for _ in range(100):
                rows.append((d, "alpha", 1.0))
                rows.append((d, "beta", 1.0))
        con.executemany("INSERT INTO analytics.uniform_events VALUES (?, ?, ?)", rows)
    finally:
        con.close()


def _build_intent_api_template(db_path: Path) -> None:
    _build_default_template(db_path)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.intent_import_bridge_events (
                event_date DATE NOT NULL,
                user_id VARCHAR NOT NULL,
                cluster VARCHAR NOT NULL,
                value DOUBLE NOT NULL
            )
            """
        )
        con.executemany(
            "INSERT INTO analytics.intent_import_bridge_events VALUES (?, ?, ?, ?)",
            [
                ("2024-01-01", "u1", "alpha", 10.0),
                ("2024-01-02", "u2", "beta", 20.0),
                ("2024-01-03", "u3", "alpha", 30.0),
            ],
        )
    finally:
        con.close()


def _table_exists(db_path: Path, schema_name: str, table_name: str) -> bool:
    con = duckdb.connect(str(db_path))
    try:
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            """,
            [schema_name, table_name],
        ).fetchone()
    finally:
        con.close()
    return row is not None and int(row[0]) > 0


def _all_tables_exist(db_path: Path, expected_tables: list[tuple[str, str]]) -> bool:
    if not db_path.exists():
        return False
    return all(
        _table_exists(db_path, schema_name, table_name)
        for schema_name, table_name in expected_tables
    )


def _validate_default_template(db_path: Path) -> bool:
    return _all_tables_exist(
        db_path,
        [
            ("analytics", "watch_events"),
            ("analytics", "player_qoe"),
            ("analytics", "ad_events"),
            ("analytics", "recommendation_events"),
        ],
    )


def _build_template_atomically(template_name: str, template_path: Path) -> None:
    temp_path = template_path.with_suffix(f"{template_path.suffix}.building")
    with suppress(FileNotFoundError):
        temp_path.unlink()
    _TEMPLATE_SPECS[template_name].builder(temp_path)
    if not _TEMPLATE_SPECS[template_name].validator(temp_path):
        with suppress(FileNotFoundError):
            temp_path.unlink()
        raise RuntimeError(f"DuckDB template {template_name!r} failed validation after rebuild")
    temp_path.replace(template_path)


_TEMPLATE_SPECS: dict[str, _TemplateSpec] = {
    "default": _TemplateSpec(
        version="default_v1",
        builder=_build_default_template,
        validator=_validate_default_template,
    ),
    "regression_8_5": _TemplateSpec(
        version="regression_8_5_v1",
        builder=_build_regression_8_5_template,
        validator=lambda db_path: _all_tables_exist(
            db_path,
            [("analytics", "watch_events"), ("analytics", "reg_events")],
        ),
    ),
    "metric_dimension_resolution": _TemplateSpec(
        version="metric_dimension_resolution_v1",
        builder=_build_metric_dimension_resolution_template,
        validator=lambda db_path: _all_tables_exist(
            db_path,
            [("analytics", "watch_events"), ("analytics", "metric_dimension_events")],
        ),
    ),
    "attribute_intent": _TemplateSpec(
        version="attribute_intent_v2",
        builder=_build_attribute_intent_template,
        validator=lambda db_path: _all_tables_exist(
            db_path,
            [("analytics", "watch_events"), ("analytics", "attr_events")],
        ),
    ),
    "diagnose_intent": _TemplateSpec(
        version="diagnose_intent_v2",
        builder=_build_diagnose_intent_template,
        validator=lambda db_path: _all_tables_exist(
            db_path,
            [("analytics", "watch_events"), ("analytics", "diag_events")],
        ),
    ),
    "forecast_intent": _TemplateSpec(
        version="forecast_intent_v2",
        builder=_build_forecast_intent_template,
        validator=lambda db_path: _all_tables_exist(
            db_path,
            [("analytics", "watch_events"), ("analytics", "forecast_events")],
        ),
    ),
    "detect_intent": _TemplateSpec(
        version="detect_intent_v2",
        builder=_build_detect_intent_template,
        validator=lambda db_path: _all_tables_exist(
            db_path,
            [
                ("analytics", "watch_events"),
                ("analytics", "detect_events"),
                ("analytics", "uniform_events"),
            ],
        ),
    ),
    "intent_api": _TemplateSpec(
        version="intent_api_v1",
        builder=_build_intent_api_template,
        validator=lambda db_path: _all_tables_exist(
            db_path,
            [("analytics", "watch_events"), ("analytics", "intent_import_bridge_events")],
        ),
    ),
}


def _template_db_path(template_name: str) -> Path:
    spec = _TEMPLATE_SPECS[template_name]
    return Path(f"/tmp/marivo_test_tpl_{spec.version}.duckdb")


def _template_lock_path(template_name: str) -> Path:
    spec = _TEMPLATE_SPECS[template_name]
    return Path(f"/tmp/marivo_test_tpl_{spec.version}.lock")


# Backward-compatible aliases used by tests/conftest.py.
_PERSISTENT_TEMPLATE = _template_db_path("default")
_LOCK_FILE = _template_lock_path("default")

# In-process flags: skip lock on repeated calls within the same worker.
_TEMPLATE_READY: set[str] = set()

_METADATA_TEMPLATE_VERSION = "sqlite_metadata_dataset_native_grounding_v1"
_METADATA_TEMPLATE = Path(f"/tmp/marivo_test_{_METADATA_TEMPLATE_VERSION}.sqlite")
_METADATA_LOCK = Path(f"/tmp/marivo_test_{_METADATA_TEMPLATE_VERSION}.lock")
_METADATA_READY = False


class ManagedSQLiteMetadataStore(SQLiteMetadataStore):
    """SQLite metadata store backed by a TemporaryDirectory test fixture."""

    def __init__(self, db_path: str | Path, temp_dir: tempfile.TemporaryDirectory[str]) -> None:
        super().__init__(db_path)
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = temp_dir

    def close(self) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def __del__(self) -> None:
        self.close()


def make_temp_metadata_store[ManagedStoreT: ManagedSQLiteMetadataStore](
    prefix: str = "marivo_metadata_",
    store_cls: type[ManagedStoreT] = ManagedSQLiteMetadataStore,
) -> ManagedStoreT:
    """Return an initialized metadata store whose temp directory is cleaned up."""
    temp_dir = tempfile.TemporaryDirectory(prefix=prefix)
    store = store_cls(Path(temp_dir.name) / "meta.sqlite", temp_dir)
    store.initialize()
    return store


def is_managed_template_path(path: str | Path) -> bool:
    """Return whether *path* points at one of the shared template files."""
    if str(path) == ":memory:":
        return False
    resolved = Path(path).resolve()
    if resolved == _METADATA_TEMPLATE.resolve():
        return True
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
                if not _TEMPLATE_SPECS[template_name].validator(template_path):
                    _build_template_atomically(template_name, template_path)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
        _TEMPLATE_READY.add(template_name)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, dest)
    return dest


def _build_metadata_template(db_path: Path) -> None:
    from marivo.adapters.schema import METADATA_DDL, metadata_schema_marker_row

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        for ddl in METADATA_DDL:
            con.execute(ddl)
        marker = metadata_schema_marker_row("sqlite")
        con.execute(
            """
            INSERT OR IGNORE INTO metadata_schema_marker (
                backend, schema_version, ddl_fingerprint
            ) VALUES (?, ?, ?)
            """,
            [marker["backend"], marker["schema_version"], marker["ddl_fingerprint"]],
        )
        con.commit()
    finally:
        con.close()


def _metadata_template_valid(db_path: Path) -> bool:
    from marivo.adapters.schema import metadata_schema_marker_row

    if not db_path.exists():
        return False
    con = sqlite3.connect(str(db_path))
    try:
        tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('sessions', 'steps', 'artifacts', 'datasources', 'metadata_schema_marker')"
            ).fetchall()
        }
        legacy_tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('source_engine_bindings', 'sources', 'engines', 'source_execution_mappings')"
            ).fetchall()
        }
        # OSI v2 tables
        osi_v2_tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
                "('semantic_models', 'semantic_datasets', "
                "'semantic_fields', 'semantic_relationships', 'semantic_metrics', "
                "'semantic_readiness_status')"
            ).fetchall()
        }
        datasource_columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(datasources)").fetchall()
        }
        session_columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(sessions)").fetchall()
        }
        # OSI v2 model columns
        osi_model_columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(semantic_models)").fetchall()
        }
        marker_rows = {
            str(row[0])
            for row in con.execute(
                "SELECT backend FROM metadata_schema_marker WHERE backend = 'sqlite'"
            ).fetchall()
        }
        marker_row = con.execute(
            """
            SELECT backend, schema_version, ddl_fingerprint
            FROM metadata_schema_marker
            WHERE backend = 'sqlite'
            """
        ).fetchone()
    finally:
        con.close()
    expected_marker = metadata_schema_marker_row("sqlite")
    actual_marker = (
        {
            "backend": str(marker_row[0]),
            "schema_version": str(marker_row[1]),
            "ddl_fingerprint": str(marker_row[2]),
        }
        if marker_row is not None
        else None
    )
    return (
        tables
        == {
            "sessions",
            "steps",
            "artifacts",
            "datasources",
            "metadata_schema_marker",
        }
        and not legacy_tables
        and osi_v2_tables
        == {
            "semantic_models",
            "semantic_datasets",
            "semantic_fields",
            "semantic_relationships",
            "semantic_metrics",
            "semantic_readiness_status",
        }
        and {
            "datasource_type",
            "connection_json",
            "owner_user",
        }.issubset(datasource_columns)
        and {
            "owner_user",
        }.issubset(session_columns)
        and {"visibility", "owner_user"}.issubset(osi_model_columns)
        and "revision" not in osi_model_columns
        and marker_rows == {"sqlite"}
        and actual_marker == expected_marker
    )


def get_seeded_metadata_path(dest: Path) -> Path:
    """Return *dest* populated with the cached empty metadata schema."""
    global _METADATA_READY

    if not _METADATA_READY:
        with open(_METADATA_LOCK, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                if not _metadata_template_valid(_METADATA_TEMPLATE):
                    temp_path = _METADATA_TEMPLATE.with_suffix(".sqlite.building")
                    with suppress(FileNotFoundError):
                        temp_path.unlink()
                    _build_metadata_template(temp_path)
                    if not _metadata_template_valid(temp_path):
                        with suppress(FileNotFoundError):
                            temp_path.unlink()
                        raise RuntimeError(
                            "SQLite metadata template failed validation after rebuild"
                        )
                    temp_path.replace(_METADATA_TEMPLATE)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
        _METADATA_READY = True

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_METADATA_TEMPLATE, dest)
    return dest
