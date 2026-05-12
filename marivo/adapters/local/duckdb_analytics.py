from __future__ import annotations

import random
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import date, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

from marivo.ports.analytics import AnalyticsEngine

# DDL for the analytical (demo data) tables that live in DuckDB.
_ANALYTICS_DDL = """
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.watch_events (
    event_date DATE NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    app_version TEXT NOT NULL,
    network_type TEXT NOT NULL,
    content_type TEXT NOT NULL,
    play_duration_seconds DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.player_qoe (
    event_date DATE NOT NULL,
    session_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    app_version TEXT NOT NULL,
    network_type TEXT NOT NULL,
    content_type TEXT NOT NULL,
    first_frame_time_ms DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.ad_events (
    event_date DATE NOT NULL,
    session_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    app_version TEXT NOT NULL,
    network_type TEXT NOT NULL,
    content_type TEXT NOT NULL,
    preroll_timeout INTEGER NOT NULL,
    preroll_duration_seconds DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.recommendation_events (
    event_date DATE NOT NULL,
    session_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    app_version TEXT NOT NULL,
    network_type TEXT NOT NULL,
    content_type TEXT NOT NULL,
    impressions INTEGER NOT NULL,
    clicks INTEGER NOT NULL
);
"""


class DuckDBAnalyticsEngine(AnalyticsEngine):
    """DuckDB-backed analytics engine for tests and local development."""

    def __init__(self, db_path: str | Path) -> None:
        # Support in-memory DuckDB via ":memory:" string
        self._is_memory = str(db_path) == ":memory:"
        if self._is_memory:
            self.db_path = db_path  # Keep as string for :memory:
        else:
            self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if not self._is_memory:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        con = import_module("duckdb").connect(str(self.db_path))
        try:
            yield con
            con.commit()
        except Exception:
            with suppress(Exception):
                con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        with self._connect() as con:
            con.execute(_ANALYTICS_DDL)
            _row = con.execute("SELECT COUNT(*) FROM analytics.watch_events").fetchone()
            row_count = _row[0] if _row else 0
            if row_count == 0:
                _seed_demo_data(con)

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        with self._connect() as con:
            cursor = con.execute(sql, params or [])
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def table_exists(self, table_name: str) -> bool:
        with self._connect() as con:
            if "." in table_name:
                schema, tbl = table_name.rsplit(".", 1)
                row = con.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                    [schema, tbl],
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                    [table_name],
                ).fetchone()
            return row is not None and row[0] > 0

    def table_row_count(self, table_name: str) -> int:
        with self._connect() as con:
            _row = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            return int(_row[0]) if _row else 0


