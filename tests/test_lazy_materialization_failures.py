"""Admission, atomic publication and authoritative failure recovery through real execution."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    RecoveryPendingError,
    SessionBusyError,
)
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.datasource.errors import DatasourceEnvVarMissingError
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

pytestmark = pytest.mark.runtime


def _setup(
    project: Path, event: Callable[[str], None] | None = None
) -> tuple[DatasetRuntime, LogicalMetricDataset]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(project, "failure-acceptance", event=event)
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar).observe(
        ref.metric("sales.revenue")
    ).aggregate()


def _bundle(runtime: DatasetRuntime) -> tuple[int, int, int, int, int]:
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as reader:
        values: list[int] = []
        for table in (
            "analysis_action_runs",
            "dataset_artifacts",
            "dataset_evidence",
            "findings",
            "analysis_action_run_terminals",
        ):
            value: object = reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            assert type(value) is int
            values.append(value)
        return values[0], values[1], values[2], values[3], values[4]


@pytest.mark.parametrize(
    "point",
    [
        "profile_resolution",
        "credential_resolution",
        "resource_create",
        "backend_compile",
        "source_statement",
        "output_reserved",
        "transfer",
        "after_rename",
        "quality",
        "evidence",
        "insert_artifact",
        "insert_evidence",
        "insert_findings",
        "insert_terminal",
        "before_commit",
    ],
)
def test_precommit_fault_admits_first_and_never_exposes_partial_bundle(
    tmp_path: Path, point: str
) -> None:
    observations: list[tuple[int, int, int, int, int]] = []

    def fault(event: str) -> None:
        if event == point:
            observations.append(_bundle(runtime))
            raise RuntimeError("private-source-canary")

    runtime, dataset = _setup(tmp_path, fault)
    with pytest.raises(RuntimeError) as raised:
        dataset.execute()
    assert "private-source-canary" in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert observations == [(1, 0, 0, 0, 0)]
    assert _bundle(runtime) == (1, 0, 0, 0, 1)
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert runtime.store.resources(runtime.session_ref) == ()
    assert not list(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


@pytest.mark.parametrize("point", ["after_commit", "delivery"])
def test_lost_commit_or_return_acknowledgement_recovers_original_artifact(
    tmp_path: Path, point: str
) -> None:
    def fault(event: str) -> None:
        if event == point:
            assert _bundle(runtime) == (1, 1, 1, 0, 1)
            raise RuntimeError("lost acknowledgement")

    runtime, dataset = _setup(tmp_path, fault)
    result = dataset.execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert record.producing_run_ref == runtime.last_run_ref
    assert runtime.statistics.primary_queries == 1
    assert _bundle(runtime) == (1, 1, 1, 0, 1)
    assert runtime.store.resources(runtime.session_ref) == ()
    assert result.to_pandas().loc[0, "revenue"] == 147
    assert dataset.execute().state == result.state
    assert runtime.statistics.primary_queries == 0


@pytest.mark.parametrize("committed", [False, True])
def test_unavailable_readback_preserves_authority_until_reconciliation(
    tmp_path: Path, committed: bool
) -> None:
    def fault(event: str) -> None:
        if event in ("readback", "after_commit" if committed else "after_rename"):
            raise OSError("readback unavailable")

    runtime, dataset = _setup(tmp_path, fault)
    with pytest.raises(OSError):
        dataset.execute()
    assert _bundle(runtime) == ((1, 1, 1, 0, 1) if committed else (1, 0, 0, 0, 0))
    fresh = DatasetRuntime.open(tmp_path, runtime.session_ref)
    with session_writer_guard(fresh.store.layout.lock_path(fresh.session_ref)):
        reconcile_session(fresh.store, fresh.session_ref, event=lambda _: None)
    assert _bundle(fresh) == ((1, 1, 1, 0, 1) if committed else (1, 0, 0, 0, 1))
    assert fresh.store.resources(fresh.session_ref) == ()
    assert fresh.statistics.primary_queries == 0


def test_unproved_connection_termination_blocks_next_writer_without_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unknown_open(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unknown backend construction outcome")

    runtime, dataset = _setup(tmp_path)
    monkeypatch.setattr(admission, "_build_backend_from_effective", unknown_open)
    with pytest.raises(RuntimeError):
        dataset.execute()
    assert _bundle(runtime) == (1, 0, 0, 0, 0)
    with pytest.raises(RecoveryPendingError):
        dataset.execute()
    assert _bundle(runtime) == (1, 0, 0, 0, 0)


def test_contender_admits_no_run_and_committed_reads_remain_available(tmp_path: Path) -> None:
    runtime, dataset = _setup(tmp_path)
    result = dataset.execute()
    contender = DatasetRuntime.open(tmp_path, runtime.session_ref)
    with session_writer_guard(runtime.store.layout.lock_path(runtime.session_ref)):
        with pytest.raises(SessionBusyError):
            dataset.execute()
        assert contender.artifact(result.state.artifact_ref).to_pandas().loc[0, "revenue"] == 147
    assert _bundle(runtime) == (1, 1, 1, 0, 1)


def test_missing_credential_has_no_execution_obligation_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    name, datasource = next(iter(registry.datasources.items()))
    variable = "MARIVO_SLICE2B_MISSING_DATABASE"
    monkeypatch.delenv(variable, raising=False)
    registry = replace(
        registry, datasources={name: replace(datasource, fields={}, env_refs={"path": variable})}
    )
    registry.freeze()
    runtime = DatasetRuntime.create(tmp_path, "credential-retry")
    dataset = (
        runtime.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(ref.metric("sales.revenue"))
        .aggregate()
    )
    with pytest.raises(DatasourceEnvVarMissingError) as raised:
        dataset.execute()
    assert not isinstance(raised.value, RecoveryPendingError)
    assert _bundle(runtime) == (1, 0, 0, 0, 1)
    assert runtime.store.resources(runtime.session_ref) == ()
    monkeypatch.setenv(variable, str(database))
    assert dataset.execute().to_pandas().loc[0, "revenue"] == 147
    assert _bundle(runtime) == (2, 1, 1, 0, 2)


def test_harmless_unpublished_garbage_stays_journaled_until_next_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fault(event: str) -> None:
        if event == "after_rename":
            raise RuntimeError("unpublished output")

    runtime, dataset = _setup(tmp_path, fault)

    def unavailable_cleanup(path: Path) -> None:
        raise OSError("temporarily unavailable owned output")

    with monkeypatch.context() as cleanup:
        cleanup.setattr(
            "marivo.analysis.materialization.resources.shutil.rmtree", unavailable_cleanup
        )
        with pytest.raises(RuntimeError):
            dataset.execute()
    assert _bundle(runtime) == (1, 0, 0, 0, 1)
    remaining = runtime.store.resources(runtime.session_ref)
    assert remaining and all(item.resource_kind == "local_storage_staging" for item in remaining)
    fresh = DatasetRuntime.open(tmp_path, runtime.session_ref)
    with session_writer_guard(fresh.store.layout.lock_path(fresh.session_ref)):
        reconcile_session(fresh.store, fresh.session_ref, event=lambda _: None)
    assert fresh.store.resources(fresh.session_ref) == ()
    assert _bundle(fresh) == (1, 0, 0, 0, 1)
    assert fresh.statistics.primary_queries == 0


def test_shared_store_runtime_events_belong_to_the_publishing_action(tmp_path: Path) -> None:
    first_events: list[str] = []
    second_events: list[str] = []

    def fail_first_publication(point: str) -> None:
        first_events.append(point)
        if point == "insert_artifact":
            raise RuntimeError("first action publication failure")

    first, first_dataset = _setup(tmp_path, fail_first_publication)
    second = DatasetRuntime(first.store, first.session_ref, event=second_events.append)
    registry, sidecar = make_execution_registry(tmp_path / "warehouse.duckdb")
    second_dataset = (
        second.sources(semantic_registry=registry, sidecar=sidecar)
        .observe(ref.metric("sales.revenue"))
        .aggregate()
    )
    assert first.store is second.store
    with pytest.raises(RuntimeError):
        first_dataset.execute()
    assert "insert_artifact" in first_events and "readback" in first_events
    assert second_events == []
    assert _bundle(first) == (1, 0, 0, 0, 1)
    first_completed = tuple(first_events)
    result = second_dataset.execute()
    assert tuple(first_events) == first_completed
    assert "after_commit" in second_events and "delivery" in second_events
    assert _bundle(second) == (2, 1, 1, 0, 2)
    assert second.last_run_ref == result.state.producing_run_ref
