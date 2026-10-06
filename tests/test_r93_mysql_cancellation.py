"""Native owned cancellation and persistent graph cleanup counterexamples."""

from collections.abc import Callable, Mapping, Sequence
from importlib import import_module
from pathlib import Path
from threading import get_ident
from typing import Protocol

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.materialization import execute_deadline, graph_publication
from marivo.analysis.materialization.errors import MaterializationError, RecoveryPendingError
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.datasource.adapters import CompiledRead, Parameter, QualifiedSource, SourceSession
from marivo.semantic.reader import SemanticProject
from tests.mysql_server_observation import wait_for_owned_connection_release
from tests.r9_source_cases import SourceData, source_case
from tests.test_r93_capability_consumers import _author_c05_project
from tests.test_r93_source_deadline import receipt


class _MysqlConnection(Protocol):
    def thread_id(self) -> int: ...


class _MysqlCursor(Protocol):
    connection: _MysqlConnection | None


class _DriverExecute(Protocol):
    def __call__(
        self, cursor: _MysqlCursor, query: str | bytes, args: object | None = None
    ) -> int | None: ...


class _DriverClose(Protocol):
    def __call__(self, cursor: _MysqlCursor) -> None: ...


@pytest.mark.runtime
@pytest.mark.parametrize("fault", ["cancel", "control_close"])
def test_mysql_graph_failure_preserves_original_artifact_and_control_obligation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    fault: str,
) -> None:

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32)",
        "(1,1,'a',2,'2026-08-01 00:00:00'),(2,1,'a',5,'2026-08-02 00:00:00'),(3,1,'b',11,'2026-08-03 00:00:00')",
        "",
        [],
    )
    with source_case("mysql", "table", tmp_path, monkeypatch, data) as case:
        _author_c05_project(
            "mysql", case, monkeypatch, semantic_project_factory, time_parse="string"
        )
        session = mv.session.get_or_create("r93-mysql-atomic", report_timezone="UTC")
        logical = session.members(ms.ref.entity("sales.facts")).read(
            ms.ref.measure("sales.facts.amount")
        )
        previous = logical.execute()
        saved = previous.to_pandas()
        runtime = session._runtime
        cursors = import_module("MySQLdb.cursors")
        driver_execute: _DriverExecute = cursors.BaseCursor.execute
        driver_submissions: list[tuple[int, str]] = []
        cursor_closes: list[tuple[int, int]] = []
        owner_thread = get_ident()
        driver_close: _DriverClose = cursors.BaseCursor.close

        def record_driver_close(cursor: _MysqlCursor) -> None:
            if cursor.connection is not None:
                cursor_closes.append((cursor.connection.thread_id(), get_ident()))
            driver_close(cursor)

        monkeypatch.setattr(cursors.BaseCursor, "close", record_driver_close)

        def record_driver_submission(
            cursor: _MysqlCursor, query: str | bytes, args: object | None = None
        ) -> int | None:
            assert cursor.connection is not None
            driver_submissions.append(
                (
                    cursor.connection.thread_id(),
                    query.decode() if isinstance(query, bytes) else query,
                )
            )
            result = driver_execute(cursor, query, args)
            if result is None or isinstance(result, int):
                return result
            raise AssertionError("Unexpected native MySQL execute result")

        monkeypatch.setattr(cursors.BaseCursor, "execute", record_driver_submission)
        owners: list[SourceSession] = []
        native_ids: list[int] = []
        prepare = SourceSession._prepare_interrupt

        def prepare_control(source: SourceSession) -> None:
            obligations = runtime.store.resources(runtime.session_ref)
            assert any(
                item.cleanup_capability_id == "mysql_owned_control_close@v1" for item in obligations
            )
            prepare(source)
            owners.append(source)
            assert source._cancel_control is not None
            control = source._cancel_control
            native_ids.extend([source._backend.con.thread_id(), control.con.thread_id()])
            if fault == "control_close":
                disconnect = control.disconnect

                def fail_acknowledgement() -> None:
                    disconnect()
                    raise RuntimeError("control release unconfirmed")

                monkeypatch.setattr(control, "disconnect", fail_acknowledgement)

        monkeypatch.setattr(SourceSession, "_prepare_interrupt", prepare_control)
        original_compile = SourceSession.compile

        def slow_compile(
            source: SourceSession,
            qualified: QualifiedSource | Sequence[QualifiedSource],
            expression: ir.Expr,
            *,
            params: Mapping[ir.Scalar, Parameter] | None = None,
            purpose: str,
            expected_schema: pa.Schema,
        ) -> CompiledRead:
            assert isinstance(expression, ir.Table)
            bound = qualified if isinstance(qualified, QualifiedSource) else qualified[0]
            for _ in range(18):
                expression = expression.cross_join(bound.binding.relation.view()).select(expression)
            expression = expression.filter(ibis.random() > 0.5)
            expression = expression.aggregate(
                **{name: expression[name].max() for name in expression.columns}
            )
            return original_compile(
                source,
                qualified,
                expression,
                params=params,
                purpose=purpose,
                expected_schema=expected_schema,
            )

        if fault == "cancel":
            monkeypatch.setattr(SourceSession, "compile", slow_compile)
            original_deadline = execute_deadline.ExecuteDeadline

            def short_deadline(start: float) -> execute_deadline.ExecuteDeadline:
                return original_deadline(start, seconds=1)

            monkeypatch.setattr(execute_deadline, "ExecuteDeadline", short_deadline)
            with pytest.raises(DomainPreparationError, match="execute_timeout"):
                logical.execute()
        else:
            with pytest.raises(MaterializationError, match="source execution failed") as failure:
                logical.execute()
            assert isinstance(failure.value.__cause__, RuntimeError)
            assert str(failure.value.__cause__) == "control release unconfirmed"
        assert owners and all(owner._closed for owner in owners)
        native_cancels = [
            (connection_id, sql)
            for connection_id, sql in driver_submissions
            if sql.startswith("KILL")
        ]
        assert native_cancels == (
            [(native_ids[1], f"KILL QUERY {native_ids[0]}")] if fault == "cancel" else []
        )
        data_cursor_closes = [
            thread for identity, thread in cursor_closes if identity == native_ids[0]
        ]
        assert data_cursor_closes and set(data_cursor_closes) == {owner_thread}
        for owner in owners:
            for submission in owner.submissions:
                assert (native_ids[0], submission.sql) in driver_submissions
        assert all(
            submission.connection_disconnected
            for owner in owners
            for submission in owner.submissions
        )
        assert runtime.last_run_ref is not None
        run = runtime.store._graph_run(runtime.last_run_ref)
        assert run is not None
        obligations = runtime.store.resources(runtime.session_ref)
        if fault == "cancel":
            assert run.lifecycle == "failed"
            assert obligations == ()
            assert len(owners[0].cancel_submissions) == 1
            assert owners[0].cancel_submissions[0].state == "succeeded"
            assert owners[0]._cancel_control_released
        else:
            assert run.lifecycle == "incomplete"
            assert len(obligations) == 1
            assert obligations[0].cleanup_capability_id == "mysql_owned_control_close@v1"
            assert not owners[0]._cancel_control_released
            with (
                session_writer_guard(
                    runtime.store.layout.lock_path(runtime.session_ref),
                    session_ref=runtime.session_ref,
                ),
                pytest.raises(
                    RecoveryPendingError,
                    match="original control close acknowledgement is unavailable",
                ),
            ):
                graph_publication._reconcile_graph(
                    runtime.store, runtime.session_ref, runtime._event
                )
            assert runtime.store.resources(runtime.session_ref) == obligations
        with runtime.store._connection() as connection:
            assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert previous.to_pandas().equals(saved)
        release_observation_seconds = wait_for_owned_connection_release(*native_ids)
        receipt(
            "mysql-graph-" + fault,
            {
                "backend": "mysql",
                "fault": fault,
                "run_lifecycle": run.lifecycle,
                "previous_artifact_preserved": True,
                "published_artifacts": 1,
                "remaining_control_obligations": len(obligations),
                "reconciliation_refused": fault == "control_close",
                "independent_connection_release": True,
                "release_observation_seconds": release_observation_seconds,
                "slow_expression_injected": fault == "cancel",
                "native_cancel_submissions": [
                    {"connection_id": identity, "sql": sql} for identity, sql in native_cancels
                ],
                "data_connection_id": native_ids[0],
                "control_connection_id": native_ids[1],
                "data_cursor_cleanup_on_owner_thread": True,
                "business_driver_submissions_verified": True,
            },
        )


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
    from marivo.semantic.errors import SemanticRuntimeError
    from tests.multisource_environment import mysql_analysis as mysql

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
            _author_c05_project(
                "mysql",
                case,
                monkeypatch,
                semantic_project_factory,
                time_parse="string",
                certified_calendar=True,
            )
            catalog = ms.load(workspace_dir=tmp_path)
            calendar = ms.ref.period_calendar("sales.fiscal")
            entry = catalog.period_calendars.get(calendar)
            assert entry.details().snapshot_status == "missing"
            connections = catalog._project._connection_service()
            source_session = connections.source_session
            owners: list[SourceSession] = []
            native_ids: list[int] = []

            def capture_owner(name: str, datasource: DatasourceIR) -> SourceSession:
                owner = source_session(name, datasource)
                if owner not in owners:
                    owners.append(owner)
                    native_ids.append(owner._backend.con.thread_id())
                return owner

            monkeypatch.setattr(connections, "source_session", capture_owner)
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
            assert submissions[0].cursor_state == "closed"
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
    from tests.test_semantic_scoped_preview import _certified_project, _scope

    catalog = _certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    calendar = ms.ref.period_calendar("sales.fiscal")
    catalog.preview(calendar, scope=_scope(max_rows=4))
    entry = catalog.period_calendars.get(calendar)
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
            catalog.preview(calendar, scope=_scope(max_rows=4))
        assert typed.value is typed_failure
    else:
        with pytest.raises(SemanticRuntimeError) as native:
            catalog.preview(calendar, scope=_scope(max_rows=4))
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
