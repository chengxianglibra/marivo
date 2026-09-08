"""Object target selection and retained read authority keep distinct repair contracts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pytest
from botocore.stub import Stubber

from marivo.analysis.materialization import admission, object_storage, reads
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ObjectReceipt
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    StorageAccessError,
)
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import ObjectTarget, S3Access, object_access
from marivo.analysis.session._lazy_read_model import FailedRun
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_retained_fixtures import setup_retained

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def _bindings(kind: Literal["missing", "ambiguous"], access: S3Access) -> tuple[S3Access, ...]:
    return () if kind == "missing" else (access, access)


@pytest.mark.parametrize("kind", ["missing", "ambiguous"])
def test_object_binding_selection_teaches_configuration_without_any_io(
    monkeypatch: pytest.MonkeyPatch, kind: Literal["missing", "ambiguous"]
) -> None:
    access = S3Access(
        "fixture", "https://unavailable.invalid", "bucket", "private-key", "private-secret"
    )
    monkeypatch.setattr(object_storage, "client", forbidden)
    with pytest.raises(MaterializationError) as caught:
        object_access(_bindings(kind, access), "fixture")
    error = caught.value
    assert type(error) is MaterializationError
    assert error.stage == "storage_selection"
    assert error.expected == "one exact current object binding"
    assert error.received == "missing or ambiguous object binding"
    assert error.repair is not None
    assert "Configure" in error.repair.action and "committed" not in error.repair.action
    assert error.__context__ is None and error.__cause__ is None
    assert "private-key" not in str(error) and "private-secret" not in str(error)


@pytest.mark.parametrize("kind", ["missing", "ambiguous"])
def test_writer_binding_failure_is_one_failed_run_before_source_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["missing", "ambiguous"]
) -> None:
    fixture = setup_retained(tmp_path)
    runtime = fixture.runtime
    access = S3Access(
        "fixture", "https://unavailable.invalid", "bucket", "private-key", "private-secret"
    )
    runtime.target = ObjectTarget("fixture")
    runtime.object_bindings = _bindings(kind, access)
    monkeypatch.setattr(admission, "_build_backend_from_effective", forbidden)
    monkeypatch.setattr(object_storage, "client", forbidden)
    with pytest.raises(MaterializationError) as caught:
        fixture.sources.population(ref.entity("sales.customers")).execute()
    assert caught.value.stage == "storage_selection"
    assert caught.value.run_ref == runtime.last_run_ref
    assert caught.value.repair is not None
    assert (
        "Configure" in caught.value.repair.action and "committed" not in caught.value.repair.action
    )
    assert runtime.last_run_ref is not None
    failed = runtime.get_run(runtime.last_run_ref)
    assert isinstance(failed, FailedRun) and failed.failure.phase == "storage_selection"
    assert failed.failure.repair is not None
    assert "Configure" in failed.failure.repair.action
    assert runtime.statistics.primary_queries == runtime.statistics.validation_queries == 0
    assert runtime.statistics.worker_pid is None
    counts = snapshot(runtime)["counts"]
    assert isinstance(counts, dict)
    assert counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 1
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == counts["findings"] == 0
    assert counts["action_resource_journal"] == 0


@pytest.mark.parametrize("failure_kind", ["other_stage", "integrity"])
def test_reader_binding_translation_preserves_unrelated_structured_failures(
    monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    constructor = MaterializationError if failure_kind == "other_stage" else IntegrityError
    failure = constructor(
        expected="the original structured requirement",
        received="the original structured failure",
        repair="Apply the original repair.",
        stage="quality" if failure_kind == "other_stage" else "storage_selection",
    )

    def failed_lookup(bindings: tuple[S3Access, ...], reference: str) -> S3Access:
        raise failure

    monkeypatch.setattr(reads, "object_access", failed_lookup)
    with pytest.raises(MaterializationError) as caught:
        reads._object_read_access((), "fixture")
    assert caught.value is failure


def test_native_target_denial_records_configuration_repair_before_source_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = setup_retained(tmp_path)
    runtime = fixture.runtime
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "private-key", "private-secret")
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    base_client = object_storage.client

    @contextmanager
    def denied_client(selected: S3Access) -> Iterator[S3Client]:
        assert selected is access
        with base_client(selected) as s3, Stubber(s3) as stub:
            stub.add_client_error(
                "get_bucket_versioning",
                service_error_code="AccessDenied",
                service_message="private-sdk-canary",
                http_status_code=403,
                expected_params={"Bucket": access.bucket},
            )
            yield s3
            stub.assert_no_pending_responses()

    monkeypatch.setattr(object_storage, "client", denied_client)
    monkeypatch.setattr(admission, "_build_backend_from_effective", forbidden)
    with pytest.raises(MaterializationError) as caught:
        fixture.sources.population(ref.entity("sales.customers")).execute()
    error = caught.value
    assert error.stage == "storage_selection"
    assert error.received == "object target access is unauthorized"
    assert error.repair is not None and "Configure" in error.repair.action
    assert "committed" not in error.repair.action
    assert error.__context__ is None and error.__cause__ is None
    assert runtime.last_run_ref is not None
    failed = runtime.get_run(runtime.last_run_ref)
    assert isinstance(failed, FailedRun) and failed.failure.phase == "storage_selection"
    assert failed.failure.repair is not None and "Configure" in failed.failure.repair.action
    assert "private-sdk-canary" not in str(error)
    assert "private-sdk-canary" not in str(failed.failure)
    assert runtime.statistics.primary_queries == runtime.statistics.validation_queries == 0
    assert runtime.statistics.worker_pid is None
    counts = snapshot(runtime)["counts"]
    assert isinstance(counts, dict)
    assert counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 1
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == counts["findings"] == 0
    assert counts["action_resource_journal"] == 0


def test_sdk_client_construction_failure_teaches_target_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import boto3

    def unavailable_client(*args: object, **kwargs: object) -> S3Client:
        raise ValueError("private-sdk-construction-canary")

    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "private-key", "private-secret")
    monkeypatch.setattr(boto3, "client", unavailable_client)
    with pytest.raises(MaterializationError) as caught:
        object_storage.validate_target(access)
    error = caught.value
    assert type(error) is MaterializationError and error.stage == "storage_selection"
    assert error.received == "object target access is unknown"
    assert error.repair is not None and "Configure" in error.repair.action
    assert error.__context__ is None and error.__cause__ is None
    assert "private-sdk-construction-canary" not in str(error)


@pytest.mark.parametrize("kind", ["missing", "ambiguous"])
def test_real_object_read_and_inspection_report_unauthorized_without_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lazy_s3_access: S3Access,
    kind: Literal["missing", "ambiguous"],
) -> None:
    fixture = setup_retained(tmp_path, "object", access=lazy_s3_access)
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, ObjectReceipt)
    bindings = _bindings(kind, lazy_s3_access)
    reader = DatasetRuntime.open(tmp_path, fixture.runtime.session_ref, object_bindings=bindings)
    before = snapshot(reader)
    handle = reader.artifact(result.state.artifact_ref)
    monkeypatch.setattr(object_storage, "client", forbidden)
    for action in (
        handle.to_pandas,
        lambda: list(
            reads.payload_batches(
                tmp_path, record.descriptor.storage_receipt, policy=ReadPolicy(), bindings=bindings
            )
        ),
    ):
        with pytest.raises(StorageAccessError) as caught:
            action()
        error = caught.value
        assert error.storage_status == "unauthorized"
        assert error.__context__ is None and error.__cause__ is None
        assert lazy_s3_access.access_key_id not in str(error)
        assert lazy_s3_access.secret_access_key not in str(error)
    checked = reader.revalidate(result.state.artifact_ref)
    assert (
        checked.artifact_integrity,
        checked.storage_authority,
        checked.evidence_integrity,
    ) == ("valid", "unauthorized", "valid")
    assert {issue.kind for issue in checked.issues} == {"storage_unauthorized"}
    assert snapshot(reader) == before
