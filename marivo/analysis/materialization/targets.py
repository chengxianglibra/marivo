"""Private fixed storage targets and current, non-persisted object access bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from marivo.analysis.compiler.placement import EngineBinding, ExecutionBinding
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import StoragePolicy

_LOCAL_STORAGE_POLICY = StoragePolicy()
_EXTERNAL_POLICY = StoragePolicy(max_stored_bytes=134_217_728)


@dataclass(frozen=True, slots=True)
class LocalTarget:
    policy: StoragePolicy = _LOCAL_STORAGE_POLICY


@dataclass(frozen=True, slots=True, repr=False)
class EngineTarget:
    datasource_ref: str
    policy: StoragePolicy = _EXTERNAL_POLICY


@dataclass(frozen=True, slots=True, repr=False)
class ObjectTarget:
    object_store_ref: str
    policy: StoragePolicy = _EXTERNAL_POLICY


MaterializationTarget: TypeAlias = LocalTarget | EngineTarget | ObjectTarget


@dataclass(frozen=True, slots=True, repr=False)
class S3Access:
    """Exact access supplied by the host; no environment or SDK credential fallback."""

    object_store_ref: str
    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str = "us-east-1"
    session_token: str | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.object_store_ref,
                self.endpoint_url,
                self.bucket,
                self.access_key_id,
                self.secret_access_key,
                self.region,
            )
        ):
            selection_error("one complete explicit object access binding", "missing access fields")


def selection_error(expected: str, received: str) -> None:
    raise MaterializationError(
        expected=expected,
        received=received,
        repair="Configure one supported storage target and its exact current access binding, then retry.",
        stage="storage_selection",
    )


def object_access(bindings: tuple[S3Access, ...], reference: str) -> S3Access:
    matches = tuple(value for value in bindings if value.object_store_ref == reference)
    if len(matches) != 1:
        selection_error("one exact current object binding", "missing or ambiguous object binding")
    return matches[0]


def engine_domain(binding: ExecutionBinding) -> str:
    """Persist a declaration digest, never credentials or a Python owning object."""
    if isinstance(binding, EngineBinding):
        return binding.domain_digest
    source = binding.owner.semantic_registry.datasources[binding.datasource_id]
    return codec.digest(
        (binding.datasource_id, source.backend_type, source.fields, source.env_refs)
    )


def access_payload(value: S3Access) -> dict[str, object]:
    """Private worker IPC only; this value must never enter Store or diagnostics."""
    return {
        "object_store_ref": value.object_store_ref,
        "endpoint_url": value.endpoint_url,
        "bucket": value.bucket,
        "access_key_id": value.access_key_id,
        "secret_access_key": value.secret_access_key,
        "region": value.region,
        "session_token": value.session_token,
    }


def decode_access(value: object) -> S3Access:
    obj = codec._obj(
        value,
        "object_store_ref endpoint_url bucket access_key_id secret_access_key region session_token",
    )
    return S3Access(
        codec._text(obj["object_store_ref"]),
        codec._text(obj["endpoint_url"]),
        codec._text(obj["bucket"]),
        codec._text(obj["access_key_id"]),
        codec._text(obj["secret_access_key"]),
        codec._text(obj["region"]),
        None if obj["session_token"] is None else codec._text(obj["session_token"]),
    )
