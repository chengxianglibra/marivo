"""Public Artifact revalidation evaluator over committed canonical state."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from marivo._compat import UTC
from marivo.analysis._artifact_authority import (
    authority_context,
    evaluate_semantic_authority,
)
from marivo.analysis._artifact_integrity import load_canonical_artifact_evidence
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.evidence.identity import canonical_json, make_issue_id
from marivo.analysis.evidence.store import EvidenceStore
from marivo.analysis.evidence.types import (
    ArtifactIssue,
    ArtifactRevalidation,
    ComparabilityIssue,
    EvidenceAvailabilityIssue,
    EvidenceRuleIssue,
    EvidenceScope,
)
from marivo.analysis.frames.base import BaseFrame
from marivo.introspection.live.model import LiveHelpTarget

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session


def _repair(action: str, *, snippet: str | None = None) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="session.revalidate"),
        snippet=snippet,
    )


def _evidence_satisfies_contract(
    *,
    evidence_status: str,
    digest_present: bool,
    issues: tuple[ArtifactIssue, ...],
) -> bool:
    if evidence_status == "complete":
        return digest_present
    if evidence_status != "partial":
        return False
    availability = tuple(issue for issue in issues if isinstance(issue, EvidenceAvailabilityIssue))
    return bool(availability) and all(
        issue.severity == "warning" and issue.fallback.rows_available for issue in availability
    )


def _lifecycle_dependency_status(
    *,
    session: Session,
    frame: BaseFrame,
    scope: EvidenceScope,
) -> tuple[
    Literal["admissible", "stale", "indeterminate"],
    tuple[ComparabilityIssue | EvidenceRuleIssue, ...],
]:
    meta = frame.meta
    if (
        getattr(meta, "kind", None) != "lifecycle_frame"
        or getattr(meta, "semantic_kind", None) == "history"
    ):
        return "admissible", ()
    source_ref = getattr(meta, "source_history_ref", None)
    expected_hash = getattr(meta, "source_history_fingerprint", None)
    if not isinstance(source_ref, str) or not isinstance(expected_hash, str):
        return "admissible", ()
    row = session._store.get_artifact(session.id, source_ref)
    source: BaseFrame | None = None
    if row is not None:
        try:
            source = session.artifact(source_ref)
        except Exception:
            source = None
    if source is None:
        missing_issue = EvidenceRuleIssue(
            issue_id=make_issue_id(
                artifact_id=frame.meta.artifact_id or frame.ref,
                kind="lifecycle_source_unavailable",
                source_refs=(source_ref,),
            ),
            kind="unknown_scope_rule",
            severity="warning",
            expected="the exact committed LifecycleFrame[history] dependency",
            received=f"source_history_ref={source_ref!r} is unavailable",
            repair=_repair(
                "Restore or replay the source Lifecycle history, then regenerate this reducer."
            ),
        )
        return "indeterminate", (missing_issue,)
    source_meta = source.meta
    valid = (
        getattr(source_meta, "kind", None) == "lifecycle_frame"
        and getattr(source_meta, "semantic_kind", None) == "history"
        and source_meta.content_hash == expected_hash
        and getattr(source_meta, "state_model_ref", None) == getattr(meta, "state_model_ref", None)
        and getattr(source_meta, "state_model_fingerprint", None)
        == getattr(meta, "state_model_fingerprint", None)
    )
    if valid:
        return "admissible", ()
    stale_issue = ComparabilityIssue(
        issue_id=make_issue_id(
            artifact_id=frame.meta.artifact_id or frame.ref,
            kind="lifecycle_source_changed",
            source_refs=(source_ref,),
        ),
        kind="comparability_incompatible",
        severity="blocking",
        source_refs=(source_ref, frame.meta.artifact_id or frame.ref),
        left_scope=scope,
        right_scope=scope,
        incompatible_fields=(
            "source_history_fingerprint",
            "state_model_ref",
            "state_model_fingerprint",
        ),
        repair=_repair(
            "Replay the current StateModel history and regenerate this Lifecycle reducer."
        ),
    )
    return "stale", (stale_issue,)


def evaluate_artifact_revalidation(
    *,
    session: Session,
    store: EvidenceStore,
    frame: BaseFrame,
    checked_at: datetime | None = None,
) -> ArtifactRevalidation:
    """Revalidate one Artifact without mutating historical state."""
    canonical = load_canonical_artifact_evidence(
        session=session,
        store=store,
        frame=frame,
    )
    is_lifecycle_reducer = (
        getattr(canonical.frame.meta, "kind", None) == "lifecycle_frame"
        and getattr(canonical.frame.meta, "semantic_kind", None) != "history"
    )
    context = authority_context(
        canonical.frame,
        session=session,
        strict_source_identity=not is_lifecycle_reducer,
    )
    semantic = evaluate_semantic_authority(context, session=session)
    issues: list[ArtifactIssue | EvidenceRuleIssue] = list(canonical.issues)
    dependency_status, dependency_issues = _lifecycle_dependency_status(
        session=session,
        frame=canonical.frame,
        scope=canonical.scope,
    )
    issues.extend(dependency_issues)

    if semantic.status == "stale":
        refs = (context.artifact_ref,)
        issues.append(
            ComparabilityIssue(
                issue_id=make_issue_id(
                    artifact_id=context.artifact_ref,
                    kind=(
                        f"definition_drift_detected:{','.join(semantic.drifted_definition_refs)}"
                    ),
                    source_refs=refs,
                ),
                kind="definition_drift_detected",
                severity="blocking",
                source_refs=refs,
                left_scope=canonical.scope,
                right_scope=canonical.scope,
                definition_refs=semantic.drifted_definition_refs,
                repair=_repair(
                    "Re-run the Artifact's producing operator under the current semantic "
                    "catalog before treating it as current.",
                ),
            )
        )
    elif semantic.status == "indeterminate":
        issues.append(
            EvidenceRuleIssue(
                issue_id=make_issue_id(
                    artifact_id=context.artifact_ref,
                    kind=(
                        "semantic_authority_unknown:"
                        f"{','.join(semantic.indeterminate_definition_refs)}"
                    ),
                    source_refs=(context.artifact_ref,),
                ),
                kind="semantic_authority_unknown",
                severity="warning",
                expected="current canonical scoped authority for every semantic dependency",
                received=(f"unresolved={semantic.indeterminate_definition_refs!r}"),
                repair=_repair(
                    "Restore the missing current semantic definitions or regenerate the Artifact "
                    "under a complete current catalog."
                ),
            )
        )

    evidence_admissible = _evidence_satisfies_contract(
        evidence_status=canonical.evidence_status,
        digest_present=canonical.digest is not None,
        issues=canonical.issues,
    )
    status: Literal["admissible", "stale", "indeterminate"]
    if semantic.status == "stale" or dependency_status == "stale":
        status = "stale"
    elif (
        semantic.status == "indeterminate"
        or dependency_status == "indeterminate"
        or not evidence_admissible
    ):
        status = "indeterminate"
    else:
        status = "admissible"

    normalized_issues = tuple(
        sorted(
            {canonical_json(issue): issue for issue in issues}.values(),
            key=lambda issue: (issue.kind, issue.issue_id),
        )
    )
    checked = checked_at or datetime.now(UTC)
    payload = {
        "revalidation_version": "v1",
        "artifact_ref": context.artifact_ref,
        "session_id": context.session_id,
        "content_hash": context.content_hash,
        "artifact_schema_version": canonical.frame.meta.artifact_schema_version,
        "recorded_catalog_fingerprint": semantic.recorded_catalog_fingerprint,
        "current_catalog_fingerprint": semantic.current_catalog_fingerprint,
        "semantic_status": semantic.status,
        "evidence_status": canonical.evidence_status,
        "dependency_status": dependency_status,
        "status": status,
        "issues": normalized_issues,
        "authority_fingerprint": semantic.authority_fingerprint,
    }
    fingerprint = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return ArtifactRevalidation.model_validate(
        {
            **payload,
            "checked_at": checked,
            "fingerprint": fingerprint,
        }
    )


__all__ = ["evaluate_artifact_revalidation"]
