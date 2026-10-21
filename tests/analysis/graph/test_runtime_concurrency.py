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
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pandas as pd
import pytest
from ibis.backends.duckdb import Backend

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import admission, terminal_publication
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.datasource import backends
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.backends import (
    BuiltDatasourceBackend,
    EffectiveDatasourceKwargs,
    _build_backend_from_effective,
)
from marivo.datasource.ir import DatasourceIR
from tests.analysis.materialization.concurrency_worker import observation, snapshot
from tests.shared_fixtures import DslCase, terminal_run, terminal_runs


def _evidence(name: str, value: dict[str, object]) -> None:
    destination = os.environ.get("MARIVO_SLICE4C_EVIDENCE_DIR")
    if destination is not None:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(
            json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )


@pytest.mark.slow
@pytest.mark.parametrize(
    ("key", "mode"),
    [
        pytest.param("same", "thread", id="same-thread"),
        pytest.param("same", "process", id="same-process"),
        pytest.param("different", "reentrant", id="different-reentrant"),
    ],
)
def test_busy_contender_preserves_real_producer(
    retained_coordinates_case: DslCase,
    key: str,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = retained_coordinates_case
    runtime = case.session._runtime
    reads: list[CompiledRead] = []
    execute = SourceSession.execute

    def capture(source: SourceSession, read: CompiledRead) -> pd.DataFrame:
        reads.append(read)
        return execute(source, read)

    monkeypatch.setattr(SourceSession, "execute", capture)
    committed = case.session.frame(
        case.session.members(case.session.catalog.entities.get("sales.customer"))
    ).persist()
    logical = observation(case.session, "sales.revenue")
    metric = "sales.revenue" if key == "same" else "sales.order_count"
    contender = observation(case.session, metric).compute()
    initial_reads = len(reads)
    paused, resume = threading.Event(), threading.Event()
    evidence: dict[str, object] = {"pid": os.getpid(), "key": key, "mode": mode}

    def check_contender() -> None:
        before = snapshot(runtime)
        run = terminal_runs(case.session)[0].run_ref
        assert run is not None
        read_count = len(reads)
        assert read_count > initial_reads
        assert any("SUM(" in read.sql.upper() for read in reads[initial_reads:])
        if mode == "process":
            child = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tests.analysis.materialization.concurrency_worker",
                    "contend",
                    str(case.root),
                    "--session",
                    runtime.session_ref,
                    "--metric",
                    metric,
                    "--artifact",
                    committed.artifact.id,
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
            assert value["artifact"] == committed.artifact.id
            assert value["pid"] != os.getpid()
            evidence["contender"] = value
        else:
            with pytest.raises(SessionBusyError) as rejected:
                contender.persist()
            assert rejected.value.session_ref == runtime.session_ref
            assert str(case.root) not in str(rejected.value)
            assert not hasattr(rejected.value, "run_ref")
        assert snapshot(runtime) == before
        assert len(reads) == read_count
        assert terminal_run(case.session, run).lifecycle == "incomplete"
        assert case.session.persisted_frame(committed.artifact.id).to_pandas().shape[0] == 3
        evidence.update({"before": before, "run": run})

    def pause(point: str) -> None:
        if point == "terminal_primary_written":
            if mode == "reentrant":
                check_contender()
            else:
                paused.set()
                assert resume.wait(30)

    monkeypatch.setattr(terminal_publication, "_event", pause)
    if mode == "reentrant":
        produced = logical.persist()
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(logical.persist)
            try:
                assert paused.wait(30)
                check_contender()
            finally:
                resume.set()
            produced = active.result(timeout=30)
    monkeypatch.setattr(terminal_publication, "_event", lambda point: None)
    completed = snapshot(runtime)
    assert completed["analysis_action_runs"] == completed["analysis_action_run_terminals"] == 2
    assert completed["dataset_artifacts"] == 2
    assert completed["dataset_evidence"] == 2
    assert (
        committed.artifact.evidence_digest().evidence_count > 0
        and produced.artifact.evidence_digest().evidence_count > 0
    )
    assert completed["action_resource_journal"] == 0
    produced_reads = len(reads) - initial_reads
    retry = logical.persist()
    assert retry.artifact.id != produced.artifact.id
    assert retry.to_pandas()[("value", "value")].sum() == 147
    assert len(reads) == initial_reads + 2 * produced_reads
    rolled = produced.reduce({"value": produced["value"].sum()}).persist()
    assert rolled.to_pandas()[("value", "value")].tolist() == [147]
    read_count = len(reads)
    before_offline = snapshot(runtime)
    # Terminal outputs hand data to pandas and never resume the execution graph.
    exported = case.session.persisted_frame(produced.artifact.id).to_pandas()
    assert exported[("value", "value")].sum() == 147
    assert exported[("value", "value")].loc[exported[("value", "value")] > 10].sum() == 140
    assert len(reads) == read_count and snapshot(runtime) == before_offline
    evidence.update(
        {
            "session": runtime.session_ref,
            "artifact": produced.artifact.id,
            "completed": completed,
        }
    )
    if key == "different":
        before_contender = snapshot(runtime)
        retried_contender = contender.persist()
        assert retried_contender.artifact.id != produced.artifact.id
        assert (
            snapshot(runtime)["analysis_action_runs"]
            == before_contender["analysis_action_runs"] + 1
        )
        assert retried_contender.to_pandas()[("value", "value")].sum() == 4
        evidence["contender_retry_artifact"] = retried_contender.artifact.id
        evidence["contender_retry_run"] = retried_contender.artifact._manifest.run_id
    _evidence(f"contention-{key}-{mode}", evidence)


def test_competing_creation_releases_each_candidate_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.errors import SessionAlreadyExistsError

    SessionStore(tmp_path)
    barrier = threading.Barrier(2)
    original_create = SessionStore.create_session
    held = threading.local()
    observed: list[str] = []

    @contextmanager
    def tracked_guard(path: Path, *, session_ref: str | None = None) -> Iterator[None]:
        assert not getattr(held, "active", False)
        with session_writer_guard(path, session_ref=session_ref):
            assert session_ref is not None
            held.active = True
            observed.append(session_ref)
            try:
                yield
            finally:
                held.active = False

    def create(
        store: SessionStore,
        name: str,
        *,
        session_ref: str | None = None,
        question: str | None = None,
        report_timezone_name: str = "UTC",
        report_timezone_resolution: Literal["iana", "fixed_offset"] = "iana",
        domains: tuple[str, ...] | None = None,
    ) -> SessionRecord:
        barrier.wait(timeout=10)
        return original_create(
            store,
            name,
            session_ref=session_ref,
            question=question,
            report_timezone_name=report_timezone_name,
            report_timezone_resolution=report_timezone_resolution,
            domains=domains,
        )

    monkeypatch.setattr(admission, "session_writer_guard", tracked_guard)
    monkeypatch.setattr(SessionStore, "create_session", create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(DatasetRuntime.create, tmp_path, "canonical") for _ in range(2)]
        winners: list[DatasetRuntime] = []
        duplicates = 0
        for future in futures:
            try:
                winners.append(future.result(timeout=20))
            except SessionAlreadyExistsError:
                duplicates += 1
    assert duplicates == len(winners) == 1
    assert snapshot(winners[0])["sessions"] == 1
    assert len(observed) == len(set(observed)) == 2


def test_duplicate_creation_does_not_recover_busy_existing_session(tmp_path: Path) -> None:
    from marivo.analysis.errors import SessionAlreadyExistsError

    winner = DatasetRuntime.create(tmp_path, "canonical")
    before = winner.store.session(winner.session_ref)
    with (
        session_writer_guard(winner.store.layout.lock_path(winner.session_ref)),
        pytest.raises(SessionAlreadyExistsError),
    ):
        DatasetRuntime.create(tmp_path, "canonical")
    assert winner.store.session(winner.session_ref) == before
    assert snapshot(winner)["sessions"] == 1


@pytest.mark.slow
def test_fresh_process_name_race_has_one_canonical_identity(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "tests.analysis.materialization.concurrency_worker",
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
        assert {item["status"] for item in records} == {"created", "duplicate"}
        assert store.session(canonical.session_ref) == canonical
        _evidence("creation-process-race", {"processes": records, "session": canonical.session_ref})
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def test_resume_is_guarded_and_existing_handle_owner_is_stable(
    retained_coordinates_case: DslCase,
) -> None:
    sessions = [mv.session.create(name) for name in ("first", "second")]
    first, second = (session._runtime for session in sessions)
    logical = observation(sessions[0], "sales.revenue")
    before_second = second.store.session(second.session_ref)
    with session_writer_guard(
        first.store.layout.lock_path(first.session_ref), session_ref=first.session_ref
    ):
        with pytest.raises(SessionBusyError) as rejected:
            mv.session.resume(first.session_ref)
        assert rejected.value.session_ref == first.session_ref
        assert (
            DatasetRuntime.open(retained_coordinates_case.root, first.session_ref).session_ref
            == first.session_ref
        )
        assert mv.session.resume(second.session_ref).id == second.session_ref
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mv.session.resume, item.session_ref) for item in (first, second)]
        assert {future.result(timeout=20).id for future in futures} == {
            first.session_ref,
            second.session_ref,
        }
    result = logical.persist()
    assert result.artifact.session_id == first.session_ref
    assert second.store.session(second.session_ref) == before_second


