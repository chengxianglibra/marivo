"""Actual initial response and fetch aborts preserve the public graph artifact."""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from threading import get_ident
from typing import Literal

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import execute_deadline
from marivo.datasource.adapters import (
    CompiledRead,
    Parameter,
    QualifiedSource,
    SourceBatchStream,
    SourceSession,
    _ClickHouseCursor,
    _ClickHouseNativeStream,
)
from marivo.semantic.reader import SemanticProject
from tests.multisource_environment import clickhouse_analysis as ch
from tests.r9_source_cases import source_case
from tests.r94_domain_recovery_worker import snapshot
from tests.shared_fixtures import run_ids
from tests.test_r93_capability_consumers import _author_c05_project
from tests.test_r93_reference_consumers import _data


@pytest.mark.runtime
@pytest.mark.parametrize("phase", ["initial-response", "fetch"])
def test_clickhouse_graph_phase_sigint_preserves_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    phase: Literal["initial-response", "fetch"],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, _data("clickhouse")) as case:
        _author_c05_project("clickhouse", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-clickhouse-phases", report_timezone="UTC")
        logical = session.members(ms.ref.entity("sales.facts")).read(
            ms.ref.measure("sales.facts.amount")
        )
        previous = logical.execute()
        retained = snapshot(previous)
        runtime = session._runtime
        before_runs = run_ids(session)

        def publications() -> list[int]:
            with runtime.store._connection() as connection:
                counts: list[int] = []
                for table in ("dataset_artifacts", "dataset_evidence", "findings"):
                    row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
                    assert row is not None and isinstance(row[0], int)
                    counts.append(row[0])
                return counts

        before = publications()
        owners: list[SourceSession] = []
        compiled: list[str] = []
        batches: list[int] = []
        native_fetches: list[int] = []
        control_targets: list[tuple[str, str]] = []
        close_threads: list[int] = []
        owner_thread = get_ident()
        identity: str | None = None
        reader: str | None = None
        active_sql: str | None = None
        signal_started: float | None = None
        prepare = SourceSession._prepare_interrupt
        compile_read = SourceSession.compile
        request_interrupt = SourceSession._request_interrupt
        cursor_init = _ClickHouseCursor.__init__
        fetchmany = _ClickHouseCursor.fetchmany
        iterate = SourceBatchStream._iterate

        def prepared(source: SourceSession) -> None:
            prepare(source)
            owners.append(source)
            assert source._cancel_control is not None
            disconnect = source._cancel_control.disconnect

            def disconnected() -> None:
                close_threads.append(get_ident())
                disconnect()

            monkeypatch.setattr(source._cancel_control, "disconnect", disconnected)

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
                initial_rows = expression
                remaining_rows = expression
                for _ in range(13):
                    initial_rows = initial_rows.cross_join(bound.binding.relation.view()).select(
                        initial_rows
                    )
                initial_rows = initial_rows.mutate(
                    key_1=(ibis.random() * 1_000_000_000_000_000).cast("int64")
                )
                for _ in range(18):
                    remaining_rows = remaining_rows.cross_join(
                        bound.binding.relation.view()
                    ).select(remaining_rows)
                remaining_rows = remaining_rows.filter(ibis.random() > 0.5).aggregate(
                    **{name: remaining_rows[name].max() for name in remaining_rows.columns}
                )
                expression = initial_rows.union(remaining_rows, distinct=False).cast(
                    ibis.Schema.from_pyarrow(expected_schema)
                )
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

        def observe_and_signal(source: SourceSession) -> None:
            nonlocal identity, reader, active_sql, signal_started
            assert identity is None
            assert source._clickhouse_active is not None
            submission, identity = source._clickhouse_active
            reader = source._clickhouse_reader
            assert reader == "analysis_reader" and submission.sql == compiled[-1]
            with ch.connection(admin=True) as observer:
                active = observer.query(
                    "SELECT user,query FROM system.processes WHERE query_id={id:String}",
                    parameters={"id": identity},
                ).result_rows
            assert len(active) == 1 and active[0][0] == reader
            active_sql = str(active[0][1])
            assert active_sql.startswith(submission.sql)
            assert active_sql[len(submission.sql) :].strip() == "FORMAT Native"
            signal_started = time.monotonic()
            os.kill(os.getpid(), signal.SIGINT)

        def initial(cursor: _ClickHouseCursor, native: _ClickHouseNativeStream) -> None:
            cursor_init(cursor, native)
            if phase == "initial-response":
                source = next((owner for owner in owners if owner._pending_cursor is cursor), None)
                if source is not None and compiled and source.submissions[-1].sql in compiled:
                    assert not batches and not native_fetches
                    assert cursor._native is native and not cursor._closed
                    observe_and_signal(source)

        def fetched(cursor: _ClickHouseCursor, size: int) -> Sequence[Sequence[object]]:
            rows = fetchmany(cursor, size)
            source = next(
                (
                    owner
                    for owner in owners
                    if any(stream._cursor is cursor for stream in owner._streams)
                ),
                None,
            )
            if source is not None and compiled and source.submissions[-1].sql in compiled:
                native_fetches.append(len(rows))
                if phase == "fetch" and len(native_fetches) == 2:
                    assert rows and batches and batches[0] > 0
                    observe_and_signal(source)
            return rows

        def yielded(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
            for batch in iterate(stream):
                if stream._session in owners and stream._submission.sql in compiled:
                    batches.append(batch.num_rows)
                yield batch

        def interrupted(source: SourceSession) -> None:
            if source._clickhouse_active is not None:
                assert source._clickhouse_reader is not None
                control_targets.append((source._clickhouse_active[1], source._clickhouse_reader))
            request_interrupt(source)

        monkeypatch.setattr(SourceSession, "_prepare_interrupt", prepared)
        monkeypatch.setattr(SourceSession, "compile", slow_compile)
        monkeypatch.setattr(SourceSession, "_request_interrupt", interrupted)
        monkeypatch.setattr(_ClickHouseCursor, "__init__", initial)
        monkeypatch.setattr(_ClickHouseCursor, "fetchmany", fetched)
        monkeypatch.setattr(SourceBatchStream, "_iterate", yielded)
        deadline = execute_deadline.ExecuteDeadline
        monkeypatch.setattr(
            execute_deadline, "ExecuteDeadline", lambda start: deadline(start, seconds=20)
        )
        with pytest.raises(KeyboardInterrupt):
            try:
                logical.execute()
            except BaseException as error:
                chain: list[dict[str, str]] = []
                current: BaseException | None = error
                while current is not None:
                    chain.append({"type": type(current).__name__, "message": str(current)})
                    current = current.__cause__ or current.__context__
                if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
                    Path(directory, "graph-clickhouse-" + phase + "-diagnostic.json").write_text(
                        json.dumps({"chain": chain, "batches": batches, "fetches": native_fetches})
                    )
                raise
        assert identity is not None and signal_started is not None
        elapsed = time.monotonic() - signal_started
        assert elapsed < 3
        assert publications() == before and snapshot(previous) == retained
        assert len(run_ids(session) - before_runs) == 1
        assert runtime.last_run_ref is not None
        run = runtime.store._graph_run(runtime.last_run_ref)
        assert run is not None and run.lifecycle == "failed"
        assert runtime.store.resources(session.id) == ()
        assert execute_deadline.CURRENT.get() is None
        assert owners and all(source._closed for source in owners)
        assert all(
            source._cancel_control_released and source._cancel_pool is None for source in owners
        )
        assert close_threads and set(close_threads) == {owner_thread}
        submissions = [submission for source in owners for submission in source.submissions]
        assert len(submissions) == 1 and submissions[0].state == "failed"
        assert submissions[0].cursor_state == "closed"
        assert submissions[0].termination == "remote_unknown"
        assert all(source._clickhouse_active is None for source in owners)
        controls = [submission for source in owners for submission in source.cancel_submissions]
        assert len(controls) == 1 and controls[0].state == "succeeded"
        assert controls[0].sql == (
            "KILL QUERY WHERE query_id={id:String} AND user={user:String} SYNC"
        )
        assert control_targets and set(control_targets) == {(identity, reader)}
        with ch.connection(admin=True) as observer:
            events: list[list[object]] = []
            until = time.monotonic() + 3
            while not events and time.monotonic() < until:
                observer.command("SYSTEM FLUSH LOGS")
                events = [
                    list(row)
                    for row in observer.query(
                        "SELECT type,exception_code,user,query FROM system.query_log WHERE query_id={id:String} AND type IN ('ExceptionBeforeStart','ExceptionWhileProcessing')",
                        parameters={"id": identity},
                    ).result_rows
                ]
                if not events:
                    time.sleep(0.02)
            assert len(events) == 1
            assert events[0][0] in {"ExceptionBeforeStart", "ExceptionWhileProcessing"}
            assert events[0][1:3] == [394, reader]
            assert events[0][3] == active_sql
            assert observer.query(
                "SELECT count() FROM system.processes WHERE query_id={id:String}",
                parameters={"id": identity},
            ).first_row == (0,)
        if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
            Path(directory, "graph-clickhouse-" + phase + "-sigint.json").write_text(
                json.dumps(
                    {
                        "phase": phase,
                        "query_id": identity,
                        "reader": reader,
                        "active_sql": active_sql,
                        "native_fetch_rows": native_fetches,
                        "real_arrow_batch_rows": batches,
                        "elapsed_seconds": elapsed,
                        "events": events,
                        "active_queries": 0,
                        "control_submissions": [asdict(item) for item in controls],
                        "control_targets": control_targets,
                        "publication_counts_before": before,
                        "publication_counts_after": publications(),
                        "new_runs": 1,
                        "run_lifecycle": run.lifecycle,
                        "resources": 0,
                        "full_previous_artifact_preserved": True,
                        "test_rescue": False,
                    },
                    sort_keys=True,
                )
            )
