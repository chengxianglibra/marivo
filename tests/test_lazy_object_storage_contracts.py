"""SDK boundary contracts use queued responses and real local journal files."""

from __future__ import annotations

import io
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from botocore.exceptions import ReadTimeoutError
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from marivo.analysis.materialization import object_storage, object_termination
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import (
    ObjectReceipt,
    ResourceRecord,
    RunDatasetInput,
    canonical_json,
)
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    RecoveryPendingError,
    StorageAccessError,
)
from marivo.analysis.materialization.object_termination import OBJECT_REQUEST_CAPABILITY
from marivo.analysis.materialization.ownership import object_artifact_prefix
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import S3Access
from tests.lazy_materialization_fixtures import descriptor

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


@pytest.fixture
def object_sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[S3Client, Stubber, S3Access]]:
    terminated: set[ResourceRecord] = set()
    monkeypatch.setattr(object_termination, "_TERMINATED", terminated)
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "test-key", "test-secret")
    with object_storage.client(access) as s3, Stubber(s3) as stub:

        @contextmanager
        def selected(binding: S3Access) -> Iterator[S3Client]:
            assert binding == access
            yield s3

        monkeypatch.setattr(object_storage, "client", selected)
        yield s3, stub, access
        stub.assert_no_pending_responses()


def _store(project: Path) -> SessionStore:
    value = descriptor()
    store = SessionStore(project)
    store.create_session("selected", session_ref="session")
    store.admit(
        "session",
        "e" * 64,
        RunDatasetInput(
            value.definition_fingerprint,
            value.row_contract.shape_id,
            value.row_contract_fingerprint,
            value.row_set_contract_fingerprint,
            ("session.population",),
            ("entity:sales.customers",),
        ),
        run_ref="run",
    )
    return store


@pytest.mark.parametrize("status", ["Enabled", "Suspended", "missing"])
def test_target_requires_versioning(
    object_sdk: tuple[S3Client, Stubber, S3Access], status: str
) -> None:
    _, stub, access = object_sdk
    stub.add_response(
        "get_bucket_versioning",
        {} if status == "missing" else {"Status": status},
        {"Bucket": access.bucket},
    )
    if status == "Enabled":
        object_storage.validate_target(access)
    else:
        with pytest.raises(MaterializationError, match="versioning unavailable"):
            object_storage.validate_target(access)


@pytest.mark.parametrize("outcome", ["acknowledged", "no_version", "null_version", "collision"])
def test_conditional_put_reserves_before_io_and_discharges_only_the_request(
    tmp_path: Path, object_sdk: tuple[S3Client, Stubber, S3Access], outcome: str
) -> None:
    s3, stub, access = object_sdk
    store = _store(tmp_path)
    expected = {
        "Bucket": access.bucket,
        "Key": "payload",
        "Body": b"data",
        "ContentLength": 4,
        "Metadata": {"marivo-ownership": "nonce"},
        "IfNoneMatch": "*",
    }
    if outcome == "collision":
        stub.add_client_error(
            "put_object", "PreconditionFailed", http_status_code=412, expected_params=expected
        )
    else:
        response = (
            {}
            if outcome == "no_version"
            else {"VersionId": "null" if outcome == "null_version" else "version-1"}
        )
        stub.add_response("put_object", response, expected)
    events: list[str] = []

    def event(name: str) -> None:
        events.append(name)
        resources = store.resources("session")
        if name == "object_before_put":
            assert {item.resource_kind for item in resources} == {
                "object_storage_staging",
                "backend_execution",
            }

    def put() -> str:
        return object_storage._put(s3, access, store, "run", "payload", b"data", 4, "nonce", event)

    if outcome == "acknowledged":
        assert put() == "version-1"
        assert events[-1] == "object_after_put"
    else:
        with pytest.raises(MaterializationError):
            put()
    assert [r.resource_kind for r in store.resources("session")] == ["object_storage_staging"]


def test_reservation_failure_precedes_any_sdk_write(
    tmp_path: Path, object_sdk: tuple[S3Client, Stubber, S3Access], monkeypatch: pytest.MonkeyPatch
) -> None:
    s3, _, access = object_sdk
    store = _store(tmp_path)

    def deny(resource: ResourceRecord) -> None:
        raise OSError("journal unavailable")

    monkeypatch.setattr(store, "reserve", deny)
    with pytest.raises(OSError, match="journal unavailable"):
        object_storage._put(
            s3, access, store, "run", "payload", b"data", 4, "nonce", lambda _: None
        )
    assert store.resources("session") == ()


