"""Real local graph interruption phases retain the prior atomic publication."""

from __future__ import annotations

import os
import signal
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from threading import Event, Lock, Thread, get_ident
from typing import Literal

import duckdb
import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest
from ibis.backends import BaseBackend

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.materialization import execute_deadline
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource import adapters
from marivo.datasource.adapters import CompiledRead, Parameter, QualifiedSource, SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import source_case
from tests.shared_fixtures import run_ids
from tests.support.json import Json, checked, encode

Backend = Literal["duckdb", "sqlite"]
Phase = Literal["pending", "fetch"]
Mode = Literal["sigint", "deadline"]
NativeConnection = duckdb.DuckDBPyConnection | sqlite3.Connection


def _publication_counts(session: mv.Session) -> list[int]:
    with session._runtime.store._connection() as connection:
        counts: list[int] = []
        for table in ("dataset_artifacts", "dataset_evidence", "findings"):
            row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
            assert row is not None and isinstance(row[0], int)
            counts.append(row[0])
    return counts


def _observe_sqlite(
    connection: sqlite3.Connection,
    active: Event,
    observation: dict[str, Json],
    phase: Phase,
) -> None:
    def progress() -> int:
        # Remove this observer before signalling: Python callbacks must not
        # supply the cancellation that the native blocking route owns.
        connection.set_progress_handler(None, 0)
        observation.update({"active": True, "native_phase": phase, "sqlite_vm_steps": 1000})
        active.set()
        return 0

    connection.set_progress_handler(progress, 1000)


class _ObservedSQLiteFetch:
    def __init__(
        self,
        native: adapters._Cursor,
        connection: sqlite3.Connection,
        active: Event,
        observation: dict[str, Json],
        close_threads: list[int],
    ) -> None:
        self._native = native
        self._connection = connection
        self._active = active
        self._observation = observation
        self._close_threads = close_threads

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        if not self._active.is_set():
            _observe_sqlite(self._connection, self._active, self._observation, "fetch")
        self._observation["fetch_inflight"] = True
        try:
            return self._native.fetchmany(size)
        finally:
            self._observation["fetch_inflight"] = False
            self._connection.set_progress_handler(None, 0)

    def close(self) -> None:
        self._close_threads.append(get_ident())
        self._native.close()


