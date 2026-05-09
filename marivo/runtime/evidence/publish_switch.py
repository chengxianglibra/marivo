"""Proposition-local Publish Switch runtime (Phase 4g-2).

Implements the atomic visibility layer that sits between the proposal refresh
stage and the canonical read surface:

    ... -> assessment (committed) -> proposal refresh -> publish switch
                                                              |
                                                              v
                                                 externally_visible bundle

Two public entry-points:

* :func:`execute_publish_switch` — atomically advances the
  ``externally_visible_assessment_id`` pointer on a proposition row to the
  supplied ``candidate_assessment_id``.  Idempotent: same assessment_id a
  second time is a no-op.  Rejects downgrade attempts (lower snapshot_seq).

* :func:`assemble_externally_visible_bundle` — read-only: assembles the
  proposition-local bundle from the *published* assessment pointer rather
  than the latest committed assessment.  Returns ``None`` when no publish
  switch has been executed yet.

Design contracts:
  - ``docs/analysis/evidence-engine/runtime-lifecycle.md`` §4, §Serialisation
  - ``docs/analysis/evidence-engine/read-surfaces.md`` §Shared Invariants
  - ``docs/analysis/evidence-engine/schemas/state-surface-schema.md``

Phase: 4g-2
"""

from __future__ import annotations

from typing import TypedDict

from marivo.adapters.server.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from marivo.runtime.evidence.proposal_refresh import (
    PublishReadyBundle,
    assemble_bundle_from_assessment,
)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

PUBLISH_SWITCH_SCHEMA_VERSION = "publish_switch_result.v1"

# ---------------------------------------------------------------------------
# Public TypedDicts
# ---------------------------------------------------------------------------


class PublishSwitchResult(TypedDict):
    """Outcome of a single :func:`execute_publish_switch` call.

    Fields
    ------
    proposition_id:
        The proposition whose publish pointer was (or was already) set.
    assessment_id:
        The assessment_id that is now the externally visible pointer.
    created:
        ``True`` when the pointer was actually updated in this call.
        ``False`` on a no-op (same assessment already published).
    noop:
        ``True`` when the canonical publish state was identical to the
        previously published state; no UPDATE was executed.
    schema_version:
        Fixed at :data:`PUBLISH_SWITCH_SCHEMA_VERSION`.
    """

    proposition_id: str
    assessment_id: str
    created: bool
    noop: bool
    schema_version: str


# ---------------------------------------------------------------------------
# Public API — publish switch
# ---------------------------------------------------------------------------


