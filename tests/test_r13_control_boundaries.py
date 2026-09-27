"""Local execution evidence for R1.3 connection and probe controls."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from time import monotonic

import ibis
import pytest
from ibis.backends import BaseBackend

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource import adapters
from marivo.datasource.adapters import _Cursor
from marivo.datasource.backends import build_backend
from marivo.datasource.engines import duckdb as duckdb_engine
from marivo.datasource.engines import sqlite as sqlite_engine
from marivo.datasource.errors import DatasourceRawSqlError
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation


def test_duckdb_connection_settings_are_effective() -> None:
    backend = duckdb_engine.connect("local", {"path": ":memory:", "force_download": True})
    try:
        timezone, threads, force_download = backend.con.execute(
            "SELECT current_setting('TimeZone'), current_setting('threads'), "
            "current_setting('force_download')"
        ).fetchone()
        assert (timezone, threads, force_download) == ("UTC", 1, True)
    finally:
        backend.disconnect()


def test_sqlite_read_only_authorizer_rejects_write_and_closes(tmp_path: Path) -> None:
    db_path = tmp_path / "read_only.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE facts (id INTEGER)")
        connection.execute("INSERT INTO facts VALUES (1)")
    backend = sqlite_engine.connect("local", {"path": str(db_path), "read_only": True})
    connection = backend.con
    try:
        assert connection.execute("SELECT id FROM facts").fetchall() == [(1,)]
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("INSERT INTO facts VALUES (2)")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("CREATE TABLE rejected (id INTEGER)")
    finally:
        backend.disconnect()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT id FROM facts")
    with sqlite3.connect(db_path) as reopened:
        assert reopened.execute("SELECT id FROM facts").fetchall() == [(1,)]


def test_governed_probe_submits_exact_ibis_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = duckdb_engine.connect("local", {"path": ":memory:"})
    submitted: list[str] = []
    original = adapters._native_cursor

    def capture(backend_arg: BaseBackend, backend_name: str, sql: str) -> _Cursor:
        submitted.append(sql)
        return original(backend_arg, backend_name, sql)

    monkeypatch.setattr(adapters, "_native_cursor", capture)
    try:
        duckdb_engine.PROFILE.probe(backend)
        expected = backend.compile(ibis.literal(1).name("probe").as_table(), limit=None)
        assert submitted == [expected]
    finally:
        backend.disconnect()


def test_sqlite_terminal_timeout_interrupts_actual_query(tmp_path: Path) -> None:
    db_path = tmp_path / "timeout.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE facts (id INTEGER)")
    md.register(md.sqlite(name="local", path=str(db_path)), project_root=tmp_path)

    with pytest.raises(DatasourceRawSqlError) as failure:
        md.raw_sql(
            ms.ref.datasource("local"),
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n "
            "WHERE x < 100000000) SELECT sum(x) FROM n",
            reason="verify terminal interrupt",
            timeout_seconds=1,
            project_root=tmp_path,
        )
    assert failure.value.effect_observed.query_executed is True
    assert failure.value.repair is not None


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1",
    reason="opt-in PostgreSQL service",
)
def test_postgres_terminal_control_uses_server_timeout_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.multisource_environment import postgres_analysis as pg

    monkeypatch.setenv("MARIVO_R13_POSTGRES_PASSWORD", pg.password())
    datasource = DatasourceIR(
        semantic_id="r13",
        name="r13",
        backend_type="postgres",
        fields={"host": pg.HOST, "port": pg.PORT, "database": pg.DATABASE, "user": pg.READER},
        env_refs={"password": "MARIVO_R13_POSTGRES_PASSWORD"},
        ai_context=AiContextIR(),
        python_symbol="r13",
        location=DatasourceSourceLocation("r13.py", 1),
    )
    backend = build_backend(datasource, read_only=True, terminal_timeout_seconds=1)
    try:
        assert backend.con.read_only is True
        assert backend.con.info.parameter_status("TimeZone") == "UTC"
        setting = backend.raw_sql("SHOW statement_timeout")
        try:
            assert setting.fetchone() == ("1s",)
        finally:
            setting.close()
        profile = adapters.provider_for("postgres")
        assert profile.authoring_timeout is not None
        started = monotonic()
        with pytest.raises(Exception), profile.authoring_timeout(backend, 1):
            backend.raw_sql("SELECT pg_sleep(3)")
        assert monotonic() - started < 2.5
        with pytest.raises(Exception), profile.authoring_timeout(backend, 1):
            backend.raw_sql("CREATE TEMP TABLE r13_rejected (id INTEGER)")
        with profile.authoring_timeout(backend, 1):
            cursor = backend.raw_sql("SELECT 1")
            try:
                assert cursor.fetchone() == (1,)
            finally:
                cursor.close()
    finally:
        backend.disconnect()


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
)
def test_trino_terminal_session_timeout_and_timezone_are_effective() -> None:
    from trino.exceptions import TrinoQueryError

    datasource = DatasourceIR(
        semantic_id="r13",
        name="r13",
        backend_type="trino",
        fields={
            "host": "127.0.0.1",
            "port": 18080,
            "catalog": "iceberg",
            "schema": "analysis",
            "user": "analysis_reader",
        },
        env_refs={},
        ai_context=AiContextIR(),
        python_symbol="r13",
        location=DatasourceSourceLocation("r13.py", 1),
    )
    backend = build_backend(datasource, read_only=True, terminal_timeout_seconds=1)
    try:
        profile = adapters.provider_for("trino")
        assert profile.authoring_timeout is not None
        with profile.authoring_timeout(backend, 1):
            setting = backend.raw_sql("SHOW SESSION LIKE 'query_max_run_time'")
            try:
                assert setting.fetchone()[1] == "1s"
            finally:
                setting.close()
            timezone = backend.raw_sql("SELECT current_timezone()")
            try:
                assert timezone.fetchone()[0] == "UTC"
            finally:
                timezone.close()
        started = monotonic()
        with pytest.raises(TrinoQueryError) as failure, profile.authoring_timeout(backend, 1):
            backend.raw_sql(
                "SELECT SUM(a.x * b.y) FROM UNNEST(sequence(1, 10000)) a(x) "
                "CROSS JOIN UNNEST(sequence(1, 10000)) b(y)"
            )
        assert failure.value.error_name == "EXCEEDED_TIME_LIMIT"
        assert monotonic() - started < 5
        with pytest.raises(TrinoQueryError), profile.authoring_timeout(backend, 1):
            backend.raw_sql("CREATE TABLE iceberg.analysis.r13_rejected (id BIGINT)")
    finally:
        backend.disconnect()


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1",
    reason="opt-in ClickHouse service",
)
def test_clickhouse_unsettable_timeout_blocks_before_user_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource import store
    from marivo.datasource.errors import DatasourceConnectionError
    from marivo.datasource.timezone import probe_engine_timezone
    from tests.multisource_environment import clickhouse_analysis as ch
    from tests.multisource_environment.credentials import password

    ch.setup()
    monkeypatch.setenv("MARIVO_R13_CH_USER", "analysis_reader")
    monkeypatch.setenv("MARIVO_R13_CH_PASSWORD", password())
    md.register(
        md.clickhouse(
            name="ch_reader",
            host="127.0.0.1",
            port=18123,
            database="qualification",
            user_env="MARIVO_R13_CH_USER",
            password_env="MARIVO_R13_CH_PASSWORD",
        ),
        project_root=tmp_path,
    )

    with pytest.raises(DatasourceRawSqlError) as failure:
        md.raw_sql(
            ms.ref.datasource("ch_reader"),
            "SELECT 1",
            reason="verify timeout preflight",
            timeout_seconds=1,
            project_root=tmp_path,
        )
    assert failure.value.effect_observed.query_executed is False
    assert failure.value.repair is not None
    datasource = store.load_one("ch_reader", project_root=tmp_path)
    assert datasource is not None
    backend = build_backend(datasource, read_only=True)
    try:
        with pytest.raises(DatasourceConnectionError) as timezone_failure:
            probe_engine_timezone(backend)
        assert timezone_failure.value.received == "clickhouse timezone unavailable"
    finally:
        backend.disconnect()


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1", reason="opt-in MySQL service"
)
def test_mysql_unverifiable_timezone_blocks_time_sensitive_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.errors import DatasourceConnectionError
    from marivo.datasource.timezone import probe_engine_timezone
    from tests.multisource_environment import mysql_analysis as mysql

    monkeypatch.setenv("MARIVO_R13_MYSQL_PASSWORD", mysql.password())
    datasource = DatasourceIR(
        semantic_id="r13",
        name="r13",
        backend_type="mysql",
        fields={
            "host": mysql.HOST,
            "port": mysql.PORT,
            "database": mysql.DATABASE,
            "user": mysql.READER,
        },
        env_refs={"password": "MARIVO_R13_MYSQL_PASSWORD"},
        ai_context=AiContextIR(),
        python_symbol="r13",
        location=DatasourceSourceLocation("r13.py", 1),
    )
    backend = build_backend(datasource, read_only=True)
    try:
        with pytest.raises(DatasourceConnectionError) as failure:
            probe_engine_timezone(backend)
        assert failure.value.received == "mysql timezone unavailable"
    finally:
        backend.disconnect()
