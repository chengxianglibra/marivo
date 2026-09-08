"""Metadata-only scan-leaf reconstruction and selected retained Evidence reads."""

from __future__ import annotations

from marivo.analysis.datasets.base import MaterializedDataset, _make_materialized_dataset
from marivo.analysis.datasets.descriptors import _exact_byte_count, _unavailable_byte_count
from marivo.analysis.datasets.state import _materialized_state
from marivo.analysis.materialization.contracts import ArtifactRecord
from marivo.analysis.observation.contracts import (
    ObservationActionPort,
    ObservationRuntimeOwner,
    make_family_registry,
    make_ids,
)
from marivo.analysis.refs import ArtifactRef


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
        session_id=session_ref,
        store_id=store_id,
        action_port=action_port,
        comparison_basis_snapshot=descriptor.comparison_basis,
    )
    state = _materialized_state(
        artifact_ref=ArtifactRef(ref=record.artifact_ref),
        artifact_session_ref=record.session_ref,
        content_authority_digest=descriptor.storage_receipt.identity_digest,
        storage_kind_id="parquet"
        if descriptor.storage_receipt.kind == "local"
        else descriptor.storage_receipt.kind,
        realized_schema=descriptor.realized_schema,
        realized_row_count=descriptor.storage_receipt.realized_row_count,
        realized_byte_count=(
            _exact_byte_count(descriptor.storage_receipt.realized_byte_count)
            if descriptor.storage_receipt.realized_byte_count is not None
            else _unavailable_byte_count("not_measured", ids=ids)
        ),
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
