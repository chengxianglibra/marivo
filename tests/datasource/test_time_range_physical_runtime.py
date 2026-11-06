"""Native physical acquisition boundaries are independent of source timezone defaults."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.errors import DatasourceAuthoringError

pytestmark = pytest.mark.runtime


@contextmanager
def _native_times(backend: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    table = "time_range_" + uuid4().hex
    if backend == "mysql":
        from tests.datasource.environment import mysql_analysis as mysql

        monkeypatch.setenv("TIME_RANGE_USER", mysql.READER)
        monkeypatch.setenv("TIME_RANGE_PASSWORD", mysql.password())
        md.register(
            md.mysql(
                name="warehouse",
                host=mysql.HOST,
                port=mysql.PORT,
                database=mysql.DATABASE,
                user=md.env("TIME_RANGE_USER"),
                password=md.env("TIME_RANGE_PASSWORD"),
                wall_clock_timezone="Asia/Tokyo",
            ),
            project_root=root,
        )
        with mysql.connection(admin=True) as connection, connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
            cursor.execute(
                f"CREATE TABLE {table} (id INT, instant TIMESTAMP(6), wall_clock DATETIME(6), day DATE)"
            )
            try:
                cursor.execute(
                    f"INSERT INTO {table} VALUES "
                    "(1,'2026-01-01 12:00:00','2026-01-01 12:00:00','2026-01-01'),"
                    "(2,'2026-01-02 00:00:00','2026-01-02 00:00:00','2026-01-02')"
                )
                yield table
            finally:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
    else:
        from tests.datasource.environment import clickhouse_analysis as clickhouse
        from tests.datasource.environment.credentials import password

        port = int(os.environ.get("MARIVO_CLICKHOUSE_TEST_PORT", "18123"))
        monkeypatch.setenv("TIME_RANGE_USER", "analysis_reader")
        monkeypatch.setenv("TIME_RANGE_PASSWORD", password())
        md.register(
            md.clickhouse(
                name="warehouse",
                host="127.0.0.1",
                port=port,
                database="qualification",
                user=md.env("TIME_RANGE_USER"),
                password=md.env("TIME_RANGE_PASSWORD"),
                wall_clock_timezone="Asia/Tokyo",
            ),
            project_root=root,
        )
        with clickhouse.connection(admin=True, port=port) as ch_connection:
            ch_connection.command(
                f"CREATE TABLE {table} (id Int64, instant DateTime, day Date) ENGINE=MergeTree ORDER BY id"
            )
            try:
                ch_connection.command(
                    f"INSERT INTO {table} VALUES "
                    "(1,toDateTime(1767268800),'2026-01-01'),"
                    "(2,toDateTime(1767312000),'2026-01-02')"
                )
                yield table
            finally:
                ch_connection.command(f"DROP TABLE IF EXISTS {table}")


@pytest.mark.parametrize("backend", ("mysql", "clickhouse"))
@pytest.mark.parametrize("projected", (False, True))
def test_native_time_range_matches_physical_instants_and_dates_before_business_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, projected: bool
) -> None:
    flag = {
        "mysql": "MARIVO_MYSQL_ANALYSIS_TEST",
        "clickhouse": "MARIVO_CLICKHOUSE_ANALYSIS_TEST",
    }[backend]
    if os.environ.get(flag) != "1":
        pytest.skip(f"Explicit ready service required: {flag}=1")
    queries = 0
    execute = SourceSession.execute

    def counted_execute(session: SourceSession, read: CompiledRead) -> object:
        nonlocal queries
        queries += 1
        return execute(session, read)

    monkeypatch.setattr(SourceSession, "execute", counted_execute)
    monkeypatch.chdir(tmp_path)
    with _native_times(backend, tmp_path, monkeypatch) as table:
        bindings = {"key": "id", "point": "instant", "business_day": "day"}
        if backend == "mysql":
            bindings["local_time"] = "wall_clock"
        source = md.table(table, columns=bindings) if projected else md.table(table)
        inspection = md.inspect(ms.ref.datasource("warehouse"), source, workspace_dir=tmp_path)
        key, instant, day, wall = (
            ("key", "point", "business_day", "local_time")
            if projected
            else ("id", "instant", "day", "wall_clock")
        )
        valid = [
            (instant, "2026-01-01T08:00:00+08:00", "2026-01-02T08:00:00+08:00"),
            (day, "2026-01-01", "2026-01-02"),
        ]
        if backend == "mysql":
            valid += [
                (wall, "2026-01-01", "2026-01-02"),
                (wall, "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
            ]
        for column, start, end in valid:
            snapshot = inspection.sample(
                scope=md.time_range(column, start=start, end=end, max_rows=5, timeout_seconds=10),
                columns=(key,),
                persist_values=True,
                refresh=True,
            )
            assert [row[key] for row in snapshot.retained_values] == [1]
            if column == instant:
                assert snapshot.coverage.pushed_predicate == (
                    (
                        "time_range",
                        instant,
                        "2026-01-01T00:00:00+00:00",
                        "2026-01-02T00:00:00+00:00",
                    ),
                )
        before_mismatches = queries
        invalid = [
            (instant, "2026-01-01", "2026-01-02"),
            (instant, "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
            (day, "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
            (day, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
        ]
        if backend == "mysql":
            invalid.append((wall, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"))
        for column, start, end in invalid:
            with pytest.raises(DatasourceAuthoringError) as caught:
                inspection.sample(
                    scope=md.time_range(
                        column, start=start, end=end, max_rows=5, timeout_seconds=10
                    ),
                    columns=(key,),
                    refresh=True,
                )
            assert caught.value.code == "time_range_boundary_mismatch"
            assert caught.value.effect_observed is not None
            assert caught.value.effect_observed.query_executed is False
        assert queries == before_mismatches


@pytest.mark.parametrize("backend", ("mysql", "clickhouse"))
def test_native_wall_clock_localization_keeps_non_null_gap_fold_values(backend: str) -> None:
    """Executor-owned gap/fold choices can differ while localization stays non-null."""
    flag = {"mysql": "MARIVO_MYSQL_ANALYSIS_TEST", "clickhouse": "MARIVO_CLICKHOUSE_ANALYSIS_TEST"}[
        backend
    ]
    if os.environ.get(flag) != "1":
        pytest.skip(f"Explicit ready service required: {flag}=1")
    if backend == "mysql":
        from tests.datasource.environment import mysql_analysis as mysql

        with mysql.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT CONVERT_TZ('2026-03-08 02:30:00', 'America/New_York', '+00:00'), "
                "CONVERT_TZ('2026-11-01 01:30:00', 'America/New_York', '+00:00'), "
                "CONVERT_TZ(NULL, 'America/New_York', '+00:00')"
            )
            assert cursor.fetchone() == (
                datetime(2026, 3, 8, 7),
                datetime(2026, 11, 1, 5, 30),
                None,
            )
    else:
        from tests.datasource.environment.clickhouse_analysis import (
            connection as clickhouse_connection,
        )

        with clickhouse_connection() as client:
            row = client.query(
                "SELECT toUnixTimestamp64Micro(toDateTime64('2026-03-08 02:30:00', 6, 'America/New_York')), "
                "toUnixTimestamp64Micro(toDateTime64('2026-11-01 01:30:00', 6, 'America/New_York')), "
                "toUnixTimestamp64Micro(toDateTime64(CAST(NULL AS Nullable(String)), 6, 'America/New_York'))"
            ).first_row
            assert row == (
                int(datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc).timestamp()) * 1_000_000,
                int(datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc).timestamp()) * 1_000_000,
                None,
            )
