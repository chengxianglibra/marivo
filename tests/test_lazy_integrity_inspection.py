"""Actual receipt inspection separates metadata, storage and Evidence authority."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import EngineReceipt, LocalReceipt, ObjectReceipt
from marivo.analysis.materialization.errors import (
    CollectionLimitError,
    IntegrityError,
    StorageAccessError,
)
from marivo.analysis.materialization.inspection import _storage_checks
from marivo.analysis.materialization.object_storage import _call, client, open_manifest
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import S3Access
from marivo.analysis.observation.sampling import engine_sample
from marivo.refs import ref
from tests.lazy_adapter_fixtures import AdapterFixture, setup_adapter
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

pytestmark = pytest.mark.runtime


def _setup(
    root: Path, request: pytest.FixtureRequest, kind: Literal["local", "engine", "object"]
) -> tuple[AdapterFixture, S3Access | None]:
    access = None
    if kind == "object":
        value: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(value, S3Access)
        access = value
    if kind != "local":
        return setup_adapter(root, kind, access=access), access
    database = root / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(root, "inspection")
    return AdapterFixture(
        runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database
    ), None


def _snapshot(store: SessionStore) -> str:
    with store._read() as conn:
        return "\n".join(conn.iterdump())


@pytest.mark.parametrize("kind", ["local", "engine", "object"])
def test_missing_unused_part_does_not_block_preview_but_full_inspection_reports_it(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: Literal["local", "engine", "object"],
) -> None:
    fixture, access = _setup(tmp_path, request, kind)
    runtime = fixture.runtime
    result = fixture.sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")],
        population=fixture.sources.population(ref.entity("sales.customers")),
    ).execute()
    original = _snapshot(runtime.store)
    good = runtime.revalidate(result.state.artifact_ref)
    assert (good.artifact_integrity, good.storage_authority, good.evidence_integrity) == (
        "valid",
        "readable",
        "valid",
    )
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and len(record.descriptor.retained_parts) == 2
    receipt = record.descriptor.retained_parts[-1].storage_receipt
    if isinstance(receipt, LocalReceipt):
        (tmp_path / receipt.project_relative_path / "data.parquet").unlink()
    elif isinstance(receipt, EngineReceipt):
        (tmp_path / receipt.qualified_relation_ref).unlink()
    else:
        assert isinstance(receipt, ObjectReceipt) and access is not None
        with client(access) as s3:
            file = open_manifest(s3, access, receipt)
            s3.delete_object(Bucket=access.bucket, Key=file.key, VersionId=file.version)
    fixture.database.rename(tmp_path / "source.offline")
    recovered = DatasetRuntime.open(
        tmp_path, runtime.session_ref, object_bindings=runtime.object_bindings
    )
    handle = recovered.artifact(result.state.artifact_ref)
    assert len(handle.to_pandas()) == 4
    inspected = recovered.revalidate(result.state.artifact_ref)
    assert (
        inspected.artifact_integrity,
        inspected.storage_authority,
        inspected.evidence_integrity,
    ) == ("valid", "missing", "valid")
    assert (
        len(inspected.issues) == 1
        and record.descriptor.retained_parts[-1].role in inspected.issues[0].safe_message
    )
    assert _snapshot(runtime.store) == original


@pytest.mark.parametrize("fault", ["producer", "evidence", "descriptor", "finding"])
def test_corrupt_metadata_and_evidence_are_independent_of_immutable_storage(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    fault: str,
) -> None:
    fixture, _ = _setup(tmp_path, request, "local")
    runtime = fixture.runtime
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    artifact_ref = result.state.artifact_ref
    with runtime.store._write() as conn:
        if fault == "producer":
            conn.execute(
                "UPDATE dataset_artifacts SET execution_key_digest=? WHERE artifact_ref=?",
                ("0" * 64, artifact_ref.ref),
            )
        elif fault == "evidence":
            conn.execute(
                "UPDATE dataset_evidence SET evidence_digest=? WHERE artifact_ref=?",
                ("0" * 64, artifact_ref.ref),
            )
        elif fault == "descriptor":
            conn.execute(
                "UPDATE dataset_artifacts SET descriptor_payload='{}' WHERE artifact_ref=?",
                (artifact_ref.ref,),
            )
        else:
            conn.execute(
                "INSERT INTO findings VALUES(?,?,?,?,?)",
                ("corrupt", artifact_ref.ref, 0, "0" * 64, "private-canary"),
            )
    before = _snapshot(runtime.store)
    if fault == "finding":
        assert runtime.artifact(artifact_ref).evidence_digest.finding_count == 0
        assert len(runtime.graph().artifacts) == 1
        with pytest.raises(IntegrityError):
            runtime.artifact(artifact_ref).findings()
    else:
        with pytest.raises(IntegrityError):
            runtime.artifact(artifact_ref)
    inspection = runtime.revalidate(artifact_ref)
    expected = {
        "producer": ("invalid", "readable", "valid"),
        "evidence": ("valid", "readable", "invalid"),
        "descriptor": ("invalid", "unknown", "unverifiable"),
        "finding": ("valid", "readable", "invalid"),
    }[fault]
    assert (
        inspection.artifact_integrity,
        inspection.storage_authority,
        inspection.evidence_integrity,
    ) == expected
    assert "private-canary" not in repr(inspection.issues)
    assert _snapshot(runtime.store) == before


def test_full_inspection_streams_above_the_primary_collection_row_limit(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    fixture, _ = _setup(tmp_path, request, "local")
    with duckdb.connect(str(fixture.database)) as backend:
        backend.execute("INSERT INTO customers(id,region) SELECT range+5,'EU' FROM range(100000)")
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    assert result.state.realized_row_count == 100004
    with pytest.raises(CollectionLimitError):
        result.to_pandas()
    inspection = fixture.runtime.revalidate(result.state.artifact_ref)
    assert inspection.storage_authority == "readable" and not inspection.issues


@pytest.mark.parametrize("kind", ["local", "engine", "object"])
def test_full_inspection_checks_exact_sampling_state(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: Literal["local", "engine", "object"],
) -> None:
    fixture, _ = _setup(tmp_path, request, kind)
    result = (
        fixture.sources.population(ref.entity("sales.customers"))
        .sample(engine_sample(target_rows=2, seed=3))
        .execute()
    )
    inspection = fixture.runtime.revalidate(result.state.artifact_ref)
    assert inspection.storage_authority == "readable" and not inspection.issues


def test_inspection_keeps_all_storage_problems_and_uses_confirmed_priority(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    fixture, _ = _setup(tmp_path, request, "local")
    result = fixture.sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]
    ).execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, LocalReceipt)
    primary = record.descriptor.storage_receipt
    data = tmp_path / primary.project_relative_path / "data.parquet"
    with data.open("r+b") as stream:
        stream.write(b"broken")
    part = record.descriptor.retained_parts[0].storage_receipt
    assert isinstance(part, LocalReceipt)
    (tmp_path / part.project_relative_path / "data.parquet").unlink()
    inspected = fixture.runtime.revalidate(result.state.artifact_ref)
    assert inspected.storage_authority == "mutated"
    assert {issue.kind for issue in inspected.issues} == {"storage_mutated", "storage_missing"}


def test_missing_object_access_is_unauthorized_without_affecting_other_axes(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    fixture, _ = _setup(tmp_path, request, "object")
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    runtime = DatasetRuntime.open(tmp_path, fixture.runtime.session_ref)
    inspection = runtime.revalidate(result.state.artifact_ref)
    assert (
        inspection.artifact_integrity,
        inspection.storage_authority,
        inspection.evidence_integrity,
    ) == ("valid", "unauthorized", "valid")


@pytest.mark.parametrize(
    "code,status,expected",
    [
        ("AccessDenied", 403, "unauthorized"),
        ("NoSuchVersion", 404, "missing"),
        ("SlowDown", 503, "unknown"),
    ],
)
def test_sdk_failure_classification_retains_no_native_message(
    code: str, status: int, expected: str
) -> None:
    from botocore.exceptions import ClientError

    def fail() -> None:
        raise ClientError(
            {
                "Error": {"Code": code, "Message": "secret-canary"},
                "ResponseMetadata": {
                    "HTTPStatusCode": status,
                    "RequestId": "fixture",
                    "HostId": "fixture",
                    "HTTPHeaders": {},
                    "RetryAttempts": 0,
                },
            },
            "GetObject",
        )

    with pytest.raises(StorageAccessError) as caught:
        _call(fail)
    assert caught.value.storage_status == expected
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "secret-canary" not in str(caught.value)


def test_audit_deadline_kills_reader_and_leaves_store_unchanged(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    fixture, _ = _setup(tmp_path, request, "local")
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    before = _snapshot(fixture.runtime.store)
    started = time.monotonic()
    checks = _storage_checks(
        tmp_path, record.descriptor, (), policy=replace(ReadPolicy(), deadline_seconds=0.001)
    )
    assert time.monotonic() - started < 5
    assert tuple(check.status for check in checks) == ("unknown",)
    assert _snapshot(fixture.runtime.store) == before


@pytest.mark.parametrize("state", ["missing", "empty", "old"])
def test_read_factory_never_initializes_missing_or_unversioned_state(
    tmp_path: Path, state: str
) -> None:
    path = tmp_path / ".marivo/analysis/generations/v3/session_store.db"
    if state != "missing":
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as conn:
            if state == "old":
                conn.execute("PRAGMA user_version=2")
    before = {
        str(item.relative_to(tmp_path)): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    with pytest.raises(IntegrityError):
        SessionStore.open_existing(tmp_path)
    after = {
        str(item.relative_to(tmp_path)): item.read_bytes()
        for item in tmp_path.rglob("*")
        if item.is_file()
    }
    assert before == after
