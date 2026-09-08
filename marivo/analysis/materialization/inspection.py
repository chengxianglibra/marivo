"""Explicit read-only Artifact inspection with independent integrity axes."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import pyarrow as pa

from marivo._compat import UTC
from marivo.analysis.evidence._dataset_types import (
    ArtifactRevalidation,
    ArtifactRevalidationIssue,
    IntegrityStatus,
    StorageStatus,
)
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization import storage
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    RetainedPart,
)
from marivo.analysis.materialization.errors import (
    CollectionLimitError,
    IntegrityError,
    MaterializationError,
    StorageAccessError,
)
from marivo.analysis.materialization.ownership import object_artifact_prefix, validate_receipt_owner
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.retained import checked_component_batches
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.store import SessionStore, _one, _text
from marivo.analysis.materialization.targets import S3Access, access_payload, decode_access
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session._lazy_runtime_reads import missing_artifact

_POLICY = ReadPolicy()
_WORKER = "from marivo.analysis.materialization.inspection import _worker; _worker()"
_STORAGE_FAILURE_PRIORITY: tuple[StorageStatus, ...] = (
    "mutated",
    "missing",
    "unauthorized",
    "unknown",
)


class _InspectionDeadlineError(Exception):
    """A check could not complete within the one inspection deadline."""


@dataclass(frozen=True, slots=True)
class _StorageCheck:
    role: str
    status: StorageStatus


def _payload_check(
    root: Path,
    descriptor: ArtifactDescriptor,
    part: RetainedPart | None,
    bindings: tuple[S3Access, ...],
    policy: ReadPolicy,
) -> None:
    receipt = descriptor.storage_receipt if part is None else part.storage_receipt
    row, rows = descriptor.row_contract, descriptor.row_set_contract
    validator = (
        storage._RowValidator(row, rows, source_key_validation=True) if part is None else None
    )
    stream = payload_batches(
        root,
        receipt,
        policy=policy,
        bindings=bindings,
        row=row if part is None else None,
        rows=rows if part is None else None,
        audit=True,
    )
    seen = False
    sampling_rows = 0
    try:
        for batch in stream:
            seen = True
            if validator is not None:
                realized = storage._realized_schema(row.schema, batch.schema)
                if codec.schema_fingerprint(realized) != receipt.schema_fingerprint:
                    raise StorageAccessError("mutated")
                validator.accept(batch)
            else:
                if part is not None and part.contract_id == "metric.sufficient_components":
                    if (
                        hashlib.sha256(batch.schema.serialize().to_pybytes()).hexdigest()
                        != receipt.schema_fingerprint
                    ):
                        raise StorageAccessError("mutated")
                    for _ in checked_component_batches((batch,), row, part.role):
                        pass
                elif part is not None and part.role == "population_sampling_state":
                    expected_schema = pa.schema(
                        [pa.field("sampling_execution_digest", pa.string(), nullable=False)]
                    )
                    if (
                        hashlib.sha256(expected_schema.serialize().to_pybytes()).hexdigest()
                        != receipt.schema_fingerprint
                    ):
                        raise StorageAccessError("mutated")
                    if batch.schema.names != [
                        "sampling_execution_digest"
                    ] or not pa.types.is_string(batch.schema.field(0).type):
                        raise StorageAccessError("mutated")
                    expected = codec.digest(
                        codec.sampling_payload(descriptor.sampling_execution or ())
                    )
                    for value in batch.column(0):
                        if value.as_py() != expected:
                            raise StorageAccessError("mutated")
                        sampling_rows += 1
                else:
                    raise StorageAccessError("unknown")
        if not seen or (
            part is not None and part.role == "population_sampling_state" and sampling_rows != 1
        ):
            raise StorageAccessError("mutated")
        if validator is not None:
            validator.finish()
    finally:
        stream.close()


def _worker() -> None:
    """Stream one safe receipt outcome at a time; never emit native diagnostics."""
    request = codec._obj(
        codec.parse_json(sys.stdin.read()), "project_root descriptor bindings deadline"
    )
    descriptor_payload = request["descriptor"]
    if not isinstance(descriptor_payload, str):
        raise codec.invalid("invalid inspection descriptor")
    descriptor = codec.decode_descriptor(descriptor_payload)
    root = Path(codec._text(request["project_root"]))
    bindings = tuple(decode_access(value) for value in codec._array(request["bindings"]))
    deadline = request["deadline"]
    if not isinstance(deadline, (float, int)) or isinstance(deadline, bool) or deadline <= 0:
        raise codec.invalid("invalid inspection deadline")
    started = time.monotonic()
    for part in (None, *descriptor.retained_parts):
        role = "primary" if part is None else part.role
        status: StorageStatus = "readable"
        remaining = float(deadline) - (time.monotonic() - started)
        if remaining <= 0:
            status = "unknown"
        else:
            try:
                _payload_check(
                    root, descriptor, part, bindings, replace(_POLICY, deadline_seconds=remaining)
                )
            except StorageAccessError as error:
                status = error.storage_status
            except CollectionLimitError:
                status = "unknown"
            except MaterializationError:
                status = "mutated"
            except Exception:
                status = "unknown"
        print(codec.canonical_json({"role": role, "status": status}), flush=True)


def _storage_checks(
    project_root: Path,
    descriptor: ArtifactDescriptor,
    bindings: tuple[S3Access, ...],
    *,
    policy: ReadPolicy = _POLICY,
) -> tuple[_StorageCheck, ...]:
    roles = ("primary", *(part.role for part in descriptor.retained_parts))
    request = codec.canonical_json(
        {
            "project_root": str(project_root),
            "descriptor": codec.encode_descriptor(descriptor),
            "bindings": [access_payload(value) for value in bindings],
            "deadline": policy.deadline_seconds,
        }
    )
    output = ""
    try:
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", _WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env={**os.environ, "MARIVO_TELEMETRY": "off"},
        )
        try:
            output, _ = process.communicate(request, timeout=policy.deadline_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
        except BaseException:
            process.kill()
            process.communicate()
            raise
    except OSError:
        pass
    checks: list[_StorageCheck] = []
    for line in output.splitlines():
        try:
            value = codec._obj(codec.parse_json(line), "role status")
            status = value["status"]
            if len(checks) >= len(roles) or value["role"] != roles[len(checks)]:
                break
            if status == "readable":
                checks.append(_StorageCheck(roles[len(checks)], "readable"))
            elif status == "unauthorized":
                checks.append(_StorageCheck(roles[len(checks)], "unauthorized"))
            elif status == "missing":
                checks.append(_StorageCheck(roles[len(checks)], "missing"))
            elif status == "mutated":
                checks.append(_StorageCheck(roles[len(checks)], "mutated"))
            elif status == "unknown":
                checks.append(_StorageCheck(roles[len(checks)], "unknown"))
            else:
                break
        except MaterializationError:
            break
    checks.extend(_StorageCheck(role, "unknown") for role in roles[len(checks) :])
    return tuple(checks)


def revalidate(
    store: SessionStore,
    reference: str | ArtifactRef,
    *,
    bindings: tuple[S3Access, ...] = (),
    policy: ReadPolicy = _POLICY,
) -> ArtifactRevalidation:
    """Inspect one immutable authority without loading its origin or repairing state."""
    from marivo.analysis.evidence._dataset_reads import audit_findings

    ref = reference if isinstance(reference, ArtifactRef) else ArtifactRef(ref=reference)
    checked_at = datetime.now(UTC)
    deadline = time.monotonic() + policy.deadline_seconds

    def check_deadline() -> None:
        if time.monotonic() >= deadline:
            raise _InspectionDeadlineError

    artifact: IntegrityStatus = "unverifiable"
    evidence: IntegrityStatus = "unverifiable"
    issues: list[ArtifactRevalidationIssue] = []
    descriptor: ArtifactDescriptor | None = None
    storage_descriptor: ArtifactDescriptor | None = None
    try:
        with store._read() as conn:
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            check_deadline()
            raw = _one(conn, "SELECT * FROM dataset_artifacts WHERE artifact_ref=?", (ref.ref,))
            if raw is None:
                raise missing_artifact(ref.ref)
            try:
                descriptor = codec.decode_descriptor(_text(raw, "descriptor_payload"))
                check_deadline()
                # Owner admission is independent of producer/Evidence validity and
                # must precede any external access even when another check fails.
                prefix = (
                    store.layout.artifact_dir(_text(raw, "session_ref"), ref.ref)
                    .relative_to(store.project_root)
                    .as_posix()
                )
                for receipt in (
                    descriptor.storage_receipt,
                    *(part.storage_receipt for part in descriptor.retained_parts),
                ):
                    validate_receipt_owner(
                        receipt, prefix, object_artifact_prefix(_text(raw, "session_ref"), ref.ref)
                    )
                storage_descriptor = descriptor
                metadata = store._artifact_metadata(conn, ref.ref)
                if metadata is None:
                    raise codec.invalid("selected Artifact disappeared within its snapshot")
                artifact = "valid"
            except (IntegrityError, ValueError):
                artifact = "invalid"
                issues.append(
                    ArtifactRevalidationIssue(
                        axis="artifact_integrity",
                        kind="metadata_invalid",
                        safe_message="Selected Artifact metadata or its producer contract is inconsistent.",
                    )
                )
            if descriptor is not None:
                # Evidence remains independently checkable when a producer edge is corrupt.
                try:
                    check_deadline()
                    metadata = codec.ArtifactMetadata(
                        ref.ref,
                        _text(raw, "session_ref"),
                        _text(raw, "execution_key_digest"),
                        descriptor,
                        _text(raw, "committed_at"),
                        "unavailable",
                    )
                    envelope = store._artifact_evidence(conn, metadata)
                    record = ArtifactRecord(
                        metadata.artifact_ref,
                        metadata.session_ref,
                        metadata.execution_key_digest,
                        descriptor,
                        metadata.committed_at,
                        metadata.producing_run_ref,
                        envelope,
                    )
                    audit_findings(conn, record, check=check_deadline)
                    check_deadline()
                    evidence = "valid"
                except MaterializationError:
                    evidence = "invalid"
                    issues.append(
                        ArtifactRevalidationIssue(
                            axis="evidence_integrity",
                            kind="evidence_invalid",
                            safe_message="The selected Evidence envelope or complete Finding set is inconsistent.",
                        )
                    )
    except (sqlite3.Error, _InspectionDeadlineError):
        if artifact == "unverifiable":
            issues.append(
                ArtifactRevalidationIssue(
                    axis="artifact_integrity",
                    kind="metadata_unavailable",
                    safe_message="The selected Store snapshot is unavailable; integrity could not be established.",
                )
            )
    remaining = deadline - time.monotonic()
    checks: tuple[_StorageCheck, ...] = ()
    if storage_descriptor is not None:
        if remaining > 0:
            checks = _storage_checks(
                store.project_root,
                storage_descriptor,
                bindings,
                policy=replace(policy, deadline_seconds=remaining),
            )
        else:
            checks = tuple(
                _StorageCheck(role, "unknown")
                for role in ("primary", *(part.role for part in storage_descriptor.retained_parts))
            )
    storage_status: StorageStatus = "unknown" if not checks else "readable"
    for status in _STORAGE_FAILURE_PRIORITY:
        if any(check.status == status for check in checks):
            storage_status = status
            break
    for check in checks:
        if check.status != "readable":
            issues.append(
                ArtifactRevalidationIssue(
                    axis="storage_authority",
                    kind=f"storage_{check.status}",
                    safe_message=f"Payload {check.role} is {check.status}.",
                )
            )
    if storage_descriptor is None:
        issues.append(
            ArtifactRevalidationIssue(
                axis="storage_authority",
                kind="storage_unverifiable",
                safe_message="Storage checks require a readable declared receipt contract.",
            )
        )
    if evidence == "unverifiable":
        issues.append(
            ArtifactRevalidationIssue(
                axis="evidence_integrity",
                kind="evidence_unverifiable",
                safe_message="Evidence checks require readable metadata and a complete selected Store snapshot.",
            )
        )
    return ArtifactRevalidation(
        artifact_ref=ref,
        checked_at=checked_at,
        artifact_integrity=artifact,
        storage_authority=storage_status,
        evidence_integrity=evidence,
        issues=tuple(issues),
    )
