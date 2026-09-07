"""Private trusted factories for the two immutable Dataset state descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetByteCount,
    DatasetSchema,
    _Descriptor,
    _ExactByteCount,
    _fail,
    _positive,
    _registered,
    _ResolvedPhysicalType,
    _stable_text,
    _StableIdRegistry,
    _UnavailableByteCount,
    _validate_registered_schema,
    _validate_variant_kind,
)
from marivo.analysis.refs import ArtifactRef


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class LogicalDatasetState(_Descriptor, _token=_CORE_TOKEN):
    """Logical state records only the absence of committed materialized backing."""

    kind: Literal["logical"] = field(default="logical", init=False)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class MaterializedDatasetState(_Descriptor, _token=_CORE_TOKEN):
    """Exact immutable authority facts supplied by a trusted committed-state owner."""

    artifact_ref: ArtifactRef
    artifact_session_ref: str
    content_authority_digest: str
    storage_kind_id: str
    realized_schema: DatasetSchema
    realized_row_count: int
    realized_byte_count: DatasetByteCount
    producing_run_ref: str
    quality_authority_digest: str
    evidence_authority_digest: str
    kind: Literal["materialized"] = field(default="materialized", init=False)

    def __repr__(self) -> str:
        return f"<MaterializedDatasetState ref={str(self.artifact_ref)[:80]} rows={self.realized_row_count}>"


def _logical_state() -> LogicalDatasetState:
    return LogicalDatasetState(_token=_CORE_TOKEN)


def _materialized_state(
    *,
    artifact_ref: ArtifactRef,
    artifact_session_ref: str,
    content_authority_digest: str,
    storage_kind_id: str,
    realized_schema: DatasetSchema,
    realized_row_count: int,
    realized_byte_count: DatasetByteCount,
    producing_run_ref: str,
    quality_authority_digest: str,
    evidence_authority_digest: str,
    ids: _StableIdRegistry,
) -> MaterializedDatasetState:
    """Validate already trusted projections; this factory does not publish an Artifact."""
    if type(artifact_ref) is not ArtifactRef:
        _fail("an exact ArtifactRef", type(artifact_ref).__name__, "state.artifact_ref")
    _stable_text(str(artifact_ref), "state.artifact_ref")
    for label, value in (
        ("artifact_session_ref", artifact_session_ref),
        ("content_authority_digest", content_authority_digest),
        ("producing_run_ref", producing_run_ref),
        ("quality_authority_digest", quality_authority_digest),
        ("evidence_authority_digest", evidence_authority_digest),
    ):
        _stable_text(value, f"state.{label}")
    _registered(storage_kind_id, ids.storage_kinds, "state.storage_kind_id")
    _positive(realized_row_count, "state.realized_row_count", zero=True)
    if type(realized_schema) is not DatasetSchema:
        _fail("an exact DatasetSchema", type(realized_schema).__name__, "state.realized_schema")
    _validate_registered_schema(realized_schema, ids=ids)
    if any(
        type(column.physical_type_state) is not _ResolvedPhysicalType
        for column in realized_schema.columns
    ):
        _fail(
            "resolved physical types for every realized field",
            "deferred physical type",
            "state.realized_schema",
        )
    if type(realized_byte_count) not in (_ExactByteCount, _UnavailableByteCount):
        _fail(
            "an exact or typed-unavailable byte count",
            type(realized_byte_count).__name__,
            "state.realized_byte_count",
        )
    if isinstance(realized_byte_count, _ExactByteCount):
        _validate_variant_kind(realized_byte_count, "exact")
        _positive(realized_byte_count.byte_count, "state.realized_byte_count", zero=True)
    elif isinstance(realized_byte_count, _UnavailableByteCount):
        _validate_variant_kind(realized_byte_count, "unavailable")
        _registered(
            realized_byte_count.reason_id,
            ids.byte_unavailable_reasons,
            "state.realized_byte_count.reason_id",
        )
    return MaterializedDatasetState(
        _token=_CORE_TOKEN,
        artifact_ref=artifact_ref,
        artifact_session_ref=artifact_session_ref,
        content_authority_digest=content_authority_digest,
        storage_kind_id=storage_kind_id,
        realized_schema=realized_schema,
        realized_row_count=realized_row_count,
        realized_byte_count=realized_byte_count,
        producing_run_ref=producing_run_ref,
        quality_authority_digest=quality_authority_digest,
        evidence_authority_digest=evidence_authority_digest,
    )


def _validate_materialized_state(
    state: MaterializedDatasetState,
    *,
    ids: _StableIdRegistry,
) -> None:
    """Revalidate a trusted state at its family admission boundary."""
    if type(state) is not MaterializedDatasetState or state.kind != "materialized":
        _fail("the exact materialized state variant", "invalid state type or kind", "state")
    _materialized_state(
        artifact_ref=state.artifact_ref,
        artifact_session_ref=state.artifact_session_ref,
        content_authority_digest=state.content_authority_digest,
        storage_kind_id=state.storage_kind_id,
        realized_schema=state.realized_schema,
        realized_row_count=state.realized_row_count,
        realized_byte_count=state.realized_byte_count,
        producing_run_ref=state.producing_run_ref,
        quality_authority_digest=state.quality_authority_digest,
        evidence_authority_digest=state.evidence_authority_digest,
        ids=ids,
    )
