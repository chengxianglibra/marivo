"""Soft invalidation and tombstone-first baseline for canonical evidence objects (Phase 4h-1).

Implements the invalidation taxonomy and downstream repair rules defined in
``docs/analysis/evidence-engine/migration-and-invalidation.md``.

Tombstone-first baseline
------------------------
Canonical evidence objects are **never hard-deleted** as a default action.
Instead, ``soft_invalidate_finding`` marks the finding with an
``invalidated_at`` timestamp and returns a :class:`InvalidationResult` that
describes the downstream repair actions needed to propagate the effect.

Executing the repair actions (recompute, proposal refresh, publish switch) is
the **caller's responsibility** — this module only identifies what needs to
happen.  This keeps invalidation auditable and replay-safe.

Downstream repair priority (from ``migration-and-invalidation.md``):
  1. Missing lineage/soft ref only → preserve object, expose missing ref
  2. Current assessment closure affected → contract membership, reopen gap
  3. Proposal input closure affected → suppress proposal refresh
  4. Latest bundle integrity affected → trigger recompute / publish rollback

Phase: 4h-1
"""

from __future__ import annotations

import logging
from typing import Literal, TypedDict

from app.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    PropositionRepository,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

INVALIDATION_SCHEMA_VERSION = "invalidation_result.v1"

# ---------------------------------------------------------------------------
# Public TypedDicts
# ---------------------------------------------------------------------------


class DownstreamRepairAction(TypedDict):
    """A single required downstream repair triggered by an invalidation.

    Fields
    ------
    action:
        What kind of repair is needed.

        ``reopen_gap``
            An evidence gap that was resolved should be reopened because its
            resolution evidence is now invalid.
        ``suppress_proposal``
            An action proposal generated from the affected assessment should
            be suppressed (not executed as an agent action).
        ``recompute_assessment``
            The assessment for the affected proposition must be recomputed
            (the supporting evidence closure has changed).
        ``bundle_rollback``
            The externally visible bundle for the proposition should be
            rolled back or refreshed because the published state now
            depends on invalidated evidence.
    target_id:
        The ID of the object that needs repair (gap_id, action_proposal_id,
        or proposition_id depending on *action*).
    reason:
        Human-readable explanation of why this repair is needed.
    """

    action: Literal["reopen_gap", "suppress_proposal", "recompute_assessment", "bundle_rollback"]
    target_id: str
    reason: str


