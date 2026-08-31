"""Canonical identity and evidence-ledger reads for Artifact revalidation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from marivo.analysis.errors import (
    AnalysisRepair,
    CrossSessionFrameError,
    EvidenceIntegrityError,
    EvidenceStoreUnavailableError,
    FrameCacheCorruptedError,
    SchemaVersionMismatchError,
)
from marivo.analysis.evidence.audit import _row_to_finding
from marivo.analysis.evidence.identity import (
    canonical_json,
    canonical_subject_key,
    make_finding_id,
)
from marivo.analysis.evidence.store import EXPECTED_SCHEMA_VERSION, EvidenceStore
from marivo.analysis.evidence.types import (
    AnalysisScope,
    ArtifactDigest,
    ArtifactIssue,
    ArtifactIssueAdapter,
    EvidenceScope,
    EvidenceScopeAdapter,
    EvidenceStatus,
    EvidenceSubject,
    EvidenceSubjectAdapter,
    Finding,
    QualitySummary,
    Subject,
)
from marivo.analysis.frames._content_hash import (
    compute_file_content_hash,
    compute_frame_content_hash,
)
from marivo.analysis.frames.base import BaseFrame
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    CatalogMetricSubjectV1,
    RuntimeExpressionIdentity,
    RuntimeExpressionSubjectV1,
)

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session


@dataclass(frozen=True, slots=True)
class CanonicalArtifactEvidence:
    """One fully cross-checked Artifact projection from committed stores."""

    frame: BaseFrame
    subject: EvidenceSubject
    scope: EvidenceScope
    quality: QualitySummary | None
    evidence_status: EvidenceStatus
    digest: ArtifactDigest | None
    findings: tuple[Finding, ...]
    issues: tuple[ArtifactIssue, ...]
    operator: str


def _repair(action: str, *, snippet: str | None = None) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="session.revalidate"),
        snippet=snippet,
    )


def _integrity_error(
    *,
    artifact_ref: str,
    expected: str,
    received: str,
    cause: Exception | None = None,
) -> EvidenceIntegrityError:
    error = EvidenceIntegrityError(
        message=f"committed evidence for artifact {artifact_ref!r} failed integrity validation",
        expected=expected,
        received=received,
        location="session.revalidate",
        repair=_repair(
            "Re-run the producing operator in a fresh analysis session when the exact Artifact "
            "and its evidence ledger can no longer be restored together.",
            snippet=f"session.artifact({artifact_ref!r})",
        ),
        context={"artifact_ref": artifact_ref},
    )
    if cause is not None:
        error.__cause__ = cause
    return error


def _store_read_error(*, store: EvidenceStore, cause: Exception) -> EvidenceStoreUnavailableError:
    return EvidenceStoreUnavailableError(
        message=f"evidence store cannot be read at {store.db_path}",
        expected="a readable current evidence store",
        received=type(cause).__name__,
        location="session.revalidate",
        repair=_repair("Restore the current Session evidence store, then retry revalidation."),
        context={"db_path": str(store.db_path)},
    )


def finding_subject_matches_artifact(
    *,
    finding: Finding,
    artifact_subject: EvidenceSubject,
    scope: EvidenceScope,
) -> bool:
    if finding.subject == artifact_subject:
        return True
    if not isinstance(finding.subject, Subject) or not isinstance(artifact_subject, Subject):
        return False
    if not isinstance(scope, AnalysisScope) or artifact_subject.typed_metric_subject is not None:
        return False
    if finding.subject.model_copy(update={"typed_metric_subject": None}) != artifact_subject:
        return False
    typed = finding.subject.typed_metric_subject
    if isinstance(typed, CatalogMetricSubjectV1):
        identity_present = any(
            isinstance(identity, CatalogMetricIdentity) and identity.metric_ref == typed.metric_ref
            for identity in scope.metric_identities
        )
    elif isinstance(typed, RuntimeExpressionSubjectV1):
        identity_present = any(
            isinstance(identity, RuntimeExpressionIdentity)
            and identity.expression_fingerprint == typed.expression_fingerprint
            for identity in scope.metric_identities
        )
    else:
        return False
    from marivo.analysis.evidence.identity import make_scope_fingerprint

    return (
        typed.session_id == finding.session_id
        and typed.artifact_id == finding.artifact_id
        and typed.scope_fingerprint == make_scope_fingerprint(scope)
        and identity_present
    )


def load_canonical_frame_identity(
    *,
    session: Session,
    frame: BaseFrame,
) -> tuple[BaseFrame, sqlite3.Row]:
    """Recover and verify one Session-registered Frame content identity."""
    if frame.meta.session_id != session.id:
        raise CrossSessionFrameError(
            message=(
                f"revalidate frame belongs to session {frame.meta.session_id!r}, not {session.id!r}"
            )
        )
    artifact_ref = frame.meta.artifact_id or frame.meta.ref
    canonical_frame = session.artifact(artifact_ref)
    session_row = session._store.get_artifact(session.id, artifact_ref)
    if session_row is None:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="one canonical Session Store Artifact registration",
            received="missing Session Store row",
        )
    canonical_data_path = session.project_root / session_row["path"]
    expected_content_hash = compute_frame_content_hash(
        meta=canonical_frame.meta,
        data_path=canonical_data_path,
    )
    if (
        canonical_frame.meta.content_hash != expected_content_hash
        or session_row["content_hash"] != expected_content_hash
    ):
        raise FrameCacheCorruptedError(
            message=f"frame '{artifact_ref}' content identity is corrupt",
            context={
                "ref": artifact_ref,
                "cause": "artifact content hash mismatch",
                "expected_content_hash": expected_content_hash,
                "sidecar_content_hash": canonical_frame.meta.content_hash,
                "store_content_hash": session_row["content_hash"],
            },
        )
    if frame.meta.content_hash != canonical_frame.meta.content_hash or (
        frame.meta.artifact_id or frame.meta.ref
    ) != (canonical_frame.meta.artifact_id or canonical_frame.meta.ref):
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="the supplied Frame identity to match canonical persisted identity",
            received=(
                f"supplied={frame.meta.content_hash!r}, "
                f"canonical={canonical_frame.meta.content_hash!r}"
            ),
        )
    return canonical_frame, session_row


def _load_linked_unavailable_evidence(
    *,
    session: Session,
    store: EvidenceStore,
    frame: BaseFrame,
) -> CanonicalArtifactEvidence | None:
    """Project a healthy unavailable linked sidecar through its canonical parent."""
    if frame.meta.kind not in {"component_frame", "coverage_frame"}:
        return None
    artifact_ref = frame.meta.artifact_id or frame.meta.ref
    parent_ref = getattr(frame.meta, "parent_ref", None)
    if not isinstance(parent_ref, str) or not parent_ref:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="one exact parent Artifact ref for a linked sidecar",
            received=repr(parent_ref),
        )
    if parent_ref == artifact_ref:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="acyclic linked sidecar parent identity",
            received="self-referential parent Artifact",
        )
    if (
        frame.evidence_status != "unavailable"
        or frame.evidence_digest is not None
        or frame.meta.analysis_scope is not None
        or frame.meta.quality_summary is not None
        or frame.meta.issues
    ):
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="a ledger-free linked sidecar to retain a healthy unavailable evidence state",
            received=(
                f"status={frame.evidence_status!r}, "
                f"digest={frame.evidence_digest is not None}, "
                f"scope={frame.meta.analysis_scope is not None}, "
                f"quality={frame.meta.quality_summary is not None}, "
                f"issues={len(frame.meta.issues)}"
            ),
        )
    try:
        parent = session.artifact(parent_ref)
        parent_evidence = load_canonical_artifact_evidence(
            session=session,
            store=store,
            frame=parent,
        )
    except Exception as exc:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="an intact canonical parent for the linked sidecar",
            received=type(exc).__name__,
            cause=exc,
        ) from exc
    return CanonicalArtifactEvidence(
        frame=frame,
        subject=parent_evidence.subject,
        scope=parent_evidence.scope,
        quality=None,
        evidence_status="unavailable",
        digest=None,
        findings=(),
        issues=(),
        operator=frame.meta.kind,
    )


def load_canonical_artifact_evidence(
    *,
    session: Session,
    store: EvidenceStore,
    frame: BaseFrame,
    _recovery_data_path: Path | None = None,
    _recovery_content_hash: str | None = None,
) -> CanonicalArtifactEvidence:
    """Load and cross-check one committed Artifact without mutating it."""
    if _recovery_data_path is None:
        canonical_frame, session_row = load_canonical_frame_identity(session=session, frame=frame)
        canonical_path = session.project_root / session_row["path"]
    else:
        if frame.meta.session_id != session.id:
            raise CrossSessionFrameError(
                message=(
                    f"revalidate frame belongs to session {frame.meta.session_id!r}, "
                    f"not {session.id!r}"
                )
            )
        canonical_frame = frame
        canonical_path = _recovery_data_path
        expected_content_hash = compute_frame_content_hash(
            meta=canonical_frame.meta,
            data_path=canonical_path,
        )
        if (
            canonical_frame.meta.content_hash != expected_content_hash
            or _recovery_content_hash != expected_content_hash
        ):
            raise FrameCacheCorruptedError(
                message=f"frame '{canonical_frame.ref}' content identity is corrupt",
                context={
                    "ref": canonical_frame.ref,
                    "cause": "artifact content hash mismatch",
                    "expected_content_hash": expected_content_hash,
                    "sidecar_content_hash": canonical_frame.meta.content_hash,
                    "recovery_content_hash": _recovery_content_hash,
                },
            )
    artifact_ref = canonical_frame.meta.artifact_id or canonical_frame.meta.ref

    conn = store.read()
    try:
        store_schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        artifact_row = conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?",
            (artifact_ref,),
        ).fetchone()
        digest_row = conn.execute(
            "SELECT * FROM artifact_digests WHERE artifact_id = ?",
            (artifact_ref,),
        ).fetchone()
        finding_rows = list(
            conn.execute(
                "SELECT * FROM findings WHERE artifact_id = ? ORDER BY finding_id",
                (artifact_ref,),
            )
        )
        issue_rows = list(
            conn.execute(
                "SELECT * FROM artifact_issues WHERE artifact_id = ? ORDER BY issue_id",
                (artifact_ref,),
            )
        )
    except sqlite3.Error as exc:
        raise _store_read_error(store=store, cause=exc) from exc
    if store_schema_version != EXPECTED_SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            message=(
                f"judgment.db schema version {store_schema_version} is unsupported; "
                f"this release requires a fresh v{EXPECTED_SCHEMA_VERSION} evidence store"
            ),
            hint="Remove the incompatible analysis session and run the analysis again.",
            context={"got": store_schema_version, "expected": EXPECTED_SCHEMA_VERSION},
        )
    if artifact_row is None:
        linked = _load_linked_unavailable_evidence(
            session=session,
            store=store,
            frame=canonical_frame,
        )
        if linked is not None:
            return linked
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="one committed evidence Artifact record",
            received="missing Artifact record",
        )
    if artifact_row["session_id"] != session.id:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected=f"evidence Artifact ownership by session {session.id!r}",
            received=f"session={artifact_row['session_id']!r}",
        )

    try:
        subject = EvidenceSubjectAdapter.validate_json(artifact_row["subject_payload"])
        scope = EvidenceScopeAdapter.validate_json(artifact_row["analysis_scope"])
        quality = (
            QualitySummary.model_validate_json(artifact_row["quality_summary"])
            if artifact_row["quality_summary"]
            else None
        )
        evidence_status = cast("EvidenceStatus", artifact_row["evidence_status"])
        if evidence_status not in {"complete", "partial", "unavailable"}:
            raise ValueError(f"unknown evidence status {evidence_status!r}")
        ledger_lineage = json.loads(artifact_row["lineage_payload"])
    except Exception as exc:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="readable current Artifact evidence identity fields",
            received=type(exc).__name__,
            cause=exc,
        ) from exc

    expected_evidence_artifact_schema = f"v{EXPECTED_SCHEMA_VERSION}"
    if artifact_row["artifact_schema_version"] != expected_evidence_artifact_schema:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected=(
                "the current evidence Artifact schema version "
                f"{expected_evidence_artifact_schema!r}"
            ),
            received=(f"ledger={artifact_row['artifact_schema_version']!r}"),
        )
    if ledger_lineage != json.loads(canonical_json(canonical_frame.meta.lineage)):
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="matching sidecar and ledger lineage",
            received="lineage payload mismatch",
        )

    digest: ArtifactDigest | None = None
    if digest_row is not None:
        if digest_row["session_id"] != session.id:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected=f"digest ownership by session {session.id!r}",
                received=f"session={digest_row['session_id']!r}",
            )
        try:
            digest = ArtifactDigest.model_validate_json(digest_row["digest_payload"])
        except Exception as exc:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="a readable current ArtifactDigest payload",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
        if digest.fingerprint != digest_row["fingerprint"]:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching digest payload and ledger fingerprints",
                received=(f"payload={digest.fingerprint!r}, ledger={digest_row['fingerprint']!r}"),
            )
        digest_mismatches: list[str] = []
        if digest.artifact_ref != artifact_ref:
            digest_mismatches.append("artifact_ref")
        if digest.scope != scope:
            digest_mismatches.append("scope")
        if digest.subject != subject:
            digest_mismatches.append("subject")
        if digest_row["operator"] != digest.operator.operator:
            digest_mismatches.append("operator")
        if digest_row["subject_key"] != canonical_subject_key(digest.subject):
            digest_mismatches.append("subject_key")
        if digest_mismatches:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching digest payload and canonical Artifact identities",
                received=f"mismatched={tuple(digest_mismatches)!r}",
            )
    if evidence_status == "complete" and digest is None:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="a persisted digest for complete evidence",
            received="missing digest",
        )

    findings: list[Finding] = []
    for row in finding_rows:
        try:
            finding = _row_to_finding(row)
        except Exception as exc:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="readable canonical Finding payloads",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
        if (
            row["session_id"] != session.id
            or row["artifact_id"] != artifact_ref
            or finding.session_id != session.id
            or finding.artifact_id != artifact_ref
            or finding.artifact_schema_version != artifact_row["artifact_schema_version"]
            or not finding_subject_matches_artifact(
                finding=finding,
                artifact_subject=subject,
                scope=scope,
            )
        ):
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="every Finding to match Artifact/session/schema/subject ownership",
                received=f"finding={finding.finding_id!r}",
            )
        expected_finding_id = make_finding_id(
            artifact_ref,
            finding.finding_type,
            finding.canonical_item_key,
        )
        finding_mirrors_match = (
            row["finding_id"] == expected_finding_id
            and row["subject_axis"] == finding.subject.analysis_axis
            and row["value_kind"] == finding.value.kind
        )
        if not finding_mirrors_match:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching Finding identities and canonical ledger mirror columns",
                received=f"finding={finding.finding_id!r}",
            )
        findings.append(finding)

    if digest is not None:
        finding_ids = {finding.finding_id for finding in findings}
        digest_source_refs = {
            finding_ref
            for item in digest.items
            for finding_ref in item.derivation.source_finding_refs
        }
        missing_finding_refs = tuple(sorted(digest_source_refs - finding_ids))
        findings_available_mismatch = digest.fallback.findings_available != bool(findings)
        if missing_finding_refs or findings_available_mismatch:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="the Digest to reference intact canonical Findings",
                received=(
                    f"missing_refs={missing_finding_refs!r}, "
                    f"digest_findings_available={digest.fallback.findings_available!r}, "
                    f"actual_findings_available={bool(findings)!r}"
                ),
            )

    issues: list[ArtifactIssue] = []
    for row in issue_rows:
        if row["session_id"] != session.id or row["artifact_id"] != artifact_ref:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="every ArtifactIssue row to match Artifact/session ownership",
                received=f"issue={row['issue_id']!r}",
            )
        try:
            issue = ArtifactIssueAdapter.validate_json(row["issue_payload"])
        except Exception as exc:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="readable current ArtifactIssue payloads",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
        if (
            issue.issue_id != row["issue_id"]
            or issue.kind != row["kind"]
            or issue.severity != row["severity"]
        ):
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching issue payload and ledger identity mirror columns",
                received=(
                    f"payload={(issue.issue_id, issue.kind, issue.severity)!r}, "
                    f"ledger={(row['issue_id'], row['kind'], row['severity'])!r}"
                ),
            )
        issues.append(issue)

    if canonical_frame.evidence_digest != digest:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="matching sidecar and ledger digest payloads",
            received=(
                f"sidecar={getattr(canonical_frame.evidence_digest, 'fingerprint', None)!r}, "
                f"ledger={getattr(digest, 'fingerprint', None)!r}"
            ),
        )
    if canonical_frame.evidence_status != evidence_status:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="matching sidecar and ledger evidence status",
            received=(f"sidecar={canonical_frame.evidence_status!r}, ledger={evidence_status!r}"),
        )
    if canonical_frame.meta.analysis_scope != scope:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="matching sidecar and ledger analysis scope",
            received="analysis scope mismatch",
        )
    if canonical_frame.meta.quality_summary != quality:
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="matching sidecar and ledger quality summary",
            received="quality summary mismatch",
        )
    if tuple(sorted(canonical_json(item) for item in canonical_frame.meta.issues)) != tuple(
        sorted(canonical_json(item) for item in issues)
    ):
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="matching sidecar and ledger ArtifactIssue payloads",
            received="issue payload mismatch",
        )

    frame_path = Path(str(artifact_row["frame_path"]))
    if frame_path.resolve() != canonical_path.resolve():
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="the evidence ledger to reference the canonical Frame path",
            received=f"ledger={frame_path}, session_store={canonical_path}",
        )
    if (
        not frame_path.is_file()
        or compute_file_content_hash(frame_path).removeprefix("sha256:")
        != artifact_row["frame_sha"]
    ):
        raise _integrity_error(
            artifact_ref=artifact_ref,
            expected="matching Artifact parquet and ledger content fingerprints",
            received="missing or mismatched frame_sha",
        )

    operator = digest.operator.operator if digest is not None else str(artifact_row["step_type"])
    return CanonicalArtifactEvidence(
        frame=canonical_frame,
        subject=subject,
        scope=scope,
        quality=quality,
        evidence_status=evidence_status,
        digest=digest,
        findings=tuple(findings),
        issues=tuple(issues),
        operator=operator,
    )


__all__ = [
    "CanonicalArtifactEvidence",
    "finding_subject_matches_artifact",
    "load_canonical_artifact_evidence",
    "load_canonical_frame_identity",
]
