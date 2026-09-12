"""Reservation, mutation and atomic publication failures against real adapters."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis.materialization import contracts as c
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
)
from marivo.analysis.materialization.storage import StoragePolicy
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget
from marivo.refs import ref
from tests.lazy_adapter_fixtures import setup_adapter

pytestmark = pytest.mark.runtime


def _assert_failed(runtime: DatasetRuntime) -> None:
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize(
    "point",
    [
        "engine_producer_reserved",
        "engine_payload_create",
        "before_rename",
        "after_rename",
        "insert_artifact",
        "insert_evidence",
        "insert_terminal",
        "before_commit",
    ],
)
def test_engine_failures_publish_nothing_and_clean_exact_reservations(
    tmp_path: Path, point: str
) -> None:
    def event(name: str) -> None:
        if name == point:
            raise RuntimeError("injected private failure")

    fixture = setup_adapter(tmp_path, "engine", event=event)
    unrelated = tmp_path / "unrelated.duckdb"
    unrelated.write_bytes(b"preserve")
    with pytest.raises(MaterializationError):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    assert unrelated.read_bytes() == b"preserve"
    assert not tuple(
        fixture.runtime.store.layout.session_dir(fixture.runtime.session_ref).rglob(
            "payload.duckdb"
        )
    )


@pytest.mark.parametrize("mutation", ["replace", "missing", "write_attempt"])
def test_engine_exact_version_rejects_mutation(tmp_path: Path, mutation: str) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    materialized = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, c.LocalReceipt)
    path = tmp_path / record.descriptor.storage_receipt.qualified_relation_ref
    if mutation == "write_attempt":
        with (
            duckdb.connect(str(path), read_only=True) as db,
            pytest.raises(duckdb.InvalidInputException),
        ):
            db.execute("DELETE FROM rows")
        assert len(materialized.to_pandas()) == 4
        return
    path.unlink()
    if mutation == "replace":
        with duckdb.connect(str(path)) as db:
            db.execute("CREATE TABLE rows AS SELECT {'id': 999::BIGINT} AS entity_identity")
    reopened = fixture.runtime.artifact(materialized.state.artifact_ref)
    with pytest.raises(IntegrityError):
        reopened.to_pandas()


@pytest.mark.parametrize("kind", ["engine", "object"])
def test_storage_configuration_failure_precedes_source_work(tmp_path: Path, kind: str) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    fixture.runtime.target = LocalTarget() if kind == "engine" else ObjectTarget("absent")
    with pytest.raises(MaterializationError, match="storage_selection"):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("source_statement", 0) == 0


def test_engine_storage_budget_aborts_without_another_target(tmp_path: Path) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    assert isinstance(fixture.runtime.target, LocalTarget)
    fixture.runtime.target = replace(
        fixture.runtime.target, policy=StoragePolicy(max_stored_bytes=1)
    )
    with pytest.raises(MaterializationError, match="storage budget") as caught:
        fixture.sources.population(ref.entity("sales.customers")).execute()
    assert caught.value.stage == "transfer_guard"
    assert caught.value.received == "engine storage budget exceeded"
    _assert_failed(fixture.runtime)
    assert fixture.runtime.last_run_ref is not None
    run = fixture.runtime.store.run(fixture.runtime.last_run_ref)
    assert run is not None and run.failure is not None
    assert run.failure.phase == "transfer_guard"
    assert run.failure.received == "engine storage budget exceeded"
    assert not tuple(tmp_path.rglob("*.parquet"))


def test_binding_hit_ignores_new_invalid_target(tmp_path: Path) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    logical = fixture.sources.population(ref.entity("sales.customers"))
    result = logical.execute()
    fixture.runtime.target = ObjectTarget("unavailable")
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert fixture.runtime.statistics.events == {"reconciliation": 1}


@pytest.mark.parametrize("kind", ["engine"])
def test_lost_commit_ack_recovers_committed_output(
    tmp_path: Path, request: pytest.FixtureRequest, kind: Literal["engine", "object"]
) -> None:

    def event(name: str) -> None:
        if name == "after_commit":
            raise RuntimeError("lost acknowledgement")

    fixture = setup_adapter(tmp_path, kind, event=event)
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    assert len(result.to_pandas()) == 4
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_one_validated_target_is_fixed_for_the_action(tmp_path: Path) -> None:
    def event(name: str) -> None:
        if name == "backend_compile":
            fixture.runtime.target = ObjectTarget("unavailable")

    fixture = setup_adapter(tmp_path, "engine", event=event)
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, c.LocalReceipt)


@pytest.mark.parametrize("kind", ["foreign_engine", "local"])
def test_identity_checkpoint_cannot_be_imported_into_source_domain(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: str,
) -> None:
    from marivo.analysis.compiler.errors import DatasetCompilationError
    from marivo.analysis.materialization.targets import LocalTarget
    from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

    fixture = setup_adapter(tmp_path, "engine")
    if kind == "local":
        fixture.runtime.target = LocalTarget()
    population = fixture.sources.population(ref.entity("sales.customers")).execute()
    sources = fixture.sources
    if kind == "foreign_engine":
        foreign = tmp_path / "foreign.duckdb"
        seed_execution_database(foreign)
        registry, sidecar = make_execution_registry(foreign)
        sources = fixture.runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logical = sources.observe(ref.metric("sales.revenue"), population=population)
    with pytest.raises(DatasetCompilationError):
        logical.execute()
    assert fixture.runtime.last_run_ref is None
    assert fixture.runtime.statistics.events == {"reconciliation": 1}


def test_pandas_result_cannot_be_uploaded_to_engine(tmp_path: Path) -> None:
    from marivo.analysis.observation.predicates import gt
    from tests.lazy_local_fixtures import REVENUE, setup_local

    runtime, sources, _ = setup_local(tmp_path)
    result = sources.observe(REVENUE).execute()
    runtime.target = LocalTarget()
    with pytest.raises(MaterializationError, match="storage_selection"):
        result.where(gt(REVENUE, 15)).execute()
    _assert_failed(runtime)
    assert runtime.statistics.events.get("local_worker_reserved", 0) == 0
    assert runtime.statistics.events.get("profile_resolution", 0) == 0


@pytest.mark.parametrize("kind", ["engine"])
def test_reservation_insert_failure_prevents_external_resource_creation(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["engine", "object"],
) -> None:
    from marivo.analysis.materialization.store import SessionStore

    fixture = setup_adapter(tmp_path, kind)
    original = SessionStore.reserve
    rejected = False

    def refuse(store: SessionStore, resource: c.ResourceRecord) -> None:
        nonlocal rejected
        if resource.resource_kind == f"{kind}_storage_staging":
            rejected = True
            raise OSError("injected reservation persistence failure")
        original(store, resource)

    monkeypatch.setattr(SessionStore, "reserve", refuse)
    with pytest.raises(MaterializationError):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    assert rejected
    _assert_failed(fixture.runtime)
    assert not tuple(tmp_path.rglob("payload.duckdb"))