@pytest.mark.runtime
@pytest.mark.parametrize(
    ("backend", "phase", "mode"),
    (
        ("duckdb", "pending", "sigint"),
        ("sqlite", "pending", "sigint"),
        ("sqlite", "fetch", "sigint"),
        ("duckdb", "pending", "deadline"),
    ),
)
def test_local_graph_native_interrupt_preserves_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: Backend,
    phase: Phase,
    mode: Mode,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case(backend, "table", tmp_path, monkeypatch, reference_data(backend)) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-local-interrupt", report_timezone="UTC")
        logical = session.members(ms.ref.entity("sales.facts")).read(
            ms.ref.measure("sales.facts.amount")
        )
        previous = logical.execute()
        original_snapshot = snapshot(previous)
        assert previous._dataset is not None
        original_tables = previous._dataset.verified()
        before = _publication_counts(session)
        before_runs = run_ids(session)
        runtime = session._runtime
        assert runtime.store.resources(runtime.session_ref) == ()
        owner_thread = get_ident()
        owners: list[SourceSession] = []
        connections: list[NativeConnection] = []
        close_threads: list[int] = []
        cursor_close_threads: list[int] = []
        interrupt_threads: list[int] = []
        compiled: list[str] = []
        active = Event()
        stopped = Event()
        gate = Lock()
        observation: dict[str, Json] = {}
        prepare = SourceSession._prepare_interrupt
        close = SourceSession.close
        request = SourceSession._request_interrupt
        compile_read = SourceSession.compile
        batches = SourceSession.batches
        native_cursor = adapters._native_cursor

        def prepared(source: SourceSession) -> None:
            prepare(source)
            owners.append(source)
            connection: object = getattr(source._backend, "con", None)
            assert isinstance(connection, (duckdb.DuckDBPyConnection, sqlite3.Connection))
            connections.append(connection)
            if isinstance(connection, duckdb.DuckDBPyConnection):
                connection.execute("SET enable_progress_bar=true")
                connection.execute("SET enable_progress_bar_print=false")
                connection.execute("SET progress_bar_time=1")

        def closed(source: SourceSession) -> None:
            if source in owners:
                close_threads.append(get_ident())
            close(source)

        def interrupted(source: SourceSession) -> None:
            if source in owners:
                interrupt_threads.append(get_ident())
            request(source)

        def slow_compile(
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
                for _ in range(18):
                    expression = expression.cross_join(bound.binding.relation.view()).select(
                        expression
                    )
                if phase == "pending":
                    expression = expression.filter(ibis.random() > -1)
                    expression = expression.aggregate(
                        **{name: expression[name].max() for name in expression.columns}
                    )
            result = compile_read(
                source,
                qualified,
                expression,
                params=params,
                purpose=purpose,
                expected_schema=expected_schema,
            )
            if purpose == "analysis.graph.stage":
                compiled.append(result.sql)
            return result

        def observed_cursor(
            source_backend: BaseBackend, backend_name: str, sql: str
        ) -> adapters._Cursor:
            if sql not in compiled:
                return native_cursor(source_backend, backend_name, sql)
            connection: object = getattr(source_backend, "con", None)
            observation["submission_entered"] = True
            observation["pending_inflight"] = True
            if isinstance(connection, sqlite3.Connection) and phase == "pending":
                _observe_sqlite(connection, active, observation, phase)
            try:
                cursor = native_cursor(source_backend, backend_name, sql)
            except BaseException as error:
                observation["native_error_type"] = type(error).__name__
                observation["native_error_message"] = str(error)
                raise
            finally:
                observation["pending_inflight"] = False
                if isinstance(connection, sqlite3.Connection):
                    connection.set_progress_handler(None, 0)
            if isinstance(connection, sqlite3.Connection) and phase == "fetch":
                return _ObservedSQLiteFetch(
                    cursor, connection, active, observation, cursor_close_threads
                )
            return cursor

        def fetch_batches(
            source: SourceSession, read: CompiledRead, *, chunk_size: int
        ) -> adapters.SourceBatchStream:
            return batches(
                source,
                read,
                chunk_size=32768 if phase == "fetch" and read.sql in compiled else chunk_size,
            )

        monkeypatch.setattr(SourceSession, "_prepare_interrupt", prepared)
        monkeypatch.setattr(SourceSession, "close", closed)
        monkeypatch.setattr(SourceSession, "_request_interrupt", interrupted)
        monkeypatch.setattr(SourceSession, "compile", slow_compile)
        monkeypatch.setattr(SourceSession, "batches", fetch_batches)
        monkeypatch.setattr(adapters, "_native_cursor", observed_cursor)
        original_deadline = execute_deadline.ExecuteDeadline

        def deadline(start: float) -> execute_deadline.ExecuteDeadline:
            return original_deadline(start, seconds=2 if mode == "deadline" else 600)

        monkeypatch.setattr(execute_deadline, "ExecuteDeadline", deadline)

        def controller() -> None:
            until = time.monotonic() + 10
            try:
                while not stopped.is_set() and time.monotonic() < until:
                    if (
                        backend == "duckdb"
                        and observation.get("submission_entered") is True
                        and connections
                    ):
                        connection = connections[-1]
                        assert isinstance(connection, duckdb.DuckDBPyConnection)
                        progress = connection.query_progress()
                        if progress >= 0:
                            observation.update(
                                {
                                    "active": True,
                                    "native_phase": phase,
                                    "duckdb_progress_percent": progress,
                                }
                            )
                            active.set()
                    if active.is_set() and observation.get(phase + "_inflight") is True:
                        if mode == "sigint":
                            with gate:
                                if stopped.is_set():
                                    return
                                observation["signal_sent"] = True
                                observation["signal_native_phase"] = phase
                                observation["native_inflight_at_signal"] = True
                                observation["signal_monotonic"] = time.monotonic()
                                os.kill(os.getpid(), signal.SIGINT)
                            if not stopped.wait(2):
                                observation["test_rescue"] = True
                                connections[-1].interrupt()
                        return
                    stopped.wait(0.005)
                if not stopped.is_set() and connections:
                    observation["observer_timeout_rescue"] = True
                    connections[-1].interrupt()
            except Exception as error:
                observation["observer_error"] = type(error).__name__

        thread = Thread(target=controller, name="r94-local-native-observer", daemon=True)
        thread.start()
        started = time.monotonic()
        try:
            with pytest.raises(
                (KeyboardInterrupt, DomainPreparationError, MaterializationError)
            ) as caught:
                logical.execute()
            interruption = caught.value
        finally:
            with gate:
                stopped.set()
            thread.join(5)
            directory = Path(os.environ.get("MARIVO_R93_EVIDENCE_DIR", str(tmp_path)))
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"graph-local-observation-{backend}-{phase}-{mode}.json").write_bytes(
                encode(
                    checked(
                        {
                            "observation": observation,
                            "compiled_sql": compiled,
                            "owners": len(owners),
                            "submissions": [
                                asdict(submission)
                                for source in owners
                                for submission in source.submissions
                            ],
                        }
                    )
                )
            )
        elapsed = time.monotonic() - started
        errors: list[BaseException] = []
        error: BaseException | None = interruption
        while error is not None and all(error is not previous_error for previous_error in errors):
            errors.append(error)
            error = error.__cause__ or error.__context__
        assert runtime.last_run_ref is not None
        run = runtime.store._graph_run(runtime.last_run_ref)
        assert run is not None
        preserved_tables = previous._dataset.verified()
        full_parts_preserved = preserved_tables.primary.equals(
            original_tables.primary, check_metadata=True
        ) and all(
            current.role == original.role
            and current.table.equals(original.table, check_metadata=True)
            for current, original in zip(preserved_tables.parts, original_tables.parts, strict=True)
        )
        report = checked(
            {
                "backend": backend,
                "phase": phase,
                "mode": mode,
                "observation": observation,
                "elapsed_seconds": elapsed,
                "interruption_kind": type(interruption).__name__,
                "error_types": [type(error).__name__ for error in errors],
                "error_messages": [str(error) for error in errors],
                "source_submissions": [
                    asdict(submission) for source in owners for submission in source.submissions
                ],
                "compiled_sql": compiled,
                "publication_counts_before": before,
                "publication_counts_after": _publication_counts(session),
                "new_runs": len(run_ids(session) - before_runs),
                "run_lifecycle": run.lifecycle,
                "resources": len(runtime.store.resources(runtime.session_ref)),
                "close_threads": close_threads,
                "cursor_close_threads": cursor_close_threads,
                "interrupt_threads": interrupt_threads,
                "owner_thread": owner_thread,
                "previous_artifact_preserved": snapshot(previous) == original_snapshot,
                "previous_primary_and_part_values_preserved": full_parts_preserved,
                "boundary": "One actual local graph phase. SQLite native timer interruption is owned by the existing SourceSession expiry case and the same graph deadline guard; no phase cross product is claimed.",
            }
        )
        directory = Path(os.environ.get("MARIVO_R93_EVIDENCE_DIR", str(tmp_path)))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"graph-local-{backend}-{phase}-{mode}.json").write_bytes(encode(report))
        assert not thread.is_alive()
        assert observation.get("active") is True, observation
        assert observation.get("native_phase") == phase
        assert observation.get("test_rescue") is not True, observation
        assert observation.get("observer_timeout_rescue") is not True, observation
        assert observation.get("observer_error") is None, observation
        if mode == "sigint":
            assert observation.get("signal_sent") is True
            assert observation.get("native_inflight_at_signal") is True
            assert observation.get("signal_native_phase") == phase
            assert isinstance(interruption, KeyboardInterrupt) or (
                backend == "duckdb"
                and any(
                    isinstance(error, (KeyboardInterrupt, duckdb.InterruptException))
                    for error in errors
                )
            ), report
        else:
            assert isinstance(interruption, (DomainPreparationError, MaterializationError))
            assert "execute_timeout" in str(interruption) or "source execution failed" in str(
                interruption
            )
            assert interrupt_threads and any(
                thread_id != owner_thread for thread_id in interrupt_threads
            )
        assert compiled and owners and all(source._closed for source in owners)
        assert close_threads and set(close_threads) == {owner_thread}
        assert set(cursor_close_threads) <= {owner_thread}
        assert all(
            submission.connection_disconnected
            for source in owners
            for submission in source.submissions
        )
        assert any(
            submission.sql in compiled
            and submission.state == "failed"
            and submission.termination == "local_closed"
            for source in owners
            for submission in source.submissions
        )
        assert _publication_counts(session) == before
        assert snapshot(previous) == original_snapshot
        assert full_parts_preserved
        assert len(run_ids(session) - before_runs) == 1
        assert run.lifecycle == "failed"
        assert runtime.store.resources(runtime.session_ref) == ()
        assert execute_deadline.CURRENT.get() is None