def test_unknown_sdk_timeout_blocks_only_the_owning_session(
    tmp_path: Path, object_sdk: tuple[S3Client, Stubber, S3Access], monkeypatch: pytest.MonkeyPatch
) -> None:
    s3, _, access = object_sdk
    store = _store(tmp_path)

    def timeout(**kwargs: object) -> None:
        raise ReadTimeoutError(endpoint_url="private-endpoint")

    monkeypatch.setattr(s3, "put_object", timeout)
    with pytest.raises(MaterializationError):
        object_storage._put(
            s3, access, store, "run", "payload", b"data", 4, "nonce", lambda _: None
        )
    original = store.resources("session")
    assert any(r.cleanup_capability_id == OBJECT_REQUEST_CAPABILITY for r in original)
    with pytest.raises(RecoveryPendingError):
        reconcile_session(SessionStore(tmp_path), "session", event=lambda _: None)
    assert store.resources("session") == original
    store.create_session("independent", session_ref="independent")
    reconcile_session(store, "independent", event=lambda _: None)
    assert store.resources("independent") == ()


@pytest.mark.runtime
def test_acknowledgement_without_durable_discharge_is_unknown_in_a_fresh_process(
    tmp_path: Path, object_sdk: tuple[S3Client, Stubber, S3Access], monkeypatch: pytest.MonkeyPatch
) -> None:
    s3, stub, access = object_sdk
    store = _store(tmp_path)
    stub.add_response(
        "put_object",
        {"VersionId": "version-1"},
        {
            "Bucket": access.bucket,
            "Key": "payload",
            "Body": ANY,
            "ContentLength": 4,
            "Metadata": {"marivo-ownership": "nonce"},
            "IfNoneMatch": "*",
        },
    )

    def lost_discharge(resource: ResourceRecord) -> None:
        raise SystemExit(73)

    monkeypatch.setattr(store, "discharge", lost_discharge)
    with pytest.raises(SystemExit):
        object_storage._put(
            s3, access, store, "run", "payload", b"data", 4, "nonce", lambda _: None
        )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys\nfrom pathlib import Path\n"
            "from marivo.analysis.materialization.store import SessionStore\n"
            "from marivo.analysis.materialization.reconciliation import reconcile_session\n"
            "from marivo.analysis.materialization.errors import RecoveryPendingError\n"
            "store = SessionStore(Path(sys.argv[1]))\n"
            "try:\n    reconcile_session(store, 'session', event=lambda _: None)\n"
            "except RecoveryPendingError:\n    print('pending')\n"
            "else:\n    raise AssertionError('missing durable request proof was ignored')\n",
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "pending"
    assert any(
        r.cleanup_capability_id == OBJECT_REQUEST_CAPABILITY for r in store.resources("session")
    )


@pytest.mark.parametrize("mutation", ["none", "version", "short", "missing"])
def test_range_read_pins_version_and_rejects_incomplete_or_missing_payload(
    object_sdk: tuple[S3Client, Stubber, S3Access], mutation: str
) -> None:
    s3, stub, access = object_sdk
    expected = {"Bucket": access.bucket, "Key": "payload", "VersionId": "old", "Range": "bytes=0-3"}
    if mutation == "missing":
        stub.add_client_error(
            "get_object", "NoSuchVersion", http_status_code=404, expected_params=expected
        )
        with pytest.raises(StorageAccessError) as caught:
            object_storage._get(s3, access, "payload", "old", 0, 4)
        assert caught.value.storage_status == "missing"
        return
    data = b"abc" if mutation == "short" else b"data"
    raw = io.BytesIO(data)
    body = StreamingBody(raw, len(data))
    stub.add_response(
        "get_object",
        {"Body": body, "VersionId": "new" if mutation == "version" else "old"},
        expected,
    )
    if mutation == "none":
        assert object_storage._get(s3, access, "payload", "old", 0, 4) == b"data"
    else:
        with pytest.raises(IntegrityError):
            object_storage._get(s3, access, "payload", "old", 0, 4)
    assert raw.closed


