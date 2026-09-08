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
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import (
    EngineTarget,
    LocalTarget,
    ObjectTarget,
    S3Access,
)
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.datasource.backends import (
    BuiltDatasourceBackend,
    EffectiveDatasourceKwargs,
    _build_backend_from_effective,
)
from marivo.datasource.ir import DatasourceIR, TableSourceIR
from marivo.refs import ref
from tests.lazy_concurrency_runtime_worker import snapshot
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

pytestmark = pytest.mark.runtime


def _access(request: pytest.FixtureRequest, kind: str) -> tuple[S3Access, ...]:
    if kind != "object":
        return ()
    value = request.getfixturevalue("lazy_s3_access")
    assert isinstance(value, S3Access)
    return (value,)


def _evidence(name: str, value: dict[str, object]) -> None:
    destination = os.environ.get("MARIVO_SLICE4C_EVIDENCE_DIR")
    if destination is not None:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(
            json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )


@pytest.mark.parametrize("kind", ["local", "engine", "object"])
@pytest.mark.parametrize("key", ["same", "different"])
@pytest.mark.parametrize("mode", ["thread", "process", "reentrant"])
def test_busy_contender_preserves_real_producer(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str, key: str, mode: str
) -> None:
    bindings = _access(request, kind)
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    target = (
        EngineTarget(next(iter(registry.datasources)))
        if kind == "engine"
        else ObjectTarget("fixture")
        if kind == "object"
        else LocalTarget()
    )
    runtime = DatasetRuntime.create(tmp_path, "writer", target=target, object_bindings=bindings)
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    committed = sources.population(ref.entity("sales.customers")).execute()
    logical = sources.observe(ref.metric("sales.revenue"))
    metric = "sales.revenue" if key == "same" else "sales.order_count"
    contender = sources.observe(ref.metric(metric))
    paused, resume = threading.Event(), threading.Event()
    evidence: dict[str, object] = {"pid": os.getpid(), "kind": kind, "key": key, "mode": mode}

    def check_contender() -> None:
        before, stats, run = snapshot(runtime), asdict(runtime.statistics), runtime.last_run_ref
        assert run is not None and stats["primary_queries"] == 1
        if mode == "process":
            child = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tests.lazy_concurrency_runtime_worker",
                    "contend",
                    str(tmp_path),
                    "--session",
                    runtime.session_ref,
                    "--metric",
                    metric,
                    "--artifact",
                    committed.state.artifact_ref.ref,
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
            assert str(tmp_path) not in str(rejected.value)
            assert rejected.value.run_ref is None
        assert snapshot(runtime) == before
        assert asdict(runtime.statistics) == stats and runtime.last_run_ref == run
        assert runtime.artifact(committed.state.artifact_ref.ref).to_pandas().shape[0] == 4
        evidence.update({"before": before, "producer_statistics": stats, "run": run})

    def pause(point: str) -> None:
        if point == "quality":
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
    retry = logical.execute()
    assert retry.state.artifact_ref == produced.state.artifact_ref
    assert snapshot(runtime) == completed
    assert runtime.statistics.events == {"reconciliation": 1}
    assert runtime.statistics.primary_queries == runtime.statistics.transferred_rows == 0
    assert runtime.statistics.worker_pid is None
    evidence.update(
        {
            "session": runtime.session_ref,
            "artifact": produced.state.artifact_ref.ref,
            "completed": completed,
            "retry_statistics": asdict(runtime.statistics),
        }
    )
    if key == "different":
        retried_contender = contender.execute()
        assert retried_contender.state.artifact_ref != produced.state.artifact_ref
        assert snapshot(runtime)["analysis_action_runs"] == 3
        evidence["contender_retry_artifact"] = retried_contender.state.artifact_ref.ref
        evidence["contender_retry_run"] = runtime.last_run_ref
    _evidence(f"contention-{kind}-{key}-{mode}", evidence)


def test_canonical_creation_releases_candidate_before_winner_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    SessionStore(tmp_path)
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

    def create(store: SessionStore, name: str, *, session_ref: str | None = None) -> SessionRecord:
        barrier.wait(timeout=10)
        record = original_create(store, name, session_ref=session_ref)
        if record.session_ref == session_ref:
            winner.append(record.session_ref)
        else:
            assert winner_released.wait(10)
        return record

    monkeypatch.setattr(admission, "session_writer_guard", tracked_guard)
    monkeypatch.setattr(SessionStore, "create_session", create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(DatasetRuntime.create, tmp_path, "canonical") for _ in range(2)]
        runtimes = [future.result(timeout=20) for future in futures]
    assert runtimes[0].session_ref == runtimes[1].session_ref
    assert snapshot(runtimes[0])["sessions"] == 1
    assert len(observed) == 3
    assert observed[-1][1] == runtimes[0].session_ref
    assert runtimes[0].store.current() == runtimes[0].store.session(runtimes[0].session_ref)


