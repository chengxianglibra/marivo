"""Real transport phase aborts retain the prior public graph publication."""

from __future__ import annotations

import json
import os
import signal
import time
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from threading import Event, Lock, Thread, get_ident
from typing import Literal, Protocol
from urllib.request import Request, urlopen

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest
from requests import Response
from trino.client import TrinoRequest
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
    SourceBatchStream,
    SourceSession,
    _Cursor,
)
from marivo.semantic.reader import SemanticProject
from tests.json_support import Json, checked, encode, obj
from tests.mysql_server_observation import wait_for_owned_connection_release
from tests.r9_source_cases import source_case
from tests.shared_fixtures import run_ids
from tests.test_r93_capability_consumers import _author_c05_project
from tests.test_r93_mysql_cancellation import _DriverClose, _DriverExecute, _MysqlCursor
from tests.test_r93_reference_consumers import _data


class _DriverFetch(Protocol):
    def __call__(
        self, cursor: _MysqlCursor, size: int | None = None
    ) -> Sequence[Sequence[object]]: ...


class _DeferredTimer:
    def __init__(self, seconds: float, callback: Callable[[], object]):
        self.daemon = False

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def join(self) -> None:
        pass


def _trino_query(query_id: str) -> dict[str, Json]:
    request = Request(
        "http://127.0.0.1:18080/v1/query/" + query_id,
        headers={"X-Trino-User": "qualifier"},
    )
    with urlopen(request, timeout=3) as response:
        return obj(checked(json.load(response)))


def _trino_active(sql: str) -> dict[str, Json] | None:
    request = Request("http://127.0.0.1:18080/v1/query", headers={"X-Trino-User": "qualifier"})
    with urlopen(request, timeout=3) as response:
        queries = checked(json.load(response))
    assert isinstance(queries, list)
    for raw in queries:
        query = obj(raw)
        if query.get("query") == sql and query.get("state") == "RUNNING":
            identity = query["queryId"]
            assert isinstance(identity, str)
            return {"query_id": identity, "state": "RUNNING", "sql": sql}
    return None


def _publications(session: mv.Session) -> tuple[int, ...]:
    counts: list[int] = []
    with session._runtime.store._connection() as connection:
        for table in ("dataset_artifacts", "dataset_evidence", "findings"):
            row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
            assert row is not None and isinstance(row[0], int)
            counts.append(row[0])
    return tuple(counts)