@pytest.mark.parametrize("truncated", [False, True])
def test_cleanup_deletes_only_the_owned_exact_version(
    tmp_path: Path, object_sdk: tuple[S3Client, Stubber, S3Access], truncated: bool
) -> None:
    _, stub, access = object_sdk
    store = _store(tmp_path)
    key = object_artifact_prefix("session", "artifact_nonce") + "primary/data.parquet"
    resource = ResourceRecord(
        "run",
        "object_storage_staging",
        "fixture",
        "nonce",
        "s3_versioned_key@v1",
        object_storage.object_locator("fixture", key),
    )
    store.reserve(resource)
    stub.add_response(
        "list_object_versions",
        {
            "IsTruncated": truncated,
            "Versions": [{"Key": key, "VersionId": "foreign"}, {"Key": key, "VersionId": "owned"}],
        },
        {"Bucket": access.bucket, "Prefix": key, "MaxKeys": 65},
    )
    if not truncated:
        for version, nonce in [("foreign", "other"), ("owned", "nonce")]:
            stub.add_response(
                "head_object",
                {"Metadata": {"marivo-ownership": nonce}},
                {"Bucket": access.bucket, "Key": key, "VersionId": version},
            )
        stub.add_response(
            "delete_object", {}, {"Bucket": access.bucket, "Key": key, "VersionId": "owned"}
        )
    assert object_storage.cleanup_object(store, resource, access) is not truncated


def test_missing_object_access_does_not_change_metadata_or_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    value = descriptor()
    receipt = ObjectReceipt(
        "fixture",
        object_artifact_prefix("session", "artifact") + "primary/manifest.json",
        "version-1",
        "a" * 64,
        value.storage_receipt.schema_fingerprint,
        2,
        256,
    )
    store.publish("run", "artifact", replace(value, storage_receipt=receipt))
    runtime = DatasetRuntime.open(tmp_path, "session")
    inspection = runtime.revalidate("artifact")
    assert (
        inspection.artifact_integrity,
        inspection.storage_authority,
        inspection.evidence_integrity,
    ) == ("valid", "unauthorized", "valid")


@pytest.mark.parametrize("fault", ["none", "manifest_hash", "payload_key", "null_version"])
def test_manifest_binds_exact_sibling_payload_and_content_hash(
    object_sdk: tuple[S3Client, Stubber, S3Access], fault: str
) -> None:
    import hashlib

    s3, stub, access = object_sdk
    key = "artifacts/primary/manifest.json"
    manifest = canonical_json(
        {
            "schema": "marivo.object_manifest/v1",
            "key": "foreign/data.parquet"
            if fault == "payload_key"
            else "artifacts/primary/data.parquet",
            "version_id": "null" if fault == "null_version" else "payload-version",
            "size_bytes": 4,
            "sha256": hashlib.sha256(b"data").hexdigest(),
        }
    ).encode()
    receipt = ObjectReceipt(
        "fixture",
        key,
        "manifest-version",
        "0" * 64 if fault == "manifest_hash" else hashlib.sha256(manifest).hexdigest(),
        "a" * 64,
        2,
        len(manifest) + 4,
    )
    stub.add_response(
        "head_object",
        {"ContentLength": len(manifest)},
        {
            "Bucket": access.bucket,
            "Key": key,
            "VersionId": "manifest-version",
        },
    )
    stub.add_response(
        "get_object",
        {
            "Body": StreamingBody(io.BytesIO(manifest), len(manifest)),
            "VersionId": "manifest-version",
        },
        {
            "Bucket": access.bucket,
            "Key": key,
            "VersionId": "manifest-version",
            "Range": f"bytes=0-{len(manifest) - 1}",
        },
    )
    if fault == "none":
        file = object_storage.open_manifest(s3, access, receipt)
        assert (file.key, file.version, file.size) == (
            "artifacts/primary/data.parquet",
            "payload-version",
            4,
        )
    else:
        with pytest.raises(IntegrityError):
            object_storage.open_manifest(s3, access, receipt)


@pytest.mark.parametrize("fault", ["none", "size", "version", "hash"])
def test_object_verification_checks_exact_version_size_and_bytes(
    object_sdk: tuple[S3Client, Stubber, S3Access], fault: str
) -> None:
    import hashlib

    s3, stub, access = object_sdk
    file = object_storage.ObjectFile("payload", "version-1", 4, hashlib.sha256(b"data").hexdigest())
    stub.add_response(
        "head_object",
        {
            "ContentLength": 5 if fault == "size" else 4,
            "VersionId": "version-2" if fault == "version" else "version-1",
        },
        {"Bucket": access.bucket, "Key": "payload", "VersionId": "version-1"},
    )
    if fault in ("none", "hash"):
        data = b"oops" if fault == "hash" else b"data"
        stub.add_response(
            "get_object",
            {
                "Body": StreamingBody(io.BytesIO(data), 4),
                "VersionId": "version-1",
            },
            {
                "Bucket": access.bucket,
                "Key": "payload",
                "VersionId": "version-1",
                "Range": "bytes=0-3",
            },
        )
    if fault == "none":
        file.verify(s3, access)
    else:
        with pytest.raises(IntegrityError):
            file.verify(s3, access)