def _seed_demo_data(con: Any) -> None:
    """Seed the demo analytical data — moved verbatim from old database.py."""
    rng = random.Random(7)
    baseline_start = date(2026, 2, 7)
    current_start = date(2026, 2, 21)
    periods = [
        ("baseline", baseline_start, 14),
        ("current", current_start, 14),
    ]

    app_versions = [
        ("android", "8.3.1"),
        ("android", "8.3.0"),
        ("ios", "6.0.0"),
        ("web", "3.4.0"),
    ]
    network_types = ["wifi", "4g"]
    content_types = ["short", "long"]

    watch_rows: list[tuple[Any, ...]] = []
    qoe_rows: list[tuple[Any, ...]] = []
    ad_rows: list[tuple[Any, ...]] = []
    recommendation_rows: list[tuple[Any, ...]] = []
    session_index = 0

    for period_name, start_day, num_days in periods:
        for day_offset in range(num_days):
            current_day = start_day + timedelta(days=day_offset)
            for platform, app_version in app_versions:
                for network_type in network_types:
                    for content_type in content_types:
                        sessions_per_slice = 20 if content_type == "short" else 12
                        for _ in range(sessions_per_slice):
                            session_index += 1
                            session_id = f"demo_sess_{session_index:06d}"
                            user_id = f"user_{rng.randint(1, 450):04d}"

                            base_watch = 68.0 if content_type == "short" else 245.0
                            if platform == "android":
                                base_watch -= 8.0
                            if platform == "web":
                                base_watch -= 22.0
                            if network_type == "4g":
                                base_watch -= 10.0
                            if (
                                period_name == "current"
                                and platform == "android"
                                and app_version == "8.3.1"
                                and network_type == "4g"
                            ):
                                base_watch -= 8.0
                            if (
                                period_name == "current"
                                and platform == "android"
                                and app_version == "8.3.1"
                                and network_type == "4g"
                                and content_type == "short"
                            ):
                                base_watch -= 26.0
                            play_duration_seconds = round(
                                max(12.0, base_watch + rng.uniform(-6.0, 6.0)), 2
                            )
                            watch_rows.append(
                                (
                                    current_day,
                                    user_id,
                                    session_id,
                                    platform,
                                    app_version,
                                    network_type,
                                    content_type,
                                    play_duration_seconds,
                                )
                            )

                            base_qoe = 860.0 if network_type == "wifi" else 1100.0
                            if platform == "web":
                                base_qoe -= 110.0
                            if (
                                period_name == "current"
                                and platform == "android"
                                and app_version == "8.3.1"
                                and network_type == "4g"
                            ):
                                base_qoe += 180.0
                            if (
                                period_name == "current"
                                and platform == "android"
                                and app_version == "8.3.1"
                                and network_type == "4g"
                                and content_type == "short"
                            ):
                                base_qoe += 260.0
                            first_frame_time_ms = round(base_qoe + rng.uniform(-70.0, 70.0), 2)
                            qoe_rows.append(
                                (
                                    current_day,
                                    session_id,
                                    platform,
                                    app_version,
                                    network_type,
                                    content_type,
                                    first_frame_time_ms,
                                )
                            )

                            timeout_probability = 0.03 if network_type == "wifi" else 0.06
                            if (
                                period_name == "current"
                                and platform == "android"
                                and app_version == "8.3.1"
                                and network_type == "4g"
                            ):
                                timeout_probability += 0.06
                            if (
                                period_name == "current"
                                and platform == "android"
                                and app_version == "8.3.1"
                                and network_type == "4g"
                                and content_type == "short"
                            ):
                                timeout_probability += 0.09
                            preroll_timeout = 1 if rng.random() < timeout_probability else 0
                            preroll_duration_seconds = 7.0 if content_type == "short" else 11.0
                            ad_rows.append(
                                (
                                    current_day,
                                    session_id,
                                    platform,
                                    app_version,
                                    network_type,
                                    content_type,
                                    preroll_timeout,
                                    preroll_duration_seconds,
                                )
                            )

                            impressions = rng.randint(8, 15)
                            base_ctr = 0.19 if content_type == "short" else 0.13
                            if network_type == "4g":
                                base_ctr -= 0.01
                            if (
                                period_name == "current"
                                and platform == "android"
                                and app_version == "8.3.1"
                                and network_type == "4g"
                            ):
                                base_ctr += 0.006
                            if (
                                period_name == "current"
                                and platform == "android"
                                and app_version == "8.3.1"
                                and network_type == "4g"
                                and content_type == "short"
                            ):
                                base_ctr += 0.008
                            ctr = max(0.02, min(0.45, base_ctr + rng.uniform(-0.015, 0.015)))
                            clicks = max(1, round(impressions * ctr))
                            recommendation_rows.append(
                                (
                                    current_day,
                                    session_id,
                                    platform,
                                    app_version,
                                    network_type,
                                    content_type,
                                    impressions,
                                    clicks,
                                )
                            )

    con.executemany(
        "INSERT INTO analytics.watch_events (event_date, user_id, session_id, platform, app_version, network_type, content_type, play_duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        watch_rows,
    )
    con.executemany(
        "INSERT INTO analytics.player_qoe (event_date, session_id, platform, app_version, network_type, content_type, first_frame_time_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        qoe_rows,
    )
    con.executemany(
        "INSERT INTO analytics.ad_events (event_date, session_id, platform, app_version, network_type, content_type, preroll_timeout, preroll_duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ad_rows,
    )
    con.executemany(
        "INSERT INTO analytics.recommendation_events (event_date, session_id, platform, app_version, network_type, content_type, impressions, clicks) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        recommendation_rows,
    )
