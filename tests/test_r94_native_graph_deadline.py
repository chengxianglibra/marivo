"""Bind real graph expiry, publication atomicity and independent remote proofs."""

import json
import os
import signal
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from threading import Event, Lock, Thread, get_ident
from typing import Literal
from urllib.request import Request, urlopen

import ibis
import ibis.expr.types as ir
import psycopg
import pyarrow as pa
import pytest
from trino.dbapi import Cursor

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.materialization import execute_deadline
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.adapters import (
    CompiledRead,
    Parameter,
    QualifiedSource,
    SourceSession,
    _ClickHouseNativeStream,
    _Cursor,
)
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, checked, encode
from tests.r9_source_cases import source_case
from tests.r94_domain_recovery_worker import snapshot
from tests.r94_native_domain_k_worker import run_ids
from tests.test_r93_capability_consumers import _author_c05_project
from tests.test_r93_mysql_cancellation import _DriverClose, _DriverExecute, _MysqlCursor
from tests.test_r93_reference_consumers import _data
from tests.test_r93_source_deadline import _ClickHouseReadClient, _NativeClickHouseRead


def graph_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: Literal["postgres", "mysql", "trino", "clickhouse"],
    mode: Literal["deadline", "sigint"],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, _data(backend)) as case:
        _author_c05_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-graph-deadline", report_timezone="UTC")
        logical = session.members(ms.ref.entity("sales.facts")).read(
            ms.ref.measure("sales.facts.amount")
        )
        previous = logical.execute()
        original_snapshot = snapshot(previous)
        runtime = session._runtime
        before_runs = run_ids(session)

        def publication_counts() -> list[int]:
            with runtime.store._connection() as connection:
                counts: list[int] = []
                for table in ("dataset_artifacts", "dataset_evidence", "findings"):
                    row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
                    assert row is not None and isinstance(row[0], int)
                    counts.append(row[0])
                return counts

        before = publication_counts()
        assert runtime.store.resources(runtime.session_ref) == ()
        driver_submissions: list[tuple[int, str]] = []
        cursor_closes: list[tuple[int, int]] = []
        cursor_close_failures: list[int] = []
        mysql_ids: list[int] = []
        control_cleanup_threads: list[int] = []
        if backend == "mysql":
            cursors = import_module("MySQLdb.cursors")
            native_execute: _DriverExecute = cursors.BaseCursor.execute
            native_close: _DriverClose = cursors.BaseCursor.close

            def driver_execute(
                cursor: _MysqlCursor, query: str | bytes, args: object | None = None
            ) -> int | None:
                assert cursor.connection is not None
                driver_submissions.append(
                    (
                        cursor.connection.thread_id(),
                        query.decode() if isinstance(query, bytes) else query,
                    )
                )
                return native_execute(cursor, query, args)

            def driver_close(cursor: _MysqlCursor) -> None:
                identity = cursor.connection.thread_id() if cursor.connection is not None else None
                if cursor.connection is not None:
                    cursor_closes.append((cursor.connection.thread_id(), get_ident()))
                try:
                    native_close(cursor)
                except BaseException:
                    if identity is not None:
                        cursor_close_failures.append(identity)
                    raise

            monkeypatch.setattr(cursors.BaseCursor, "execute", driver_execute)
            monkeypatch.setattr(cursors.BaseCursor, "close", driver_close)
        owners: list[SourceSession] = []
        postgres_ids: list[int] = []
        trino_cursors: list[Cursor] = []
        close_threads: list[int] = []
        clickhouse_requests: list[dict[str, str | float | int]] = []
        clickhouse_request_sql: list[str] = []
        clickhouse_cancel_targets: list[tuple[str, str]] = []
        owner_thread = get_ident()
        prepare = SourceSession._prepare_interrupt
        own = SourceSession._own_pending_cursor
        close = SourceSession.close

        def prepared(source: SourceSession) -> None:
            prepare(source)
            owners.append(source)
            if backend == "postgres":
                assert isinstance(source._backend.con, psycopg.Connection)
                postgres_ids.append(source._backend.con.info.backend_pid)
            elif backend == "mysql":
                assert source._cancel_control is not None
                control = source._cancel_control
                mysql_ids.extend((source._backend.con.thread_id(), control.con.thread_id()))
                disconnect = control.disconnect

                def control_closed() -> None:
                    control_cleanup_threads.append(get_ident())
                    disconnect()

                monkeypatch.setattr(control, "disconnect", control_closed)
            elif backend == "clickhouse":
                assert source._cancel_control is not None
                control = source._cancel_control
                disconnect = control.disconnect

                def clickhouse_control_closed() -> None:
                    control_cleanup_threads.append(get_ident())
                    disconnect()

                monkeypatch.setattr(control, "disconnect", clickhouse_control_closed)
                client = source._backend.con
                assert isinstance(client, _ClickHouseReadClient)
                native: _NativeClickHouseRead = client.query_rows_stream

                def requested(
                    query: str, *, settings: dict[str, str | float | int] | None = None
                ) -> _ClickHouseNativeStream:
                    assert settings is not None
                    if mode == "sigint" and query in compiled:
                        settings = {
                            **settings,
                            "max_execution_time": 10,
                            "readonly": 1,
                            "cancel_http_readonly_queries_on_client_close": 1,
                        }
                    if query in compiled:
                        clickhouse_requests.append(dict(settings))
                        clickhouse_request_sql.append(query)
                    return native(query, settings=settings)

                monkeypatch.setattr(client, "query_rows_stream", requested)

        def pending(source: SourceSession, cursor: _Cursor) -> None:
            if isinstance(cursor, Cursor):
                trino_cursors.append(cursor)
            own(source, cursor)

        def closed(source: SourceSession) -> None:
            if source in owners:
                close_threads.append(get_ident())
            close(source)

        monkeypatch.setattr(SourceSession, "_prepare_interrupt", prepared)
        monkeypatch.setattr(SourceSession, "_own_pending_cursor", pending)
        monkeypatch.setattr(SourceSession, "close", closed)
        request_interrupt = SourceSession._request_interrupt

        def interrupted(source: SourceSession) -> None:
            if backend == "clickhouse" and source._clickhouse_active is not None:
                assert source._clickhouse_reader is not None
                clickhouse_cancel_targets.append(
                    (source._clickhouse_active[1], source._clickhouse_reader)
                )
            request_interrupt(source)

        monkeypatch.setattr(SourceSession, "_request_interrupt", interrupted)
        compile_read = SourceSession.compile
        compiled: list[str] = []

        def slow_compile(
            source: SourceSession,
            qualified: QualifiedSource | Sequence[QualifiedSource],
            expression: ir.Expr,
            *,
            params: Mapping[ir.Scalar, Parameter] | None = None,
            purpose: str,
            expected_schema: pa.Schema,
        ) -> CompiledRead:
            if purpose != "analysis.graph.stage":
                return compile_read(
                    source,
                    qualified,
                    expression,
                    params=params,
                    purpose=purpose,
                    expected_schema=expected_schema,
                )
            assert isinstance(expression, ir.Table)
            bound = qualified if isinstance(qualified, QualifiedSource) else qualified[0]
            for _ in range(16 if backend == "clickhouse" and mode == "sigint" else 18):
                expression = expression.cross_join(bound.binding.relation.view()).select(expression)
            expression = expression.filter(ibis.random() > 0.5).aggregate(
                **{name: expression[name].max() for name in expression.columns}
            )
            if backend == "clickhouse":
                expression = expression.cast(ibis.Schema.from_pyarrow(expected_schema))
            result = compile_read(
                source,
                qualified,
                expression,
                params=params,
                purpose=purpose,
                expected_schema=expected_schema,
            )
            compiled.append(result.sql)
            return result

        monkeypatch.setattr(SourceSession, "compile", slow_compile)
        original_deadline = execute_deadline.ExecuteDeadline
        deadline_seconds = 600 if mode == "sigint" else 5 if backend == "clickhouse" else 1

        def short_deadline(start: float) -> execute_deadline.ExecuteDeadline:
            return original_deadline(start, seconds=deadline_seconds)

        signal_observation: dict[str, Json] = {}
        if mode == "deadline":
            monkeypatch.setattr(execute_deadline, "ExecuteDeadline", short_deadline)
            with pytest.raises(
                (DomainPreparationError, MaterializationError),
                match=r"execute_timeout|source execution failed",
            ) as raised:
                logical.execute()
            interruption: BaseException = raised.value
        else:
            stopped = Event()
            signal_gate = Lock()

            def signal_active_query() -> None:
                try:
                    until = time.monotonic() + 10
                    while not stopped.is_set() and time.monotonic() < until:
                        if compiled and backend == "postgres" and postgres_ids:
                            from tests.multisource_environment import postgres_analysis as pg

                            with pg.connection(admin=True) as observer:
                                row = observer.execute(
                                    "SELECT state, query FROM pg_stat_activity WHERE pid=%s",
                                    (postgres_ids[-1],),
                                ).fetchone()
                            if (
                                row
                                and row[0] == "active"
                                and isinstance(row[1], str)
                                and len(row[1]) > 100
                                and compiled[0].startswith(row[1])
                            ):
                                signal_observation.update(
                                    {
                                        "active": True,
                                        "backend_pid": postgres_ids[-1],
                                        "server_query_prefix": row[1],
                                    }
                                )
                                break
                        elif compiled and backend == "mysql" and mysql_ids:
                            from tests.multisource_environment import mysql_analysis as mysql

                            with (
                                mysql.connection(admin=True) as observer,
                                observer.cursor() as cursor,
                            ):
                                cursor.execute(
                                    "SELECT COMMAND,INFO FROM information_schema.PROCESSLIST WHERE ID=%s",
                                    (mysql_ids[0],),
                                )
                                row = cursor.fetchone()
                            if row and row[0] == "Query" and row[1] == compiled[0]:
                                signal_observation.update(
                                    {
                                        "active": True,
                                        "data_connection_id": mysql_ids[0],
                                        "server_sql": row[1],
                                    }
                                )
                                break
                        elif compiled and backend == "trino" and trino_cursors:
                            query_id = trino_cursors[-1].query_id
                            if isinstance(query_id, str):
                                request = Request(
                                    "http://127.0.0.1:18080/v1/query/" + query_id,
                                    headers={"X-Trino-User": "qualifier"},
                                )
                                with urlopen(request, timeout=3) as response:
                                    observed = json.load(response)
                                if (
                                    observed["state"] == "RUNNING"
                                    and observed["query"] == compiled[0]
                                ):
                                    signal_observation.update(
                                        {
                                            "active": True,
                                            "query_id": query_id,
                                            "server_state": "RUNNING",
                                            "server_sql": observed["query"],
                                        }
                                    )
                                    break
                        elif compiled and backend == "clickhouse" and clickhouse_requests:
                            from tests.multisource_environment import clickhouse_analysis as ch

                            identity = clickhouse_requests[0]["query_id"]
                            assert isinstance(identity, str)
                            with ch.connection(admin=True) as observer:
                                active = observer.query(
                                    "SELECT query FROM system.processes WHERE query_id={id:String}",
                                    parameters={"id": identity},
                                ).result_rows
                            if len(active) == 1 and str(active[0][0]).startswith(compiled[0]):
                                signal_observation.update(
                                    {
                                        "active": True,
                                        "query_id": identity,
                                        "server_sql": str(active[0][0]),
                                    }
                                )
                                break
                        stopped.wait(0.02)
                except Exception as error:
                    signal_observation["observer_error"] = type(error).__name__
                with signal_gate:
                    if not stopped.is_set():
                        signal_observation["signal_sent"] = True
                        os.kill(os.getpid(), signal.SIGINT)
                if (
                    backend == "mysql"
                    and signal_observation.get("active") is True
                    and not stopped.wait(5)
                ):
                    from tests.multisource_environment import mysql_analysis as mysql

                    with mysql.connection(admin=True) as observer, observer.cursor() as cursor:
                        cursor.execute(
                            "SELECT INFO FROM information_schema.PROCESSLIST WHERE ID=%s",
                            (mysql_ids[0],),
                        )
                        row = cursor.fetchone()
                        if row and row[0] == compiled[0]:
                            signal_observation["test_rescue"] = True
                            cursor.execute(f"KILL QUERY {mysql_ids[0]}")

            controller = Thread(target=signal_active_query, daemon=True)
            controller.start()
            signal_started = time.monotonic()
            try:
                with pytest.raises(KeyboardInterrupt) as cancelled:
                    logical.execute()
                interruption = cancelled.value
            finally:
                with signal_gate:
                    stopped.set()
                controller.join(5)
                signal_observation["elapsed_seconds"] = time.monotonic() - signal_started
                if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
                    Path(directory, "graph-sigint-diagnostic-" + backend + ".json").write_bytes(
                        encode(signal_observation)
                    )
            assert not controller.is_alive()
            assert signal_observation.get("test_rescue") is not True, signal_observation
            assert (
                signal_observation.get("active") is True
                and signal_observation.get("signal_sent") is True
            )
        errors: list[BaseException] = []
        error: BaseException | None = interruption
        while error is not None and all(error is not previous_error for previous_error in errors):
            errors.append(error)
            error = error.__cause__ or error.__context__
        diagnostic = {
            "error_types": [type(error).__name__ for error in errors],
            "causes": [str(error) for error in errors[1:]],
            "owners": len(owners),
            "clickhouse_requests": len(clickhouse_requests),
        }
        if not compiled:
            (tmp_path / "deadline-diagnostic.json").write_text(json.dumps(diagnostic, indent=2))
        assert compiled and owners and all(source._closed for source in owners), diagnostic
        assert execute_deadline.CURRENT.get() is None
        assert close_threads and set(close_threads) == {owner_thread}
        assert all(
            submission.connection_disconnected
            for source in owners
            for submission in source.submissions
        )
        assert any(
            submission.sql in compiled and submission.state == "failed"
            for source in owners
            for submission in source.submissions
        )
        assert publication_counts() == before
        assert snapshot(previous) == original_snapshot
        after_runs = run_ids(session)
        assert len(after_runs - before_runs) == 1
        assert runtime.last_run_ref is not None
        run = runtime.store._graph_run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed"
        assert runtime.store.resources(runtime.session_ref) == ()
        server: dict[str, Json] = {}
        if backend == "postgres":
            from tests.multisource_environment import postgres_analysis as pg

            assert postgres_ids
            if mode == "deadline":
                assert any(
                    isinstance(error, psycopg.errors.QueryCanceled) and error.sqlstate == "57014"
                    for error in errors
                )
            with pg.connection(admin=True) as observer:
                for identity in postgres_ids:
                    assert (
                        observer.execute(
                            "SELECT pid FROM pg_stat_activity WHERE pid=%s", (identity,)
                        ).fetchone()
                        is None
                    )
            server = {
                "backend_pids": list(postgres_ids),
                "active_connections": 0,
                "sqlstate": "57014" if mode == "deadline" else None,
            }
        elif backend == "mysql":
            assert len(mysql_ids) == 2 and mysql_ids[0] != mysql_ids[1]
            assert control_cleanup_threads and set(control_cleanup_threads) == {owner_thread}
            assert all(
                source._cancel_control is None and source._cancel_control_released
                for source in owners
            )
            data_closes = [thread for identity, thread in cursor_closes if identity == mysql_ids[0]]
            assert data_closes and set(data_closes) == {owner_thread}
            cancels = [submission for source in owners for submission in source.cancel_submissions]
            assert len(cancels) == 1 and cancels[0].state == "succeeded"
            expected_control = (mysql_ids[1], f"KILL QUERY {mysql_ids[0]}")
            assert [
                (identity, sql) for identity, sql in driver_submissions if sql.startswith("KILL")
            ] == [expected_control]
            assert all(
                (mysql_ids[0], submission.sql) in driver_submissions
                for source in owners
                for submission in source.submissions
            )
            from tests.mysql_server_observation import wait_for_owned_connection_release

            release_observation_seconds = wait_for_owned_connection_release(*mysql_ids)
            server = {
                "data_connection_id": mysql_ids[0],
                "control_connection_id": mysql_ids[1],
                "active_connections": 0,
                "release_observation_seconds": release_observation_seconds,
                "control_statement": expected_control[1],
                "control_state": "succeeded",
                "driver_submissions": [
                    {"connection_id": identity, "sql": sql} for identity, sql in driver_submissions
                ],
                "data_cursor_close_attempt_on_owner_thread": True,
                "data_cursor_close_failed": mysql_ids[0] in cursor_close_failures,
                "control_cleanup_on_owner_thread": True,
            }
        elif backend == "trino":
            assert len(trino_cursors) == 1
            query_id = trino_cursors[0].query_id
            assert isinstance(query_id, str)
            request = Request(
                "http://127.0.0.1:18080/v1/query/" + query_id, headers={"X-Trino-User": "qualifier"}
            )
            with urlopen(request, timeout=5) as response:
                observed = json.load(response)
            assert (
                observed["state"] == "FAILED" and observed["errorCode"]["name"] == "USER_CANCELED"
            )
            assert observed["query"] in compiled
            server = {
                "query_id": query_id,
                "state": "FAILED",
                "error": "USER_CANCELED",
                "sql": observed["query"],
            }
        else:
            from tests.multisource_environment import clickhouse_analysis as ch

            assert clickhouse_requests
            if mode == "sigint":
                assert len(clickhouse_requests) == 1
            settings = clickhouse_requests[-1]
            interrupted_sql = clickhouse_request_sql[-1]
            assert interrupted_sql in compiled
            candidate_id = settings["query_id"]
            assert isinstance(candidate_id, str)
            query_id = candidate_id
            assert control_cleanup_threads and set(control_cleanup_threads) == {owner_thread}
            assert all(
                source._cancel_control is None and source._cancel_control_released
                for source in owners
            )
            cancels = [submission for source in owners for submission in source.cancel_submissions]
            if cancels:
                assert len(cancels) == 1 and cancels[0].state == "succeeded"
                assert cancels[0].sql == (
                    "KILL QUERY WHERE query_id={id:String} AND user={user:String} SYNC"
                )
                assert clickhouse_cancel_targets and set(clickhouse_cancel_targets) == {
                    (query_id, "analysis_reader")
                }
            assert 0 < float(settings["max_execution_time"]) <= deadline_seconds
            with ch.connection(admin=True) as observer:
                events: list[tuple[object, ...]] = []
                until = time.monotonic() + 3
                while not events and time.monotonic() < until:
                    observer.command("SYSTEM FLUSH LOGS")
                    events = [
                        tuple(row)
                        for row in observer.query(
                            "SELECT type, exception_code, query FROM system.query_log WHERE query_id={id:String} AND type IN ('ExceptionBeforeStart','ExceptionWhileProcessing')",
                            parameters={"id": query_id},
                        ).result_rows
                    ]
                    if not events:
                        time.sleep(0.02)
                expected_code = 394 if mode == "sigint" else 159
                if len(events) == 1 and events[0][1] == 394:
                    assert cancels and cancels[0].state == "succeeded"
                    expected_code = 394
                if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
                    Path(directory, "graph-observed-" + mode + "-clickhouse.json").write_bytes(
                        encode(
                            checked(
                                {
                                    "query_id": query_id,
                                    "events": [list(row) for row in events],
                                    "active_queries": observer.query(
                                        "SELECT count() FROM system.processes WHERE query_id={id:String}",
                                        parameters={"id": query_id},
                                    ).first_row[0],
                                    "settings": dict(settings),
                                    "expected_code": expected_code,
                                    "publication_counts_before": before,
                                    "publication_counts_after": publication_counts(),
                                    "new_runs": 1,
                                    "run_lifecycle": run.lifecycle,
                                    "resources": 0,
                                    "previous_artifact_preserved": True,
                                    "source_submissions": [
                                        asdict(submission)
                                        for source in owners
                                        for submission in source.submissions
                                    ],
                                }
                            )
                        )
                    )
                assert len(events) == 1 and events[0][0] in {
                    "ExceptionBeforeStart",
                    "ExceptionWhileProcessing",
                }, events
                assert events[0][1] == expected_code, events
                if mode == "sigint":
                    elapsed = signal_observation["elapsed_seconds"]
                    assert isinstance(elapsed, (int, float)) and elapsed < 3
                sql = events[0][2]
                assert isinstance(sql, str) and sql.startswith(interrupted_sql)
                assert sql[len(interrupted_sql) :].strip() == "FORMAT Native"
                assert observer.query(
                    "SELECT count() FROM system.processes WHERE query_id={id:String}",
                    parameters={"id": query_id},
                ).first_row == (0,)
            server = {
                "query_id": query_id,
                "event": events[0][0],
                "code": expected_code,
                "active_queries": 0,
                "sql": sql,
                "settings": dict(settings),
                "control_submissions": [asdict(submission) for submission in cancels],
                "control_targets": [
                    list(target) for target in sorted(set(clickhouse_cancel_targets))
                ],
                "control_cleanup_on_owner_thread": True,
                "data_requests": [
                    {"sql": sql, "settings": dict(request)}
                    for sql, request in zip(
                        clickhouse_request_sql, clickhouse_requests, strict=True
                    )
                ],
            }
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, "graph-" + mode + "-" + backend + ".json").write_bytes(
                encode(
                    checked(
                        {
                            "backend": backend,
                            "profile": profile,
                            "deadline_seconds": deadline_seconds,
                            "interruption_kind": mode,
                            "signal_observation": signal_observation,
                            "error_types": [type(error).__name__ for error in errors],
                            "publication_counts_before": before,
                            "publication_counts_after": publication_counts(),
                            "new_runs": 1,
                            "run_ref": runtime.last_run_ref,
                            "run_lifecycle": run.lifecycle,
                            "resources": 0,
                            "previous_snapshot": original_snapshot,
                            "previous_artifact_preserved": True,
                            "slow_expression_injected": True,
                            "compiled_sql": compiled,
                            "source_submissions": [
                                asdict(submission)
                                for source in owners
                                for submission in source.submissions
                            ],
                            "connection_disconnected": True,
                            "cleanup_on_owner_thread": True,
                            "independent_server_proof": server,
                            "boundary": "Ordinary int64 read, native table/Iceberg/MergeTree, pending abort only; initial-response/fetch, other profiles/producers and untested interruption kinds remain independent.",
                        }
                    )
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_graph_deadline_preserves_publication_and_proves_remote_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: Literal["postgres", "mysql", "trino", "clickhouse"],
) -> None:
    graph_abort(tmp_path, monkeypatch, semantic_project_factory, backend, "deadline")


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ("postgres", "mysql", "trino", "clickhouse"))
def test_graph_sigint_preserves_publication_and_proves_remote_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: Literal["postgres", "mysql", "trino", "clickhouse"],
) -> None:
    graph_abort(tmp_path, monkeypatch, semantic_project_factory, backend, "sigint")