def test_losing_candidate_cannot_activate_busy_canonical_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = DatasetRuntime.create(tmp_path, "canonical")
    current = DatasetRuntime.create(tmp_path, "current")
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
        DatasetRuntime.create(tmp_path, "canonical")
    assert rejected.value.session_ref == winner.session_ref
    assert winner.store.current() == winner.store.session(current.session_ref)
    assert snapshot(winner)["sessions"] == 2


def test_fresh_process_name_race_has_one_canonical_identity(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    first, second = (DatasetRuntime.create(tmp_path, name) for name in ("first", "second"))
    logical = first.sources(semantic_registry=registry, sidecar=sidecar).observe(
        ref.metric("sales.revenue")
    )
    with session_writer_guard(
        first.store.layout.lock_path(first.session_ref), session_ref=first.session_ref
    ):
        with pytest.raises(SessionBusyError) as rejected:
            DatasetRuntime.create(tmp_path, "first")
        assert rejected.value.session_ref == first.session_ref
        assert first.store.current() == first.store.session(second.session_ref)
        opened = DatasetRuntime.open(tmp_path, first.session_ref)
        assert opened.session_ref == first.session_ref and opened.statistics.events == {}
        assert DatasetRuntime.create(tmp_path, "second").session_ref == second.session_ref
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
            pool.submit(DatasetRuntime.create, tmp_path, name) for name in ("first", "second")
        ]
        assert {value.result(timeout=20).session_ref for value in futures} == {
            first.session_ref,
            second.session_ref,
        }
    current = first.store.current()
    assert current is not None and current.session_ref in (first.session_ref, second.session_ref)
    result = logical.execute()
    assert result._owner.session_id == first.session_ref
    assert first.store.current() == current


@pytest.mark.parametrize("kind", ["local", "engine", "object"])
def test_different_sessions_overlap_inside_real_duckdb_queries(
    tmp_path: Path, request: pytest.FixtureRequest, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings = _access(request, kind)
    entered = threading.Barrier(3)
    release = threading.Event()
    original = _build_backend_from_effective
    query_threads: set[int] = set()
    thread_guard = threading.Lock()

    def build(
        datasource: DatasourceIR, effective: EffectiveDatasourceKwargs, *, read_only: bool = False
    ) -> BuiltDatasourceBackend:
        built = original(datasource, effective, read_only=read_only)
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

        backend.con.create_function("overlap_probe", blocking_identity, ["DOUBLE"], "DOUBLE")
        backend.raw_sql(
            "CREATE TEMP VIEW blocked_orders AS SELECT * REPLACE (overlap_probe(amount) AS amount) FROM orders"
        )
        return built

    monkeypatch.setattr(admission, "_build_backend_from_effective", build)
    runtimes, logicals = [], []
    for name in ("first", "second"):
        database = tmp_path / f"{name}.duckdb"
        seed_execution_database(database)
        registry, sidecar = make_execution_registry(database)
        order = registry.entities["sales.orders"]
        assert isinstance(order.source, TableSourceIR)
        registry = replace(
            registry,
            entities={
                **registry.entities,
                "sales.orders": replace(
                    order, source=replace(order.source, table="blocked_orders")
                ),
            },
        )
        registry.freeze()
        target = (
            EngineTarget(next(iter(registry.datasources)))
            if kind == "engine"
            else ObjectTarget("fixture")
            if kind == "object"
            else LocalTarget()
        )
        runtime = DatasetRuntime.create(tmp_path, name, target=target, object_bindings=bindings)
        runtimes.append(runtime)
        logicals.append(
            runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
                ref.metric("sales.revenue")
            )
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(logical.execute) for logical in logicals]
        try:
            entered.wait(timeout=20)
            assert len(query_threads) == 2
            assert all(runtime.last_run_ref is not None for runtime in runtimes)
        finally:
            release.set()
        results = [future.result(timeout=30) for future in futures]
    assert {value._owner.session_id for value in results} == {
        runtime.session_ref for runtime in runtimes
    }
    assert all(result.to_pandas()["revenue"].sum() == 147.0 for result in results)
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