class InvalidationResult(TypedDict):
    """Outcome of a soft-invalidation call.

    Fields
    ------
    invalidated_id:
        The ID of the object that was soft-invalidated.
    object_type:
        Whether the invalidated object is a ``"finding"`` or
        ``"proposition"``.
    downstream_repair_actions:
        Ordered list of repair actions that callers should execute to
        propagate the effect of the invalidation.  May be empty if the
        invalidated object has no downstream dependants with published state.
    schema_version:
        Fixed at :data:`INVALIDATION_SCHEMA_VERSION`.
    """

    invalidated_id: str
    object_type: Literal["finding", "proposition"]
    downstream_repair_actions: list[DownstreamRepairAction]
    schema_version: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def soft_invalidate_finding(
    *,
    session_id: str,
    finding_id: str,
    reason: str,
    finding_repo: FindingRepository,
    proposition_repo: PropositionRepository,
    gap_repo: EvidenceGapRepository,
    proposal_repo: ActionProposalRepository,
    assessment_repo: AssessmentRepository,
) -> InvalidationResult:
    """Soft-invalidate a finding and return the downstream repair plan.

    This function:

    1. Marks *finding_id* as soft-invalidated via
       :meth:`~app.storage.evidence_repositories.FindingRepository.soft_invalidate`.
    2. Identifies propositions seeded by this finding.
    3. For each affected proposition with a published bundle, schedules
       ``recompute_assessment`` and ``bundle_rollback`` repair actions.
    4. For each open gap on an affected proposition, schedules a
       ``reopen_gap`` repair action.
    5. For each existing action proposal on an affected proposition,
       schedules a ``suppress_proposal`` repair action.

    **The repair actions are not executed** — they are returned as a plan.
    Callers should drive execution via
    :func:`~app.evidence_engine.replay_recovery.recover_proposition_pipeline`
    or the main downstream pipeline.

    Parameters
    ----------
    session_id:
        Session that owns the finding and downstream objects.
    finding_id:
        The finding to soft-invalidate.
    reason:
        Machine-readable or free-text explanation (e.g.
        ``"upstream_artifact_retracted"``).
    finding_repo:
        Repository for findings.
    proposition_repo:
        Repository for propositions.
    gap_repo:
        Repository for evidence gaps.
    proposal_repo:
        Repository for action proposals.
    assessment_repo:
        Repository for assessments (used to check published state).

    Returns
    -------
    InvalidationResult

    Raises
    ------
    ValueError
        If *finding_id* does not exist.
    """
    # Step 1 — mark the finding as invalidated (tombstone-first).
    finding_repo.soft_invalidate(finding_id, reason)
    logger.info(
        "soft_invalidate_finding: invalidated finding %s (session=%s, reason=%r)",
        finding_id,
        session_id,
        reason,
    )

    # Step 2 — identify propositions seeded by this finding.
    affected_proposition_ids = proposition_repo.list_seeded_proposition_ids(finding_id)

    repair_actions: list[DownstreamRepairAction] = []

    for proposition_id in affected_proposition_ids:
        proposition = proposition_repo.get(proposition_id)
        if proposition is None:
            continue

        has_published_bundle = proposition.get("externally_visible_assessment_id") is not None

        # Step 3 — schedule assessment recompute + bundle rollback if published.
        if has_published_bundle:
            repair_actions.append(
                DownstreamRepairAction(
                    action="recompute_assessment",
                    target_id=proposition_id,
                    reason=(
                        f"finding {finding_id!r} was a seed finding for this proposition "
                        f"and has been invalidated ({reason})"
                    ),
                )
            )
            repair_actions.append(
                DownstreamRepairAction(
                    action="bundle_rollback",
                    target_id=proposition_id,
                    reason=(
                        f"externally visible bundle for proposition {proposition_id!r} "
                        f"may depend on invalidated finding {finding_id!r}"
                    ),
                )
            )

        # Step 4 — schedule gap reopens for resolved gaps whose resolution evidence
        # may now be invalid.  Open gaps are unaffected — they already represent
        # unresolved evidence needs and do not need to be "reopened."
        # v1 baseline: conservatively reopen all resolved gaps on the affected
        # proposition without tracing individual inference record inputs.
        resolved_gaps = gap_repo.list_by_proposition(proposition_id, status="resolved")
        for gap in resolved_gaps:
            repair_actions.append(
                DownstreamRepairAction(
                    action="reopen_gap",
                    target_id=gap["gap_id"],
                    reason=(
                        f"gap {gap['gap_id']!r} was resolved; the resolution evidence "
                        f"may be invalidated because finding {finding_id!r} has been "
                        f"soft-invalidated ({reason})"
                    ),
                )
            )

        # Step 5 — schedule proposal suppression for any existing proposals.
        proposals = proposal_repo.list_by_proposition(session_id, proposition_id)
        for proposal in proposals:
            repair_actions.append(
                DownstreamRepairAction(
                    action="suppress_proposal",
                    target_id=proposal["action_proposal_id"],
                    reason=(
                        f"action proposal {proposal['action_proposal_id']!r} was generated "
                        f"from an assessment whose evidence closure now includes invalidated "
                        f"finding {finding_id!r}"
                    ),
                )
            )

    return InvalidationResult(
        invalidated_id=finding_id,
        object_type="finding",
        downstream_repair_actions=repair_actions,
        schema_version=INVALIDATION_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "INVALIDATION_SCHEMA_VERSION",
    "DownstreamRepairAction",
    "InvalidationResult",
    "soft_invalidate_finding",
]
