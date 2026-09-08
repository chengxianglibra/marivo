"""Version-pinned S3 Parquet payloads with exact pre-create resource ownership."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.contracts import (
    LocalReceipt,
    ObjectReceipt,
    ResourceRecord,
    RetainedPart,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.object_termination import OBJECT_REQUEST_CAPABILITY
from marivo.analysis.materialization.ownership import object_artifact_prefix
from marivo.analysis.materialization.resources import prove_local_termination
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    _checked_path,
    _fail,
    _integrity,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import S3Access, selection_error

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import PutObjectOutputTypeDef

_T = TypeVar("_T")


def _call(action: Callable[[], _T]) -> _T:
    """Drop SDK errors and their raw request/credential-bearing exception chains."""
    try:
        return action()
    except Exception:
        pass
    raise MaterializationError(
        expected="successful access to the exact configured object version",
        received="object request failed",
        repair="Restore the configured object's access or exact version, then retry.",
        stage="storage_access",
    )


@contextmanager
def client(access: S3Access) -> Iterator[S3Client]:
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        selection_error("the installed S3 storage client", "S3 client unavailable")
    value = _call(
        lambda: boto3.client(
            "s3",
            endpoint_url=access.endpoint_url,
            region_name=access.region,
            aws_access_key_id=access.access_key_id,
            aws_secret_access_key=access.secret_access_key,
            aws_session_token=access.session_token,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"total_max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
        )
    )
    try:
        yield value
    finally:
        value.close()


def validate_target(access: S3Access) -> None:
    with client(access) as s3:
        versioning = _call(lambda: s3.get_bucket_versioning(Bucket=access.bucket))
        if versioning.get("Status") != "Enabled":
            selection_error("an enabled versioned object bucket", "object versioning unavailable")


def object_locator(reference: str, key: str) -> str:
    return codec.canonical_json({"object_store_ref": reference, "key": key})


def decode_locator(locator: str) -> tuple[str, str]:
    obj = codec._obj(codec.parse_json(locator), "object_store_ref key")
    reference, key = codec._text(obj["object_store_ref"]), codec._text(obj["key"])
    codec._relative(key)
    return reference, key


def _put(
    s3: S3Client,
    access: S3Access,
    store: SessionStore,
    run_ref: str,
    key: str,
    data: bytes | io.BufferedReader,
    size: int,
    nonce: str,
    event: Callable[[str], None],
) -> str:
    locator = object_locator(access.object_store_ref, key)
    resource = ResourceRecord(
        run_ref,
        "object_storage_staging",
        access.object_store_ref,
        nonce,
        "s3_versioned_key@v1",
        locator,
    )
    store.reserve(resource)
    event("object_reserved")
    request = ResourceRecord(
        run_ref,
        "backend_execution",
        access.object_store_ref,
        nonce,
        OBJECT_REQUEST_CAPABILITY,
        locator,
    )
    store.reserve(request)
    try:
        event("object_before_put")
    except BaseException:
        prove_local_termination(request)
        raise
    from botocore.exceptions import ClientError

    response: PutObjectOutputTypeDef | None = None
    try:
        response = s3.put_object(
            Bucket=access.bucket,
            Key=key,
            Body=data,
            ContentLength=size,
            Metadata={"marivo-ownership": nonce},
            IfNoneMatch="*",
        )
    except ClientError as error:
        status: object = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if isinstance(status, int) and 400 <= status < 600:
            prove_local_termination(request)
    except Exception:
        pass
    if response is None:
        _fail(
            "an acknowledged immutable object write",
            "object write did not succeed",
            stage="storage_finalization",
        )
    # A completed response proves this synchronous PUT is terminal; timeouts do not.
    prove_local_termination(request)
    version = response.get("VersionId")
    if not version or version == "null":
        _fail(
            "an immutable acknowledged object version",
            "missing object version",
            stage="storage_finalization",
        )
    event("object_after_put")
    return version


def write_object_dataset(
    *,
    store: SessionStore,
    run_ref: str,
    session_ref: str,
    artifact_ref: str,
    source: DatasetWriteResult[LocalReceipt],
    access: S3Access,
    max_stored_bytes: int,
    event: Callable[[str], None],
) -> DatasetWriteResult[ObjectReceipt]:
    """Export private Parquet encoding, never publish a local intermediate Artifact."""
    prefix = object_artifact_prefix(session_ref, artifact_ref)
    nonce = artifact_ref.removeprefix("artifact_")
    payloads = (
        ("primary", source.primary_receipt),
        *((f"parts/{part.role}", part.storage_receipt) for part in source.retained_parts),
    )
    receipts: list[ObjectReceipt] = []
    total = 0
    with client(access) as s3:
        for role, receipt in payloads:
            if not isinstance(receipt, LocalReceipt) or len(receipt.file_manifest) != 1:
                _fail("one private Parquet payload per role", "unsupported object export")
            entry = receipt.file_manifest[0]
            key = prefix + role + "/data.parquet"
            data = _checked_path(
                store.project_root, Path(receipt.project_relative_path) / entry.relative_path
            )
            total += entry.size_bytes
            if total > max_stored_bytes:
                _fail(
                    "combined object payloads within storage budget",
                    "object storage budget exceeded",
                    stage="transfer_guard",
                )
            with data.open("rb") as stream:
                version = _put(
                    s3, access, store, run_ref, key, stream, entry.size_bytes, nonce, event
                )
            manifest = codec.canonical_json(
                {
                    "schema": "marivo.object_manifest/v1",
                    "key": key,
                    "version_id": version,
                    "size_bytes": entry.size_bytes,
                    "sha256": entry.sha256,
                }
            ).encode()
            total += len(manifest)
            if total > max_stored_bytes:
                _fail(
                    "combined objects and manifests within storage budget",
                    "object manifest budget exceeded",
                    stage="transfer_guard",
                )
            manifest_key = prefix + role + "/manifest.json"
            manifest_version = _put(
                s3, access, store, run_ref, manifest_key, manifest, len(manifest), nonce, event
            )
            result = ObjectReceipt(
                access.object_store_ref,
                manifest_key,
                manifest_version,
                hashlib.sha256(manifest).hexdigest(),
                receipt.schema_fingerprint,
                receipt.realized_row_count,
                entry.size_bytes + len(manifest),
            )
            # Final receipt verification uses the same exact-version reader as recovery.
            selected = open_manifest(s3, access, result)
            selected.verify(s3, access)
            receipts.append(result)
    return DatasetWriteResult(
        receipts[0],
        tuple(
            RetainedPart(part.role, part.contract_id, part.contract_version, receipt)
            for part, receipt in zip(source.retained_parts, receipts[1:], strict=True)
        ),
        source.realized_schema,
        source.realized_row_count,
    )


def _get(s3: S3Client, access: S3Access, key: str, version: str, start: int, length: int) -> bytes:
    if length == 0:
        return b""
    response = _call(
        lambda: s3.get_object(
            Bucket=access.bucket,
            Key=key,
            VersionId=version,
            Range=f"bytes={start}-{start + length - 1}",
        )
    )
    body = response["Body"]
    try:
        data = _call(lambda: body.read(length + 1))
    finally:
        body.close()
    if len(data) != length or response.get("VersionId") != version:
        _integrity(
            "the exact requested bounded object version range", "object range or version differs"
        )
    return data


@dataclass(frozen=True, slots=True, repr=False)
class ObjectFile:
    key: str
    version: str
    size: int
    sha256: str

    def verify(self, s3: S3Client, access: S3Access) -> None:
        head = _call(
            lambda: s3.head_object(Bucket=access.bucket, Key=self.key, VersionId=self.version)
        )
        if head.get("ContentLength") != self.size or head.get("VersionId") != self.version:
            _integrity("the exact committed object size and version", "object backing differs")
        digest = hashlib.sha256()
        for start in range(0, self.size, 1_048_576):
            digest.update(
                _get(s3, access, self.key, self.version, start, min(1_048_576, self.size - start))
            )
        if digest.hexdigest() != self.sha256:
            _integrity("the committed object content hash", "object content changed")


def open_manifest(s3: S3Client, access: S3Access, receipt: ObjectReceipt) -> ObjectFile:
    if receipt.object_store_ref != access.object_store_ref:
        _integrity("the receipt's exact current object binding", "foreign object binding")
    head = _call(
        lambda: s3.head_object(
            Bucket=access.bucket,
            Key=receipt.immutable_prefix_or_manifest_ref,
            VersionId=receipt.object_version_or_manifest_hash,
        )
    )
    size = head.get("ContentLength", 0)
    if not 0 < size <= 65_536:
        _integrity("a bounded committed object manifest", "invalid manifest size")
    data = _get(
        s3,
        access,
        receipt.immutable_prefix_or_manifest_ref,
        receipt.object_version_or_manifest_hash,
        0,
        size,
    )
    if hashlib.sha256(data).hexdigest() != receipt.manifest_hash:
        _integrity("the exact committed manifest hash", "object manifest changed")
    obj = codec._obj(codec.parse_json(data.decode()), "schema key version_id size_bytes sha256")
    if obj["schema"] != "marivo.object_manifest/v1":
        _integrity("the registered object manifest", "unsupported manifest version")
    key = codec._text(obj["key"])
    if (
        key
        != receipt.immutable_prefix_or_manifest_ref.removesuffix("manifest.json") + "data.parquet"
    ):
        _integrity("the manifest's exact sibling payload", "foreign object payload")
    result = ObjectFile(
        key,
        codec._text(obj["version_id"]),
        codec._int(obj["size_bytes"]),
        codec._text(obj["sha256"]),
    )
    codec._hash(result.sha256)
    if result.version == "null" or receipt.realized_byte_count != result.size + size:
        _integrity("versioned exact object bytes", "object version or size differs")
    return result


class ObjectRangeFile(io.RawIOBase):
    """Seekable PyArrow input with one bounded, exact-version range per read."""

    def __init__(self, s3: S3Client, access: S3Access, file: ObjectFile, max_read: int) -> None:
        self.s3, self.access, self.file, self.max_read = s3, access, file, max_read
        self.position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        position = offset + (self.position if whence == 1 else self.file.size if whence == 2 else 0)
        if whence not in (0, 1, 2) or not 0 <= position <= self.file.size:
            _integrity("an in-bounds object byte range", "invalid object seek")
        self.position = position
        return position

    def read(self, size: int = -1) -> bytes:
        length = (
            self.file.size - self.position
            if size < 0
            else min(size, self.file.size - self.position)
        )
        if length > self.max_read:
            _fail(
                "bounded Parquet object range reads",
                "object range exceeds batch budget",
                stage="transfer_guard",
            )
        data = _get(self.s3, self.access, self.file.key, self.file.version, self.position, length)
        self.position += length
        return data


def cleanup_object(store: SessionStore, resource: ResourceRecord, access: S3Access) -> bool:
    reference, key = decode_locator(resource.safe_locator)
    run = store.run(resource.run_ref)
    if (
        run is None
        or reference != access.object_store_ref
        or not key.startswith(
            object_artifact_prefix(run.session_ref, "artifact_" + resource.ownership_nonce)
        )
    ):
        _integrity("an exact Run-owned object key", "inconsistent object cleanup obligation")
    # List only this reserved key. Never delete another version without its nonce proof.
    with client(access) as s3:
        versions = _call(
            lambda: s3.list_object_versions(Bucket=access.bucket, Prefix=key, MaxKeys=65)
        )
        if versions.get("IsTruncated"):
            return False
        for item in versions.get("Versions", []):
            if item.get("Key") != key:
                continue
            version = item["VersionId"]
            head = _call(partial(s3.head_object, Bucket=access.bucket, Key=key, VersionId=version))
            if head.get("Metadata", {}).get("marivo-ownership") != resource.ownership_nonce:
                # A conditional write may collide with an independently owned
                # version. Only our nonce can identify garbage from this Run.
                continue
            _call(partial(s3.delete_object, Bucket=access.bucket, Key=key, VersionId=version))
    return True
