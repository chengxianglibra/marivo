"""Action Proposal Refresh 与 Publish-Ready Bundle (Phase 4g-1).

Implements the proposal policy engine layer that sits immediately after
a committed ``latest_assessment`` in the canonical abstraction chain:

    artifact → finding → proposition → assessment → action proposal

Two public entry-points:

* :func:`run_action_proposal_refresh` — given a committed ``latest_assessment``,
  generates deterministic action proposal candidates and persists them only
  when the canonical proposal set differs from the current committed set
  (no-op otherwise).

* :func:`assemble_publish_ready_bundle` — read-only: assembles the
  proposition-local ``PublishReadyBundle`` that groups together the
  proposition, its latest assessment, the live evidence closure, and the
  current committed proposals.  Returns the bundle as a pure Python dict;
  does NOT write any canonical state.

Design contracts:
  - ``docs/analysis/evidence-engine/proposal-policy-engine.md``
  - ``docs/analysis/evidence-engine/schemas/action-proposal.md``
  - ``docs/analysis/evidence-engine/runtime-pipeline.md``

Phase: 4g-1
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypedDict, cast

from marivo.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

REFRESH_SCHEMA_VERSION = "proposal_refresh_result.v1"
BUNDLE_SCHEMA_VERSION = "publish_ready_bundle.v1"

# ---------------------------------------------------------------------------
# assessment_type → recommended investigation step family (v1)
# ---------------------------------------------------------------------------

_ASSESSMENT_TO_STEP_FAMILY: dict[str, str] = {
    "change_assessment": "compare",
    "decomposition_assessment": "decompose",
    "anomaly_assessment": "detect",
    "correlation_assessment": "correlate",
    "test_hypothesis_assessment": "test",
    "forecast_assessment": "forecast",
}

# ---------------------------------------------------------------------------
# Public TypedDicts
# ---------------------------------------------------------------------------


class ProposalRefreshResult(TypedDict):
    """Outcome of a single :func:`run_action_proposal_refresh` call.

    Fields
    ------
    primary_assessment_id:
        The ``assessment_id`` that was the authority input for this refresh.
    proposal_ids:
        Sorted list of ``action_proposal_id`` values in the committed canonical
        proposal set after this refresh (empty list is valid).
    materialized_count:
        Number of *new* proposal snapshots written in this run.  Zero on
        no-op runs (``noop=True``) and on runs that produced an empty set.
    noop:
        ``True`` when the canonical proposal set was identical to the
        previously committed set; no new rows were written.
    schema_version:
        Fixed at :data:`REFRESH_SCHEMA_VERSION`.
    """

    primary_assessment_id: str
    proposal_ids: list[str]
    materialized_count: int
    noop: bool
    schema_version: str


class LiveClosure(TypedDict):
    """Evidence closure referenced by a :class:`PublishReadyBundle`.

    All lists are ordered and derived from the ``latest_assessment`` snapshot:

    * ``supporting_findings`` — findings in ``supporting_finding_ids``
    * ``opposing_findings``   — findings in ``opposing_finding_ids``
    * ``open_gaps``           — EvidenceGap rows from the assessment's
      ``gap_memberships_json``, hydrated and filtered to ``status='open'``.
      Anchored to this specific assessment snapshot; does NOT include gaps
      opened by later (unpublished) assessments.
    * ``applied_inference_records`` — InferenceRecord rows for the assessment
    """

    supporting_findings: list[dict[str, Any]]
    opposing_findings: list[dict[str, Any]]
    open_gaps: list[dict[str, Any]]
    applied_inference_records: list[dict[str, Any]]


class PublishReadyBundle(TypedDict):
    """Proposition-local publish-ready bundle.

    Assembles the complete, self-consistent snapshot of a proposition's
    current state that can be atomically surfaced to consumers.

    ``publish_ready`` means the bundle is fully assembled; it does NOT
    mean it has been switched to externally visible (that is 4g-2's
    responsibility).

    Fields
    ------
    session_id, proposition_id:
        Canonical identifiers.
    proposition:
        The proposition row (deserialized JSON fields).
    latest_assessment:
        The committed latest assessment snapshot (highest ``snapshot_seq``).
    live_closure:
        Evidence objects referenced by ``latest_assessment``.
    action_proposals:
        Committed action proposals for ``latest_assessment``, ordered by
        ``priority_rank ASC, created_at ASC, action_proposal_id ASC``.
    schema_version:
        Fixed at :data:`BUNDLE_SCHEMA_VERSION`.
    """

    session_id: str
    proposition_id: str
    proposition: dict[str, Any]
    latest_assessment: dict[str, Any]
    live_closure: LiveClosure
    action_proposals: list[dict[str, Any]]
    schema_version: str


# ---------------------------------------------------------------------------
# Internal helpers — identity
# ---------------------------------------------------------------------------


def _make_proposal_id(
    session_id: str,
    action_kind: str,
    primary_assessment_ref: dict[str, Any],
    target_proposition_ref: dict[str, Any],
    proposal_context: dict[str, Any],
    payload_semantic: dict[str, Any],
) -> str:
    """Derive a stable ``action_proposal_id`` from canonical identity inputs.

    Inputs excluded from identity (per spec):
    - ``schema_version`` / ``policy_version`` (when only impacting ordering)
    - ``priority_rank``
    - explanation text / ``rationale.summary``
    - ``created_at``

    ``target_proposition_ref`` is included per ``proposal-policy-engine.md`` §Identity.
    In practice ``primary_assessment_ref.proposition_id`` carries the same
    information, but including both keeps the hash self-documenting.

    Format: ``"prop_ap_"`` + first 24 hex chars of SHA-256.
    """
    raw = json.dumps(
        {
            "session_id": session_id,
            "action_kind": action_kind,
            "primary_assessment_ref": primary_assessment_ref,
            "target_proposition_ref": target_proposition_ref,
            "proposal_context": proposal_context,
            "payload_semantic": payload_semantic,
        },
        sort_keys=True,
    )
    return "prop_ap_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Internal helpers — candidate generation (v1 deterministic policy)
# ---------------------------------------------------------------------------

# Gap kinds that produce a ``validate`` proposal rather than ``investigate``.
_VALIDATE_GAP_KINDS: frozenset[str] = frozenset(
    {"data_quality_risk", "comparability_risk", "resolution_conflict"}
)


def _make_investigate_payload_semantic(
    closes_gap_refs: list[dict[str, Any]],
    step_family: str,
    proposition_id: str,
) -> dict[str, Any]:
    """Return the identity-relevant payload fields for an investigate proposal."""
    return {
        "next_intent": {
            "intent_family": step_family,
            "proposition_id": proposition_id,
            "schema_version": "intent_ref.v1",
        },
        "closes_gap_refs": sorted(closes_gap_refs, key=lambda r: r["gap_id"]),
    }


def _make_validate_payload_semantic(
    closes_gap_refs: list[dict[str, Any]],
    validation_target: str,
) -> dict[str, Any]:
    """Return the identity-relevant payload fields for a validate proposal."""
    return {
        "validation_target": validation_target,
        "closes_gap_refs": sorted(closes_gap_refs, key=lambda r: r["gap_id"]),
    }


def _priority_axes_for(
    action_kind: str,
    is_blocking: bool,
    status: str,
) -> tuple[dict[str, Any], float]:
    """Return ``(priority_axes, priority_rank)`` for a candidate.

    v1 deterministic policy table:
    - Blocking gap + investigate  → high/low/high/high,  rank=1.0
    - Blocking gap + validate     → medium/low/high/medium, rank=2.0
    - mixed + validate (no block) → medium/low/medium/medium, rank=3.0
    - insufficient, no gaps       → medium/low/low/medium,  rank=4.0
    """
    if is_blocking and action_kind == "investigate":
        axes = {
            "information_gain": "high",
            "execution_cost": "low",
            "urgency": "high",
            "expected_impact": "high",
        }
        rank = 1.0
    elif is_blocking and action_kind == "validate":
        axes = {
            "information_gain": "medium",
            "execution_cost": "low",
            "urgency": "high",
            "expected_impact": "medium",
        }
        rank = 2.0
    elif status == "mixed":
        axes = {
            "information_gain": "medium",
            "execution_cost": "low",
            "urgency": "medium",
            "expected_impact": "medium",
        }
        rank = 3.0
    else:
        # insufficient with no gaps
        axes = {
            "information_gain": "medium",
            "execution_cost": "low",
            "urgency": "low",
            "expected_impact": "medium",
        }
        rank = 4.0
    return axes, rank


def _driver_tokens_for(
    action_kind: str,
    is_blocking: bool,
    gap_kind: str | None,
    status: str,
) -> list[str]:
    tokens: list[str] = [f"assessment_status:{status}"]
    if is_blocking:
        tokens.append("blocking_gap_present")
    if gap_kind is not None:
        tokens.append(f"gap_kind:{gap_kind}")
    tokens.append(f"action_kind:{action_kind}")
    return sorted(tokens)


def _gap_ref_from_membership(membership: dict[str, Any]) -> dict[str, Any]:
    return cast("dict[str, Any]", membership["gap_ref"])  # {gap_id, proposition_id}


def _generate_candidate_proposals(
    *,
    session_id: str,
    proposition_id: str,
    assessment: dict[str, Any],
    gap_memberships: list[dict[str, Any]],
    gap_repo: EvidenceGapRepository,
    proposal_context: dict[str, Any],
    policy_version: str,
) -> list[dict[str, Any]]:
    """Apply v1 deterministic rules to produce candidate proposal dicts.

    Each returned dict is ready to be committed via
    :meth:`ActionProposalRepository.create` (all JSON fields are already
    ``json.dumps``-serialized strings).

    Generation order (determines ``priority_rank``):
    1. One proposal per open *blocking* gap (investigate or validate by gap_kind)
    2. If ``status=mixed`` and no blocking gaps → one validate proposal
    3. If ``status=insufficient`` and no gap_memberships at all → one investigate

    Candidates are deduplicated by identity before return.
    Empty set is a valid result.
    """
    assessment_id: str = assessment["assessment_id"]
    assessment_type: str = assessment["assessment_type"]
    snapshot_seq: int = assessment["snapshot_seq"]
    status: str = assessment["status"]

    step_family = _ASSESSMENT_TO_STEP_FAMILY.get(assessment_type, "observe")

    primary_assessment_ref = {
        "assessment_id": assessment_id,
        "proposition_id": proposition_id,
        "snapshot_seq": snapshot_seq,
    }
    target_proposition_ref = {
        "session_id": session_id,
        "proposition_id": proposition_id,
    }

    # Deduplicate by proposal_id
    seen_ids: set[str] = set()
    candidates: list[dict[str, Any]] = []

    # ----------------------------------------------------------------
    # Rule 1: one proposal per open blocking gap
    # ----------------------------------------------------------------
    for membership in gap_memberships:
        if not membership.get("blocking"):
            continue
        gap_ref = _gap_ref_from_membership(membership)
        gap_row = gap_repo.get(gap_ref["gap_id"])
        gap_kind: str = gap_row["gap_kind"] if gap_row else "missing_rule_precondition"

        if gap_kind in _VALIDATE_GAP_KINDS:
            action_kind = "validate"
            validation_target = (
                "data_quality_risk"
                if gap_kind == "data_quality_risk"
                else "comparability_risk"
                if gap_kind == "comparability_risk"
                else "supporting_evidence"
            )
            payload_semantic = _make_validate_payload_semantic(
                closes_gap_refs=[gap_ref], validation_target=validation_target
            )
        else:
            # missing_rule_precondition and others → investigate
            action_kind = "investigate"
            payload_semantic = _make_investigate_payload_semantic(
                closes_gap_refs=[gap_ref],
                step_family=step_family,
                proposition_id=proposition_id,
            )

        proposal_id = _make_proposal_id(
            session_id=session_id,
            action_kind=action_kind,
            primary_assessment_ref=primary_assessment_ref,
            target_proposition_ref=target_proposition_ref,
            proposal_context=proposal_context,
            payload_semantic=payload_semantic,
        )
        if proposal_id in seen_ids:
            continue
        seen_ids.add(proposal_id)

        axes, rank = _priority_axes_for(action_kind, is_blocking=True, status=status)
        driver_tokens = _driver_tokens_for(action_kind, True, gap_kind, status)

        # Build the full payload dict
        if action_kind == "investigate":
            payload = {
                "next_intent": payload_semantic["next_intent"],
                "expected_output": "finding_set",
                "closes_gap_refs": payload_semantic["closes_gap_refs"],
            }
        else:
            # action_kind == "validate": payload_semantic["validation_target"] is always set
            payload = {
                "next_intent": {
                    "intent_family": step_family,
                    "proposition_id": proposition_id,
                    "schema_version": "intent_ref.v1",
                },
                "validation_target": payload_semantic["validation_target"],
                "closes_gap_refs": payload_semantic["closes_gap_refs"],
            }

        rationale = {
            "summary": f"{action_kind} proposal for {assessment_type}",
            "driver_tokens": driver_tokens,
            "served_gap_refs": [gap_ref],
            "expected_assessment_outcomes": ["resolve_gap"],
            "notes": [],
        }

        candidates.append(
            _build_proposal_row(
                action_proposal_id=proposal_id,
                session_id=session_id,
                action_kind=action_kind,
                primary_assessment_ref=primary_assessment_ref,
                target_proposition_ref=target_proposition_ref,
                proposal_context=proposal_context,
                axes=axes,
                rank=rank,
                rationale=rationale,
                payload=payload,
                policy_version=policy_version,
            )
        )

    # ----------------------------------------------------------------
    # Rule 2: status=mixed with no blocking gaps → validate
    # ----------------------------------------------------------------
    has_blocking = any(m.get("blocking") for m in gap_memberships)
    if status == "mixed" and not has_blocking:
        action_kind = "validate"
        validation_target = "supporting_evidence"
        payload_semantic = _make_validate_payload_semantic(
            closes_gap_refs=[], validation_target=validation_target
        )
        proposal_id = _make_proposal_id(
            session_id=session_id,
            action_kind=action_kind,
            primary_assessment_ref=primary_assessment_ref,
            target_proposition_ref=target_proposition_ref,
            proposal_context=proposal_context,
            payload_semantic=payload_semantic,
        )
        if proposal_id not in seen_ids:
            seen_ids.add(proposal_id)
            axes, rank = _priority_axes_for(action_kind, is_blocking=False, status=status)
            driver_tokens = _driver_tokens_for(action_kind, False, None, status)
            payload = {
                "next_intent": {
                    "intent_family": step_family,
                    "proposition_id": proposition_id,
                    "schema_version": "intent_ref.v1",
                },
                "validation_target": validation_target,  # always set: Rule 2 only runs when action_kind=="validate"
                "closes_gap_refs": [],
            }
            rationale = {
                "summary": f"validate proposal for mixed {assessment_type}",
                "driver_tokens": driver_tokens,
                "served_gap_refs": [],
                "expected_assessment_outcomes": ["resolve_conflicting_evidence"],
                "notes": [],
            }
            candidates.append(
                _build_proposal_row(
                    action_proposal_id=proposal_id,
                    session_id=session_id,
                    action_kind=action_kind,
                    primary_assessment_ref=primary_assessment_ref,
                    target_proposition_ref=target_proposition_ref,
                    proposal_context=proposal_context,
                    axes=axes,
                    rank=rank,
                    rationale=rationale,
                    payload=payload,
                    policy_version=policy_version,
                )
            )

    # ----------------------------------------------------------------
    # Rule 3: status=insufficient with no gap_memberships → investigate
    # ----------------------------------------------------------------
    if status == "insufficient" and not gap_memberships:
        action_kind = "investigate"
        payload_semantic = _make_investigate_payload_semantic(
            closes_gap_refs=[], step_family=step_family, proposition_id=proposition_id
        )
        proposal_id = _make_proposal_id(
            session_id=session_id,
            action_kind=action_kind,
            primary_assessment_ref=primary_assessment_ref,
            target_proposition_ref=target_proposition_ref,
            proposal_context=proposal_context,
            payload_semantic=payload_semantic,
        )
        if proposal_id not in seen_ids:
            seen_ids.add(proposal_id)
            axes, rank = _priority_axes_for(action_kind, is_blocking=False, status=status)
            driver_tokens = _driver_tokens_for(action_kind, False, None, status)
            payload = {
                "next_intent": payload_semantic["next_intent"],
                "expected_output": "finding_set",
                "closes_gap_refs": [],
            }
            rationale = {
                "summary": f"investigate proposal for insufficient {assessment_type}",
                "driver_tokens": driver_tokens,
                "served_gap_refs": [],
                "expected_assessment_outcomes": ["produce_candidate_findings"],
                "notes": [],
            }
            candidates.append(
                _build_proposal_row(
                    action_proposal_id=proposal_id,
                    session_id=session_id,
                    action_kind=action_kind,
                    primary_assessment_ref=primary_assessment_ref,
                    target_proposition_ref=target_proposition_ref,
                    proposal_context=proposal_context,
                    axes=axes,
                    rank=rank,
                    rationale=rationale,
                    payload=payload,
                    policy_version=policy_version,
                )
            )

    # Rules 2+3 both return early with empty set for supported/contradicted + no blocking gaps.

    return candidates


def _build_proposal_row(
    *,
    action_proposal_id: str,
    session_id: str,
    action_kind: str,
    primary_assessment_ref: dict[str, Any],
    target_proposition_ref: dict[str, Any],
    proposal_context: dict[str, Any],
    axes: dict[str, Any],
    rank: float,
    rationale: dict[str, Any],
    payload: dict[str, Any],
    policy_version: str,
) -> dict[str, Any]:
    """Build a dict ready for ``ActionProposalRepository.create``."""
    return {
        "action_proposal_id": action_proposal_id,
        "session_id": session_id,
        "action_kind": action_kind,
        "primary_assessment_ref_json": json.dumps(primary_assessment_ref, sort_keys=True),
        "related_assessment_refs_json": "[]",
        "target_proposition_ref_json": json.dumps(target_proposition_ref, sort_keys=True),
        "proposal_context_json": json.dumps(proposal_context, sort_keys=True),
        "priority_axes_json": json.dumps(axes, sort_keys=True),
        "priority_rank": rank,
        "rationale_json": json.dumps(rationale, sort_keys=True),
        "payload_json": json.dumps(payload, sort_keys=True),
        "policy_version": policy_version,
        "schema_version": "v1",
    }


# ---------------------------------------------------------------------------
# No-op detection helpers
# ---------------------------------------------------------------------------


def _proposal_set_key(proposal_ids: list[str]) -> str:
    """Stable identity string for a canonical proposal set (sorted ids)."""
    return json.dumps(sorted(proposal_ids))


# ---------------------------------------------------------------------------
# Public API — proposal refresh orchestrator
# ---------------------------------------------------------------------------


def run_action_proposal_refresh(
    *,
    session_id: str,
    proposition_id: str,
    latest_assessment_id: str,
    proposal_context: dict[str, Any],
    proposal_repo: ActionProposalRepository,
    assessment_repo: AssessmentRepository,
    gap_repo: EvidenceGapRepository,
    policy_version: str = "v1",
) -> ProposalRefreshResult:
    """Run the proposal policy engine for a single proposition.

    **Authority input**: a committed ``latest_assessment`` (identified by
    ``latest_assessment_id``).  Raises :exc:`ValueError` if that assessment
    does not exist or belongs to a different session / proposition.

    **Determinism**: same inputs → same ``proposal_ids`` and ``noop`` result.
    On no-op the function returns without writing any new rows.

    Parameters
    ----------
    session_id:
        Session owning the proposition.
    proposition_id:
        The proposition to refresh proposals for.
    latest_assessment_id:
        The committed assessment that drives candidate generation.  Must be
        the *latest* snapshot (highest ``snapshot_seq``) for
        ``proposition_id``; callers are responsible for selecting it via
        :meth:`AssessmentRepository.get_latest`.
    proposal_context:
        Explicit policy context dict — ``{session_goal, risk_budget,
        policy_profile}``.  ``policy_profile`` must be non-empty.
    proposal_repo, assessment_repo, gap_repo:
        Repository dependencies.
    policy_version:
        Policy version string embedded in committed proposal rows.

    Returns
    -------
    ProposalRefreshResult
    """
    # ------------------------------------------------------------------
    # Guard: latest_assessment must exist and belong to this proposition
    # ------------------------------------------------------------------
    assessment = assessment_repo.get(latest_assessment_id)
    if assessment is None:
        raise ValueError(
            f"latest_assessment_id={latest_assessment_id!r} not found; "
            "proposal refresh requires a committed latest assessment."
        )
    if assessment["proposition_id"] != proposition_id:
        raise ValueError(
            f"assessment {latest_assessment_id!r} belongs to proposition "
            f"{assessment['proposition_id']!r}, not {proposition_id!r}."
        )
    if assessment["session_id"] != session_id:
        raise ValueError(
            f"assessment {latest_assessment_id!r} belongs to session "
            f"{assessment['session_id']!r}, not {session_id!r}."
        )

    # ------------------------------------------------------------------
    # Guard: policy_profile must be non-empty
    # ------------------------------------------------------------------
    if not proposal_context.get("policy_profile"):
        raise ValueError("proposal_context.policy_profile must be non-empty.")

    # ------------------------------------------------------------------
    # Load gap_memberships from the assessment snapshot
    # ------------------------------------------------------------------
    gap_memberships: list[dict[str, Any]] = assessment.get("gap_memberships_json") or []

    # ------------------------------------------------------------------
    # Generate candidates (v1 deterministic rules)
    # ------------------------------------------------------------------
    candidates = _generate_candidate_proposals(
        session_id=session_id,
        proposition_id=proposition_id,
        assessment=assessment,
        gap_memberships=gap_memberships,
        gap_repo=gap_repo,
        proposal_context=proposal_context,
        policy_version=policy_version,
    )
    candidate_ids = sorted(c["action_proposal_id"] for c in candidates)

    # ------------------------------------------------------------------
    # No-op detection: compare against currently committed proposals
    # ------------------------------------------------------------------
    existing_proposals = proposal_repo.list_by_assessment(session_id, latest_assessment_id)
    existing_ids = sorted(p["action_proposal_id"] for p in existing_proposals)

    if _proposal_set_key(candidate_ids) == _proposal_set_key(existing_ids):
        return ProposalRefreshResult(
            primary_assessment_id=latest_assessment_id,
            proposal_ids=existing_ids,
            materialized_count=0,
            noop=True,
            schema_version=REFRESH_SCHEMA_VERSION,
        )

    # ------------------------------------------------------------------
    # Commit new proposals
    # ------------------------------------------------------------------
    for row in candidates:
        proposal_repo.create(row)

    return ProposalRefreshResult(
        primary_assessment_id=latest_assessment_id,
        proposal_ids=candidate_ids,
        materialized_count=len(candidates),
        noop=False,
        schema_version=REFRESH_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Internal helper — bundle assembly from a known assessment row
# ---------------------------------------------------------------------------


def assemble_bundle_from_assessment(
    *,
    session_id: str,
    proposition_id: str,
    proposition: dict[str, Any],
    assessment: dict[str, Any],
    gap_repo: EvidenceGapRepository,
    finding_repo: FindingRepository,
    proposal_repo: ActionProposalRepository,
    inference_record_repo: InferenceRecordRepository,
) -> PublishReadyBundle:
    """Assemble a :class:`PublishReadyBundle` from a pre-loaded *assessment* row.

    Shared by :func:`assemble_publish_ready_bundle` (uses latest assessment)
    and :func:`assemble_externally_visible_bundle` (uses the publish pointer).

    ``open_gaps`` is derived from ``assessment["gap_memberships_json"]``, not
    from a proposition-wide live query.  This anchors the gap set to the
    specific assessment snapshot and prevents gaps opened by later (unpublished)
    assessments from leaking into the externally visible bundle.
    """
    assessment_id: str = assessment["assessment_id"]

    supporting_ids: list[str] = assessment.get("supporting_finding_ids_json") or []
    opposing_ids: list[str] = assessment.get("opposing_finding_ids_json") or []

    supporting_findings = [f for fid in supporting_ids if (f := finding_repo.get(fid)) is not None]
    opposing_findings = [f for fid in opposing_ids if (f := finding_repo.get(fid)) is not None]

    # Anchor open_gaps to this assessment's gap_memberships snapshot.
    # Per proposal-policy-engine.md §Input Closure Assembly:
    #   "open_gap_memberships 取自主 assessment 当前 gap memberships 中 status = open 的条目"
    gap_memberships: list[dict[str, Any]] = assessment.get("gap_memberships_json") or []
    open_gaps: list[dict[str, Any]] = [
        gap
        for entry in gap_memberships
        if (gap := gap_repo.get(entry["gap_ref"]["gap_id"])) is not None
        and gap.get("status") == "open"
    ]

    applied_inference_records = inference_record_repo.list_by_assessment(assessment_id)

    live_closure = LiveClosure(
        supporting_findings=supporting_findings,
        opposing_findings=opposing_findings,
        open_gaps=open_gaps,
        applied_inference_records=applied_inference_records,
    )

    action_proposals = proposal_repo.list_by_assessment(session_id, assessment_id)

    return PublishReadyBundle(
        session_id=session_id,
        proposition_id=proposition_id,
        proposition=proposition,
        latest_assessment=assessment,
        live_closure=live_closure,
        action_proposals=action_proposals,
        schema_version=BUNDLE_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Public API — publish-ready bundle assembler
# ---------------------------------------------------------------------------


def assemble_publish_ready_bundle(
    *,
    session_id: str,
    proposition_id: str,
    assessment_repo: AssessmentRepository,
    gap_repo: EvidenceGapRepository,
    finding_repo: FindingRepository,
    proposal_repo: ActionProposalRepository,
    inference_record_repo: InferenceRecordRepository,
    proposition_repo: PropositionRepository,
) -> PublishReadyBundle:
    """Assemble a proposition-local publish-ready bundle (read-only).

    Raises :exc:`ValueError` if the proposition has no committed latest
    assessment (``latest_assessment = None``).  Callers should gate on the
    existence of a committed assessment before calling this function.

    The returned bundle is a plain Python dict; no canonical state is written.
    ``publish_ready`` means the bundle is fully assembled and internally
    consistent — it does NOT imply external visibility (4g-2's responsibility).

    Parameters
    ----------
    session_id, proposition_id:
        Canonical identifiers.
    assessment_repo, gap_repo, finding_repo, proposal_repo,
    inference_record_repo, proposition_repo:
        Read dependencies.

    Returns
    -------
    PublishReadyBundle
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
    # Load latest assessment
    # ------------------------------------------------------------------
    latest_assessment = assessment_repo.get_latest(proposition_id)
    if latest_assessment is None:
        raise ValueError(
            f"proposition {proposition_id!r} has no committed latest assessment; "
            "publish-ready bundle requires a committed assessment."
        )

    return assemble_bundle_from_assessment(
        session_id=session_id,
        proposition_id=proposition_id,
        proposition=proposition,
        assessment=latest_assessment,
        gap_repo=gap_repo,
        finding_repo=finding_repo,
        proposal_repo=proposal_repo,
        inference_record_repo=inference_record_repo,
    )


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "REFRESH_SCHEMA_VERSION",
    "LiveClosure",
    "ProposalRefreshResult",
    "PublishReadyBundle",
    "assemble_bundle_from_assessment",
    "assemble_publish_ready_bundle",
    "run_action_proposal_refresh",
]
