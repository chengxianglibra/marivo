"""Selection-wide compatibility over canonical persisted Findings."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from marivo.analysis._artifact_authority import (
    ArtifactAuthorityContext,
    ScopedDependencyAuthority,
    authority_context,
    evaluate_semantic_authority,
)
from marivo.analysis._artifact_integrity import finding_subject_matches_artifact
from marivo.analysis.errors import (
    AnalysisRepair,
    EvidenceIntegrityError,
    EvidenceSelectionError,
    EvidenceStoreUnavailableError,
    FindingNotFoundError,
)
from marivo.analysis.evidence.audit import get_findings_batch
from marivo.analysis.evidence.digest import inference_boundaries_for_operator
from marivo.analysis.evidence.identity import (
    canonical_json,
    canonical_subject_key,
    make_issue_id,
)
from marivo.analysis.evidence.store import EvidenceStore
from marivo.analysis.evidence.types import (
    AnalysisScope,
    ArtifactDigest,
    ArtifactIssue,
    ArtifactIssueAdapter,
    ComparabilityIssue,
    CompatibilityDimensionStatus,
    DataQualityIssue,
    EpistemicKind,
    EventAnalysisScope,
    EventFunnelAnalysisScope,
    EventSubject,
    EventTimeToEventAnalysisScope,
    EvidenceAvailabilityIssue,
    EvidenceCompatibility,
    EvidenceCompatibilityIssue,
    EvidenceRuleIssue,
    EvidenceScope,
    EvidenceScopeAdapter,
    EvidenceStatus,
    EvidenceSubjectAdapter,
    Finding,
    InferenceBoundary,
    LifecycleAnalysisScope,
    LifecycleSubject,
    QualitySummary,
    Subject,
    SubjectSetAnalysisScope,
    SubjectSetSubject,
)
from marivo.analysis.frames._content_hash import compute_file_content_hash
from marivo.analysis.frames.base import BaseFrame
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    CatalogMetricSubjectV1,
    DeltaMetricSubjectV1,
    RuntimeExpressionIdentity,
    RuntimeExpressionSubjectV1,
)

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

_MAX_FINDINGS = 20
_MAX_ISSUES = 20
_EPISTEMIC_ORDER: tuple[EpistemicKind, ...] = (
    "observed",
    "algebraic",
    "estimated",
    "tested",
    "predicted",
    "candidate",
)
_SUBJECT_COMPARATOR_TYPES = (Subject, EventSubject, LifecycleSubject, SubjectSetSubject)
_SCOPE_COMPARATOR_TYPES = (
    AnalysisScope,
    EventAnalysisScope,
    EventFunnelAnalysisScope,
    EventTimeToEventAnalysisScope,
    LifecycleAnalysisScope,
    SubjectSetAnalysisScope,
)


@dataclass(frozen=True, slots=True)
class _CanonicalFinding:
    finding: Finding
    scope: EvidenceScope
    evidence_status: EvidenceStatus
    quality: QualitySummary | None
    issues: tuple[ArtifactIssue, ...]
    operator: str
    omitted_item_count: int
    frame: BaseFrame
    authority: ArtifactAuthorityContext
    dependencies: tuple[ScopedDependencyAuthority, ...]
    source_artifact_refs: tuple[str, ...]
    authority_complete: bool


def _compatibility_repair(action: str, *, snippet: str | None = None) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(
            surface="analysis",
            canonical_id="session.evidence.compatibility",
        ),
        snippet=snippet,
    )


def _selection_error(*, message: str, received: str) -> EvidenceSelectionError:
    return EvidenceSelectionError(
        message=message,
        expected="between 1 and 20 unique canonical Finding ids",
        received=received,
        location="session.evidence.compatibility(finding_ids)",
        repair=_compatibility_repair(
            "Submit one non-empty selection containing at most twenty unique Finding ids.",
            snippet="session.evidence.compatibility(finding_ids=[finding.finding_id])",
        ),
    )


def _normalize_selection(finding_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(finding_ids, (str, bytes)):
        raise _selection_error(
            message="finding_ids must be a sequence, not one string",
            received=type(finding_ids).__name__,
        )
    values = tuple(finding_ids)
    if not values:
        raise _selection_error(message="finding selection is empty", received="0 ids")
    if any(not isinstance(value, str) for value in values):
        received = ", ".join(type(value).__name__ for value in values)
        raise _selection_error(
            message="finding_ids contains a non-string identity",
            received=received,
        )
    if len(values) > _MAX_FINDINGS:
        raise _selection_error(
            message="finding selection exceeds the twenty-id bound",
            received=f"{len(values)} ids",
        )
    if len(set(values)) != len(values):
        duplicates = tuple(sorted(value for value in set(values) if values.count(value) > 1))
        raise _selection_error(
            message="finding selection contains duplicate ids",
            received=f"duplicates={duplicates!r}",
        )
    return tuple(sorted(values))


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
        location="session.evidence.compatibility",
        repair=_compatibility_repair(
            "Recover the exact artifact and re-run its producing operator in a fresh analysis "
            "session if the committed evidence graph cannot be restored.",
            snippet=f"session.get_frame({artifact_ref!r})",
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
        location="session.evidence.compatibility",
        repair=_compatibility_repair(
            "Restore the current Session evidence store, then retry the exact Finding selection."
        ),
        context={"db_path": str(store.db_path)},
    )


def _load_canonical_selection(
    *,
    session: Session,
    store: EvidenceStore,
    finding_ids: tuple[str, ...],
) -> tuple[_CanonicalFinding, ...]:
    try:
        findings = get_findings_batch(
            store=store,
            session_id=session.id,
            finding_ids=finding_ids,
        )
    except FindingNotFoundError:
        raise
    except EvidenceStoreUnavailableError:
        raise
    except sqlite3.Error as exc:
        raise _store_read_error(store=store, cause=exc) from exc
    except Exception as exc:
        raise _integrity_error(
            artifact_ref="selection",
            expected="readable canonical Finding records",
            received=type(exc).__name__,
            cause=exc,
        ) from exc
    artifact_refs = tuple(sorted({finding.artifact_id for finding in findings}))
    placeholders = ",".join("?" for _ in artifact_refs)
    conn = store.read()
    try:
        artifact_rows = list(
            conn.execute(
                f"SELECT * FROM artifacts WHERE session_id = ? AND artifact_id IN ({placeholders})",
                (session.id, *artifact_refs),
            )
        )
        digest_rows = list(
            conn.execute(
                f"SELECT * FROM artifact_digests WHERE session_id = ? "
                f"AND artifact_id IN ({placeholders})",
                (session.id, *artifact_refs),
            )
        )
        issue_rows = list(
            conn.execute(
                f"SELECT * FROM artifact_issues WHERE session_id = ? "
                f"AND artifact_id IN ({placeholders}) ORDER BY issue_id",
                (session.id, *artifact_refs),
            )
        )
    except sqlite3.Error as exc:
        raise _store_read_error(store=store, cause=exc) from exc
    artifacts = {str(row["artifact_id"]): row for row in artifact_rows}
    digests: dict[str, ArtifactDigest] = {}
    for row in digest_rows:
        artifact_ref = str(row["artifact_id"])
        try:
            digest = ArtifactDigest.model_validate_json(row["digest_payload"])
        except Exception as exc:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="a readable current ArtifactDigest payload",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
        if digest.fingerprint != row["fingerprint"]:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching digest payload and ledger fingerprints",
                received=f"payload={digest.fingerprint}, ledger={row['fingerprint']}",
            )
        digests[artifact_ref] = digest

    issues_by_artifact: dict[str, list[ArtifactIssue]] = defaultdict(list)
    for row in issue_rows:
        artifact_ref = str(row["artifact_id"])
        try:
            issue = ArtifactIssueAdapter.validate_json(row["issue_payload"])
        except Exception as exc:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="a readable current ArtifactIssue payload",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
        if issue.issue_id != row["issue_id"]:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching issue payload and ledger identities",
                received=f"payload={issue.issue_id}, ledger={row['issue_id']}",
            )
        issues_by_artifact[artifact_ref].append(issue)

    frames: dict[str, BaseFrame] = {}
    records: list[_CanonicalFinding] = []
    for finding in findings:
        artifact_ref = finding.artifact_id
        row = artifacts.get(artifact_ref)
        if row is None:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="a committed Artifact record for every Finding",
                received="missing Artifact record",
            )
        if finding.session_id != session.id or row["session_id"] != session.id:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected=f"session ownership {session.id!r}",
                received=f"finding={finding.session_id!r}, artifact={row['session_id']!r}",
            )
        if finding.artifact_schema_version != row["artifact_schema_version"]:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching Finding and Artifact schema versions",
                received=(
                    f"finding={finding.artifact_schema_version}, "
                    f"artifact={row['artifact_schema_version']}"
                ),
            )
        try:
            artifact_subject = EvidenceSubjectAdapter.validate_json(row["subject_payload"])
            scope = EvidenceScopeAdapter.validate_json(row["analysis_scope"])
        except Exception as exc:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="readable canonical Artifact subject and scope payloads",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
        if not finding_subject_matches_artifact(
            finding=finding,
            artifact_subject=artifact_subject,
            scope=scope,
        ):
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="the Finding subject to belong to its canonical Artifact subject and scope",
                received=f"finding={canonical_json(finding.subject)}",
            )
        try:
            quality = (
                QualitySummary.model_validate_json(row["quality_summary"])
                if row["quality_summary"]
                else None
            )
            evidence_status = cast("EvidenceStatus", row["evidence_status"])
            if evidence_status not in {"complete", "partial", "unavailable"}:
                raise ValueError(f"unknown evidence status {evidence_status!r}")
        except Exception as exc:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="readable current quality and evidence status fields",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
        selected_digest = digests.get(artifact_ref)
        if evidence_status == "complete" and selected_digest is None:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="a persisted digest for complete evidence",
                received="missing digest",
            )
        if selected_digest is not None:
            digest_row = next(item for item in digest_rows if item["artifact_id"] == artifact_ref)
            digest_mismatches: list[str] = []
            if selected_digest.artifact_ref != artifact_ref:
                digest_mismatches.append("artifact_ref")
            if selected_digest.scope != scope:
                digest_mismatches.append("scope")
            if selected_digest.subject != artifact_subject:
                digest_mismatches.append("subject")
            if digest_row["operator"] != selected_digest.operator.operator:
                digest_mismatches.append("operator")
            if digest_row["subject_key"] != canonical_subject_key(selected_digest.subject):
                digest_mismatches.append("subject_key")
            if digest_mismatches:
                raise _integrity_error(
                    artifact_ref=artifact_ref,
                    expected="matching digest payload and canonical Artifact identities",
                    received=f"mismatched={tuple(digest_mismatches)!r}",
                )
        try:
            frame = session.get_frame(artifact_ref)
        except Exception as exc:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="an exactly recoverable content-addressed Artifact",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
        frames[artifact_ref] = frame
        sidecar_digest = frame.evidence_digest
        if sidecar_digest != selected_digest:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching sidecar and ledger digest payloads",
                received=(
                    f"sidecar={getattr(sidecar_digest, 'fingerprint', None)!r}, "
                    f"ledger={getattr(selected_digest, 'fingerprint', None)!r}"
                ),
            )
        if frame.evidence_status != evidence_status:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching sidecar and ledger evidence status",
                received=f"sidecar={frame.evidence_status}, ledger={evidence_status}",
            )
        frame_path = Path(str(row["frame_path"]))
        if (
            not frame_path.is_file()
            or compute_file_content_hash(frame_path).removeprefix("sha256:") != row["frame_sha"]
        ):
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching Artifact parquet and ledger content fingerprints",
                received="missing or mismatched frame_sha",
            )
        sidecar_issues = tuple(sorted(canonical_json(issue) for issue in frame.meta.issues))
        ledger_issues = tuple(
            sorted(canonical_json(issue) for issue in issues_by_artifact[artifact_ref])
        )
        if sidecar_issues != ledger_issues:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching sidecar and ledger ArtifactIssue payloads",
                received="issue payload mismatch",
            )
        if frame.meta.analysis_scope != scope:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching sidecar and ledger analysis scope",
                received="analysis scope mismatch",
            )
        if frame.meta.quality_summary != quality:
            raise _integrity_error(
                artifact_ref=artifact_ref,
                expected="matching sidecar and ledger quality summary",
                received="quality summary mismatch",
            )
        authority = authority_context(frame, session=session, frames=frames)
        dependencies = tuple(
            dependency
            for dependency in authority.semantic_dependencies
            if isinstance(dependency, ScopedDependencyAuthority)
        )
        authority_complete = bool(dependencies) and len(dependencies) == len(
            authority.semantic_dependencies
        )
        source_artifact_refs = authority.source_refs
        operator = (
            selected_digest.operator.operator
            if selected_digest is not None
            else finding.derivation.operator
        )
        records.append(
            _CanonicalFinding(
                finding=finding,
                scope=scope,
                evidence_status=evidence_status,
                quality=quality,
                issues=tuple(issues_by_artifact[artifact_ref]),
                operator=operator,
                omitted_item_count=(
                    selected_digest.omissions.omitted_items if selected_digest is not None else 0
                ),
                frame=frame,
                authority=authority,
                dependencies=dependencies,
                source_artifact_refs=source_artifact_refs,
                authority_complete=authority_complete,
            )
        )

    source_finding_ids = tuple(
        sorted(
            {
                source_id
                for record in records
                for source_id in record.finding.derivation.source_finding_refs
            }
        )
    )
    source_findings_by_id: dict[str, Finding] = {}
    if source_finding_ids:
        try:
            source_findings = get_findings_batch(
                store=store,
                session_id=session.id,
                finding_ids=source_finding_ids,
            )
            source_findings_by_id = {finding.finding_id: finding for finding in source_findings}
        except EvidenceStoreUnavailableError:
            raise
        except sqlite3.Error as exc:
            raise _store_read_error(store=store, cause=exc) from exc
        except Exception as exc:
            raise _integrity_error(
                artifact_ref="selection",
                expected="every derivation source Finding to resolve in the current Session",
                received=type(exc).__name__,
                cause=exc,
            ) from exc
    for record in records:
        allowed_artifact_refs = {
            record.finding.artifact_id,
            *record.source_artifact_refs,
        }
        allowed_source_refs = {
            *allowed_artifact_refs,
            *(dependency.ref.path for dependency in record.dependencies),
        }
        for source_ref in record.finding.source_refs:
            if source_ref in allowed_source_refs:
                continue
            raise _integrity_error(
                artifact_ref=source_ref,
                expected="a source ref in this Finding's own typed dependency closure",
                received="unrelated Finding source ref",
            )
        for source_id in record.finding.derivation.source_finding_refs:
            source_finding = source_findings_by_id[source_id]
            if (
                source_id == record.finding.finding_id
                or source_finding.artifact_id not in allowed_artifact_refs
            ):
                raise _integrity_error(
                    artifact_ref=record.finding.artifact_id,
                    expected="a derivation source Finding in this Finding's typed Artifact closure",
                    received=(
                        f"source_finding={source_id!r}, "
                        f"source_artifact={source_finding.artifact_id!r}"
                    ),
                )
    return tuple(records)


def _metric_identity_key(identity: object) -> tuple[str, str] | None:
    if isinstance(identity, CatalogMetricIdentity):
        return ("catalog", identity.metric_ref.path)
    if isinstance(identity, RuntimeExpressionIdentity):
        return ("runtime", identity.expression_fingerprint)
    return None


def _subject_key(subject: object) -> tuple[object, ...] | None:
    if isinstance(subject, Subject):
        typed = subject.typed_metric_subject
        metric_identities: tuple[tuple[str, str], ...] | None = None
        if isinstance(typed, CatalogMetricSubjectV1):
            metric_identities = (("catalog", typed.metric_ref.path),)
        elif isinstance(typed, RuntimeExpressionSubjectV1):
            metric_identities = (("runtime", typed.expression_fingerprint),)
        elif isinstance(typed, DeltaMetricSubjectV1):
            identities = {
                key
                for value in (typed.comparison.current, typed.comparison.baseline)
                if (key := _metric_identity_key(value)) is not None
            }
            if identities:
                metric_identities = tuple(sorted(identities))
        if metric_identities is None:
            return None
        return (
            "metric",
            metric_identities,
            subject.entity_ref.path if subject.entity_ref is not None else None,
        )
    if isinstance(subject, EventSubject):
        return (
            "event",
            subject.subject_entity_ref.path,
            subject.subject_identity_signature,
        )
    if isinstance(subject, LifecycleSubject):
        return (
            "lifecycle",
            subject.subject_entity_ref.path,
            subject.subject_identity_signature,
        )
    if isinstance(subject, SubjectSetSubject):
        return (
            "subject_set",
            subject.subject_entity_ref.path,
            subject.subject_identity_signature,
        )
    return None


def _identity_set(scope: AnalysisScope) -> tuple[tuple[str, str], ...]:
    identities = scope.metric_identities
    if not identities and scope.comparison is not None:
        identities = (scope.comparison.current, scope.comparison.baseline)
    return tuple(
        sorted(
            {key for identity in identities if (key := _metric_identity_key(identity)) is not None}
        )
    )


def _comparison_direction(
    record: _CanonicalFinding,
) -> tuple[object, ...] | None:
    comparison = record.scope.comparison if isinstance(record.scope, AnalysisScope) else None
    if comparison is None:
        return None
    source_current_ref = getattr(record.frame.meta, "source_current_ref", None)
    source_baseline_ref = getattr(record.frame.meta, "source_baseline_ref", None)
    return (
        _metric_identity_key(comparison.current),
        _metric_identity_key(comparison.baseline),
        source_current_ref,
        source_baseline_ref,
    )


def _event_base(scope: EvidenceScope) -> EventAnalysisScope | None:
    if isinstance(scope, EventAnalysisScope):
        return scope
    if isinstance(scope, EventFunnelAnalysisScope | EventTimeToEventAnalysisScope):
        return scope.source_scope
    return None


def _scope_mismatches(left: _CanonicalFinding, right: _CanonicalFinding) -> tuple[str, ...] | None:
    left_scope = left.scope
    right_scope = right.scope
    fields: list[str] = []
    if isinstance(left_scope, AnalysisScope) and isinstance(right_scope, AnalysisScope):
        if _identity_set(left_scope) != _identity_set(right_scope):
            fields.append("scope.metric_identities")
        if tuple(ref.to_dict() for ref in left_scope.axis_refs) != tuple(
            ref.to_dict() for ref in right_scope.axis_refs
        ):
            fields.append("scope.axis_refs")
        if canonical_json(left_scope.segment_predicates) != canonical_json(
            right_scope.segment_predicates
        ):
            fields.append("scope.segment_predicates")
        if canonical_json(left_scope.window) != canonical_json(right_scope.window):
            fields.append("scope.window")
        if _comparison_direction(left) != _comparison_direction(right):
            fields.append("scope.comparison_direction")
        if left_scope.assumptions != right_scope.assumptions:
            fields.append("scope.assumptions")
        left_grain = (
            left.finding.subject.grain if isinstance(left.finding.subject, Subject) else None
        )
        right_grain = (
            right.finding.subject.grain if isinstance(right.finding.subject, Subject) else None
        )
        if left_grain != right_grain:
            fields.append("scope.grain")
        return tuple(fields)

    left_event = _event_base(left_scope)
    right_event = _event_base(right_scope)
    if left_event is not None and right_event is not None:
        if left_event != right_event:
            fields.append("scope.event_source")
        if isinstance(left_scope, EventFunnelAnalysisScope) and isinstance(
            right_scope, EventFunnelAnalysisScope
        ):
            if left_scope.axes != right_scope.axes:
                fields.append("scope.axes")
            if left_scope.grouped_reconciliation != right_scope.grouped_reconciliation:
                fields.append("scope.grouped_reconciliation")
        if isinstance(left_scope, EventTimeToEventAnalysisScope) and isinstance(
            right_scope, EventTimeToEventAnalysisScope
        ):
            if left_scope.start_step != right_scope.start_step:
                fields.append("scope.start_step")
            if left_scope.end_step != right_scope.end_step:
                fields.append("scope.end_step")
            if left_scope.axes != right_scope.axes:
                fields.append("scope.axes")
        return tuple(fields)

    if isinstance(left_scope, LifecycleAnalysisScope) and isinstance(
        right_scope, LifecycleAnalysisScope
    ):
        for field_name in (
            "state_model_ref",
            "state_model_fingerprint",
            "window",
            "coverage",
            "cohort_binding",
            "assumptions",
        ):
            if canonical_json(getattr(left_scope, field_name)) != canonical_json(
                getattr(right_scope, field_name)
            ):
                fields.append(f"scope.{field_name}")
        if left_scope.analysis_axis == right_scope.analysis_axis:
            if canonical_json(left_scope.reducer) != canonical_json(right_scope.reducer):
                fields.append("scope.reducer")
            if canonical_json(left_scope.replay_semantics) != canonical_json(
                right_scope.replay_semantics
            ):
                fields.append("scope.replay_semantics")
        return tuple(fields)

    if isinstance(left_scope, SubjectSetAnalysisScope) and isinstance(
        right_scope, SubjectSetAnalysisScope
    ):
        for field_name in (
            "source_artifact_fingerprint",
            "selection",
            "selection_fingerprint",
            "coverage_status",
        ):
            if canonical_json(getattr(left_scope, field_name)) != canonical_json(
                getattr(right_scope, field_name)
            ):
                fields.append(f"scope.{field_name}")
        return tuple(fields)
    known = isinstance(left_scope, _SCOPE_COMPARATOR_TYPES) and isinstance(
        right_scope, _SCOPE_COMPARATOR_TYPES
    )
    return ("scope.kind",) if known else None


def _issue_wrapper(
    records: tuple[_CanonicalFinding, ...], detail: Any
) -> EvidenceCompatibilityIssue:
    return EvidenceCompatibilityIssue(
        finding_ids=tuple(sorted(record.finding.finding_id for record in records)),
        artifact_refs=tuple(sorted({record.finding.artifact_id for record in records})),
        detail=detail,
    )


def _rule_issue(
    *,
    records: tuple[_CanonicalFinding, ...],
    kind: Literal[
        "semantic_authority_unknown",
        "unknown_subject_rule",
        "unknown_scope_rule",
        "unknown_operator_evidence_rule",
    ],
    expected: str,
    received: str,
) -> EvidenceCompatibilityIssue:
    refs = tuple(sorted({record.finding.artifact_id for record in records}))
    return _issue_wrapper(
        records,
        EvidenceRuleIssue(
            issue_id=make_issue_id(
                artifact_id="|".join(refs),
                kind=kind,
                source_refs=refs,
            ),
            kind=kind,
            severity="warning",
            expected=expected,
            received=received,
            repair=_compatibility_repair(
                "Inspect the attributed Finding and Artifact refs, then retry only after the "
                "installed compatibility matrix can prove this rule."
            ),
        ),
    )


def _comparability_issue(
    *,
    left: _CanonicalFinding,
    right: _CanonicalFinding,
    kind: Literal["comparability_incompatible", "definition_drift_detected"],
    fields: tuple[str, ...] = (),
    definition_refs: tuple[str, ...] = (),
) -> EvidenceCompatibilityIssue:
    refs = tuple(sorted({left.finding.artifact_id, right.finding.artifact_id}))
    return _issue_wrapper(
        (left, right),
        ComparabilityIssue(
            issue_id=make_issue_id(
                artifact_id="|".join(refs),
                kind=f"{kind}:{','.join(fields or definition_refs)}",
                source_refs=refs,
            ),
            kind=kind,
            severity="blocking",
            source_refs=refs,
            left_scope=left.scope,
            right_scope=right.scope,
            incompatible_fields=fields,
            definition_refs=definition_refs,
            repair=_compatibility_repair(
                "Submit a smaller selection whose attributed subject, scope, and current "
                "semantic definitions align."
            ),
        ),
    )


def _deduplicate_issues(
    issues: Sequence[EvidenceCompatibilityIssue],
) -> tuple[EvidenceCompatibilityIssue, ...]:
    by_key: dict[str, EvidenceCompatibilityIssue] = {}
    for issue in issues:
        key = canonical_json(issue)
        by_key.setdefault(key, issue)
    return tuple(
        sorted(
            by_key.values(),
            key=lambda issue: (
                issue.detail.kind,
                issue.finding_ids,
                issue.detail.issue_id,
            ),
        )
    )


def _dimension_status(*, incompatible: bool, indeterminate: bool) -> CompatibilityDimensionStatus:
    if incompatible:
        return "incompatible"
    if indeterminate:
        return "indeterminate"
    return "compatible"


def _aggregate_evidence(records: tuple[_CanonicalFinding, ...]) -> EvidenceStatus:
    statuses = {record.evidence_status for record in records}
    if "unavailable" in statuses:
        return "unavailable"
    if "partial" in statuses:
        return "partial"
    return "complete"


def _aggregate_quality(records: tuple[_CanonicalFinding, ...]) -> str:
    statuses = {record.finding.quality_status for record in records}
    if "not_ready" in statuses:
        return "not_ready"
    if "needs_attention" in statuses:
        return "needs_attention"
    if None in statuses:
        return "not_assessed"
    return "ready"


def evaluate_compatibility(
    *,
    session: Session,
    store: EvidenceStore,
    finding_ids: Sequence[str],
) -> EvidenceCompatibility:
    """Evaluate one canonical selection without mutating committed evidence."""
    normalized_ids = _normalize_selection(finding_ids)
    records = _load_canonical_selection(
        session=session,
        store=store,
        finding_ids=normalized_ids,
    )
    issues: list[EvidenceCompatibilityIssue] = []
    subject_incompatible = False
    subject_indeterminate = False
    scope_incompatible = False
    scope_indeterminate = False
    semantic_incompatible = False
    semantic_indeterminate = False
    operator_indeterminate = False

    for record in records:
        if (
            not isinstance(record.finding.subject, _SUBJECT_COMPARATOR_TYPES)
            or _subject_key(record.finding.subject) is None
        ):
            subject_indeterminate = True
            issues.append(
                _rule_issue(
                    records=(record,),
                    kind="unknown_subject_rule",
                    expected="one registered EvidenceSubject variant with governed identity",
                    received=type(record.finding.subject).__name__,
                )
            )
        if not isinstance(record.scope, _SCOPE_COMPARATOR_TYPES):
            scope_indeterminate = True
            issues.append(
                _rule_issue(
                    records=(record,),
                    kind="unknown_scope_rule",
                    expected="one registered EvidenceScope variant",
                    received=type(record.scope).__name__,
                )
            )
        authority_evaluation = evaluate_semantic_authority(
            record.authority,
            session=session,
        )
        if authority_evaluation.status == "indeterminate":
            semantic_indeterminate = True
            issues.append(
                _rule_issue(
                    records=(record,),
                    kind="semantic_authority_unknown",
                    expected="current canonical authority for every recorded dependency",
                    received=(f"unresolved={authority_evaluation.indeterminate_definition_refs!r}"),
                )
            )
        elif authority_evaluation.status == "stale":
            semantic_incompatible = True
            drifted = authority_evaluation.drifted_definition_refs
            refs = (record.finding.artifact_id,)
            detail = ComparabilityIssue(
                issue_id=make_issue_id(
                    artifact_id=record.finding.artifact_id,
                    kind=f"definition_drift_detected:{','.join(drifted)}",
                    source_refs=refs,
                ),
                kind="definition_drift_detected",
                severity="blocking",
                source_refs=refs,
                left_scope=record.scope,
                right_scope=record.scope,
                definition_refs=drifted,
                repair=_compatibility_repair(
                    "Re-run the source operator under the current semantic catalog before "
                    "combining this Finding."
                ),
            )
            issues.append(_issue_wrapper((record,), detail))

        for issue in record.issues:
            if isinstance(issue, (DataQualityIssue, ComparabilityIssue, EvidenceAvailabilityIssue)):
                issues.append(_issue_wrapper((record,), issue))
        try:
            inference_boundaries_for_operator(
                record.operator,
                (record.finding,),
                omitted_item_count=record.omitted_item_count,
            )
        except ValueError:
            operator_indeterminate = True
            issues.append(
                _rule_issue(
                    records=(record,),
                    kind="unknown_operator_evidence_rule",
                    expected="one registered operator evidence rule",
                    received=record.operator,
                )
            )

    for left, right in combinations(records, 2):
        left_subject = _subject_key(left.finding.subject)
        right_subject = _subject_key(right.finding.subject)
        if left_subject is not None and right_subject is not None and left_subject != right_subject:
            subject_incompatible = True
            issues.append(
                _comparability_issue(
                    left=left,
                    right=right,
                    kind="comparability_incompatible",
                    fields=("subject.identity",),
                )
            )
        mismatches = _scope_mismatches(left, right)
        if mismatches is None:
            scope_indeterminate = True
            issues.append(
                _rule_issue(
                    records=(left, right),
                    kind="unknown_scope_rule",
                    expected="two registered EvidenceScope variants",
                    received=f"{type(left.scope).__name__}/{type(right.scope).__name__}",
                )
            )
        elif mismatches:
            scope_incompatible = True
            issues.append(
                _comparability_issue(
                    left=left,
                    right=right,
                    kind="comparability_incompatible",
                    fields=mismatches,
                )
            )
        left_by_key: dict[str, set[str]] = defaultdict(set)
        right_by_key: dict[str, set[str]] = defaultdict(set)
        for dependency in left.dependencies:
            left_by_key[dependency.key].add(dependency.fingerprint)
        for dependency in right.dependencies:
            right_by_key[dependency.key].add(dependency.fingerprint)
        drifted_refs = tuple(
            sorted(
                key
                for key in set(left_by_key) & set(right_by_key)
                if left_by_key[key] != right_by_key[key]
            )
        )
        if drifted_refs:
            semantic_incompatible = True
            issues.append(
                _comparability_issue(
                    left=left,
                    right=right,
                    kind="definition_drift_detected",
                    definition_refs=drifted_refs,
                )
            )

    full_issues = _deduplicate_issues(issues)
    blocking_issue = any(issue.detail.severity == "blocking" for issue in full_issues)
    rule_unknown = any(isinstance(issue.detail, EvidenceRuleIssue) for issue in full_issues)
    quality_status = _aggregate_quality(records)
    overall_status: Literal["compatible", "incompatible", "indeterminate"]
    if blocking_issue or quality_status == "not_ready":
        overall_status = "incompatible"
    elif (
        rule_unknown
        or subject_indeterminate
        or scope_indeterminate
        or semantic_indeterminate
        or operator_indeterminate
    ):
        overall_status = "indeterminate"
    else:
        overall_status = "compatible"

    boundaries: dict[str, InferenceBoundary] = {}
    by_artifact: dict[str, list[_CanonicalFinding]] = defaultdict(list)
    for record in records:
        by_artifact[record.finding.artifact_id].append(record)
    for artifact_records in by_artifact.values():
        first = artifact_records[0]
        try:
            projected = inference_boundaries_for_operator(
                first.operator,
                (record.finding for record in artifact_records),
                omitted_item_count=first.omitted_item_count,
            )
        except ValueError:
            projected = ()
        for boundary in projected:
            boundaries.setdefault(boundary.kind, boundary)

    retained_issues = full_issues[:_MAX_ISSUES]
    omitted = full_issues[_MAX_ISSUES:]
    omitted_kinds = tuple(sorted({issue.detail.kind for issue in omitted}))
    epistemic_kinds = tuple(
        kind
        for kind in _EPISTEMIC_ORDER
        if any(record.finding.epistemic_kind == kind for record in records)
    )
    artifact_refs = tuple(sorted({record.finding.artifact_id for record in records}))
    payload = {
        "compatibility_version": "v1",
        "status": overall_status,
        "finding_ids": normalized_ids,
        "artifact_refs": artifact_refs,
        "session_id": session.id,
        "subject_status": _dimension_status(
            incompatible=subject_incompatible,
            indeterminate=subject_indeterminate,
        ),
        "scope_status": _dimension_status(
            incompatible=scope_incompatible,
            indeterminate=scope_indeterminate,
        ),
        "semantic_status": _dimension_status(
            incompatible=semantic_incompatible,
            indeterminate=semantic_indeterminate,
        ),
        "evidence_status": _aggregate_evidence(records),
        "quality_status": quality_status,
        "epistemic_kinds": epistemic_kinds,
        "issues": full_issues,
        "boundaries": tuple(boundaries[key] for key in sorted(boundaries)),
        "evaluated_pair_count": len(records) * (len(records) - 1) // 2,
        "omitted_issue_count": len(omitted),
        "omitted_issue_kinds": omitted_kinds,
    }
    fingerprint = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    payload["issues"] = retained_issues
    payload["fingerprint"] = fingerprint
    return EvidenceCompatibility.model_validate(payload)


__all__ = ["evaluate_compatibility"]
