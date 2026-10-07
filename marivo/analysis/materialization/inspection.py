"""Read-only Store 7 Artifact inspection with explicit integrity axes."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from marivo._compat import UTC
from marivo.analysis.evidence._dataset_types import (
    ArtifactRevalidation,
    ArtifactRevalidationIssue,
    IntegrityStatus,
    StorageStatus,
)
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.materialization.errors import MaterializationError, StorageAccessError
from marivo.analysis.materialization.graph_findings import collection
from marivo.analysis.materialization.graph_protocol import (
    DESCRIPTOR,
    Descriptor,
    PartReceipt,
    PrimaryReceipt,
    decode,
    validate_metadata,
)
from marivo.analysis.materialization.graph_storage import read_result, read_table
from marivo.analysis.materialization.ownership import validate_receipt_owner
from marivo.analysis.materialization.store import SessionStore, _one, _text
from marivo.analysis.refs import ArtifactRef


def revalidate(store: SessionStore, reference: str | ArtifactRef) -> ArtifactRevalidation:
    """Check retained metadata, values and Evidence without source access or repair."""
    ref = reference if isinstance(reference, ArtifactRef) else ArtifactRef(ref=reference)
    artifact: IntegrityStatus = "unverifiable"
    evidence: IntegrityStatus = "unverifiable"
    storage: StorageStatus = "unknown"
    descriptor: Descriptor | None = None
    issues: list[ArtifactRevalidationIssue] = []
    with store._read() as connection:
        raw = _one(connection, "SELECT * FROM dataset_artifacts WHERE artifact_ref=?", (ref.ref,))
        if raw is None:
            from marivo.analysis.session._lazy_runtime_reads import missing_artifact

            raise missing_artifact(ref.ref)
        try:
            selected = decode(_text(raw, "descriptor_payload"), DESCRIPTOR)
            validate_metadata(selected)
            prefix = store.layout.artifact_dir(_text(raw, "session_ref"), ref.ref)
            local_prefix = prefix.relative_to(store.project_root).as_posix()
            selected_receipts: tuple[PrimaryReceipt | PartReceipt, ...] = (
                selected.primary_receipt,
                *selected.parts,
            )
            for receipt in selected_receipts:
                validate_receipt_owner(receipt.local, local_prefix)
            descriptor = selected
            if graph_store.artifact_metadata(store, connection, ref.ref) is None:
                raise invalid("selected Artifact disappeared")
            artifact = "valid"
        except (MaterializationError, ValueError):
            artifact = "invalid"
            issues.append(
                ArtifactRevalidationIssue(
                    axis="artifact_integrity",
                    kind="metadata_invalid",
                    safe_message="The selected graph Artifact or producer metadata is inconsistent.",
                )
            )
        if descriptor is not None:
            checks: list[StorageStatus] = []
            receipts: tuple[PrimaryReceipt | PartReceipt, ...] = (
                descriptor.primary_receipt,
                *descriptor.parts,
            )
            for receipt in receipts:
                status: StorageStatus = "readable"
                try:
                    read_table(store.project_root, receipt.local)
                except StorageAccessError as error:
                    status = error.storage_status
                except MaterializationError:
                    status = "mutated"
                except (OSError, ValueError):
                    status = "unknown"
                checks.append(status)
                if status != "readable":
                    role = "primary" if receipt.kind == "primary" else receipt.role
                    issues.append(
                        ArtifactRevalidationIssue(
                            axis="storage_authority",
                            kind="storage_" + status,
                            safe_message=f"Payload {role} is {status}.",
                        )
                    )
            storage = next(
                (
                    status
                    for status in ("mutated", "missing", "unauthorized", "unknown")
                    if status in checks
                ),
                "readable",
            )
            if storage == "readable":
                try:
                    read_result(store.project_root, descriptor)
                except MaterializationError:
                    storage = "mutated"
            if storage == "readable":
                try:
                    collection(store, connection, descriptor, ref.ref)
                    evidence = "valid"
                except MaterializationError:
                    evidence = "invalid"
                except sqlite3.Error:
                    evidence = "unverifiable"
    if storage != "readable" and not any(issue.axis == "storage_authority" for issue in issues):
        issues.append(
            ArtifactRevalidationIssue(
                axis="storage_authority",
                kind="storage_" + storage,
                safe_message="The selected primary or required retained parts are " + storage + ".",
            )
        )
    if evidence != "valid":
        issues.append(
            ArtifactRevalidationIssue(
                axis="evidence_integrity",
                kind="evidence_" + evidence,
                safe_message="Complete retained Evidence is " + evidence + ".",
            )
        )
    return ArtifactRevalidation(
        artifact_ref=ref,
        checked_at=datetime.now(UTC),
        artifact_integrity=artifact,
        storage_authority=storage,
        evidence_integrity=evidence,
        issues=tuple(issues),
    )
