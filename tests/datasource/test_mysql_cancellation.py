"""Native MySQL certification timeout and diagnostic cleanup counterexamples."""

from collections.abc import Callable, Mapping, Sequence
from importlib import import_module
from pathlib import Path

import ibis.expr.types as ir
import pyarrow as pa
import pytest

import marivo.semantic as ms
from marivo.datasource.adapters import CompiledRead, Parameter, QualifiedSource, SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.driver_protocols import _DriverExecute, _MysqlCursor
from tests.datasource.source_cases import SourceData, source_case
from tests.datasource.source_receipts import receipt


@pytest.mark.runtime
def test_mysql_certified_calendar_deadline_terminates_slow_native_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    import time

    import marivo.datasource as md
    from marivo.datasource.capabilities import provider_statement_log
    from marivo.datasource.ir import DatasourceIR, TableSourceIR
    from marivo.datasource.runtime import DatasourceConnectionService
    from marivo.semantic.errors import SemanticRuntimeError
    from tests.datasource.environment import mysql_analysis as mysql

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32), calendar_day DATE, period VARCHAR(10)",
        "(1,1,'a',2,'2026-08-01 00:00:00','2026-08-01','P1'),(2,1,'a',5,'2026-08-02 00:00:00','2026-08-02','P1'),(3,1,'b',11,'2026-08-03 00:00:00','2026-08-03','P1')",
        "",
        [],
    )
    with source_case("mysql", "table", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        table_name = case.source.table
        view_name = table_name + "_slow"
        columns = ["id", "revision", "tenant", "amount", "happened", "calendar_day", "period"]
        aggregates = ",".join(f"MIN(a.{column}) AS {column}" for column in columns)
        joins = " ".join(f"CROSS JOIN {table_name} b{index}" for index in range(18))
        with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
            cursor.execute(
                f"CREATE VIEW {view_name} AS SELECT {aggregates} FROM {table_name} a {joins} WHERE RAND()>0.5"
            )
        try:
            case.source = TableSourceIR(view_name)
            author_source_project(
                "mysql",
                case,
                monkeypatch,
                semantic_project_factory,
                time_parse="string",
                certified_calendar=True,
            )
            catalog = ms.load(workspace_dir=tmp_path)
            calendar = ms.ref.period_calendar("sales.fiscal")
            entry = catalog.period_calendars.get(calendar.path)
            assert entry.details().snapshot_status == "missing"
            source_session = DatasourceConnectionService.source_session
            owners: list[SourceSession] = []
            native_ids: list[int] = []

            def capture_owner(
                service: DatasourceConnectionService, name: str, datasource: DatasourceIR
            ) -> SourceSession:
                owner = source_session(service, name, datasource)
                if owner not in owners:
                    owners.append(owner)
                    native_ids.append(owner._backend.con.thread_id())
                return owner

            monkeypatch.setattr(DatasourceConnectionService, "source_session", capture_owner)
            cursors = import_module("MySQLdb.cursors")
            driver_execute: _DriverExecute = cursors.BaseCursor.execute
            native_errors: list[int] = []
            native_sql: list[tuple[int, str]] = []

            def record_driver(
                cursor: _MysqlCursor, query: str | bytes, args: object | None = None
            ) -> int | None:
                assert cursor.connection is not None
                native_sql.append(
                    (
                        cursor.connection.thread_id(),
                        query.decode() if isinstance(query, bytes) else query,
                    )
                )
                try:
                    return driver_execute(cursor, query, args)
                except Exception as error:
                    if error.args and isinstance(error.args[0], int):
                        native_errors.append(error.args[0])
                    raise

            monkeypatch.setattr(cursors.BaseCursor, "execute", record_driver)
            started = time.monotonic()
            with pytest.raises(SemanticRuntimeError) as failure:
                catalog.preview(calendar, scope=md.unpruned(max_rows=5, timeout_seconds=1))
            elapsed = time.monotonic() - started
            assert native_errors == [3024]
            assert elapsed < 4
            assert failure.value.details["query_executed"] is True
            assert failure.value.details["backend_code"] == "3024"
            assert failure.value.details["timeout_seconds"] == 1
            assert entry.details().snapshot_status == "missing"
            assert owners and all(owner._closed for owner in owners)
            submissions = [submission for owner in owners for submission in owner.submissions]
            assert len(submissions) == 1
            assert submissions[0].state == "failed"
            assert submissions[0].connection_disconnected
            assert (native_ids[0], submissions[0].sql) in native_sql
            controls = [item for owner in owners for item in provider_statement_log(owner._backend)]
            deadline_controls = [
                item for item in controls if item.statement_id.startswith("mysql.authoring.")
            ]
            assert [item.sql for item in deadline_controls] == [
                "SET SESSION max_execution_time = 1000",
                "SELECT @@session.max_execution_time",
            ]
            assert all(item.state == "succeeded" for item in deadline_controls)
            assert all((native_ids[0], item.sql) in native_sql for item in deadline_controls)
            assert not any(sql.startswith("KILL") for _identity, sql in native_sql)
            with mysql.connection(admin=True) as observer, observer.cursor() as cursor:
                for native_id in native_ids:
                    cursor.execute(
                        "SELECT ID FROM information_schema.PROCESSLIST WHERE ID=%s", (native_id,)
                    )
                    assert cursor.fetchall() == ()
            receipt(
                "mysql-certified-native-timeout",
                {
                    "backend": "mysql",
                    "scope_timeout_seconds": 1,
                    "elapsed_seconds": elapsed,
                    "native_error_code": 3024,
                    "snapshot_status": entry.details().snapshot_status,
                    "connection_disconnected": True,
                    "business_driver_submission_verified": True,
                    "independent_connection_termination": True,
                    "controls": [item.sql for item in deadline_controls],
                    "no_kill_or_rescue": True,
                },
            )
        finally:
            with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
                cursor.execute(f"DROP VIEW IF EXISTS {view_name}")


@pytest.mark.parametrize("fault", ["native", "typed"])
def test_certified_capture_error_keeps_prior_snapshot_and_submission_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    fault: str,
) -> None:
    from marivo.datasource.adapters import _invalid
    from marivo.datasource.errors import DatasourceSourceCapabilityError
    from marivo.semantic.errors import SemanticRuntimeError
    from tests.semantic.preview_helpers import certified_project, default_scope

    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    calendar = ms.ref.period_calendar("sales.fiscal")
    catalog.preview(calendar, scope=default_scope(max_rows=4))
    entry = catalog.period_calendars.get(calendar.path)
    previous = entry.details()
    assert previous.snapshot_status == "current"
    typed_failure = _invalid("a qualified source capture", "fixture rejection before submission")

    def fail_before_submission(
        source: SourceSession,
        qualified: QualifiedSource | Sequence[QualifiedSource],
        expression: ir.Expr,
        *,
        params: Mapping[ir.Scalar, Parameter] | None = None,
        purpose: str,
        expected_schema: pa.Schema,
    ) -> CompiledRead:
        assert source.submissions == []
        if fault == "typed":
            raise typed_failure
        raise RuntimeError("password=private-test-secret")

    monkeypatch.setattr(SourceSession, "compile", fail_before_submission)
    if fault == "typed":
        with pytest.raises(DatasourceSourceCapabilityError) as typed:
            catalog.preview(calendar, scope=default_scope(max_rows=4))
        assert typed.value is typed_failure
    else:
        with pytest.raises(SemanticRuntimeError) as native:
            catalog.preview(calendar, scope=default_scope(max_rows=4))
        assert native.value.details["query_executed"] is False
        assert native.value.details["backend_exception"] == "RuntimeError"
        assert "private-test-secret" not in str(native.value)
        assert native.value.repair is not None
    assert entry.details() == previous


@pytest.mark.parametrize("code", [3024, True, "3024"])
def test_native_error_diagnostics_read_only_exact_integer_dbapi_codes(code: object) -> None:
    from marivo.datasource.errors import _backend_failure_summary

    summary = _backend_failure_summary(RuntimeError(code, "password=private-test-secret"))
    assert summary.backend_code == ("3024" if type(code) is int else None)
    assert "private-test-secret" not in summary.message
