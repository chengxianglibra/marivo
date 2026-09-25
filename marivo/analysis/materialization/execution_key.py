"""Versioned private execution keys for the existing and inactive DSL routes."""

from __future__ import annotations

import re

from marivo.analysis.compiler.normalize import artifact_inputs, require_unmixed_inputs
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.descriptors import _canonical_digest, _is_stable_identifier
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.contracts import ArtifactRecord, LocalReceipt
from marivo.analysis.materialization.errors import IntegrityError

_DEFINITION = re.compile(r"ds_[0-9a-f]{64}\Z")
_DSL_PROTOCOL = "marivo.dataset_execution_key/v2"


def _definition_root(dataset: LogicalDataset) -> LogicalRootHandle:
    if not isinstance(dataset, LogicalDataset):
        raise IntegrityError(
            expected="one normalized versioned DSL definition",
            received="invalid definition identity",
            repair="Reconstruct the logical Dataset through its owning method contract.",
            stage="admission",
        )
    root = dataset._root
    if (
        not isinstance(root, LogicalRootHandle)
        or _DEFINITION.fullmatch(dataset.definition_fingerprint) is None
    ):
        raise IntegrityError(
            expected="one normalized versioned DSL definition",
            received="invalid definition identity",
            repair="Reconstruct the logical Dataset through its owning method contract.",
            stage="admission",
        )
    return root


def _fixed_input_error() -> IntegrityError:
    return IntegrityError(
        expected="the exact selected fixed Artifact, producing Run and complete receipts",
        received="fixed input binding mismatch",
        repair="Select and inspect the exact input Artifacts before retrying this definition.",
        stage="admission",
    )


def execution_key(definition_fingerprint: str) -> str:
    """Bind a definition to common materialization protocol v1, within one Session."""
    return _canonical_digest(("marivo.dataset_execution_key/v1", definition_fingerprint, 1))


def source_execution_key(dataset: LogicalDataset, run_ref: str) -> str:
    """Bind one admitted source evaluation to its already allocated Run ref."""
    root = _definition_root(dataset)
    classification = require_unmixed_inputs(root)
    if classification.kind != "source" or not _is_stable_identifier(run_ref):
        raise IntegrityError(
            expected="a source-only DSL definition and an allocated stable Run ref",
            received="invalid source evaluation identity",
            repair="Classify the graph and allocate one Run ref at the guarded admission boundary.",
            stage="admission",
        )
    return _canonical_digest((_DSL_PROTOCOL, "source", root.definition_fingerprint, run_ref))


def fixed_execution_key(dataset: LogicalDataset, records: tuple[ArtifactRecord, ...]) -> str:
    """Bind a fixed-only definition to selected immutable receipt occurrences."""
    root = _definition_root(dataset)
    classification = require_unmixed_inputs(root)
    if classification.kind != "artifact":
        raise _fixed_input_error()
    retained = artifact_inputs(dataset)
    if type(records) is not tuple or len(records) != len(retained):
        raise _fixed_input_error()

    bindings: list[
        tuple[str, str, str, str, tuple[tuple[str, str, int, str], ...]]
        | tuple[str, str, str, str, tuple[tuple[str, str, int, str], ...], str]
    ] = []
    for value, record in zip(retained, records, strict=True):
        if not isinstance(record, ArtifactRecord):
            raise _fixed_input_error()
        descriptor = record.descriptor
        receipt = descriptor.storage_receipt
        state = value.state
        if (
            value._owner.store_id != dataset._owner.store_id
            or record.artifact_ref != state.artifact_ref.ref
            or record.session_ref != state.artifact_session_ref
            or record.producing_run_ref != state.producing_run_ref
            or record.evidence.evidence_digest != state.evidence_authority_digest
            or record.evidence.quality_summary_digest != state.quality_authority_digest
            or descriptor.definition_fingerprint != value.definition_fingerprint
            or descriptor.row_contract_fingerprint != value._root.row_contract_fingerprint
            or descriptor.row_set_contract_fingerprint != value._root.row_set_contract_fingerprint
            or not isinstance(receipt, LocalReceipt)
            or receipt.identity_digest != state.content_authority_digest
        ):
            raise _fixed_input_error()
        parts = descriptor.retained_parts
        if type(parts) is not tuple or any(
            not isinstance(part.storage_receipt, LocalReceipt) for part in parts
        ):
            raise _fixed_input_error()
        binding = (
            record.session_ref,
            record.artifact_ref,
            record.producing_run_ref,
            receipt.identity_digest,
            tuple(
                (
                    part.role,
                    part.contract_id,
                    part.contract_version,
                    part.storage_receipt.identity_digest,
                )
                for part in parts
            ),
        )
        member_binding = (
            descriptor.j1_exchange.member_binding if descriptor.j1_exchange is not None else None
        )
        bindings.append((*binding, member_binding) if member_binding is not None else binding)
    return _canonical_digest((_DSL_PROTOCOL, "fixed", root.definition_fingerprint, tuple(bindings)))
