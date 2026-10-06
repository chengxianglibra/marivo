"""Actual source-read deadline boundaries without domain preparation."""

import json
import os
import sys
import time
from collections.abc import Callable
from contextlib import closing
from importlib import import_module
from pathlib import Path
from threading import Event, get_ident
from urllib.request import Request, urlopen

import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.materialization.execute_deadline import (
    COMMITTED,
    CURRENT,
    ExecuteDeadline,
    check,
    execution_budget,
    guard,
    remaining,
)
from marivo.datasource.adapters import (
    CompiledRead,
    PhysicalRequirement,
    SourceBatchStream,
    SourceSession,
    _ClickHouseNativeStream,
)
from tests.datasource.driver_protocols import (
    _ClickHouseReadClient,
    _ClickHouseServerError,
    _NativeClickHouseRead,
)
from tests.datasource.source_cases import source_case
from tests.datasource.source_receipts import receipt
from tests.shared_fixtures import DslCaseFactory


def test_native_read_budget_keeps_the_original_monotonic_start() -> None:
    now = [200.0]
    token = CURRENT.set(ExecuteDeadline(100.0, lambda: now[0]))
    try:
        assert remaining() == 500
        with execution_budget(start=9999):
            now[0] = 300
            assert remaining() == 400
        committed = COMMITTED.set(True)
        try:
            assert remaining() is None
        finally:
            COMMITTED.reset(committed)
        now[0] = 700.001
        with pytest.raises(DomainPreparationError, match="execute_timeout"):
            remaining()
    finally:
        CURRENT.reset(token)
    assert remaining() is None


