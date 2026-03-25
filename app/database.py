from __future__ import annotations

import random
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import duckdb


class DuckDBStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(self.db_path))
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    budget_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS steps (
                    step_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    step_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    observation_type TEXT NOT NULL,
                    subject_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    significance_json TEXT NOT NULL,
                    quality_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    confidence DOUBLE NOT NULL,
                    status TEXT NOT NULL,
                    supporting_observation_ids_json TEXT NOT NULL,
                    contradicting_observation_ids_json TEXT NOT NULL,
                    confidence_breakdown_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS evidence_edges (
                    edge_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    from_node_id TEXT NOT NULL,
                    from_node_type TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    to_node_type TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    weight DOUBLE NOT NULL,
                    explanation TEXT NOT NULL,
                    match_basis_json TEXT NOT NULL DEFAULT '{}',
                    score_components_json TEXT NOT NULL DEFAULT '{}',
                    supporting_observation_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS recommendations (
                    rec_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    action_text TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    expected_impact TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    validation_metric_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS watch_events (
                    event_date DATE NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    network_type TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    play_duration_seconds DOUBLE NOT NULL
                );

                CREATE TABLE IF NOT EXISTS player_qoe (
                    event_date DATE NOT NULL,
                    session_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    network_type TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    first_frame_time_ms DOUBLE NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ad_events (
                    event_date DATE NOT NULL,
                    session_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    network_type TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    preroll_timeout INTEGER NOT NULL,
                    preroll_duration_seconds DOUBLE NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recommendation_events (
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
            )

            watch_row_count = con.execute("SELECT COUNT(*) FROM watch_events").fetchone()[0]
            if watch_row_count == 0:
                self._seed_demo_data(con)

    def table_counts(self) -> dict[str, int]:
        with self.connect() as con:
            tables = ["watch_events", "player_qoe", "ad_events", "recommendation_events"]
            return {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}

    def _seed_demo_data(self, con: duckdb.DuckDBPyConnection) -> None:
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

        watch_rows = []
        qoe_rows = []
        ad_rows = []
        recommendation_rows = []
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
                                if period_name == "current" and platform == "android" and app_version == "8.3.1" and network_type == "4g":
                                    base_watch -= 8.0
                                if (
                                    period_name == "current"
                                    and platform == "android"
                                    and app_version == "8.3.1"
                                    and network_type == "4g"
                                    and content_type == "short"
                                ):
                                    base_watch -= 26.0
                                play_duration_seconds = round(max(12.0, base_watch + rng.uniform(-6.0, 6.0)), 2)
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
                                if period_name == "current" and platform == "android" and app_version == "8.3.1" and network_type == "4g":
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
                                if period_name == "current" and platform == "android" and app_version == "8.3.1" and network_type == "4g":
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
                                if period_name == "current" and platform == "android" and app_version == "8.3.1" and network_type == "4g":
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
                                clicks = max(1, int(round(impressions * ctr)))
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
            """
            INSERT INTO watch_events (
                event_date, user_id, session_id, platform, app_version, network_type, content_type, play_duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            watch_rows,
        )
        con.executemany(
            """
            INSERT INTO player_qoe (
                event_date, session_id, platform, app_version, network_type, content_type, first_frame_time_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            qoe_rows,
        )
        con.executemany(
            """
            INSERT INTO ad_events (
                event_date, session_id, platform, app_version, network_type, content_type, preroll_timeout, preroll_duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ad_rows,
        )
        con.executemany(
            """
            INSERT INTO recommendation_events (
                event_date, session_id, platform, app_version, network_type, content_type, impressions, clicks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            recommendation_rows,
        )