def _graph_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: Literal["mysql", "trino"],
    phase: Literal["initial-response", "fetch"],
    mode: Literal["timer", "owner_checkpoint", "sigint"],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = "iceberg" if backend == "trino" else "table"
    with source_case(backend, profile, tmp_path, monkeypatch, _data(backend)) as case:
        _author_c05_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-graph-phase", report_timezone="UTC")
        logical = session.members(ms.ref.entity("sales.facts")).read(
            ms.ref.measure("sales.facts.amount")
        )
        previous = logical.execute()
        assert previous._dataset is not None
        retained = previous._dataset.verified()
        descriptor = previous._dataset.artifact.descriptor
        contract = asdict(previous.contract())
        state = previous.state
        frame = previous.to_pandas()
        runtime = session._runtime
        before_runs = run_ids(session)
        before = _publications(session)
        assert runtime.store.resources(session.id) == ()
        owner_thread = get_ident()
        phase_ready = Event()
        release = Event()
        stopped = Event()
        interrupt_requested = Event()
        native_cancel_completed = Event()
        signal_gate = Lock()
        owners: list[SourceSession] = []
        cursors: list[Cursor] = []
        deadlines: list[execute_deadline.ExecuteDeadline] = []
        compiled: list[str] = []
        native_fetches: list[int] = []
        arrow_batches: list[int] = []
        interrupt_threads: list[int] = []
        interrupt_states: list[dict[str, Json]] = []
        close_threads: list[int] = []
        close_failure_threads: list[int] = []
        native_cancels: list[tuple[int, str | None]] = []
        mysql_cancel_threads: list[int] = []
        mysql_ids: list[int] = []
        mysql_target_cursors: set[int] = set()
        driver_submissions: list[tuple[int, str]] = []
        control_close_threads: list[int] = []
        observation: dict[str, Json] = {}

        deadline_type = execute_deadline.ExecuteDeadline

        def bounded(start: float) -> execute_deadline.ExecuteDeadline:
            deadline = deadline_type(start, seconds=600 if mode == "sigint" else 5)
            deadlines.append(deadline)
            return deadline

        monkeypatch.setattr(execute_deadline, "ExecuteDeadline", bounded)
        if mode == "owner_checkpoint":
            monkeypatch.setattr(execute_deadline, "Timer", _DeferredTimer)

        compile_read = SourceSession.compile

        def streaming_compile(
            source: SourceSession,
            qualified: QualifiedSource | Sequence[QualifiedSource],
            expression: ir.Expr,
            *,
            params: Mapping[ir.Scalar, Parameter] | None = None,
            purpose: str,
            expected_schema: pa.Schema,
        ) -> CompiledRead:
            if purpose == "analysis.graph.stage":
                assert isinstance(expression, ir.Table)
                bound = qualified if isinstance(qualified, QualifiedSource) else qualified[0]
                # Only two batches are consumed; the remaining finite output keeps the query active.
                for _ in range(13 if backend == "trino" else 12):
                    expression = expression.cross_join(bound.binding.relation.view()).select(
                        expression
                    )
                expression = expression.filter(ibis.random() > 0.5)
            read = compile_read(
                source,
                qualified,
                expression,
                params=params,
                purpose=purpose,
                expected_schema=expected_schema,
            )
            if purpose == "analysis.graph.stage":
                compiled.append(read.sql)
            return read

        monkeypatch.setattr(SourceSession, "compile", streaming_compile)
        prepare = SourceSession._prepare_interrupt
        own = SourceSession._own_pending_cursor
        request_interrupt = SourceSession._request_interrupt
        close_source = SourceSession.close
        iterate = SourceBatchStream._iterate

        def prepared(source: SourceSession) -> None:
            prepare(source)
            owners.append(source)
            if backend == "mysql":
                assert source._cancel_control is not None
                control = source._cancel_control
                mysql_ids.extend((source._backend.con.thread_id(), control.con.thread_id()))
                disconnect = control.disconnect

                def control_closed() -> None:
                    control_close_threads.append(get_ident())
                    disconnect()

                monkeypatch.setattr(control, "disconnect", control_closed)

        def owned(source: SourceSession, cursor: _Cursor) -> None:
            if isinstance(cursor, Cursor):
                cursors.append(cursor)
            own(source, cursor)

        def requested(source: SourceSession) -> None:
            if source in owners:
                interrupt_threads.append(get_ident())
                interrupt_states.append(
                    {
                        "source_closed": source._closed,
                        "submission_states": [item.state for item in source.submissions],
                    }
                )
                interrupt_requested.set()
            request_interrupt(source)

        def closed(source: SourceSession) -> None:
            if source in owners:
                close_threads.append(get_ident())
            close_source(source)

        def batches(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
            for batch in iterate(stream):
                if stream._session in owners:
                    arrow_batches.append(batch.num_rows)
                yield batch

        monkeypatch.setattr(SourceSession, "_prepare_interrupt", prepared)
        monkeypatch.setattr(SourceSession, "_own_pending_cursor", owned)
        monkeypatch.setattr(SourceSession, "_request_interrupt", requested)
        monkeypatch.setattr(SourceSession, "close", closed)
        monkeypatch.setattr(SourceBatchStream, "_iterate", batches)

        def fetch_gate(count: int) -> None:
            native_fetches.append(count)
            if len(native_fetches) == 2:
                phase_ready.set()
                assert release.wait(12), "The observed transport phase was not released"

        if backend == "trino":
            cancel_cursor: Callable[[Cursor], None] = Cursor.cancel
            close_cursor: Callable[[Cursor], None] = Cursor.close
            fetch_cursor: Callable[[Cursor, int | None], Sequence[Sequence[object]]] = (
                Cursor.fetchmany
            )
            post: Callable[[TrinoRequest, str, dict[str, object] | None], Response] = (
                TrinoRequest.post
            )

            def cancelled(cursor: Cursor) -> None:
                if cursor in cursors:
                    native_cancels.append((get_ident(), cursor.query_id))
                cancel_cursor(cursor)
                if cursor in cursors and mode == "timer":
                    native_cancel_completed.set()
                    release.set()

            def cursor_closed(cursor: Cursor) -> None:
                if cursor in cursors:
                    close_threads.append(get_ident())
                close_cursor(cursor)

            def fetched(cursor: Cursor, size: int | None = None) -> Sequence[Sequence[object]]:
                rows = fetch_cursor(cursor, size)
                if phase == "fetch" and compiled and cursor.query == compiled[0] and rows:
                    fetch_gate(len(rows))
                return rows

            def posted(
                request: TrinoRequest,
                sql: str,
                additional_http_headers: dict[str, object] | None = None,
            ) -> Response:
                response = post(request, sql, additional_http_headers)
                if phase == "initial-response" and compiled and sql == compiled[0]:
                    assert cursors and cursors[-1].query_id is None
                    payload = obj(checked(json.loads(response.content)))
                    identity, next_uri = payload["id"], payload["nextUri"]
                    assert response.status_code == 200 and isinstance(identity, str)
                    assert isinstance(next_uri, str) and identity in next_uri
                    assert obj(payload["stats"])["state"] == "QUEUED"
                    assert payload.get("error") is None
                    observation["initial_response"] = {
                        "http_status": response.status_code,
                        "query_id": identity,
                        "next_uri": next_uri,
                        "stats": payload["stats"],
                        "driver_query_id": cursors[-1].query_id,
                    }
                    phase_ready.set()
                    assert release.wait(12), "The initial response was not released"
                return response

            monkeypatch.setattr(Cursor, "cancel", cancelled)
            monkeypatch.setattr(Cursor, "close", cursor_closed)
            monkeypatch.setattr(Cursor, "fetchmany", fetched)
            monkeypatch.setattr(TrinoRequest, "post", posted)
        else:
            driver = import_module("MySQLdb.cursors")
            execute: _DriverExecute = driver.BaseCursor.execute
            close: _DriverClose = driver.BaseCursor.close
            fetch: _DriverFetch = driver.CursorUseResultMixIn.fetchmany

            def executed(
                cursor: _MysqlCursor, sql: str | bytes, args: object | None = None
            ) -> int | None:
                assert cursor.connection is not None
                text = sql.decode() if isinstance(sql, bytes) else sql
                driver_submissions.append((cursor.connection.thread_id(), text))
                if text.startswith("KILL"):
                    mysql_cancel_threads.append(get_ident())
                if compiled and text == compiled[0]:
                    mysql_target_cursors.add(id(cursor))
                return execute(cursor, sql, args)

            def mysql_closed(cursor: _MysqlCursor) -> None:
                owned_cursor = (
                    mysql_ids
                    and cursor.connection is not None
                    and cursor.connection.thread_id() == mysql_ids[0]
                )
                if owned_cursor:
                    close_threads.append(get_ident())
                try:
                    close(cursor)
                except BaseException:
                    if owned_cursor:
                        close_failure_threads.append(get_ident())
                    raise

            def mysql_fetched(
                cursor: _MysqlCursor, size: int | None = None
            ) -> Sequence[Sequence[object]]:
                rows = fetch(cursor, size)
                if id(cursor) in mysql_target_cursors and rows:
                    fetch_gate(len(rows))
                return rows

            monkeypatch.setattr(driver.BaseCursor, "execute", executed)
            monkeypatch.setattr(driver.BaseCursor, "close", mysql_closed)
            monkeypatch.setattr(driver.CursorUseResultMixIn, "fetchmany", mysql_fetched)

        def active() -> dict[str, Json] | None:
            assert compiled
            if backend == "trino":
                return _trino_active(compiled[0])
            from tests.multisource_environment import mysql_analysis as mysql

            assert mysql_ids
            with mysql.connection(admin=True) as observer, observer.cursor() as cursor:
                cursor.execute(
                    "SELECT COMMAND,INFO FROM information_schema.PROCESSLIST WHERE ID=%s",
                    (mysql_ids[0],),
                )
                row = cursor.fetchone()
            if row and row == ("Query", compiled[0]):
                return {"data_connection_id": mysql_ids[0], "state": "Query", "sql": compiled[0]}
            return None

        def controller() -> None:
            try:
                until = time.monotonic() + 10
                while (
                    not phase_ready.is_set() and not stopped.is_set() and time.monotonic() < until
                ):
                    stopped.wait(0.02)
                if stopped.is_set():
                    return
                assert phase_ready.is_set(), "The real transport phase was not entered"
                if phase == "initial-response":
                    initial = obj(observation["initial_response"])
                    request = Request(
                        "http://127.0.0.1:18080/v1/query",
                        headers={"X-Trino-User": "qualifier"},
                    )
                    with urlopen(request, timeout=3) as response:
                        queries = checked(json.load(response))
                    assert isinstance(queries, list)
                    matching: list[Json] = [
                        raw
                        for raw in queries
                        if obj(raw).get("query") == compiled[0]
                        or obj(raw).get("queryId") == initial["query_id"]
                    ]
                    observation["dispatch_manager_matches"] = matching
                    assert matching == []
                    # Trino 483 registers the queued statement on POST and dispatches on GET.
                    observation["query_id"] = initial["query_id"]
                    observation["queued_response_state"] = "QUEUED"
                    observation["dispatch_registered_before_first_cancel"] = False
                    observation["remote_active_before_first_cancel_proven"] = False
                else:
                    until = time.monotonic() + 3
                    observed = active()
                    while observed is None and time.monotonic() < until and not stopped.is_set():
                        stopped.wait(0.02)
                        observed = active()
                    assert observed is not None, "The owned query was not independently active"
                    observation.update(observed)
                observation["phase_observed"] = phase
                if phase == "fetch":
                    assert arrow_batches and native_fetches[:2] == [1024, 1024]
                if mode == "sigint":
                    with signal_gate:
                        if not stopped.is_set():
                            observation["signal_sent"] = True
                            os.kill(os.getpid(), signal.SIGINT)
                elif mode == "owner_checkpoint":
                    assert len(deadlines) == 1
                    end = deadlines[0].start + deadlines[0].seconds + 0.02
                    while not stopped.is_set() and time.monotonic() < end:
                        stopped.wait(min(0.02, end - time.monotonic()))
                    assert active() is not None, "The query finished before the owner checkpoint"
                    observation["active_at_expiry"] = True
                else:
                    assert interrupt_requested.wait(8), "The execution timer did not request cancel"
                    if backend == "trino":
                        assert native_cancel_completed.wait(3), "Native timer cancel did not return"
            except Exception as error:
                observation["controller_error"] = str(error)
                if mode == "sigint":
                    with signal_gate:
                        if not stopped.is_set():
                            os.kill(os.getpid(), signal.SIGINT)
            finally:
                release.set()

        worker = Thread(target=controller, daemon=True)
        worker.start()
        started = time.monotonic()
        interruption: BaseException | None = None
        try:
            try:
                logical.execute()
            except BaseException as error:
                interruption = error
                observation["reported_error"] = str(error)
                observation["reported_error_type"] = type(error).__name__
                causes: list[Json] = []
                current_error: BaseException | None = error
                seen_errors: set[int] = set()
                while current_error is not None and id(current_error) not in seen_errors:
                    seen_errors.add(id(current_error))
                    causes.append(
                        {
                            "type": type(current_error).__name__,
                            "message": str(current_error),
                            "locations": [
                                f"{item.filename}:{item.lineno}:{item.name}"
                                for item in traceback.extract_tb(current_error.__traceback__)
                            ],
                        }
                    )
                    current_error = current_error.__cause__ or current_error.__context__
                observation["error_chain"] = causes
        finally:
            with signal_gate:
                stopped.set()
            release.set()
            worker.join(5)
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"graph-transport-phase-{backend}-{phase}-{mode}.json").write_bytes(
                encode(
                    checked(
                        {
                            "observation": observation,
                            "worker_stopped": not worker.is_alive(),
                            "elapsed_seconds": time.monotonic() - started,
                            "native_fetch_rows": native_fetches,
                            "arrow_batch_rows": arrow_batches,
                            "native_cancels": [list(item) for item in native_cancels],
                            "publication_counts_before": list(before),
                            "publication_counts_after": list(_publications(session)),
                            "new_runs": len(run_ids(session) - before_runs),
                            "resources_remaining": len(runtime.store.resources(session.id)),
                        }
                    )
                )
            )
        assert not worker.is_alive()
        assert interruption is not None, "The interrupted graph succeeded"
        assert "controller_error" not in observation, observation
        assert observation["phase_observed"] == phase
        assert len(compiled) == len(owners) == len(deadlines) == 1
        assert execute_deadline.CURRENT.get() is None
        assert _publications(session) == before
        assert len(run_ids(session) - before_runs) == 1
        assert runtime.last_run_ref is not None
        run = runtime.store._graph_run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed"
        assert runtime.store.resources(session.id) == ()
        assert previous.state == state and previous._dataset.artifact.descriptor == descriptor
        assert asdict(previous.contract()) == contract and previous.to_pandas().equals(frame)
        current = previous._dataset.verified()
        assert current.primary.equals(retained.primary, check_metadata=True)
        assert tuple(part.role for part in current.parts) == tuple(
            part.role for part in retained.parts
        )
        assert all(
            current_part.table.equals(old_part.table, check_metadata=True)
            for current_part, old_part in zip(current.parts, retained.parts, strict=True)
        )
        assert all(source._closed and not source._streams for source in owners)
        assert close_threads and set(close_threads) == {owner_thread}
        assert all(
            submission.state == "failed" and submission.connection_disconnected
            for source in owners
            for submission in source.submissions
        )
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(
                directory, f"graph-transport-diagnostic-{backend}-{phase}-{mode}.json"
            ).write_bytes(
                encode(
                    checked(
                        {
                            "observation": observation,
                            "elapsed_seconds": time.monotonic() - started,
                            "native_fetch_rows": native_fetches,
                            "arrow_batch_rows": arrow_batches,
                            "interrupt_threads": interrupt_threads,
                            "interrupt_states": interrupt_states,
                            "close_threads": close_threads,
                            "close_failure_threads": close_failure_threads,
                            "mysql_cancel_threads": mysql_cancel_threads,
                            "owner_thread": owner_thread,
                            "native_cancels": [list(item) for item in native_cancels],
                            "driver_submissions": [list(item) for item in driver_submissions],
                            "cancel_submissions": [
                                asdict(item)
                                for source in owners
                                for item in source.cancel_submissions
                            ],
                            "publication_counts_before": list(before),
                            "publication_counts_after": list(_publications(session)),
                            "run_lifecycle": run.lifecycle,
                            "resources": 0,
                            "previous_artifact_and_full_parts_preserved": True,
                        }
                    )
                )
            )
        if backend == "trino":
            assert len(cursors) == 1 and native_cancels
            identity = cursors[0].query_id
            assert isinstance(identity, str) and identity == observation["query_id"]
            observed = _trino_query(identity)
            assert observed["state"] == "FAILED" and observed["query"] == compiled[0]
            assert obj(observed["errorCode"])["name"] == "USER_CANCELED"
            if mode == "owner_checkpoint":
                assert interrupt_threads == [] and observation["active_at_expiry"] is True
                assert {thread for thread, _identity in native_cancels} == {owner_thread}
            elif mode == "timer":
                assert interrupt_threads and all(
                    thread != owner_thread for thread in interrupt_threads
                )
                assert any(thread != owner_thread for thread, _identity in native_cancels)
                if phase == "initial-response":
                    assert observation["dispatch_registered_before_first_cancel"] is False
                    assert observation["remote_active_before_first_cancel_proven"] is False
                    assert native_cancels[0][1] is None
                    assert any(candidate == identity for _thread, candidate in native_cancels)
            server: dict[str, Json] = {
                "query_id": identity,
                "state": "FAILED",
                "error": "USER_CANCELED",
            }
        else:
            assert len(mysql_ids) == 2 and mysql_ids[0] != mysql_ids[1]
            cancels = [item for source in owners for item in source.cancel_submissions]
            assert len(cancels) == 1 and cancels[0].state == "succeeded"
            assert cancels[0].sql == f"KILL QUERY {mysql_ids[0]}"
            assert [item for item in driver_submissions if item[1].startswith("KILL")] == [
                (mysql_ids[1], cancels[0].sql)
            ]
            assert control_close_threads and set(control_close_threads) == {owner_thread}
            assert owners[0]._cancel_control is None and owners[0]._cancel_control_released
            assert interrupt_threads and any(thread != owner_thread for thread in interrupt_threads)
            assert len(mysql_cancel_threads) == 1
            if mode == "timer":
                assert mysql_cancel_threads[0] != owner_thread
            server = {
                "data_connection_id": mysql_ids[0],
                "control_connection_id": mysql_ids[1],
                "connection_release_seconds": wait_for_owned_connection_release(*mysql_ids),
                "owned_control_sql": cancels[0].sql,
                "active_connections": 0,
            }
        if mode == "sigint":
            assert observation["signal_sent"] is True
            assert isinstance(interruption, KeyboardInterrupt), observation
        else:
            assert isinstance(interruption, (DomainPreparationError, MaterializationError))
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, f"graph-transport-{backend}-{phase}-{mode}.json").write_bytes(
                encode(
                    checked(
                        {
                            "backend": backend,
                            "profile": profile,
                            "phase": phase,
                            "mode": mode,
                            "elapsed_seconds": time.monotonic() - started,
                            "observation": observation,
                            "native_fetch_rows": native_fetches,
                            "arrow_batch_rows": arrow_batches,
                            "interrupt_threads": interrupt_threads,
                            "native_cancels": [list(item) for item in native_cancels],
                            "mysql_cancel_threads": mysql_cancel_threads,
                            "native_close_failure_threads": close_failure_threads,
                            "owner_thread": owner_thread,
                            "publication_counts_before": list(before),
                            "publication_counts_after": list(_publications(session)),
                            "new_failed_runs": 1,
                            "resources": 0,
                            "previous_artifact_and_full_parts_preserved": True,
                            "server": server,
                            "source_submissions": [
                                asdict(submission)
                                for source in owners
                                for submission in source.submissions
                            ],
                        }
                    )
                )
            )


@pytest.mark.runtime
@pytest.mark.parametrize(
    "phase,mode",
    (
        ("initial-response", "timer"),
        ("fetch", "timer"),
        ("fetch", "owner_checkpoint"),
        ("fetch", "sigint"),
    ),
    ids=("initial-response-timer", "fetch-timer", "fetch-owner-checkpoint", "fetch-sigint"),
)
def test_trino_graph_phase_abort_preserves_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    phase: Literal["initial-response", "fetch"],
    mode: Literal["timer", "owner_checkpoint", "sigint"],
) -> None:
    _graph_phase(tmp_path, monkeypatch, semantic_project_factory, "trino", phase, mode)


@pytest.mark.runtime
@pytest.mark.parametrize("mode", ("timer", "sigint"))
def test_mysql_graph_fetch_abort_preserves_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    mode: Literal["timer", "sigint"],
) -> None:
    _graph_phase(tmp_path, monkeypatch, semantic_project_factory, "mysql", "fetch", mode)
