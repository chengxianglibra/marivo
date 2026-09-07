"""Metadata-only scan-leaf reconstruction and selected retained Evidence reads."""

from __future__ import annotations

from marivo.analysis.datasets.base import MaterializedDataset, _make_materialized_dataset
from marivo.analysis.datasets.descriptors import _exact_byte_count
from marivo.analysis.datasets.state import _materialized_state
from marivo.analysis.evidence.artifact_reads import Finding, FindingPage
from marivo.analysis.evidence.types import (
    AnalysisScope,
    ArtifactDigest,
    OmissionSummary,
    OperatorSemantics,
    RawFallback,
    Subject,
)
from marivo.analysis.materialization.contracts import ArtifactRecord
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.observation.contracts import (
    ObservationActionPort,
    ObservationRuntimeOwner,
    make_family_registry,
    make_ids,
)
from marivo.analysis.refs import ArtifactRef
from marivo.refs import RefPayloadV1, SemanticKind


def recover_dataset(
    record: ArtifactRecord,
    *,
    session_ref: str,
    store_id: str,
    action_port: ObservationActionPort,
) -> MaterializedDataset:
    """Decode only committed metadata; a retained owner has no catalog or origin."""
    descriptor = record.descriptor
    ids = make_ids(())
    registry = make_family_registry(ids)
    owner = ObservationRuntimeOwner(
        session_id=session_ref, store_id=store_id, action_port=action_port
    )
    state = _materialized_state(
        artifact_ref=ArtifactRef(ref=record.artifact_ref),
        artifact_session_ref=record.session_ref,
        content_authority_digest=descriptor.storage_receipt.identity_digest,
        storage_kind_id="parquet",
        realized_schema=descriptor.realized_schema,
        realized_row_count=descriptor.storage_receipt.realized_row_count,
        realized_byte_count=_exact_byte_count(descriptor.storage_receipt.realized_byte_count),
        producing_run_ref=record.producing_run_ref,
        quality_authority_digest=record.evidence.quality_summary_digest,
        evidence_authority_digest=record.evidence.evidence_digest,
        ids=ids,
    )
    return _make_materialized_dataset(
        owner=owner,
        registry=registry,
        family_id=descriptor.row_contract.shape_id.family_id,
        row_contract=descriptor.row_contract,
        row_set_contract=descriptor.row_set_contract,
        state=state,
        definition_fingerprint=descriptor.definition_fingerprint,
    )


def evidence_digest(record: ArtifactRecord) -> ArtifactDigest:
    """Project the complete zero-Finding envelope without scanning rows or Findings."""
    descriptor = record.descriptor
    contract = descriptor.dataset_materialization_contract
    return ArtifactDigest(
        artifact_ref=record.artifact_ref,
        operator=OperatorSemantics(
            operator=contract.producer_id,
            operator_version=str(contract.producer_contract_version),
            artifact_family=descriptor.row_contract.shape_id.family_id,
            semantic_shape=descriptor.row_contract.shape_id.local_shape_id,
        ),
        subject=Subject(
            analysis_axis="quality",
            entity_ref=RefPayloadV1(
                schema="marivo.semantic_ref/v1",
                kind=SemanticKind.ENTITY,
                path=descriptor.population_authority.entity_ref,
            ),
        ),
        scope=AnalysisScope(),
        omissions=OmissionSummary(retained_items=0, omitted_items=0, bounded=False),
        quality=descriptor.quality_summary,
        fallback=RawFallback(
            artifact_ref=record.artifact_ref, findings_available=True, rows_available=True
        ),
        fingerprint=record.evidence.evidence_digest,
    )


def empty_findings(record: ArtifactRecord, *, limit: int, cursor: str | None) -> FindingPage:
    if type(limit) is not int or not 1 <= limit <= 100 or cursor is not None:
        raise MaterializationError(
            expected="a Finding page limit from 1 to 100 with no cursor for an empty set",
            received="invalid page selection",
            repair="Read this complete empty Finding set with findings(limit=20).",
            stage="presentation",
        )
    if record.evidence.finding_count != 0:
        raise IntegrityError(
            expected="the registered complete zero-Finding envelope",
            received="unexpected Finding count",
            repair="Inspect the selected Artifact metadata integrity.",
            stage="presentation",
        )
    return FindingPage(items=(), limit=limit, has_more=False, next_cursor=None)


def missing_finding(record: ArtifactRecord, finding_id: str) -> Finding:
    raise MaterializationError(
        expected="a Finding in this Artifact's committed Finding set",
        received="the registered Finding set is empty",
        repair="Read evidence_digest for the committed observation quality summary.",
        stage="presentation",
    )