@pytest.mark.runtime
@pytest.mark.parametrize(
    "boundary", ["source_submission", "source_publication", "fixed_publication"]
)
def test_ordinary_graph_starts_its_own_expiry_budget(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    from marivo.analysis.materialization import execute_deadline as deadlines

    case = analysis_dsl_case_factory("j2")
    values = case.session.members(ms.ref.entity("sales.order")).read(
        ms.ref.measure("sales.order.amount")
    )
    previous = values.execute()
    saved = previous.to_pandas()
    logical = (
        previous.summarize(mv.count())
        if boundary == "fixed_publication"
        else values.summarize(mv.count())
    )
    assert CURRENT.get() is None
    now = [0.0]
    created: list[ExecuteDeadline] = []

    def budget(start: float) -> ExecuteDeadline:
        now[0] = start
        deadline = ExecuteDeadline(start, clock=lambda: now[0])
        created.append(deadline)
        return deadline

    monkeypatch.setattr(deadlines, "ExecuteDeadline", budget)
    original = SourceSession.batches
    seen: list[SourceSession] = []

    def expire(source: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        seen.append(source)
        now[0] += 600.001
        return original(source, read, chunk_size=chunk_size)

    runtime = case.session._runtime

    def late_result(point: str) -> None:
        if point == "graph_receipts_verified":
            now[0] += 600.001

    if boundary == "source_submission":
        monkeypatch.setattr(SourceSession, "batches", expire)
    else:
        runtime._hook = late_result
    try:
        with pytest.raises(DomainPreparationError, match="execute_timeout"):
            logical.execute()
    finally:
        runtime._hook = None
    assert len(created) == 1
    assert CURRENT.get() is None
    assert runtime.last_run_ref is not None
    run = runtime.store._graph_run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert runtime.store.resources(runtime.session_ref) == ()
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 1
    assert previous.to_pandas().equals(saved)
    if boundary == "source_submission":
        assert seen and all(not source.submissions for source in seen)
    receipt(
        "deadline-ordinary-" + boundary,
        {
            "boundary": boundary,
            "implicit_budget": True,
            "run_lifecycle": run.lifecycle,
            "published_artifacts": 1,
            "previous_artifact_preserved": True,
            "resources": 0,
            "source_submissions": sum(len(source.submissions) for source in seen),
        },
    )


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["zscore", "mad"])
def test_statistic_checks_expiry_before_native_submission(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    logical = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .deviation(method="zscore" if method == "zscore" else "mad")
    )
    original = SourceSession.batches
    seen: list[SourceSession] = []
    now = [0.0]

    def expire(source: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        seen.append(source)
        assert source.domain_authority is None
        now[0] = 600.001
        return original(source, read, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", expire)
    token = CURRENT.set(ExecuteDeadline(0.0, lambda: now[0]))
    try:
        with pytest.raises(DomainPreparationError, match="execute_timeout"):
            logical.execute()
        assert seen and all(not source.submissions for source in seen)
        runtime = case.session._runtime
        assert runtime.last_run_ref is not None
        run = runtime.store._graph_run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed"
        assert runtime.store.resources(runtime.session_ref) == ()
        with runtime.store._connection() as connection:
            assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0
        receipt(
            "deadline-before-submission-" + method,
            {
                "method": "deviation." + method + "@v1",
                "native_submissions": 0,
                "run_lifecycle": run.lifecycle,
                "published_artifacts": 0,
                "resources": 0,
            },
        )
    finally:
        CURRENT.reset(token)


@pytest.mark.runtime
@pytest.mark.parametrize("backend", ["duckdb", "sqlite"])
def test_native_interrupt_keeps_cleanup_on_the_owner_thread(
    backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with source_case(backend, "table", tmp_path, monkeypatch) as case:
        source = case.session
        assert source.domain_authority is None
        bound = source.bind(case.source, source_identity="source_deadline.deadline")
        expression = bound.relation
        for _ in range(18):
            expression = expression.cross_join(bound.relation.view()).select(expression)
        expression = expression.mutate(noise=ibis.random()).aggregate(value=lambda t: t.noise.sum())
        qualified = source.qualify(
            bound,
            PhysicalRequirement(
                "source_deadline.deadline", 1, frozenset({"scan", "join", "group"})
            ),
        )
        read = source.compile(
            qualified,
            expression,
            purpose="source_deadline.deadline",
            expected_schema=expression.schema().to_pyarrow(),
        )
        source._checkpoint = check
        owner_thread = get_ident()
        interrupt_threads: list[int] = []

        def request() -> None:
            interrupt_threads.append(get_ident())
            source._request_interrupt()

        started = time.monotonic()
        token = CURRENT.set(ExecuteDeadline(started, seconds=0.05))
        try:
            with (
                pytest.raises(DomainPreparationError, match="execute_timeout"),
                guard(request),
                closing(source.batches(read, chunk_size=1)) as stream,
            ):
                list(stream)
        finally:
            CURRENT.reset(token)
        assert time.monotonic() - started < 3
        assert not source._closed
        source.close()
        assert len(interrupt_threads) == 1 and interrupt_threads[0] != owner_thread
        assert source._closed and not source._streams
        assert source.submissions[-1].state == "failed"
        assert source.submissions[-1].termination == "local_closed"
        assert source.submissions[-1].connection_disconnected
        receipt(
            "deadline-native-" + backend,
            {
                "backend": backend,
                "deadline_seconds": 0.05,
                "elapsed_seconds": time.monotonic() - started,
                "sql": read.sql,
                "state": source.submissions[-1].state,
                "cursor_state": source.submissions[-1].cursor_state,
                "termination": source.submissions[-1].termination,
                "connection_disconnected": source.submissions[-1].connection_disconnected,
                "cleanup_on_owner_thread": True,
                "interrupt_on_timer_thread": True,
            },
        )


@pytest.mark.runtime
def test_unconfirmed_source_disconnect_prevents_publication(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.datasets.errors import DatasetConstructionError
    from marivo.datasource import runtime as datasource_runtime

    case = analysis_dsl_case_factory("j2")
    logical = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .deviation(method="zscore")
    )
    original = datasource_runtime._disconnect

    def unconfirmed(backend: object) -> bool:
        assert original(backend)
        return False

    monkeypatch.setattr(datasource_runtime, "_disconnect", unconfirmed)
    with pytest.raises(DatasetConstructionError, match="confirmed source connection release"):
        logical.execute()
    runtime = case.session._runtime
    assert runtime.last_run_ref is not None
    run = runtime.store._graph_run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert runtime.store.resources(runtime.session_ref) == ()
    with runtime.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == 0
    receipt(
        "deadline-unconfirmed-release",
        {
            "run_lifecycle": run.lifecycle,
            "published_artifacts": 0,
            "resources": 0,
            "unconfirmed_release_rejected": True,
        },
    )


@pytest.mark.runtime
def test_postgres_cancel_has_independent_server_termination_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    from tests.datasource.environment import postgres_analysis as pg

    with source_case("postgres", "table", tmp_path, monkeypatch) as case:
        source = case.session
        bound = source.bind(case.source, source_identity="source_deadline.postgres-cancel")
        connection = getattr(source._backend, "con", None)
        assert isinstance(connection, psycopg.Connection)
        backend_pid = connection.info.backend_pid
        expression = bound.relation
        for _ in range(18):
            expression = expression.cross_join(bound.relation.view()).select(expression)
        expression = expression.mutate(noise=ibis.random()).aggregate(value=lambda t: t.noise.sum())
        qualified = source.qualify(
            bound,
            PhysicalRequirement("source_deadline.cancel", 1, frozenset({"scan", "join", "group"})),
        )
        read = source.compile(
            qualified,
            expression,
            purpose="source_deadline.cancel",
            expected_schema=expression.schema().to_pyarrow(),
        )
        source._checkpoint = check
        started = time.monotonic()
        token = CURRENT.set(ExecuteDeadline(started, seconds=0.05))
        try:
            with (
                pytest.raises(DomainPreparationError, match="execute_timeout") as raised,
                guard(source._request_interrupt),
                closing(source.batches(read, chunk_size=1)) as stream,
            ):
                list(stream)
        finally:
            CURRENT.reset(token)
        assert time.monotonic() - started < 3
        assert isinstance(raised.value.__context__, psycopg.errors.QueryCanceled)
        assert raised.value.__context__.sqlstate == "57014"
        assert not source._closed
        source.close()
        assert source.submissions[-1].state == "failed"
        assert source.submissions[-1].connection_disconnected
        assert source.submissions[-1].termination == "remote_unknown"
        with pg.connection(admin=True) as observer:
            assert (
                observer.execute(
                    "SELECT pid FROM pg_stat_activity WHERE pid = %s", (backend_pid,)
                ).fetchone()
                is None
            )
        receipt(
            "deadline-native-postgres",
            {
                "backend": "postgres",
                "deadline_seconds": 0.05,
                "elapsed_seconds": time.monotonic() - started,
                "sql": read.sql,
                "state": "failed",
                "sqlstate": "57014",
                "termination": "remote_unknown",
                "connection_disconnected": True,
                "cleanup_on_owner_thread": True,
                "independent_server_termination": True,
                "backend_pid": backend_pid,
                "psycopg_version": psycopg.__version__,
            },
        )


@pytest.mark.runtime
@pytest.mark.parametrize("phase", ["pending", "initial-response", "fetch"])
def test_trino_cancel_has_independent_server_termination_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from trino.dbapi import Cursor

    with source_case("trino", "iceberg", tmp_path, monkeypatch) as case:
        source = case.session
        bound = source.bind(case.source, source_identity="source_deadline.trino-cancel")
        expression = bound.relation
        for _ in range(18):
            expression = expression.cross_join(bound.relation.view()).select(expression)
        expression = expression.mutate(noise=ibis.random())
        if phase != "fetch":
            expression = expression.aggregate(value=lambda t: t.noise.sum())
        qualified = source.qualify(
            bound,
            PhysicalRequirement("source_deadline.cancel", 1, frozenset({"scan", "join", "group"})),
        )
        read = source.compile(
            qualified,
            expression,
            purpose="source_deadline.cancel",
            expected_schema=expression.schema().to_pyarrow(),
        )
        cursors: list[Cursor] = []
        close_threads: list[int] = []
        native_cancel_threads: list[int] = []
        original = source._own_pending_cursor
        close_cursor: Callable[[Cursor], None] = Cursor.close
        cancel_cursor: Callable[[Cursor], None] = Cursor.cancel

        def native_cancel(cursor: Cursor) -> None:
            if any(cursor is owned for owned in cursors):
                native_cancel_threads.append(get_ident())
            cancel_cursor(cursor)

        def close(cursor: Cursor) -> None:
            if any(cursor is owned for owned in cursors):
                close_threads.append(get_ident())
            close_cursor(cursor)

        monkeypatch.setattr(Cursor, "close", close)
        monkeypatch.setattr(Cursor, "cancel", native_cancel)

        def own(cursor: object) -> None:
            assert isinstance(cursor, Cursor)
            cursors.append(cursor)
            original(cursor)

        monkeypatch.setattr(source, "_own_pending_cursor", own)
        source._checkpoint = check
        requested = Event()
        if phase == "initial-response":
            execute: Callable[[Cursor, str], Cursor] = Cursor.execute

            def delayed_execute(cursor: Cursor, sql: str) -> Cursor:
                assert requested.wait(3)
                return execute(cursor, sql)

            monkeypatch.setattr(Cursor, "execute", delayed_execute)
        prepared = source.batches(read, chunk_size=1024) if phase == "fetch" else None
        started = time.monotonic()
        token = CURRENT.set(ExecuteDeadline(started, seconds=1))
        owner_thread = get_ident()
        cancel_threads: list[int] = []

        def cancel() -> None:
            cancel_threads.append(get_ident())
            requested.set()
            source._request_interrupt()

        try:
            with (
                pytest.raises(DomainPreparationError, match="execute_timeout"),
                guard(cancel),
                closing(
                    prepared if prepared is not None else source.batches(read, chunk_size=1)
                ) as stream,
            ):
                list(stream)
        finally:
            CURRENT.reset(token)
        elapsed = time.monotonic() - started
        assert 1 < elapsed < 5
        assert (phase == "fetch" or cancel_threads) and all(
            thread != owner_thread for thread in cancel_threads
        )
        assert native_cancel_threads
        if not cancel_threads:
            assert all(thread == owner_thread for thread in native_cancel_threads)
        assert len(cursors) == 1
        query_id = cursors[0].query_id
        assert isinstance(query_id, str)
        request = Request(
            "http://127.0.0.1:18080/v1/query/" + query_id,
            headers={"X-Trino-User": "qualifier"},
        )
        with urlopen(request, timeout=5) as response:
            observed = json.load(response)
        assert observed["state"] == "FAILED"
        assert observed["errorCode"]["name"] == "USER_CANCELED"
        assert observed["query"] == read.sql
        assert not source._closed
        source.close()
        assert close_threads and all(thread == owner_thread for thread in close_threads)
        submission = source.submissions[-1]
        assert submission.state == "failed"
        assert submission.cursor_state == "closed"
        assert submission.connection_disconnected
        assert submission.termination == "remote_unknown"
        receipt(
            "deadline-native-trino-" + phase,
            {
                "backend": "trino",
                "profile": "iceberg",
                "phase": phase,
                "deadline_seconds": 1,
                "elapsed_seconds": elapsed,
                "sql": read.sql,
                "query_id": query_id,
                "state": "failed",
                "server_state": observed["state"],
                "server_error": observed["errorCode"]["name"],
                "termination": "remote_unknown",
                "interrupt_on_timer_thread": bool(cancel_threads),
                "cancellation_mode": "timer" if cancel_threads else "owner_checkpoint",
                "native_cancel_on_owner_thread": owner_thread in native_cancel_threads,
                "connection_disconnected": True,
                "cleanup_on_owner_thread": True,
                "independent_server_termination": True,
            },
        )


@pytest.mark.runtime
def test_clickhouse_native_timeout_before_monotonic_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deadline_type = ExecuteDeadline

    def frozen_clock(start: float, *, seconds: float) -> ExecuteDeadline:
        return deadline_type(start, clock=lambda: start, seconds=seconds)

    directory = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
    monkeypatch.setattr(sys.modules[__name__], "ExecuteDeadline", frozen_clock)
    monkeypatch.setenv("MARIVO_R93_EVIDENCE_DIR", str(tmp_path))
    test_clickhouse_deadline_has_independent_server_termination_proof(
        tmp_path, monkeypatch, "pending"
    )
    observed: dict[str, object] = json.loads(
        (tmp_path / "deadline-native-clickhouse-pending.json").read_text()
    )
    assert observed["reported_error"] == "native_http_159"
    assert observed["timer_request_observed"] is False
    if directory:
        Path(directory, "supporting-clickhouse-native-first.json").write_text(
            json.dumps(observed, sort_keys=True)
        )


@pytest.mark.runtime
def test_trino_fetch_checkpoint_cancels_on_owner_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DeferredTimer:
        def __init__(self, seconds: float, callback: Callable[[], object]):
            self.daemon = False

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

        def join(self) -> None:
            pass

    directory = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
    monkeypatch.setattr("marivo.analysis.materialization.execute_deadline.Timer", DeferredTimer)
    monkeypatch.setenv("MARIVO_R93_EVIDENCE_DIR", str(tmp_path))
    test_trino_cancel_has_independent_server_termination_proof(tmp_path, monkeypatch, "fetch")
    observed: dict[str, object] = json.loads(
        (tmp_path / "deadline-native-trino-fetch.json").read_text()
    )
    assert observed["cancellation_mode"] == "owner_checkpoint"
    assert observed["interrupt_on_timer_thread"] is False
    assert observed["native_cancel_on_owner_thread"] is True
    if directory:
        Path(directory, "supporting-trino-owner-checkpoint.json").write_text(
            json.dumps(observed, sort_keys=True)
        )


@pytest.mark.runtime
@pytest.mark.parametrize("phase", ["pending", "fetch"])
def test_clickhouse_deadline_has_independent_server_termination_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    error_type: object = import_module("clickhouse_connect.driver.exceptions").DatabaseError
    assert isinstance(error_type, type) and issubclass(error_type, Exception)
    from urllib3.response import HTTPResponse

    from tests.datasource.environment import clickhouse_analysis as ch

    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch) as case:
        source = case.session
        bound = source.bind(case.source, source_identity="source_deadline.clickhouse-deadline")
        expression = bound.relation
        for _ in range(18):
            expression = expression.cross_join(bound.relation.view()).select(expression)
        expression = expression.mutate(noise=ibis.random())
        if phase == "pending":
            expression = expression.aggregate(value=lambda t: t.noise.sum())
        qualified = source.qualify(
            bound,
            PhysicalRequirement("source_deadline.cancel", 1, frozenset({"scan", "join", "group"})),
        )
        read = source.compile(
            qualified,
            expression,
            purpose="source_deadline.cancel",
            expected_schema=expression.schema().to_pyarrow(),
        )
        client = getattr(source._backend, "con", None)
        assert isinstance(client, _ClickHouseReadClient)
        native: _NativeClickHouseRead = client.query_rows_stream
        requests: list[dict[str, str | float | int]] = []

        def stream(
            query: str, *, settings: dict[str, str | float | int] | None = None
        ) -> _ClickHouseNativeStream:
            assert query == read.sql and settings is not None
            requests.append(dict(settings))
            return native(query, settings=settings)

        monkeypatch.setattr(client, "query_rows_stream", stream)
        close_response: Callable[[HTTPResponse], None] = HTTPResponse.close
        close_threads: list[int] = []

        def response_closed(response: HTTPResponse) -> None:
            if (
                requests
                and response.headers.get("X-ClickHouse-Query-Id") == requests[0]["query_id"]
            ):
                close_threads.append(get_ident())
            close_response(response)

        monkeypatch.setattr(HTTPResponse, "close", response_closed)
        source._checkpoint = check
        source._seconds_remaining = remaining
        started = time.monotonic()
        token = CURRENT.set(ExecuteDeadline(started, seconds=1))
        owner = get_ident()
        cancel_threads: list[int] = []
        entered_stream = False

        def interrupt() -> None:
            cancel_threads.append(get_ident())
            source._request_interrupt()

        try:
            with (
                pytest.raises((DomainPreparationError, error_type)) as expired,
                guard(interrupt),
                closing(source.batches(read, chunk_size=1024)) as rows,
            ):
                entered_stream = True
                for _batch in rows:
                    pass
        finally:
            CURRENT.reset(token)
        elapsed = time.monotonic() - started
        if isinstance(expired.value, DomainPreparationError):
            assert expired.value.constraint_id == "r7.execute_timeout"
        else:
            assert isinstance(expired.value, _ClickHouseServerError)
            assert expired.value.code == 159
        assert entered_stream is (phase == "fetch")
        assert all(thread != owner for thread in cancel_threads)
        assert close_threads and all(thread == owner for thread in close_threads)
        assert len(requests) == 1
        settings = requests[0]
        assert 0 < elapsed < 3
        assert 0 < float(settings["max_execution_time"]) <= 1
        assert settings["timeout_before_checking_execution_speed"] == 0
        assert settings["timeout_overflow_mode"] == "throw"
        query_id = settings["query_id"]
        assert isinstance(query_id, str)
        with ch.connection(admin=True) as observer:
            observed: list[tuple[object, ...]] = []
            until = time.monotonic() + 3
            while not observed and time.monotonic() < until:
                observer.command("SYSTEM FLUSH LOGS")
                observed = [
                    tuple(row)
                    for row in observer.query(
                        "SELECT type, exception_code, query FROM system.query_log "
                        "WHERE query_id = {id:String} AND type = 'ExceptionWhileProcessing'",
                        parameters={"id": query_id},
                    ).result_rows
                ]
                if not observed:
                    time.sleep(0.02)
            assert len(observed) == 1
            assert observed[0][:2] == ("ExceptionWhileProcessing", 159)
            server_sql = observed[0][2]
            assert isinstance(server_sql, str) and server_sql.startswith(read.sql)
            assert server_sql[len(read.sql) :].strip() == "FORMAT Native"
            assert observer.query(
                "SELECT count() FROM system.processes WHERE query_id = {id:String}",
                parameters={"id": query_id},
            ).first_row == (0,)
        source.close()
        submission = source.submissions[-1]
        assert submission.state == "failed" and submission.cursor_state == "closed"
        assert submission.connection_disconnected and submission.termination == "remote_unknown"
        receipt(
            "deadline-native-clickhouse-" + phase,
            {
                "backend": "clickhouse",
                "profile": "mergetree",
                "phase": phase,
                "deadline_seconds": 1,
                "elapsed_seconds": elapsed,
                "sql": read.sql,
                "server_sql": server_sql,
                "query_id": query_id,
                "settings": settings,
                "state": "failed",
                "server_event": "ExceptionWhileProcessing",
                "server_code": 159,
                "active_queries": 0,
                "termination": "remote_unknown",
                "deadline_mode": "native_http",
                "reported_error": "native_http_159"
                if not isinstance(expired.value, DomainPreparationError)
                else "monotonic_deadline",
                "timer_request_observed": bool(cancel_threads),
                "connection_disconnected": True,
                "cleanup_on_owner_thread": True,
                "independent_server_termination": True,
            },
        )


@pytest.mark.runtime
def test_mysql_cancel_has_independent_server_termination_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    with source_case("mysql", "table", tmp_path, monkeypatch) as case:
        source = case.session
        source._prepare_interrupt()
        thread_id = source._backend.con.thread_id()
        assert source._cancel_control is not None
        control = source._cancel_control
        control_id = control.con.thread_id()
        owner_thread = get_ident()
        cleanup_threads: list[int] = []
        synchronize = source._synchronize_interrupt
        data_disconnect = source._backend.disconnect
        control_disconnect = control.disconnect

        def synchronize_on_owner() -> None:
            cleanup_threads.append(get_ident())
            synchronize()

        def disconnect_data_on_owner() -> None:
            cleanup_threads.append(get_ident())
            data_disconnect()

        def disconnect_control_on_owner() -> None:
            cleanup_threads.append(get_ident())
            control_disconnect()

        monkeypatch.setattr(source, "_synchronize_interrupt", synchronize_on_owner)
        monkeypatch.setattr(source._backend, "disconnect", disconnect_data_on_owner)
        monkeypatch.setattr(control, "disconnect", disconnect_control_on_owner)
        assert control_id != thread_id
        bound = source.bind(case.source, source_identity="source_deadline.mysql-cancel")
        expression = bound.relation
        for _ in range(18):
            expression = expression.cross_join(bound.relation.view()).select(expression)
        expression = expression.mutate(noise=ibis.random()).aggregate(value=lambda t: t.noise.sum())
        qualified = source.qualify(
            bound,
            PhysicalRequirement("source_deadline.cancel", 1, frozenset({"scan", "join", "group"})),
        )
        read = source.compile(
            qualified,
            expression,
            purpose="source_deadline.cancel",
            expected_schema=expression.schema().to_pyarrow(),
        )
        source._checkpoint = check
        started = time.monotonic()
        token = CURRENT.set(ExecuteDeadline(started, seconds=0.05))
        try:
            with (
                pytest.raises(DomainPreparationError, match="execute_timeout"),
                guard(source._request_interrupt),
                closing(source.batches(read, chunk_size=1)) as stream,
            ):
                list(stream)
        finally:
            CURRENT.reset(token)
        elapsed = time.monotonic() - started
        assert elapsed < 3
        assert len(source.cancel_submissions) == 1
        submission = source.cancel_submissions[0]
        assert submission.sql == f"KILL QUERY {thread_id}"
        assert submission.state == "succeeded"
        source.close()
        assert source._cancel_control is None
        assert source.submissions[-1].connection_disconnected
        assert source.submissions[-1].cursor_state == "closed"
        assert len(cleanup_threads) >= 3
        assert set(cleanup_threads) == {owner_thread}
        from tests.datasource.mysql_server_observation import wait_for_owned_connection_release

        release_observation_seconds = wait_for_owned_connection_release(thread_id, control_id)
        receipt(
            "deadline-native-mysql",
            {
                "backend": "mysql",
                "release_observation_seconds": release_observation_seconds,
                "deadline_seconds": 0.05,
                "elapsed_seconds": elapsed,
                "sql": read.sql,
                "state": "failed",
                "termination": "remote_unknown",
                "connection_disconnected": True,
                "cleanup_on_owner_thread": set(cleanup_threads) == {owner_thread},
                "cleanup_observations": len(cleanup_threads),
                "independent_server_termination": True,
                "data_connection_id": thread_id,
                "control_connection_id": control_id,
                "control_statement": submission.sql,
                "control_state": submission.state,
                "control_connection_disconnected": True,
            },
        )