def test_different_sessions_overlap_inside_real_duckdb_queries(
    retained_coordinates_case: DslCase, monkeypatch: pytest.MonkeyPatch
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
        connection_timeout_seconds: int | None = None,
    ) -> BuiltDatasourceBackend:
        built = original(
            datasource,
            effective,
            read_only=read_only,
            terminal_timeout_seconds=terminal_timeout_seconds,
            connection_timeout_seconds=connection_timeout_seconds,
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
    case = retained_coordinates_case
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(model.read_text().replace("md.table('order')", "md.table('blocked_orders')"))
    ms.load(workspace_dir=case.root)
    sessions = [mv.session.create(name) for name in ("first", "second")]
    runtimes = [session._runtime for session in sessions]
    logicals = [observation(session, "sales.revenue") for session in sessions]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(logical.persist) for logical in logicals]
        try:
            try:
                entered.wait(timeout=20)
            except threading.BrokenBarrierError:
                release.set()
                for future in futures:
                    future.result(timeout=5)
                raise
            assert len(query_threads) == 2
            assert all(snapshot(runtime)["analysis_action_runs"] == 0 for runtime in runtimes)
        finally:
            release.set()
        results = [future.result(timeout=30) for future in futures]
    assert {value.artifact.session_id for value in results} == {
        runtime.session_ref for runtime in runtimes
    }
    assert all(result.to_pandas()[("value", "value")].sum() == 147.0 for result in results)
    assert all(snapshot(runtime)["analysis_action_run_terminals"] == 2 for runtime in runtimes)
    assert all(len(terminal_runs(session)) == 1 for session in sessions)
    _evidence(
        "backend-overlap",
        {
            "pid": os.getpid(),
            "query_threads": sorted(query_threads),
            "sessions": [runtime.session_ref for runtime in runtimes],
            "runs": [value.artifact._manifest.run_id for value in results],
        },
    )
