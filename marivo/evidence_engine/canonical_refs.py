"""Canonical ref and membership TypedDicts for the evidence pipeline.

This module defines the typed reference structures used across all canonical
objects in the evidence pipeline:

    artifact -> finding -> proposition -> assessment -> action proposal

All TypedDicts correspond directly to the schemas defined in
``docs/analysis/evidence-engine/schemas/``.

## Membership vs. Lineage Invariants (Phase 4a-3)

These structures enforce the strict separation between:

- **Creation-time lineage refs** (e.g. ``PropositionSeedRef``):
  Written once when the object is created; never updated to reflect runtime
  evidence membership.  ``PropositionSeedRef`` must NOT be used to track live
  supporting / opposing evidence — that responsibility belongs to assessment.

- **Runtime snapshot membership** (e.g. ``GapMembershipEntry``):
  Owned by an immutable ``assessment`` snapshot.  ``GapMembershipEntry.blocking``
  and ``GapMembershipEntry.severity`` are snapshot-owned classifications — they
  are NOT fields on ``EvidenceGap`` itself.  The same open gap can be re-classified
  across successive assessment snapshots.

- **Assessment-derived hard refs** (e.g. ``AssessmentRef``):
  Include ``snapshot_seq`` to unambiguously anchor to a single immutable snapshot.
  These are ``hard refs`` per ``graph-and-reference-semantics.md``.

## Graph-and-Reference-Semantics Summary

From ``docs/analysis/evidence-engine/graph-and-reference-semantics.md``:

- v1 canonical refs MUST be typed — no bare-string locators.
- Cross-session refs are forbidden by default.
- The canonical ref graph must be a DAG.
- ``assessment``-derived refs (AssessmentRef inside action_proposals) are hard refs.
- ``seeded_by`` (PropositionSeedRef) is lineage — NOT equivalent to ``supports``.

See also: ``canonical_finding.py`` for finding-layer TypedDicts (FindingRef,
ArtifactItemRef, ArtifactItemRefRef, StepRef, FindingBase, etc.).
"""

from __future__ import annotations

from typing import Literal, TypedDict

from marivo.evidence_engine.canonical_finding import FindingRef

# ---------------------------------------------------------------------------
# Proposition-layer refs
# ---------------------------------------------------------------------------


class PropositionRef(TypedDict):
    """Canonical reference to a single proposition.

    Propositions are session-local; the ``session_id`` is required to keep the
    ref self-contained without implicit context.
    """

    session_id: str
    proposition_id: str


PropositionSeedRole = Literal["primary", "secondary", "context"]


class PropositionSeedRef(TypedDict):
    """Creation-time seed ref from a finding to a proposition.

    Invariant: this ref is written exactly once when the proposition is first
    registered.  It MUST NOT be updated to reflect runtime evidence.
    Specifically:

    - ``seed_finding_refs`` on a proposition encodes the ``seeded_by`` edge
      in the canonical graph.
    - ``supporting_finding_ids`` / ``opposing_finding_ids`` on an assessment
      encode runtime membership; they are a separate concept.

    ``role`` classifies the finding's contribution to the proposition's seed:
    - ``"primary"``:   the finding is the primary fact motivating the proposition.
    - ``"secondary"``: the finding provides supplementary seeding context.
    - ``"context"``:   the finding provides background / quality context only.
    """

    finding_ref: FindingRef
    role: PropositionSeedRole


class ArtifactLineageRef(TypedDict):
    """Lineage pointer from a proposition to an upstream artifact.

    Used in ``PropositionLineage.source_artifact_lineages``.

    Null semantics:
    - ``artifact_schema_version = None``: artifact contract does not expose a
      separate schema version.
    - ``extractor_version = None``: this lineage was not created via an
      independently versioned extractor (e.g. authored propositions).
    """

    artifact_id: str
    artifact_schema_version: str | None
    extractor_version: str | None


# ---------------------------------------------------------------------------
# Assessment-layer refs
# ---------------------------------------------------------------------------


