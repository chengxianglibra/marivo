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
    context = authority_context(canonical.frame, session=session)
    semantic = evaluate_semantic_authority(context, session=session)
    issues: list[ArtifactIssue | EvidenceRuleIssue] = list(canonical.issues)

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
    if semantic.status == "stale":
        status = "stale"
    elif semantic.status == "indeterminate" or not evidence_admissible:
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
