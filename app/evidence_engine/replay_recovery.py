"""Replay and crash-recovery helpers for the canonical downstream pipeline (Phase 4h-1).

Implements the stage recovery rules defined in
``docs/analysis/evidence-engine/runtime-lifecycle.md §Stage recovery rules``:

- extraction crash: re-run extraction if artifact + findings not yet committed
  (out of scope for this module — handled by the artifact commit boundary)
- seeding crash: re-seed from the committed finding snapshot
  (handled by ``run_canonical_downstream`` — it is already idempotent)
- assessment crash: if the new snapshot is not yet committed, re-run full
  pipeline; if already committed but not published, resume from
  proposal refresh + publish switch
- publish crash: retry the publish switch (already idempotent in
  ``execute_publish_switch``)

Entry points
------------
:func:`get_proposition_checkpoint`
    Read-only probe that reports how far a proposition's pipeline has progressed.

:func:`recover_proposition_pipeline`
    Resumes the pipeline from whichever stage is incomplete.

Design contracts
----------------
- ``docs/analysis/evidence-engine/runtime-lifecycle.md``
- ``docs/analysis/evidence-engine/migration-and-invalidation.md``

Phase: 4h-1
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from app.evidence_engine.canonical_pipeline_runtime import (
    PropositionPipelineResult,
    run_single_proposition_pipeline,
)
from app.evidence_engine.proposal_refresh_run import (
    ProposalRefreshResult,
    run_action_proposal_refresh,
)
from app.evidence_engine.publish_switch import (
    PublishSwitchResult,
    execute_publish_switch,
)
from app.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

RECOVERY_CHECKPOINT_SCHEMA_VERSION = "proposition_recovery_checkpoint.v1"

# Minimal proposal context used when no agent-authored context is available.
_DEFAULT_PROPOSAL_CONTEXT: dict[str, Any] = {
    "session_goal": None,
    "risk_budget": None,
    "policy_profile": "default",
}

# ---------------------------------------------------------------------------
# Public TypedDicts
# ---------------------------------------------------------------------------


class PropositionRecoveryCheckpoint(TypedDict):
    """Describes how far a proposition's downstream pipeline has progressed.

    Fields
    ------
    proposition_id:
        The proposition being examined.
    assessment_committed:
        ``True`` when at least one assessment snapshot exists in the DB for
        this proposition (i.e. the recompute stage has already completed at
        least once).
    assessment_id:
        The ``assessment_id`` of the latest committed snapshot, or ``None``
        if no snapshots exist yet.
    externally_visible:
        ``True`` when the latest committed assessment is already the
        externally visible bundle (i.e. ``externally_visible_assessment_id``
        on the proposition row matches the latest ``assessment_id``).
    schema_version:
        Fixed at :data:`RECOVERY_CHECKPOINT_SCHEMA_VERSION`.
    """

    proposition_id: str
    assessment_committed: bool
    assessment_id: str | None
    externally_visible: bool
    schema_version: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_proposition_checkpoint(
    *,
    proposition_id: str,
    assessment_repo: AssessmentRepository,
    proposition_repo: PropositionRepository,
) -> PropositionRecoveryCheckpoint:
    """Return a read-only checkpoint describing pipeline progress.

    Parameters
    ----------
    proposition_id:
        The proposition to inspect.
    assessment_repo:
        Repository for assessment snapshots.
    proposition_repo:
        Repository for propositions (needed to read the publish pointer).

    Returns
    -------
    PropositionRecoveryCheckpoint
    """
    latest = assessment_repo.get_latest(proposition_id)
    assessment_committed = latest is not None
    latest_assessment_id: str | None = latest["assessment_id"] if latest is not None else None

    externally_visible = False
    if assessment_committed:
        proposition = proposition_repo.get(proposition_id)
        if proposition is not None:
            visible_id = proposition.get("externally_visible_assessment_id")
            externally_visible = visible_id is not None and visible_id == latest_assessment_id

    return PropositionRecoveryCheckpoint(
        proposition_id=proposition_id,
        assessment_committed=assessment_committed,
        assessment_id=latest_assessment_id,
        externally_visible=externally_visible,
        schema_version=RECOVERY_CHECKPOINT_SCHEMA_VERSION,
    )


def recover_proposition_pipeline(
    *,
    session_id: str,
    proposition_id: str,
    trigger_finding_ids: list[str],
    proposition_repo: PropositionRepository,
    assessment_repo: AssessmentRepository,
    gap_repo: EvidenceGapRepository,
    inference_record_repo: InferenceRecordRepository,
    finding_repo: FindingRepository,
    proposal_repo: ActionProposalRepository,
) -> PropositionPipelineResult:
    """Resume the downstream pipeline from whichever stage is incomplete.

    Recovery strategy (per ``runtime-lifecycle.md §Stage recovery rules``):

    - **Already published** (checkpoint.externally_visible is ``True``):
      Return a noop result without touching the DB.
    - **Assessment committed, not yet published**:
      Skip recompute; run proposal refresh + publish switch only.
    - **No assessment committed**:
      Run the full pipeline: recompute → proposal refresh → publish switch.

    All paths are safe to call multiple times — the underlying stages are
    idempotent (canonical-diff gate in recompute; noop in publish switch).

    Parameters
    ----------
    session_id:
        Session that owns the canonical objects.
    proposition_id:
        The proposition to recover.
    trigger_finding_ids:
        Committed finding IDs that should drive any re-run.  Used only on
        the full-pipeline path.
    proposition_repo, assessment_repo, gap_repo, inference_record_repo,
    finding_repo, proposal_repo:
        Repository dependencies.

    Returns
    -------
    PropositionPipelineResult
        Same shape as the orchestrator's per-proposition result.  ``error``
        is ``None`` on success.
    """
    checkpoint = get_proposition_checkpoint(
        proposition_id=proposition_id,
        assessment_repo=assessment_repo,
        proposition_repo=proposition_repo,
    )

    # ------------------------------------------------------------------
    # Case 1 — fully up-to-date: noop
    # ------------------------------------------------------------------
    if checkpoint["externally_visible"]:
        logger.debug(
            "recovery noop for proposition %s: already externally visible (assessment %s)",
            proposition_id,
            checkpoint["assessment_id"],
        )
        return PropositionPipelineResult(
            proposition_id=proposition_id,
            recompute_result=None,
            proposal_result=None,
            publish_result=None,
            error=None,
        )

    # ------------------------------------------------------------------
    # Case 2 — assessment committed, publish not yet complete
    # ------------------------------------------------------------------
    if checkpoint["assessment_committed"]:
        committed_assessment_id: str = checkpoint["assessment_id"]  # type: ignore[assignment]
        logger.debug(
            "recovery for proposition %s: assessment %s committed, resuming from proposal refresh",
            proposition_id,
            committed_assessment_id,
        )
        try:
            # Restore the original proposal_context from a committed proposal so that
            # action_proposal_id hashes (which include proposal_context) remain
            # identical to the original run.  Fall back to the default only when no
            # proposals have been committed for this assessment yet.
            existing_proposals = proposal_repo.list_by_assessment(
                session_id, committed_assessment_id
            )
            proposal_context: dict[str, Any] = (
                existing_proposals[0]["proposal_context_json"]
                if existing_proposals
                else _DEFAULT_PROPOSAL_CONTEXT
            )

            proposal_result: ProposalRefreshResult = run_action_proposal_refresh(
                session_id=session_id,
                proposition_id=proposition_id,
                latest_assessment_id=committed_assessment_id,
                proposal_context=proposal_context,
                proposal_repo=proposal_repo,
                assessment_repo=assessment_repo,
                gap_repo=gap_repo,
            )
            publish_result: PublishSwitchResult = execute_publish_switch(
                session_id=session_id,
                proposition_id=proposition_id,
                candidate_assessment_id=committed_assessment_id,
                assessment_repo=assessment_repo,
                proposition_repo=proposition_repo,
            )
            return PropositionPipelineResult(
                proposition_id=proposition_id,
                recompute_result=None,
                proposal_result=proposal_result,
                publish_result=publish_result,
                error=None,
            )
        except Exception as exc:
            logger.warning(
                "recovery (partial path) failed for proposition %s: %s",
                proposition_id,
                exc,
                exc_info=True,
            )
            return PropositionPipelineResult(
                proposition_id=proposition_id,
                recompute_result=None,
                proposal_result=None,
                publish_result=None,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Case 3 — nothing committed: run full pipeline
    # ------------------------------------------------------------------
    logger.debug(
        "recovery for proposition %s: no assessment committed, running full pipeline",
        proposition_id,
    )
    return run_single_proposition_pipeline(
        session_id=session_id,
        proposition_id=proposition_id,
        trigger_finding_ids=trigger_finding_ids,
        proposition_repo=proposition_repo,
        assessment_repo=assessment_repo,
        gap_repo=gap_repo,
        inference_record_repo=inference_record_repo,
        finding_repo=finding_repo,
        proposal_repo=proposal_repo,
    )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "RECOVERY_CHECKPOINT_SCHEMA_VERSION",
    "PropositionRecoveryCheckpoint",
    "get_proposition_checkpoint",
    "recover_proposition_pipeline",
]
