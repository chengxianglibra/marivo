"""Real runtime contention, canonical Session activation, and backend overlap."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from ibis.backends.duckdb import Backend

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.datasource import backends
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.datasource.backends import (
    BuiltDatasourceBackend,
    EffectiveDatasourceKwargs,
    _build_backend_from_effective,
)
from marivo.datasource.ir import DatasourceIR
from tests.lazy_concurrency_runtime_worker import observation, snapshot
from tests.shared_fixtures import DslCase

pytestmark = pytest.mark.runtime


def _evidence(name: str, value: dict[str, object]) -> None:
    destination = os.environ.get("MARIVO_SLICE4C_EVIDENCE_DIR")
    if destination is not None:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(
            json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )


@pytest.mark.parametrize("kind", ["local"])
@pytest.mark.parametrize("key", ["same", "different"])
@pytest.mark.parametrize("mode", ["thread", "process", "reentrant"])
def test_busy_contender_preserves_real_producer(
    retained_r54_case: DslCase, kind: str, key: str, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = retained_r54_case
    runtime = case.session._runtime
    reads: list[CompiledRead] = []
    batches = SourceSession.batches

    def capture(source: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        reads.append(read)
        return batches(source, read, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", capture)
    committed = case.session.members(ms.ref.entity("sales.customer")).execute()
    logical = observation(case.session, "sales.revenue")
    metric = "sales.revenue" if key == "same" else "sales.order_count"
    contender = observation(case.session, metric)
    initial_reads = len(reads)
    paused, resume = threading.Event(), threading.Event()
    evidence: dict[str, object] = {"pid": os.getpid(), "kind": kind, "key": key, "mode": mode}

    def check_contender() -> None:
        before, stats, run = snapshot(runtime), asdict(runtime.statistics), runtime.last_run_ref
        assert run is not None
        read_count = len(reads)
        assert read_count > initial_reads
        assert any("original_state__sum" in read.schema.names for read in reads[initial_reads:])
        if mode == "process":
            child = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tests.lazy_concurrency_runtime_worker",
                    "contend",
                    str(case.root),
                    "--session",
                    runtime.session_ref,
                    "--metric",
                    metric,
                    "--artifact",
                    committed._run().state.artifact_ref.ref,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                env={**os.environ, "MARIVO_TELEMETRY": "off"},
            )
            value = json.loads(child.stdout)
            assert value["session"] == runtime.session_ref
            assert value["before"] == value["after"] == before
            assert value["statistics"]["events"] == {}
            assert value["last_run_ref"] is None
            assert value["pid"] != os.getpid()
            evidence["contender"] = value
        else:
            with pytest.raises(SessionBusyError) as rejected:
                contender.execute()
            assert rejected.value.session_ref == runtime.session_ref
            assert str(case.root) not in str(rejected.value)
            assert rejected.value.run_ref is None
        assert snapshot(runtime) == before
        assert len(reads) == read_count
        assert asdict(runtime.statistics) == stats and runtime.last_run_ref == run
        assert case.session.artifact(committed.state.artifact_ref.ref).to_pandas().shape[0] == 3
        evidence.update({"before": before, "producer_statistics": stats, "run": run})

    def pause(point: str) -> None:
        if point == "graph_primary_written":
            if mode == "reentrant":
                check_contender()
            else:
                paused.set()
                assert resume.wait(30)

    runtime._hook = pause
    if mode == "reentrant":
        produced = logical.execute()
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(logical.execute)
            try:
                assert paused.wait(30)
                check_contender()
            finally:
                resume.set()
            produced = active.result(timeout=30)
    runtime._hook = None
    completed = snapshot(runtime)
    assert completed["analysis_action_runs"] == completed["analysis_action_run_terminals"] == 2
    assert completed["dataset_artifacts"] == completed["dataset_evidence"] == 2
    assert completed["action_resource_journal"] == 0
    produced_reads = len(reads) - initial_reads
    retry = logical.execute()
    assert retry._run().state.artifact_ref != produced._run().state.artifact_ref
    assert retry.to_pandas()["value"].sum() == 147
    assert len(reads) == initial_reads + 2 * produced_reads
    fixed = produced.rollup()
    first_fixed = fixed.execute()
    assert first_fixed.to_pandas()["value"].tolist() == [147]
    fixed_snapshot = snapshot(runtime)
    read_count = len(reads)
    fixed_retry = fixed.execute()
    assert len(reads) == read_count
    assert fixed_retry._run().state.artifact_ref == first_fixed._run().state.artifact_ref
    assert snapshot(runtime) == fixed_snapshot
    assert runtime.statistics.primary_queries == runtime.statistics.transferred_rows == 0
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    different_fixed = produced.where(produced.value.gt(10)).rollup().execute()
    assert different_fixed.state.artifact_ref != first_fixed.state.artifact_ref
    assert different_fixed.to_pandas()["value"].tolist() == [140]
    assert len(reads) == read_count
    evidence.update(
        {
            "session": runtime.session_ref,
            "artifact": produced._run().state.artifact_ref.ref,
            "completed": completed,
            "retry_statistics": asdict(runtime.statistics),
        }
    )
    if key == "different":
        before_contender = snapshot(runtime)
        retried_contender = contender.execute()
        assert retried_contender._run().state.artifact_ref != produced._run().state.artifact_ref
        assert (
            snapshot(runtime)["analysis_action_runs"]
            == before_contender["analysis_action_runs"] + 1
        )
        assert retried_contender.to_pandas()["value"].sum() == 4
        evidence["contender_retry_artifact"] = retried_contender._run().state.artifact_ref.ref
        evidence["contender_retry_run"] = runtime.last_run_ref
    _evidence(f"contention-{kind}-{key}-{mode}", evidence)


def test_canonical_creation_releases_candidate_before_winner_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    SessionStore._graph_store(tmp_path)
    barrier = threading.Barrier(2)
    original_create = SessionStore.create_session
    held = threading.local()
    observed: list[tuple[int, str]] = []
    winner: list[str] = []
    winner_released = threading.Event()

    @contextmanager
    def tracked_guard(path: Path, *, session_ref: str | None = None) -> Iterator[None]:
        assert not getattr(held, "active", False)
        with session_writer_guard(path, session_ref=session_ref):
            assert session_ref is not None
            held.active = True
            observed.append((threading.get_ident(), session_ref))
            try:
                yield
            finally:
                held.active = False
        if winner and session_ref == winner[0]:
            winner_released.set()

    def create(
        store: SessionStore,
        name: str,
        *,
        session_ref: str | None = None,
        question: str | None = None,
        report_timezone_name: str = "UTC",
        report_timezone_resolution: Literal["iana", "fixed_offset"] = "iana",
    ) -> SessionRecord:
        barrier.wait(timeout=10)
        record = original_create(
            store,
            name,
            session_ref=session_ref,
            question=question,
            report_timezone_name=report_timezone_name,
            report_timezone_resolution=report_timezone_resolution,
        )
        if record.session_ref == session_ref:
            winner.append(record.session_ref)
        else:
            assert winner_released.wait(10)
        return record

    monkeypatch.setattr(admission, "session_writer_guard", tracked_guard)
    monkeypatch.setattr(SessionStore, "create_session", create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(DatasetRuntime.create, tmp_path, "canonical", _generation=7)
            for _ in range(2)
        ]
        runtimes = [future.result(timeout=20) for future in futures]
    assert runtimes[0].session_ref == runtimes[1].session_ref
    assert snapshot(runtimes[0])["sessions"] == 1
    assert len(observed) == 3
    assert observed[-1][1] == runtimes[0].session_ref
    assert runtimes[0].store.current() == runtimes[0].store.session(runtimes[0].session_ref)


def test_losing_candidate_cannot_activate_busy_canonical_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = DatasetRuntime.create(tmp_path, "canonical", _generation=7)
    current = DatasetRuntime.create(tmp_path, "current", _generation=7)
    original = SessionStore.session_by_name
    first = True

    def stale_lookup(store: SessionStore, name: str) -> SessionRecord | None:
        nonlocal first
        if first:
            first = False
            return None
        return original(store, name)

    monkeypatch.setattr(SessionStore, "session_by_name", stale_lookup)
    with (
        session_writer_guard(winner.store.layout.lock_path(winner.session_ref)),
        pytest.raises(SessionBusyError) as rejected,
    ):
        DatasetRuntime.create(tmp_path, "canonical", _generation=7)
    assert rejected.value.session_ref == winner.session_ref
    assert winner.store.current() == winner.store.session(current.session_ref)
    assert snapshot(winner)["sessions"] == 2


def test_fresh_process_name_race_has_one_canonical_identity(tmp_path: Path) -> None:
    store = SessionStore._graph_store(tmp_path)
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "tests.lazy_concurrency_runtime_worker",
                "create",
                str(tmp_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "MARIVO_TELEMETRY": "off"},
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("go\n")
            process.stdin.flush()
        records = []
        for process in processes:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, output + error
            records.append(json.loads(output))
        canonical = store.session_by_name("raced")
        assert canonical is not None
        assert {item["session"] for item in records} == {canonical.session_ref}
        assert "created" in {item["status"] for item in records}
        assert store.current() == canonical
        _evidence("creation-process-race", {"processes": records, "session": canonical.session_ref})
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def test_activation_is_guarded_and_existing_handle_owner_is_stable(
    retained_r54_case: DslCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = retained_r54_case.root
    sessions = [mv.session.get_or_create(name) for name in ("first", "second")]
    first, second = (session._runtime for session in sessions)
    logical = observation(sessions[0], "sales.revenue")
    with session_writer_guard(
        first.store.layout.lock_path(first.session_ref), session_ref=first.session_ref
    ):
        with pytest.raises(SessionBusyError) as rejected:
            DatasetRuntime.create(tmp_path, "first", _generation=7)
        assert rejected.value.session_ref == first.session_ref
        assert first.store.current() == first.store.session(second.session_ref)
        opened = DatasetRuntime.open(tmp_path, first.session_ref, _generation=7)
        assert opened.session_ref == first.session_ref and opened.statistics.events == {}
        assert (
            DatasetRuntime.create(tmp_path, "second", _generation=7).session_ref
            == second.session_ref
        )
    original = SessionStore.activate
    barrier = threading.Barrier(2)

    def activate(
        store: SessionStore, session_ref: str, *, question: str | None = None
    ) -> SessionRecord:
        barrier.wait(timeout=10)
        return original(store, session_ref, question=question)

    monkeypatch.setattr(SessionStore, "activate", activate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(DatasetRuntime.create, tmp_path, name, _generation=7)
            for name in ("first", "second")
        ]
        assert {value.result(timeout=20).session_ref for value in futures} == {
            first.session_ref,
            second.session_ref,
        }
    current = first.store.current()
    assert current is not None and current.session_ref in (first.session_ref, second.session_ref)
    result = logical.execute()
    assert result._run().runtime.session_ref == first.session_ref
    assert first.store.current() == current


@pytest.mark.parametrize("kind", ["local"])
def test_different_sessions_overlap_inside_real_duckdb_queries(
    retained_r54_case: DslCase, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Barrier(3)
    release = threading.Event()
    original = _build_backend_from_effective
    query_threads: set[int] = set()
    thread_guard = threading.Lock()

    def build(
        datasource: DatasourceIR,
        effective: EffectiveDatasourceKwargs,
        *,
        read_only: bool = False,
        terminal_timeout_seconds: int | None = None,
    ) -> BuiltDatasourceBackend:
        built = original(
            datasource,
            effective,
            read_only=read_only,
            terminal_timeout_seconds=terminal_timeout_seconds,
        )
        backend = built.backend
        assert isinstance(backend, Backend)
        first = True

        def blocking_identity(value: float) -> float:
            nonlocal first
            if first:
                first = False
                with thread_guard:
                    query_threads.add(threading.get_ident())
                entered.wait(timeout=20)
                assert release.wait(20)
            return value

        function = "overlap_probe_" + uuid4().hex
        backend.con.create_function(function, blocking_identity, ["DOUBLE"], "DOUBLE")
        backend.raw_sql(
            f'CREATE TEMP VIEW blocked_orders AS SELECT * REPLACE (CAST({function}(amount) AS BIGINT) AS amount) FROM "order"'
        )
        return built

    monkeypatch.setattr(backends, "_build_backend_from_effective", build)
    case = retained_r54_case
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(model.read_text().replace("md.table('order')", "md.table('blocked_orders')"))
    ms.load(workspace_dir=case.root)
    sessions = [mv.session.get_or_create(name) for name in ("first", "second")]
    runtimes = [session._runtime for session in sessions]
    logicals = [observation(session, "sales.revenue") for session in sessions]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(logical.execute) for logical in logicals]
        try:
            try:
                entered.wait(timeout=20)
            except threading.BrokenBarrierError:
                release.set()
                for future in futures:
                    future.result(timeout=5)
                raise
            assert len(query_threads) == 2
            assert all(runtime.last_run_ref is not None for runtime in runtimes)
        finally:
            release.set()
        results = [future.result(timeout=30) for future in futures]
    assert {value._run().runtime.session_ref for value in results} == {
        runtime.session_ref for runtime in runtimes
    }
    assert all(result.to_pandas()["value"].sum() == 147.0 for result in results)
    assert all(snapshot(runtime)["analysis_action_run_terminals"] == 2 for runtime in runtimes)
    _evidence(
        f"backend-overlap-{kind}",
        {
            "pid": os.getpid(),
            "kind": kind,
            "query_threads": sorted(query_threads),
            "sessions": [runtime.session_ref for runtime in runtimes],
            "runs": [runtime.last_run_ref for runtime in runtimes],
            "statistics": [asdict(runtime.statistics) for runtime in runtimes],
        },
    )
