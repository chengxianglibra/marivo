"""Canonical Evidence Pipeline Downstream Orchestrator (Phase 4g-3).

Wires the full post-commit canonical pipeline:

    committed findings
        -> proposition seeding      (4e-3)
        -> assessment recompute     (4f-1 / 4f-2)
        -> action proposal refresh  (4g-1)
        -> publish switch           (4g-2)
                |
                v
       externally_visible bundle

Entry point: :func:`run_canonical_downstream`.

Call this immediately after :meth:`~marivo.runtime.runtime.MarivoRuntime.commit_artifact_with_extraction`
succeeds.  The function is synchronous; async scheduling is a Phase 4h / Phase 5 concern.

Design contracts:
  - ``docs/analysis/evidence-engine/runtime-pipeline.md``
  - ``docs/analysis/evidence-engine/runtime-lifecycle.md``

Phase: 4g-3
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from marivo.evidence_engine.assessment_evaluation_context import (
    build_assessment_evaluation_context,
)
from marivo.evidence_engine.assessment_recompute import (
    AssessmentRecomputeResult,
    make_assessment_id,
    recompute_proposition_assessment,
)
from marivo.evidence_engine.proposal_refresh_run import (
    ProposalRefreshResult,
    run_action_proposal_refresh,
)
from marivo.evidence_engine.proposition_seeding_run import (
    SeedingRunResult,
    SimpleMaterializationContext,
    run_system_seeded_propositions,
)
from marivo.evidence_engine.publish_switch import (
    PublishSwitchResult,
    execute_publish_switch,
)
from marivo.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from marivo.storage.metadata import MetadataStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

CANONICAL_DOWNSTREAM_SCHEMA_VERSION = "canonical_downstream_result.v1"

# Minimal proposal context used when no agent-authored context is available.
_DEFAULT_PROPOSAL_CONTEXT: dict[str, Any] = {
    "session_goal": None,
    "risk_budget": None,
    "policy_profile": "default",
}

# ---------------------------------------------------------------------------
# Public TypedDicts
# ---------------------------------------------------------------------------


class PropositionPipelineResult(TypedDict):
    """Per-proposition result within a canonical downstream run.

    Fields
    ------
    proposition_id:
        The proposition processed in this slot.
    recompute_result:
        Result from :func:`recompute_proposition_assessment`.  ``None`` if
        recompute raised an exception.
    proposal_result:
        Result from :func:`run_action_proposal_refresh`.  ``None`` when
        recompute was a no-op or raised.
    publish_result:
        Result from :func:`execute_publish_switch`.  ``None`` when proposal
        refresh was skipped or raised.
    error:
        Exception message if any stage raised for this proposition.
        ``None`` on success.
    """

    proposition_id: str
    recompute_result: AssessmentRecomputeResult | None
    proposal_result: ProposalRefreshResult | None
    publish_result: PublishSwitchResult | None
    error: str | None


class CanonicalDownstreamResult(TypedDict):
    """Outcome of a :func:`run_canonical_downstream` call.

    Fields
    ------
    seeding_result:
        Result from the proposition seeding stage.
    proposition_results:
        One entry per ``affected_proposition_id`` from seeding.
    schema_version:
        Fixed at :data:`CANONICAL_DOWNSTREAM_SCHEMA_VERSION`.
    """

    seeding_result: SeedingRunResult
    proposition_results: list[PropositionPipelineResult]
    schema_version: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_canonical_downstream(
    *,
    session_id: str,
    trigger_finding_ids: list[str],
    finding_repo: FindingRepository,
    proposition_repo: PropositionRepository,
    assessment_repo: AssessmentRepository,
    gap_repo: EvidenceGapRepository,
    inference_record_repo: InferenceRecordRepository,
    proposal_repo: ActionProposalRepository,
    metadata_store: MetadataStore,
) -> CanonicalDownstreamResult:
    """Run the full canonical downstream pipeline for a set of trigger findings.

    Stages (in order):

    1. **Proposition seeding** — :func:`run_system_seeded_propositions` maps
       each committed finding to zero or more system-seeded propositions.
    2. **Assessment recompute** — for every affected proposition, assemble an
       :class:`~assessment_evaluation_context.AssessmentEvaluationContext`
       and run :func:`recompute_proposition_assessment`.
    3. **Proposal refresh** — when a new assessment snapshot was committed,
       run :func:`run_action_proposal_refresh` to materialise the v1 action
       proposal set.
    4. **Publish switch** — atomically advance the
       ``externally_visible_assessment_id`` pointer via
       :func:`execute_publish_switch`.

    Each proposition is processed independently.  An exception in one slot is
    caught and recorded in ``PropositionPipelineResult.error``; the remaining
    propositions continue.

    Parameters
    ----------
    session_id:
        Session that owns the trigger findings and resulting canonical objects.
    trigger_finding_ids:
        IDs of committed findings that entered the seeding pipeline.
    finding_repo, proposition_repo, assessment_repo, gap_repo,
    inference_record_repo, proposal_repo:
        Repository dependencies.
    metadata_store:
        Backing metadata store — used to construct
        :class:`SimpleMaterializationContext`.

    Returns
    -------
    CanonicalDownstreamResult
    """
    if not trigger_finding_ids:
        # Nothing to seed — return an empty shell without touching any repo.
        empty_seeding: SeedingRunResult = {
            "created_proposition_ids": [],
            "existing_proposition_ids": [],
            "affected_proposition_ids": [],
            "schema_version": "finding_proposition_seeding_run.v1",
        }
        return CanonicalDownstreamResult(
            seeding_result=empty_seeding,
            proposition_results=[],
            schema_version=CANONICAL_DOWNSTREAM_SCHEMA_VERSION,
        )

    # ------------------------------------------------------------------
    # Stage 1 — Proposition seeding
    # ------------------------------------------------------------------
    mat_ctx = SimpleMaterializationContext(finding_repo, metadata_store)
    seeding_result = run_system_seeded_propositions(
        session_id=session_id,
        trigger_finding_ids=trigger_finding_ids,
        proposition_repo=proposition_repo,
        finding_repo=finding_repo,
        ctx=mat_ctx,
    )

    affected_ids: list[str] = seeding_result["affected_proposition_ids"]
    proposition_results: list[PropositionPipelineResult] = []

    # ------------------------------------------------------------------
    # Stages 2–4 — per-proposition pipeline
    # ------------------------------------------------------------------
    for proposition_id in affected_ids:
        slot_result = _run_proposition_pipeline(
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
        proposition_results.append(slot_result)

    return CanonicalDownstreamResult(
        seeding_result=seeding_result,
        proposition_results=proposition_results,
        schema_version=CANONICAL_DOWNSTREAM_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_proposition_pipeline(
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
    """Run recompute → proposal refresh → publish switch for one proposition.

    Returns a :class:`PropositionPipelineResult` with an ``error`` field set
    on failure; never raises.
    """
    try:
        proposition = proposition_repo.get(proposition_id)
        if proposition is None:
            return PropositionPipelineResult(
                proposition_id=proposition_id,
                recompute_result=None,
                proposal_result=None,
                publish_result=None,
                error=f"proposition {proposition_id!r} not found after seeding",
            )

        # Pre-allocate a deterministic candidate assessment identity (Phase 4h-1).
        # Calling next_snapshot_seq here is safe: nothing has been committed for
        # this proposition in this pipeline run yet, so the value returned by the
        # internal call inside recompute_proposition_assessment will be identical.
        seq = assessment_repo.next_snapshot_seq(proposition_id)
        candidate_assessment_id = make_assessment_id(session_id, proposition_id, seq)

        # Stage 2a — evaluation context
        ctx = build_assessment_evaluation_context(
            session_id=session_id,
            proposition_id=proposition_id,
            proposition=proposition,
            candidate_assessment_id=candidate_assessment_id,
            trigger_finding_ids=trigger_finding_ids,
            assessment_repo=assessment_repo,
            gap_repo=gap_repo,
            finding_repo=finding_repo,
            inference_record_repo=inference_record_repo,
        )

        # Stage 2b — assessment recompute
        recompute_result = recompute_proposition_assessment(
            ctx=ctx,
            assessment_repo=assessment_repo,
            gap_repo=gap_repo,
            inference_record_repo=inference_record_repo,
            finding_repo=finding_repo,
        )

        if not recompute_result["created"]:
            # No canonical diff → no new assessment → skip downstream stages.
            return PropositionPipelineResult(
                proposition_id=proposition_id,
                recompute_result=recompute_result,
                proposal_result=None,
                publish_result=None,
                error=None,
            )

        committed_assessment_id: str = recompute_result["assessment_id"]  # type: ignore[assignment]

        # Stage 3 — action proposal refresh
        proposal_result = run_action_proposal_refresh(
            session_id=session_id,
            proposition_id=proposition_id,
            latest_assessment_id=committed_assessment_id,
            proposal_context=_DEFAULT_PROPOSAL_CONTEXT,
            proposal_repo=proposal_repo,
            assessment_repo=assessment_repo,
            gap_repo=gap_repo,
        )

        # Stage 4 — publish switch (atomic visibility advance)
        publish_result = execute_publish_switch(
            session_id=session_id,
            proposition_id=proposition_id,
            candidate_assessment_id=committed_assessment_id,
            assessment_repo=assessment_repo,
            proposition_repo=proposition_repo,
        )

        return PropositionPipelineResult(
            proposition_id=proposition_id,
            recompute_result=recompute_result,
            proposal_result=proposal_result,
            publish_result=publish_result,
            error=None,
        )

    except Exception as exc:
        logger.warning(
            "canonical downstream failed for proposition %s: %s",
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


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------


def run_single_proposition_pipeline(
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
    """Public entry point for single-proposition pipeline execution.

    Delegates to :func:`_run_proposition_pipeline`.  Exposed in ``__all__``
    so that external modules (e.g. ``replay_recovery``) can import it without
    coupling to a private symbol.
    """
    return _run_proposition_pipeline(
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


__all__ = [
    "CANONICAL_DOWNSTREAM_SCHEMA_VERSION",
    "CanonicalDownstreamResult",
    "PropositionPipelineResult",
    "run_canonical_downstream",
    "run_single_proposition_pipeline",
]
