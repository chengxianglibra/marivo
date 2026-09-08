"""Reservation, mutation and atomic publication failures against real adapters."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import duckdb
import pytest
from botocore.exceptions import ReadTimeoutError

from marivo.analysis.materialization import contracts as c
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
)
from marivo.analysis.materialization.object_storage import client, open_manifest
from marivo.analysis.materialization.storage import StoragePolicy
from marivo.analysis.materialization.targets import EngineTarget, ObjectTarget, S3Access
from marivo.refs import ref
from tests.lazy_adapter_fixtures import setup_adapter

pytestmark = pytest.mark.runtime

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


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


@pytest.mark.parametrize(
    "point",
    [
        "object_reserved",
        "object_before_put",
        "object_after_put",
        "insert_artifact",
        "insert_evidence",
        "before_commit",
    ],
)
def test_object_failures_keep_no_partial_bundle(
    tmp_path: Path, lazy_s3_access: S3Access, point: str
) -> None:
    def event(name: str) -> None:
        if name == point:
            raise RuntimeError("injected object write failure")

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access, event=event)
    with pytest.raises(MaterializationError):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    with client(lazy_s3_access) as s3:
        assert not s3.list_object_versions(Bucket=lazy_s3_access.bucket).get("Versions")


@pytest.mark.parametrize("mutation", ["replace", "missing", "write_attempt"])
def test_engine_exact_version_rejects_mutation(tmp_path: Path, mutation: str) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    materialized = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, c.EngineReceipt)
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


def test_object_new_version_does_not_change_old_artifact_and_deleted_version_fails(
    tmp_path: Path, lazy_s3_access: S3Access
) -> None:
    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access)
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, c.ObjectReceipt)
    with client(lazy_s3_access) as s3:
        file = open_manifest(s3, lazy_s3_access, record.descriptor.storage_receipt)
        s3.put_object(
            Bucket=lazy_s3_access.bucket, Key=file.key, Body=b"a different latest version"
        )
    assert result.to_pandas()["entity_identity"].tolist() == [(1,), (2,), (3,), (4,)]
    with client(lazy_s3_access) as s3:
        s3.delete_object(Bucket=lazy_s3_access.bucket, Key=file.key, VersionId=file.version)
    with pytest.raises(MaterializationError):
        result.to_pandas()


@pytest.mark.parametrize("kind", ["engine", "object"])
def test_storage_configuration_failure_precedes_source_work(tmp_path: Path, kind: str) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    fixture.runtime.target = EngineTarget("foreign") if kind == "engine" else ObjectTarget("absent")
    with pytest.raises(MaterializationError, match="storage_selection"):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("source_statement", 0) == 0


def test_engine_storage_budget_aborts_without_another_target(tmp_path: Path) -> None:
    fixture = setup_adapter(tmp_path, "engine")
    assert isinstance(fixture.runtime.target, EngineTarget)
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


@pytest.mark.parametrize("kind", ["engine", "object"])
def test_lost_commit_ack_recovers_committed_output(
    tmp_path: Path, request: pytest.FixtureRequest, kind: Literal["engine", "object"]
) -> None:
    access = request.getfixturevalue("lazy_s3_access") if kind == "object" else None

    def event(name: str) -> None:
        if name == "after_commit":
            raise RuntimeError("lost acknowledgement")

    fixture = setup_adapter(tmp_path, kind, access=access, event=event)
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    assert len(result.to_pandas()) == 4
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_unknown_object_request_termination_blocks_only_its_session(
    tmp_path: Path,
    lazy_s3_access: S3Access,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import object_storage
    from marivo.analysis.materialization.resources import confirm_execution_termination

    original = object_storage.client

    def timeout(**kwargs: object) -> None:
        raise ReadTimeoutError(endpoint_url="private-endpoint-canary")

    @contextmanager
    def interrupted(access: S3Access) -> Iterator[S3Client]:
        with original(access) as s3:
            s3.meta.events.register("before-call.s3.PutObject", timeout)
            yield s3

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access)
    monkeypatch.setattr(object_storage, "client", interrupted)
    logical = fixture.sources.population(ref.entity("sales.customers"))
    with pytest.raises(RecoveryPendingError) as caught:
        logical.execute()
    assert "private-endpoint-canary" not in str(caught.value)
    resources = fixture.runtime.store.resources(fixture.runtime.session_ref)
    assert any(
        item.cleanup_capability_id == "s3_request@v1" and not confirm_execution_termination(item)
        for item in resources
    )
    with pytest.raises(RecoveryPendingError):
        logical.execute()
    independent = DatasetRuntime.create(
        tmp_path,
        "independent",
        target=EngineTarget(next(iter(fixture.sources._owner.semantic_registry.datasources))),
    )
    sources = independent.sources(
        semantic_registry=fixture.sources._owner.semantic_registry,
        sidecar=fixture.sources._owner.sidecar,
    )
    assert len(sources.population(ref.entity("sales.customers")).execute().to_pandas()) == 4


def test_conditional_object_collision_preserves_the_foreign_version(
    tmp_path: Path,
    lazy_s3_access: S3Access,
) -> None:
    from marivo.analysis.materialization.object_storage import decode_locator

    created: list[str] = []

    def event(name: str) -> None:
        if name == "object_reserved" and not created:
            resource = next(
                item
                for item in fixture.runtime.store.resources(fixture.runtime.session_ref)
                if item.resource_kind == "object_storage_staging"
            )
            _, key = decode_locator(resource.safe_locator)
            with client(lazy_s3_access) as s3:
                s3.put_object(
                    Bucket=lazy_s3_access.bucket,
                    Key=key,
                    Body=b"foreign",
                    Metadata={"marivo-ownership": "foreign"},
                )
            created.append(key)

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access, event=event)
    with pytest.raises(MaterializationError):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    with client(lazy_s3_access) as s3:
        response = s3.get_object(Bucket=lazy_s3_access.bucket, Key=created[0])
        try:
            assert response["Body"].read() == b"foreign"
        finally:
            response["Body"].close()


def test_object_required_part_failure_discards_the_whole_bundle(
    tmp_path: Path,
    lazy_s3_access: S3Access,
) -> None:
    puts = 0

    def event(name: str) -> None:
        nonlocal puts
        if name == "object_after_put":
            puts += 1
            if puts == 3:
                raise RuntimeError("required part write failed")

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access, event=event)
    with pytest.raises(MaterializationError):
        fixture.sources.observe(ref.metric("sales.mean_amount")).execute()
    _assert_failed(fixture.runtime)
    with client(lazy_s3_access) as s3:
        assert not s3.list_object_versions(Bucket=lazy_s3_access.bucket).get("Versions")


def test_one_validated_target_is_fixed_for_the_action(tmp_path: Path) -> None:
    def event(name: str) -> None:
        if name == "backend_compile":
            fixture.runtime.target = ObjectTarget("unavailable")

    fixture = setup_adapter(tmp_path, "engine", event=event)
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, c.EngineReceipt)


def test_harmless_private_staging_cleanup_does_not_block_publication_or_next_run(
    tmp_path: Path,
    lazy_s3_access: S3Access,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    from marivo.analysis.materialization.reconciliation import reconcile_session

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access)

    def denied(path: Path) -> None:
        raise OSError("harmless private staging cleanup unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", denied)
        first = fixture.sources.population(ref.entity("sales.customers")).execute()
        assert fixture.runtime.store.resources(fixture.runtime.session_ref)
        second = fixture.sources.population(ref.entity("sales.orders")).execute()
        assert first.state.artifact_ref != second.state.artifact_ref
        assert len(first.to_pandas()) == 4
    reconcile_session(
        fixture.runtime.store,
        fixture.runtime.session_ref,
        object_bindings=(lazy_s3_access,),
        event=lambda name: None,
    )
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
    assert len(first.to_pandas()) == 4


def test_named_create_defers_terminal_object_cleanup_until_bindings_are_available(
    tmp_path: Path,
    lazy_s3_access: S3Access,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import object_storage
    from marivo.analysis.materialization.store import SessionStore
    from tests.lazy_adapter_runtime_worker import snapshot

    def fail_before_commit(name: str) -> None:
        if name == "before_commit":
            raise RuntimeError("leave terminal object garbage for later cleanup")

    def defer_cleanup(store: SessionStore, resource: c.ResourceRecord, access: S3Access) -> bool:
        return False

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access, event=fail_before_commit)
    with monkeypatch.context() as patch:
        patch.setattr(object_storage, "cleanup_object", defer_cleanup)
        with pytest.raises(MaterializationError):
            fixture.sources.population(ref.entity("sales.customers")).execute()
    assert fixture.runtime.last_run_ref is not None
    failed = fixture.runtime.store.run(fixture.runtime.last_run_ref)
    assert failed is not None and failed.lifecycle == "failed"
    resources = fixture.runtime.store.resources(fixture.runtime.session_ref)
    assert resources and all(item.resource_kind == "object_storage_staging" for item in resources)
    _, key = object_storage.decode_locator(resources[0].safe_locator)
    with client(lazy_s3_access) as s3:
        foreign = s3.put_object(
            Bucket=lazy_s3_access.bucket,
            Key=key,
            Body=b"foreign",
            Metadata={"marivo-ownership": "foreign"},
        )["VersionId"]
        versions_before = s3.list_object_versions(Bucket=lazy_s3_access.bucket)["Versions"]
    assert len(versions_before) > 1
    independent = DatasetRuntime.create(tmp_path, "independent")
    before = snapshot(fixture.runtime)

    reopened = DatasetRuntime.create(tmp_path, "adapter")
    assert reopened.session_ref == fixture.runtime.session_ref != independent.session_ref
    active = reopened.store.current()
    assert active is not None and active.session_ref == fixture.runtime.session_ref
    assert reopened.store.resources(reopened.session_ref) == resources
    assert snapshot(reopened) == before
    assert reopened.statistics.events == {"reconciliation": 1}
    with client(lazy_s3_access) as s3:
        assert s3.list_object_versions(Bucket=lazy_s3_access.bucket)["Versions"] == versions_before

    restored = DatasetRuntime.create(tmp_path, "adapter", object_bindings=(lazy_s3_access,))
    assert restored.session_ref == fixture.runtime.session_ref
    assert restored.store.resources(restored.session_ref) == ()
    assert restored.store.run(failed.run_ref) == failed
    assert snapshot(restored) == {**before, "action_resource_journal": 0}
    assert restored.statistics.events == {"reconciliation": 1}
    with client(lazy_s3_access) as s3:
        versions = s3.list_object_versions(Bucket=lazy_s3_access.bucket)["Versions"]
        assert [(item["Key"], item["VersionId"]) for item in versions] == [(key, foreign)]


def test_object_credentials_are_absent_from_errors_chains_and_store(
    tmp_path: Path,
    lazy_s3_access: S3Access,
) -> None:
    import traceback

    canary = "slice4b-private-credential-canary"
    access = replace(lazy_s3_access, secret_access_key=canary)
    fixture = setup_adapter(tmp_path, "object", access=access)
    with pytest.raises(MaterializationError) as caught:
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    pending: list[BaseException] = [caught.value]
    while pending:
        error = pending.pop()
        assert canary not in repr(error) + str(error) + "".join(traceback.format_exception(error))
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None:
            pending.append(error.__context__)
    assert canary.encode() not in fixture.runtime.store.db_path.read_bytes()


@pytest.mark.parametrize("kind", ["foreign_engine", "local", "object"])
def test_identity_checkpoint_cannot_be_imported_into_source_domain(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: str,
) -> None:
    from marivo.analysis.compiler.errors import DatasetCompilationError
    from marivo.analysis.materialization.targets import LocalTarget
    from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

    access: S3Access | None = None
    if kind == "object":
        value: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(value, S3Access)
        access = value
    fixture = setup_adapter(tmp_path, "object" if kind == "object" else "engine", access=access)
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
    runtime.target = EngineTarget(next(iter(sources._owner.semantic_registry.datasources)))
    with pytest.raises(MaterializationError, match="storage_selection"):
        result.where(gt(REVENUE, 15)).execute()
    _assert_failed(runtime)
    assert runtime.statistics.events.get("local_worker_reserved", 0) == 0
    assert runtime.statistics.events.get("profile_resolution", 0) == 0


def test_corrupted_uploaded_version_fails_finalization_and_cleans_exact_objects(
    tmp_path: Path,
    lazy_s3_access: S3Access,
) -> None:
    corrupted = False

    def corrupt(name: str) -> None:
        nonlocal corrupted
        if name == "object_before_put" and not corrupted:
            paths = tuple(tmp_path.rglob("*.parquet"))
            assert len(paths) == 1
            payload = bytearray(paths[0].read_bytes())
            payload[len(payload) // 2] ^= 1
            paths[0].write_bytes(payload)
            corrupted = True

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access, event=corrupt)
    with pytest.raises(MaterializationError, match="object content changed"):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    assert corrupted
    _assert_failed(fixture.runtime)
    with client(lazy_s3_access) as s3:
        assert not s3.list_object_versions(Bucket=lazy_s3_access.bucket).get("Versions")


@pytest.mark.parametrize("kind", ["engine", "object"])
def test_reservation_insert_failure_prevents_external_resource_creation(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["engine", "object"],
) -> None:
    from marivo.analysis.materialization.store import SessionStore

    access: S3Access | None = None
    if kind == "object":
        value: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(value, S3Access)
        access = value
    fixture = setup_adapter(tmp_path, kind, access=access)
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
    if access is not None:
        with client(access) as s3:
            assert not s3.list_object_versions(Bucket=access.bucket).get("Versions")
    else:
        assert not tuple(tmp_path.rglob("payload.duckdb"))


@pytest.mark.parametrize("outcome", ["success", "rollback", "lost_ack"])
def test_object_termination_proofs_follow_durable_journal_lifetime(
    tmp_path: Path,
    lazy_s3_access: S3Access,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    from marivo.analysis.materialization.object_termination import (
        OBJECT_REQUEST_CAPABILITY,
        object_request_is_terminal,
    )
    from marivo.analysis.materialization.store import SessionStore

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access)
    requests: list[c.ResourceRecord] = []
    rollback_checked = False
    original_discharge = SessionStore.discharge

    def discharge(store: SessionStore, resource: c.ResourceRecord) -> None:
        if resource.cleanup_capability_id == OBJECT_REQUEST_CAPABILITY:
            assert object_request_is_terminal(resource)
            assert resource in store.resources(fixture.runtime.session_ref)
            requests.append(resource)
        original_discharge(store, resource)
        assert not object_request_is_terminal(resource)

    monkeypatch.setattr(SessionStore, "discharge", discharge)

    def event(name: str) -> None:
        nonlocal rollback_checked
        if name in ("object_after_put", "before_commit"):
            resources = fixture.runtime.store.resources(fixture.runtime.session_ref)
            assert requests and not any(object_request_is_terminal(item) for item in requests)
            assert not any(
                item.cleanup_capability_id == OBJECT_REQUEST_CAPABILITY for item in resources
            )
            assert any(item.resource_kind == "object_storage_staging" for item in resources)
        if name == "before_commit" and outcome == "rollback":
            raise RuntimeError("rollback after recording terminal requests")
        if name == "readback" and outcome == "rollback":
            assert not any(object_request_is_terminal(item) for item in requests)
            rollback_checked = True
        if name == "after_commit":
            assert requests and not any(object_request_is_terminal(item) for item in requests)
            if outcome == "lost_ack":
                raise RuntimeError("lost committed acknowledgement")

    monkeypatch.setattr(fixture.runtime, "_hook", event)
    logical = fixture.sources.population(ref.entity("sales.customers"))
    if outcome == "rollback":
        with pytest.raises(MaterializationError):
            logical.execute()
        assert rollback_checked
        _assert_failed(fixture.runtime)
    else:
        assert len(logical.execute().to_pandas()) == 4
    assert requests and not any(object_request_is_terminal(item) for item in requests)
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()


def test_acknowledged_request_discharge_failure_retains_live_proof_until_cleanup(
    tmp_path: Path,
    lazy_s3_access: S3Access,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization.object_termination import (
        OBJECT_REQUEST_CAPABILITY,
        object_request_is_terminal,
    )
    from marivo.analysis.materialization.store import SessionStore

    fixture = setup_adapter(tmp_path, "object", access=lazy_s3_access)
    request: c.ResourceRecord | None = None
    readback_checked = False
    original = SessionStore.discharge

    def unavailable(store: SessionStore, resource: c.ResourceRecord) -> None:
        nonlocal request
        if resource.cleanup_capability_id == OBJECT_REQUEST_CAPABILITY and request is None:
            request = resource
            assert object_request_is_terminal(resource)
            assert resource in store.resources(fixture.runtime.session_ref)
            raise OSError("request discharge unavailable")
        original(store, resource)

    def event(name: str) -> None:
        nonlocal readback_checked
        if name == "readback":
            assert request is not None and object_request_is_terminal(request)
            assert request in fixture.runtime.store.resources(fixture.runtime.session_ref)
            readback_checked = True

    monkeypatch.setattr(fixture.runtime, "_hook", event)
    monkeypatch.setattr(SessionStore, "discharge", unavailable)
    with pytest.raises(MaterializationError):
        fixture.sources.population(ref.entity("sales.customers")).execute()
    _assert_failed(fixture.runtime)
    assert readback_checked
    assert request is not None and not object_request_is_terminal(request)