class AssessmentRef(TypedDict):
    """Canonical reference to a single immutable assessment snapshot.

    ``snapshot_seq`` is included to anchor the ref to a specific snapshot — not
    just the latest.  This is a *hard ref*: it must be resolvable at read time;
    a dangling AssessmentRef is a canonical inconsistency.

    ``assessment_type`` is deliberately omitted from the ref; the caller
    resolves the type by reading the referenced assessment object.
    """

    assessment_id: str
    proposition_id: str
    snapshot_seq: int


class EvidenceGapRef(TypedDict):
    """Canonical reference to an evidence gap.

    ``proposition_id`` is included because gaps are proposition-scoped; this
    makes the ref self-contained without requiring outer context.
    """

    gap_id: str
    proposition_id: str


GapSeverity = Literal["low", "medium", "high", "critical"]


class GapMembershipEntry(TypedDict):
    """Snapshot-owned classification of a gap within one assessment snapshot.

    ``blocking`` and ``severity`` are OWNED by the assessment snapshot, NOT by
    the EvidenceGap object itself.  The same open gap can change these values
    across successive snapshots (which triggers a new superseding snapshot).

    Invariant: a single ``gap_ref`` may appear at most once per assessment
    snapshot.
    """

    gap_ref: EvidenceGapRef
    blocking: bool
    severity: GapSeverity


class InferenceRecordRef(TypedDict):
    """Canonical reference to a single inference record.

    Three-way binding: records are scoped to (proposition, assessment) so the
    ref carries all three IDs for unambiguous lookup.
    """

    inference_record_id: str
    proposition_id: str
    assessment_id: str


# ---------------------------------------------------------------------------
# Action proposal refs
# ---------------------------------------------------------------------------

SessionGoal = Literal[
    "explain_change",
    "validate_hypothesis",
    "triage_anomaly",
    "monitor_risk",
    "prepare_escalation",
    "other",
]

RiskBudget = Literal["minimal", "low", "medium", "high"]


class ProposalContext(TypedDict):
    """Policy / session context that shaped an action proposal.

    Invariant: any input that would change the proposal's generation or
    ordering result MUST appear here — not as an implicit runtime parameter.

    Null semantics:
    - ``session_goal = None``: proposal is not bound to an explicit session goal.
    - ``risk_budget = None``: no risk budget constraint applies.
    - ``policy_profile``:  required, even for the default profile.
    """

    session_goal: SessionGoal | None
    risk_budget: RiskBudget | None
    policy_profile: str


# ProposalContextRef is a discriminated union.  Each variant is a separate
# TypedDict keyed by ``kind``.  Callers use the ``kind`` field to determine
# which optional ref fields are present.


class PropositionContextRef(TypedDict):
    """ProposalContextRef variant: points to a proposition."""

    kind: Literal["proposition"]
    proposition_ref: PropositionRef


class AssessmentContextRef(TypedDict):
    """ProposalContextRef variant: points to an assessment snapshot."""

    kind: Literal["assessment"]
    assessment_ref: AssessmentRef


class FindingContextRef(TypedDict):
    """ProposalContextRef variant: points to a finding."""

    kind: Literal["finding"]
    finding_ref: FindingRef


class EvidenceGapContextRef(TypedDict):
    """ProposalContextRef variant: points to an evidence gap."""

    kind: Literal["evidence_gap"]
    gap_ref: EvidenceGapRef


# Union alias used in EscalateProposal.required_context_refs and similar lists.
ProposalContextRef = (
    PropositionContextRef | AssessmentContextRef | FindingContextRef | EvidenceGapContextRef
)

# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------

__all__ = [
    "ArtifactLineageRef",
    "AssessmentContextRef",
    "AssessmentRef",
    "EvidenceGapContextRef",
    "EvidenceGapRef",
    "FindingContextRef",
    "FindingRef",
    "GapMembershipEntry",
    "GapSeverity",
    "InferenceRecordRef",
    "ProposalContext",
    "ProposalContextRef",
    "PropositionContextRef",
    "PropositionRef",
    "PropositionSeedRef",
    "PropositionSeedRole",
    "RiskBudget",
    "SessionGoal",
]
