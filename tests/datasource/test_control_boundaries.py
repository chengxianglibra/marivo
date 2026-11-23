"""Local execution evidence for R1.3 connection and probe controls."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from time import monotonic
from uuid import uuid4

import ibis
import ibis.expr.types as ir
import pytest
from ibis.backends import BaseBackend
from trino.exceptions import TrinoQueryError

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.engines import duckdb as duckdb_engine
from marivo.datasource.engines.mysql import verify_source_timezone
from marivo.datasource.errors import DatasourceConnectionError, DatasourceRawSqlError
from marivo.datasource.ir import AiContextIR, DatasourceIR, DatasourceSourceLocation
from marivo.datasource.runtime import adapters as adapters
from marivo.datasource.runtime.backends import build_backend
from marivo.datasource.runtime.session_factory import probe_backend
from tests.datasource.environment import clickhouse_analysis as ch
from tests.datasource.environment import mysql_analysis as mysql
from tests.datasource.environment import postgres_analysis as pg
from tests.datasource.environment.credentials import password


def test_duckdb_connection_settings_are_effective() -> None:
    backend = duckdb_engine.connect("local", {"path": ":memory:", "force_download": True})
    try:
        timezone, threads, force_download, print_progress = backend.con.execute(
            "SELECT current_setting('TimeZone'), current_setting('threads'), "
            "current_setting('force_download'), current_setting('enable_progress_bar_print')"
        ).fetchone()
        assert (timezone, threads, force_download, print_progress) == ("UTC", 1, True, False)
    finally:
        backend.disconnect()


def test_duckdb_production_connection_uses_eight_threads() -> None:
    # A fresh interpreter excludes pytest's independent one-thread worker cap.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from marivo.datasource.engines.duckdb import connect; "
            "b=connect('local', {'path': ':memory:', 'threads': 2}); "
            "print(b.con.execute(\"select current_setting('threads')\").fetchone()[0]); "
            "b.disconnect()",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "8"


def test_governed_probe_submits_exact_ibis_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = duckdb_engine.connect("local", {"path": ":memory:"})
    submitted: list[str] = []
    original = type(backend).execute

    def capture(
        backend_arg: BaseBackend,
        expression: ir.Expr,
        *,
        params: object = None,
        limit: int | str | None = None,
        **kwargs: object,
    ) -> object:
        submitted.append(backend_arg.compile(expression, params=params, limit=limit))
        return original(backend_arg, expression, params=params, limit=limit, **kwargs)

    monkeypatch.setattr(type(backend), "execute", capture)
    try:
        probe_backend(duckdb_engine.PROFILE.name, backend)
        expected = backend.compile(ibis.literal(1).name("probe").as_table(), limit=None)
        assert submitted == [expected]
    finally:
        backend.disconnect()


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1",
    reason="opt-in PostgreSQL service",
)
def test_postgres_terminal_control_uses_server_timeout_and_autocommit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setenv("MARIVO_SOURCE_CONTROL_POSTGRES_PASSWORD", pg.password())
    datasource = DatasourceIR(
        semantic_id="source_control",
        name="source_control",
        backend_type="postgres",
        fields={"host": pg.HOST, "port": pg.PORT, "database": pg.DATABASE, "user": pg.READER},
        env_refs={"password": "MARIVO_SOURCE_CONTROL_POSTGRES_PASSWORD"},
        ai_context=AiContextIR(),
        python_symbol="source_control",
        location=DatasourceSourceLocation("source_control.py", 1),
    )
    backend = build_backend(datasource, terminal_timeout_seconds=1)
    try:
        assert backend.con.autocommit is True
        assert backend.con.info.parameter_status("TimeZone") == "UTC"
        setting = backend.raw_sql("SHOW statement_timeout")
        try:
            assert setting.fetchone() == ("1s",)
        finally:
            setting.close()
        profile = adapters.provider_for("postgres")
        assert profile.native_query_timeout is not None
        started = monotonic()
        with pytest.raises(Exception), profile.native_query_timeout(backend, 1):
            backend.raw_sql("SELECT pg_sleep(3)")
        assert monotonic() - started < 2.5
        with pytest.raises(Exception), profile.native_query_timeout(backend, 1):
            backend.raw_sql("CREATE TEMP TABLE source_control_rejected (id INTEGER)")
        with profile.native_query_timeout(backend, 1):
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

    datasource = DatasourceIR(
        semantic_id="source_control",
        name="source_control",
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
        python_symbol="source_control",
        location=DatasourceSourceLocation("source_control.py", 1),
    )
    profile = adapters.provider_for("trino")
    assert profile.native_query_timeout is not None
    # Inspect server-side session settings independently of the 1s timeout
    # exercised below, so metadata planning is not itself the timing oracle.
    inspection = build_backend(datasource, terminal_timeout_seconds=5)
    try:
        with profile.native_query_timeout(inspection, 5):
            setting = inspection.raw_sql("SHOW SESSION LIKE 'query_max_run_time'")
            try:
                assert setting.fetchone()[1] == "5s"
            finally:
                setting.close()
            timezone = inspection.raw_sql("SELECT current_timezone()")
            try:
                assert timezone.fetchone()[0] == "UTC"
            finally:
                timezone.close()
    finally:
        inspection.disconnect()

    backend = build_backend(datasource, terminal_timeout_seconds=1)
    try:
        assert backend.con.session_properties["query_max_run_time"] == "1s"
        started = monotonic()
        with pytest.raises(TrinoQueryError) as failure, profile.native_query_timeout(backend, 1):
            # The 3x cross join cannot finish inside the 1s limit; a lighter
            # pair has completed within the enforcement window on fast hosts.
            backend.raw_sql(
                "SELECT SUM(a.x * b.y * c.z) FROM UNNEST(sequence(1, 10000)) a(x) "
                "CROSS JOIN UNNEST(sequence(1, 10000)) b(y) "
                "CROSS JOIN UNNEST(sequence(1, 3000)) c(z)"
            )
        assert failure.value.error_name == "EXCEEDED_TIME_LIMIT"
        assert monotonic() - started < 5
        with pytest.raises(TrinoQueryError), profile.native_query_timeout(backend, 1):
            backend.raw_sql("CREATE TABLE iceberg.analysis.source_control_rejected (id BIGINT)")
    finally:
        backend.disconnect()


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1", reason="opt-in Trino service"
)
def test_trino_raw_sql_repairs_guide_qualified_column_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARIVO_RAW_SQL_TEST_USER", "analysis_reader")
    spec = md.trino(
        name="warehouse",
        host="127.0.0.1",
        port=18080,
        catalog="iceberg",
        user=md.env("MARIVO_RAW_SQL_TEST_USER"),
    )
    md.register(spec, project_root=tmp_path)
    for sql, native_name, kind in (
        ('SELECT * FROM "runtime.nodes" LIMIT 0', "MISSING_SCHEMA_NAME", "retry"),
        ("SELECT marivo_missing_column FROM system.runtime.nodes", "COLUMN_NOT_FOUND", "inspect"),
    ):
        with pytest.raises(DatasourceRawSqlError) as caught:
            md.raw_sql(spec.ref, sql, reason="verify Trino SQL repair", project_root=tmp_path)
        error = caught.value
        assert error.code == "invalid_sql"
        assert error.received is not None and f"name={native_name}" in error.received
        assert error.repair is not None and error.repair.kind == kind

    result = md.raw_sql(
        spec.ref,
        'SHOW COLUMNS FROM "system"."runtime"."nodes"',
        reason="verify the qualified metadata route",
        project_root=tmp_path,
    )
    assert "node_id" in {row["Column"] for row in result.rows}


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1", reason="opt-in MySQL service"
)
def test_mysql_raw_sql_classifies_native_timeout_preflight_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setenv("MARIVO_RAW_SQL_TEST_USER", mysql.READER)
    monkeypatch.setenv("MARIVO_RAW_SQL_TEST_PASSWORD", mysql.password())
    spec = md.mysql(
        name="warehouse",
        host=mysql.HOST,
        port=mysql.PORT,
        database=mysql.DATABASE,
        user=md.env("MARIVO_RAW_SQL_TEST_USER"),
        password=md.env("MARIVO_RAW_SQL_TEST_PASSWORD"),
    )
    md.register(spec, project_root=tmp_path)

    with pytest.raises(DatasourceRawSqlError) as caught:
        md.raw_sql(
            spec.ref,
            "SELECT 1",
            reason="verify the MySQL timeout range",
            timeout_seconds=4_294_968,
            project_root=tmp_path,
        )
    failure = caught.value
    assert failure.code == "timeout_configuration"
    assert failure.effect_observed is not None and not failure.effect_observed.query_executed
    assert failure.expected is not None and "1..4294967" in failure.expected
    assert failure.repair is not None and failure.repair.kind == "configure"


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1",
    reason="opt-in ClickHouse service",
)
def test_clickhouse_unsettable_timeout_blocks_before_user_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    ch.setup()
    user = "timeout_locked_" + uuid4().hex
    monkeypatch.setenv("MARIVO_SOURCE_CONTROL_CH_USER", user)
    monkeypatch.setenv("MARIVO_SOURCE_CONTROL_CH_PASSWORD", password())
    md.register(
        md.clickhouse(
            name="ch_reader",
            host="127.0.0.1",
            port=18123,
            database="qualification",
            user=md.env("MARIVO_SOURCE_CONTROL_CH_USER"),
            password=md.env("MARIVO_SOURCE_CONTROL_CH_PASSWORD"),
        ),
        project_root=tmp_path,
    )

    # The shared reader permits per-query deadlines. This separate account
    # supplies the immutable setting that this refusal case actually needs.
    with ch.connection(admin=True) as admin:
        admin.command(
            f"CREATE USER {user} IDENTIFIED BY {{password:String}} "
            "SETTINGS readonly=1, max_execution_time=0",
            parameters={"password": password()},
        )
        admin.command(f"GRANT SELECT ON qualification.* TO {user}")
        try:
            with pytest.raises(DatasourceRawSqlError) as failure:
                md.raw_sql(
                    ms.ref.datasource("ch_reader"),
                    "SELECT 1",
                    reason="verify timeout preflight",
                    timeout_seconds=1,
                    project_root=tmp_path,
                )
            assert failure.value.effect_observed is not None
            assert failure.value.effect_observed.query_executed is False
            assert failure.value.repair is not None
        finally:
            admin.command(f"DROP USER {user}")


@pytest.mark.runtime
@pytest.mark.skipif(
    os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1", reason="opt-in MySQL service"
)
def test_mysql_unavailable_named_zone_blocks_time_sensitive_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setenv("MARIVO_SOURCE_CONTROL_MYSQL_PASSWORD", mysql.password())
    datasource = DatasourceIR(
        semantic_id="source_control",
        name="source_control",
        backend_type="mysql",
        fields={
            "host": mysql.HOST,
            "port": mysql.PORT,
            "database": mysql.DATABASE,
            "user": mysql.READER,
        },
        env_refs={"password": "MARIVO_SOURCE_CONTROL_MYSQL_PASSWORD"},
        ai_context=AiContextIR(),
        python_symbol="source_control",
        location=DatasourceSourceLocation("source_control.py", 1),
    )
    backend = build_backend(datasource)
    try:
        with pytest.raises(DatasourceConnectionError) as failure:
            verify_source_timezone(backend, "Marivo/Unavailable")
        assert failure.value.received == "Marivo/Unavailable"
    finally:
        backend.disconnect()