def execute_publish_switch(
    *,
    session_id: str,
    proposition_id: str,
    candidate_assessment_id: str,
    assessment_repo: AssessmentRepository,
    proposition_repo: PropositionRepository,
) -> PublishSwitchResult:
    """Atomically advance the externally visible pointer to *candidate_assessment_id*.

    The switch is a single ``UPDATE`` statement on the ``propositions`` table —
    inherently crash-safe in SQLite.  After the switch completes, any call to
    :func:`assemble_externally_visible_bundle` will use the new assessment as
    its read anchor.

    **Caller responsibility**: run :func:`~proposal_refresh_run.run_action_proposal_refresh`
    against *candidate_assessment_id* before calling this function.  The publish
    switch does not independently re-validate the proposal set.

    Parameters
    ----------
    session_id:
        Session owning the proposition.
    proposition_id:
        The proposition to publish.
    candidate_assessment_id:
        The assessment that should become externally visible.
    assessment_repo, proposition_repo:
        Repository dependencies.

    Returns
    -------
    PublishSwitchResult

    Raises
    ------
    ValueError
        If the proposition does not exist, the assessment does not exist,
        either object belongs to a different session/proposition, or the
        candidate has a lower ``snapshot_seq`` than the currently published
        assessment (downgrade rejected).
    """
    # ------------------------------------------------------------------
    # Guard: proposition must exist and belong to session
    # ------------------------------------------------------------------
    proposition = proposition_repo.get(proposition_id)
    if proposition is None:
        raise ValueError(
            f"proposition_id={proposition_id!r} not found; "
            "publish switch requires an existing proposition."
        )
    if proposition["session_id"] != session_id:
        raise ValueError(
            f"proposition {proposition_id!r} belongs to session "
            f"{proposition['session_id']!r}, not {session_id!r}."
        )

    # ------------------------------------------------------------------
    # Guard: candidate assessment must exist and belong to this proposition
    # ------------------------------------------------------------------
    candidate = assessment_repo.get(candidate_assessment_id)
    if candidate is None:
        raise ValueError(
            f"candidate_assessment_id={candidate_assessment_id!r} not found; "
            "publish switch requires a committed assessment."
        )
    if candidate["proposition_id"] != proposition_id:
        raise ValueError(
            f"assessment {candidate_assessment_id!r} belongs to proposition "
            f"{candidate['proposition_id']!r}, not {proposition_id!r}."
        )
    if candidate["session_id"] != session_id:
        raise ValueError(
            f"assessment {candidate_assessment_id!r} belongs to session "
            f"{candidate['session_id']!r}, not {session_id!r}."
        )

    # ------------------------------------------------------------------
    # Idempotency: same assessment already published → noop
    # ------------------------------------------------------------------
    current_visible_id: str | None = proposition.get("externally_visible_assessment_id")
    if current_visible_id == candidate_assessment_id:
        return PublishSwitchResult(
            proposition_id=proposition_id,
            assessment_id=candidate_assessment_id,
            created=False,
            noop=True,
            schema_version=PUBLISH_SWITCH_SCHEMA_VERSION,
        )

    # ------------------------------------------------------------------
    # Atomic switch: conditional UPDATE enforces anti-downgrade at DB level.
    # Returns False if the current pointer already has a snapshot_seq >=
    # the candidate's (downgrade rejected) or if the proposition row
    # disappeared between the guard above and this UPDATE.
    # ------------------------------------------------------------------
    candidate_snapshot_seq: int = int(candidate["snapshot_seq"])
    updated = proposition_repo.set_externally_visible_assessment(
        proposition_id, candidate_assessment_id, candidate_snapshot_seq
    )
    if not updated:
        raise ValueError(
            f"publish switch rejected: candidate assessment "
            f"{candidate_assessment_id!r} (snapshot_seq={candidate_snapshot_seq}) "
            f"did not advance the publish pointer on proposition "
            f"{proposition_id!r} — downgrade or concurrent write detected."
        )

    return PublishSwitchResult(
        proposition_id=proposition_id,
        assessment_id=candidate_assessment_id,
        created=True,
        noop=False,
        schema_version=PUBLISH_SWITCH_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Public API — externally visible bundle assembler
# ---------------------------------------------------------------------------


def assemble_externally_visible_bundle(
    *,
    session_id: str,
    proposition_id: str,
    assessment_repo: AssessmentRepository,
    gap_repo: EvidenceGapRepository,
    finding_repo: FindingRepository,
    proposal_repo: ActionProposalRepository,
    inference_record_repo: InferenceRecordRepository,
    proposition_repo: PropositionRepository,
) -> PublishReadyBundle | None:
    """Assemble the externally visible proposition-local bundle (read-only).

    Unlike :func:`~proposal_refresh_run.assemble_publish_ready_bundle` which
    always reads the *latest* committed assessment, this function reads from
    the ``externally_visible_assessment_id`` pointer set by
    :func:`execute_publish_switch`.

    Returns ``None`` when no publish switch has been executed yet for this
    proposition (``externally_visible_assessment_id IS NULL``).  The
    proposition exists but has no externally visible bundle.

    Parameters
    ----------
    session_id, proposition_id:
        Canonical identifiers.
    assessment_repo, gap_repo, finding_repo, proposal_repo,
    inference_record_repo, proposition_repo:
        Read dependencies.

    Returns
    -------
    PublishReadyBundle | None
    """
    # ------------------------------------------------------------------
    # Load proposition + validate ownership
    # ------------------------------------------------------------------
    proposition = proposition_repo.get(proposition_id)
    if proposition is None:
        raise ValueError(f"proposition_id={proposition_id!r} not found.")
    if proposition["session_id"] != session_id:
        raise ValueError(
            f"proposition {proposition_id!r} belongs to session "
            f"{proposition['session_id']!r}, not {session_id!r}."
        )

    # ------------------------------------------------------------------
    # Read publish pointer — None means not yet published
    # ------------------------------------------------------------------
    visible_assessment_id: str | None = proposition.get("externally_visible_assessment_id")
    if visible_assessment_id is None:
        return None

    # ------------------------------------------------------------------
    # Load the published assessment
    # ------------------------------------------------------------------
    assessment = assessment_repo.get(visible_assessment_id)
    if assessment is None:
        # Pointer exists but assessment is missing — dangling ref.
        # Surface as ValueError rather than silently returning None so
        # callers can distinguish "never published" from "data inconsistency".
        raise ValueError(
            f"externally_visible_assessment_id={visible_assessment_id!r} "
            f"is set on proposition {proposition_id!r} but the assessment "
            "row cannot be found.  The pointer may be dangling."
        )

    # ------------------------------------------------------------------
    # Assemble bundle from the published assessment (not latest)
    # ------------------------------------------------------------------
    return assemble_bundle_from_assessment(
        session_id=session_id,
        proposition_id=proposition_id,
        proposition=proposition,
        assessment=assessment,
        gap_repo=gap_repo,
        finding_repo=finding_repo,
        proposal_repo=proposal_repo,
        inference_record_repo=inference_record_repo,
    )


__all__ = [
    "PUBLISH_SWITCH_SCHEMA_VERSION",
    "PublishSwitchResult",
    "assemble_externally_visible_bundle",
    "execute_publish_switch",
]
