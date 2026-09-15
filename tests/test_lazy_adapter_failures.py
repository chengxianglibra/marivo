"""Reservation, mutation and atomic publication failures against real adapters."""

from __future__ import annotations

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
from marivo.analysis.materialization.targets import ObjectTarget
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
        "output_reserved",
        "transfer",
        "before_rename",
        "after_rename",
        "insert_artifact",
        "insert_evidence",
        "insert_terminal",
        "before_commit",
    ],
)
def test_parquet_failures_publish_nothing_and_clean_exact_reservations(
    tmp_path: Path, point: str
) -> None:
    def event(name: str) -> None:
        if name == point:
            raise RuntimeError("injected private failure")

    fixture = setup_adapter(tmp_path, "engine", event=event)
    unrelated = tmp_path / "unrelated.duckdb"
    unrelated.write_bytes(b"preserve")
    with pytest.raises(RuntimeError):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    assert unrelated.read_bytes() == b"preserve"
    assert not tuple(
        fixture.runtime.store.layout.session_dir(fixture.runtime.session_ref).rglob("*.parquet")
    )


@pytest.mark.parametrize("mutation", ["replace", "missing", "in_place"])
def test_parquet_exact_content_rejects_mutation(tmp_path: Path, mutation: str) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    materialized = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, c.LocalReceipt)
    path = tmp_path / record.descriptor.storage_receipt.project_relative_path / "data.parquet"
    original = path.read_bytes()
    if mutation == "in_place":
        with path.open("r+b") as stream:
            stream.write(b"FAIL")
        assert path.stat().st_size == len(original)
    else:
        path.unlink()
        if mutation == "replace":
            path.write_bytes(b"invalid parquet replacement")
    reopened = fixture.runtime.artifact(materialized.state.artifact_ref)
    with pytest.raises(IntegrityError):
        reopened.to_pandas()


def test_missing_object_configuration_precedes_source_work(tmp_path: Path) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    fixture.runtime.target = ObjectTarget("absent")
    with pytest.raises(MaterializationError, match="storage_selection"):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("source_statement", 0) == 0


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


@pytest.mark.parametrize("independent_source", [False, True])
def test_parquet_identity_checkpoint_uses_registered_native_scan(
    tmp_path: Path,
    independent_source: bool,
) -> None:
    from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

    fixture = setup_adapter(tmp_path, "engine")
    population = fixture.sources.population(ref.entity("sales.customers")).execute()
    sources = fixture.sources
    if independent_source:
        foreign = tmp_path / "foreign.duckdb"
        seed_execution_database(foreign)
        registry, sidecar = make_execution_registry(foreign)
        sources = fixture.runtime.sources(semantic_registry=registry, sidecar=sidecar)
    with duckdb.connect(str(fixture.database)) as connection:
        connection.execute("DROP TABLE customers")
    result = (
        sources.observe(ref.metric("sales.revenue"), population=population).aggregate().execute()
    )
    assert result.to_pandas()["revenue"].tolist() == [147.0]
    assert fixture.runtime.last_run_ref is not None
    run = fixture.runtime.get_run(fixture.runtime.last_run_ref)
    assert run.input_artifact_refs == (population.state.artifact_ref,)
    assert fixture.runtime.statistics.events.get("local_execution_started", 0) == 0


def test_pandas_result_publishes_parquet_without_source_upload(tmp_path: Path) -> None:
    from marivo.analysis.observation.predicates import gt
    from tests.lazy_local_fixtures import REVENUE, pandas_methods, setup_local

    runtime, sources, database = setup_local(tmp_path)
    result = sources.observe(REVENUE).execute()
    database.rename(database.with_suffix(".offline"))
    with pandas_methods("metric.where"):
        output = result.where(gt(REVENUE, 15)).execute()
    assert sorted(output.to_pandas()["revenue"].tolist()) == [30.0, 100.0]
    assert runtime.statistics.events.get("local_execution_started", 0) > 0
    assert runtime.statistics.primary_queries == 0
    record = runtime.store.artifact(output.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, c.LocalReceipt)
    assert runtime.store.resources(runtime.session_ref) == ()


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
        if resource.resource_kind == "local_storage_staging":
            rejected = True
            raise OSError("injected reservation persistence failure")
        original(store, resource)

    monkeypatch.setattr(SessionStore, "reserve", refuse)
    with pytest.raises(MaterializationError):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    assert rejected
    _assert_failed(fixture.runtime)
    assert not tuple(tmp_path.rglob("*.parquet"))
