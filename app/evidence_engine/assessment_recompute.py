"""Assessment Recompute Runtime (Phase 4f-2).

Implements the 9-step evaluation pipeline that consumes an
:class:`AssessmentEvaluationContext` (Phase 4f-1), runs all v1 rule families,
and persists an immutable assessment snapshot **only when** the canonical
judgment output differs from the current latest snapshot (no-op otherwise).

Evaluation order (fixed per ``inference-and-gap-engine.md``):
  1. Context — pre-loaded by caller, passed as *ctx*
  2. Candidate identity pre-allocation — from ``ctx["candidate_assessment_id"]``
  3. Gate families — precondition, quality, comparability (v1 minimal)
  4. Support evidence aggregation (v1: finding-type-based)
  5. Oppose evidence aggregation (v1 stub: always miss)
  6. Status resolution — implements the 4-step threshold algorithm
  7. Gap management — open / keep / resolve precondition-based gaps
  8. Confidence shaping — applies global guardrails from assessment schema
  9. Assessment transition — canonical diff detection + conditional commit

Commit order respects FK constraints:
  assessments → inference_records → evidence_gaps (open/resolve)

Design contracts:
  - ``docs/analysis/evidence-engine/inference-and-gap-engine.md``
  - ``docs/analysis/evidence-engine/support-oppose-and-status-resolution.md``
  - ``docs/analysis/evidence-engine/gap-confidence-and-transition-materialization.md``
  - ``docs/analysis/evidence-engine/schemas/assessment.md``

Phase: 4f-2
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, TypedDict

from app.evidence_engine.assessment_evaluation_context import AssessmentEvaluationContext
from app.storage.evidence_repositories import (
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

RECOMPUTE_SCHEMA_VERSION = "assessment_recompute_result.v1"

# ---------------------------------------------------------------------------
# assessment_type → directional finding_type (v1)
# ---------------------------------------------------------------------------

_DIRECTIONAL_FINDING_TYPE: dict[str, str] = {
    "change_assessment": "delta",
    "decomposition_assessment": "decomposition_item",
    "anomaly_assessment": "anomaly_candidate",
    "correlation_assessment": "correlation_result",
    "test_hypothesis_assessment": "test_result",
    "forecast_assessment": "forecast_point",
}

# ---------------------------------------------------------------------------
# Public TypedDict
# ---------------------------------------------------------------------------


class AssessmentRecomputeResult(TypedDict):
    """Outcome of a single ``recompute_proposition_assessment`` call.

    Fields
    ------
    assessment_id:
        The ``assessment_id`` of the newly committed snapshot, or ``None``
        when the run was a no-op (canonical output unchanged).
    created:
        ``True`` when a new snapshot was committed; ``False`` for no-op.
    snapshot_seq:
        The ``snapshot_seq`` of the committed snapshot, or ``None`` on no-op.
    status:
        The ``status`` written to the committed snapshot (``"supported"``,
        ``"contradicted"``, ``"mixed"``, ``"insufficient"``), or ``None`` on
        no-op.
    candidate_assessment_id:
        The ``candidate_assessment_id`` from the input context.  Always set
        (even on no-op) so callers can trace the candidate identity.
    schema_version:
        Fixed at :data:`RECOMPUTE_SCHEMA_VERSION`.
    """

    assessment_id: str | None
    created: bool
    snapshot_seq: int | None
    status: str | None
    candidate_assessment_id: str
    schema_version: str


# ---------------------------------------------------------------------------
# Internal intermediate types (module-private)
# ---------------------------------------------------------------------------


class _GateOutput(TypedDict):
    rule_id: str
    result: str  # "hit" | "miss"
    satisfied_tokens: list[str]
    unsatisfied_tokens: list[str]
    data_quality_impact: str  # for quality gate only; else ""
    input_finding_ids: list[str]


class _DirectionalOutput(TypedDict):
    rule_id: str
    direction: str  # "support" | "oppose"
    result: str  # "hit" | "miss"
    candidate_finding_ids: list[str]
    satisfied_tokens: list[str]
    unsatisfied_tokens: list[str]


class _StatusResolutionOutput(TypedDict):
    rule_id: str
    status: str
    supporting_finding_ids: list[str]
    opposing_finding_ids: list[str]
    guardrail_blocked: bool


class _GapAction(TypedDict):
    kind: str  # "open" | "keep" | "resolve"
    gap_id: str
    gap_row: dict[str, Any] | None  # populated for "open" only


class _GapManagementOutput(TypedDict):
    rule_id: str
    gap_actions: list[_GapAction]
    gap_memberships: list[dict[str, Any]]  # list[GapMembershipEntry]
    opened_gap_ids: list[str]
    resolved_gap_ids: list[str]


class _ConfidenceOutput(TypedDict):
    rule_id: str
    confidence_grade: str
    confidence_rationale: dict[str, Any]


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------


def _make_assessment_id(session_id: str, proposition_id: str, snapshot_seq: int) -> str:
    """Derive a stable ``assessment_id`` from (session, proposition, seq).

    Format: ``"assess_"`` + first 24 hex chars of SHA-256.
    """
    raw = f"{session_id}:{proposition_id}:{snapshot_seq}"
    return "assess_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _make_inference_record_id(
    session_id: str, proposition_id: str, assessment_id: str, rule_id: str
) -> str:
    """Derive a stable ``inference_record_id``.

    Format: ``"irec_"`` + first 24 hex chars of SHA-256.
    """
    raw = f"{session_id}:{proposition_id}:{assessment_id}:{rule_id}"
    return "irec_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _make_gap_id(
    session_id: str,
    proposition_id: str,
    requirement_key: str,
    opened_by_assessment_id: str,
) -> str:
    """Derive a stable ``gap_id`` from (session, proposition, requirement_key, assessment).

    ``opened_by_assessment_id`` is passed as the current run's
    ``candidate_assessment_id`` (not yet a committed assessment).  Including it
    ensures that a re-opened gap (after a previous gap with the same
    ``requirement_key`` was resolved) gets a new, distinct ``gap_id`` —
    satisfying the spec requirement that reopen must not reuse the old gap
    object.  The "keep" branch reuses the existing gap_id from the original
    open run, so this parameter only affects freshly-opened gaps.

    Format: ``"gap_"`` + first 24 hex chars of SHA-256.
    """
    raw = f"{session_id}:{proposition_id}:{requirement_key}:{opened_by_assessment_id}"
    return "gap_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Step 3 — Gate families
# ---------------------------------------------------------------------------


def _run_precondition_gate(
    ctx: AssessmentEvaluationContext,
    candidate_id: str,
    finding_repo: FindingRepository,
) -> tuple[_GateOutput, dict[str, Any]]:
    """v1 precondition gate: checks for directional findings for this assessment_type.

    Checks that at least one candidate finding matches the required directional
    finding type (e.g. ``delta`` for ``change_assessment``).  A wrong-type
    finding (e.g. ``observation`` for ``change_assessment``) does NOT satisfy
    the precondition, ensuring a ``missing_rule_precondition`` gap is opened
    so the proposal engine can generate an actionable investigate proposal.
    """
    session_id = ctx["session_id"]
    proposition_id = ctx["proposition"]["proposition_id"]
    assessment_type = ctx["assessment_type"]
    rule_id = "precondition_gate.v1.finding_presence"
    irec_id = _make_inference_record_id(session_id, proposition_id, candidate_id, rule_id)

    target_type = _DIRECTIONAL_FINDING_TYPE.get(assessment_type, "")
    has_findings = (
        any(
            (f := finding_repo.get(fid)) is not None and f.get("finding_type") == target_type
            for fid in ctx["candidate_finding_ids"]
        )
        if target_type
        else bool(ctx["candidate_finding_ids"])
    )
    if has_findings:
        gate: _GateOutput = {
            "rule_id": rule_id,
            "result": "hit",
            "satisfied_tokens": ["has_candidate_findings"],
            "unsatisfied_tokens": [],
            "data_quality_impact": "",
            "input_finding_ids": list(ctx["candidate_finding_ids"]),
        }
        irec_result = "hit"
        matched = ["has_candidate_findings"]
        unmatched: list[str] = []
    else:
        gate = {
            "rule_id": rule_id,
            "result": "miss",
            "satisfied_tokens": [],
            "unsatisfied_tokens": ["no_candidate_findings"],
            "data_quality_impact": "",
            "input_finding_ids": [],
        }
        irec_result = "miss"
        matched = []
        unmatched = ["no_candidate_findings"]

    irec = {
        "inference_record_id": irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": rule_id,
        "rule_version": "v1",
        "result": irec_result,
        "input_finding_ids_json": json.dumps(gate["input_finding_ids"]),
        "input_assessment_ids_json": "[]",
        "opened_gap_ids_json": "[]",
        "resolved_gap_ids_json": "[]",
        "produced_status_transition_json": None,
        "confidence_contribution_json": json.dumps({"direction": "neutral", "magnitude": "small"}),
        "justification_json": json.dumps(
            {"matched_conditions": matched, "unmatched_conditions": unmatched, "notes": []}
        ),
        "schema_version": "v1",
    }
    return gate, irec


def _run_quality_gate(
    ctx: AssessmentEvaluationContext, candidate_id: str
) -> tuple[_GateOutput, dict[str, Any]]:
    """v1 quality gate: always passes (no quality metadata in v1)."""
    session_id = ctx["session_id"]
    proposition_id = ctx["proposition"]["proposition_id"]
    rule_id = "quality_gate.v1.baseline"
    irec_id = _make_inference_record_id(session_id, proposition_id, candidate_id, rule_id)

    gate: _GateOutput = {
        "rule_id": rule_id,
        "result": "hit",
        "satisfied_tokens": ["quality_baseline_passed"],
        "unsatisfied_tokens": [],
        "data_quality_impact": "none",
        "input_finding_ids": [],
    }
    irec = {
        "inference_record_id": irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": rule_id,
        "rule_version": "v1",
        "result": "hit",
        "input_finding_ids_json": "[]",
        "input_assessment_ids_json": "[]",
        "opened_gap_ids_json": "[]",
        "resolved_gap_ids_json": "[]",
        "produced_status_transition_json": None,
        "confidence_contribution_json": json.dumps({"direction": "neutral", "magnitude": "small"}),
        "justification_json": json.dumps(
            {
                "matched_conditions": ["quality_baseline_passed"],
                "unmatched_conditions": [],
                "notes": [],
            }
        ),
        "schema_version": "v1",
    }
    return gate, irec


def _run_comparability_gate(
    ctx: AssessmentEvaluationContext, candidate_id: str
) -> tuple[_GateOutput, dict[str, Any]]:
    """v1 comparability gate: always passes (no comparability metadata in v1)."""
    session_id = ctx["session_id"]
    proposition_id = ctx["proposition"]["proposition_id"]
    rule_id = "comparability_gate.v1.baseline"
    irec_id = _make_inference_record_id(session_id, proposition_id, candidate_id, rule_id)

    gate: _GateOutput = {
        "rule_id": rule_id,
        "result": "hit",
        "satisfied_tokens": ["comparability_baseline_passed"],
        "unsatisfied_tokens": [],
        "data_quality_impact": "",
        "input_finding_ids": [],
    }
    irec = {
        "inference_record_id": irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": rule_id,
        "rule_version": "v1",
        "result": "hit",
        "input_finding_ids_json": "[]",
        "input_assessment_ids_json": "[]",
        "opened_gap_ids_json": "[]",
        "resolved_gap_ids_json": "[]",
        "produced_status_transition_json": None,
        "confidence_contribution_json": json.dumps({"direction": "neutral", "magnitude": "small"}),
        "justification_json": json.dumps(
            {
                "matched_conditions": ["comparability_baseline_passed"],
                "unmatched_conditions": [],
                "notes": [],
            }
        ),
        "schema_version": "v1",
    }
    return gate, irec


# ---------------------------------------------------------------------------
# Steps 4–5 — Directional evidence
# ---------------------------------------------------------------------------


def _run_support_evidence(
    ctx: AssessmentEvaluationContext,
    candidate_id: str,
    precond_gate: _GateOutput,
    finding_repo: FindingRepository,
) -> tuple[_DirectionalOutput, dict[str, Any]]:
    """v1 support evidence: finds directional findings for this assessment_type."""
    session_id = ctx["session_id"]
    proposition_id = ctx["proposition"]["proposition_id"]
    assessment_type = ctx["assessment_type"]
    rule_id = "support_evidence.v1.finding_type_match"
    irec_id = _make_inference_record_id(session_id, proposition_id, candidate_id, rule_id)

    target_type = _DIRECTIONAL_FINDING_TYPE.get(assessment_type, "")
    matched_ids: list[str] = []

    if precond_gate["result"] == "hit" and target_type:
        for fid in ctx["candidate_finding_ids"]:
            f = finding_repo.get(fid)
            if f is not None and f.get("finding_type") == target_type:
                matched_ids.append(fid)

    if matched_ids:
        out: _DirectionalOutput = {
            "rule_id": rule_id,
            "direction": "support",
            "result": "hit",
            "candidate_finding_ids": matched_ids,
            "satisfied_tokens": ["has_directional_finding"],
            "unsatisfied_tokens": [],
        }
        irec_result = "hit"
        matched_cond = ["has_directional_finding"]
        unmatched_cond: list[str] = []
    else:
        out = {
            "rule_id": rule_id,
            "direction": "support",
            "result": "miss",
            "candidate_finding_ids": [],
            "satisfied_tokens": [],
            "unsatisfied_tokens": ["has_directional_finding"],
        }
        irec_result = "miss"
        matched_cond = []
        unmatched_cond = ["has_directional_finding"]

    irec = {
        "inference_record_id": irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": rule_id,
        "rule_version": "v1",
        "result": irec_result,
        "input_finding_ids_json": json.dumps(matched_ids),
        "input_assessment_ids_json": "[]",
        "opened_gap_ids_json": "[]",
        "resolved_gap_ids_json": "[]",
        "produced_status_transition_json": None,
        "confidence_contribution_json": json.dumps(
            {
                "direction": "increase" if matched_ids else "neutral",
                "magnitude": "medium" if matched_ids else "small",
            }
        ),
        "justification_json": json.dumps(
            {
                "matched_conditions": matched_cond,
                "unmatched_conditions": unmatched_cond,
                "notes": [],
            }
        ),
        "schema_version": "v1",
    }
    return out, irec


def _run_oppose_evidence(
    ctx: AssessmentEvaluationContext, candidate_id: str
) -> tuple[_DirectionalOutput, dict[str, Any]]:
    """v1 oppose evidence stub: always miss (no contra-evidence logic in v1)."""
    session_id = ctx["session_id"]
    proposition_id = ctx["proposition"]["proposition_id"]
    rule_id = "oppose_evidence.v1.baseline"
    irec_id = _make_inference_record_id(session_id, proposition_id, candidate_id, rule_id)

    out: _DirectionalOutput = {
        "rule_id": rule_id,
        "direction": "oppose",
        "result": "miss",
        "candidate_finding_ids": [],
        "satisfied_tokens": [],
        "unsatisfied_tokens": ["has_oppose_finding"],
    }
    irec = {
        "inference_record_id": irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": rule_id,
        "rule_version": "v1",
        "result": "miss",
        "input_finding_ids_json": "[]",
        "input_assessment_ids_json": "[]",
        "opened_gap_ids_json": "[]",
        "resolved_gap_ids_json": "[]",
        "produced_status_transition_json": None,
        "confidence_contribution_json": json.dumps({"direction": "neutral", "magnitude": "small"}),
        "justification_json": json.dumps(
            {
                "matched_conditions": [],
                "unmatched_conditions": ["has_oppose_finding"],
                "notes": [],
            }
        ),
        "schema_version": "v1",
    }
    return out, irec


# ---------------------------------------------------------------------------
# Step 6 — Status resolution
# ---------------------------------------------------------------------------


def _run_status_resolution(
    ctx: AssessmentEvaluationContext,
    candidate_id: str,
    precond_gate: _GateOutput,
    support_out: _DirectionalOutput,
    oppose_out: _DirectionalOutput,
) -> tuple[_StatusResolutionOutput, dict[str, Any]]:
    """Implements the 4-step threshold algorithm from support-oppose-and-status-resolution.md."""
    session_id = ctx["session_id"]
    proposition_id = ctx["proposition"]["proposition_id"]
    rule_id = "status_resolution.v1.threshold_algorithm"
    irec_id = _make_inference_record_id(session_id, proposition_id, candidate_id, rule_id)

    support_threshold_met = "has_directional_finding" in support_out["satisfied_tokens"]
    oppose_threshold_met = "has_oppose_finding" in oppose_out["satisfied_tokens"]

    # Precondition gate failure is a guardrail: force insufficient
    guardrail_blocked = precond_gate["result"] == "miss"

    if guardrail_blocked:
        status = "insufficient"
    elif support_threshold_met and oppose_threshold_met:
        status = "mixed"
    elif support_threshold_met:
        status = "supported"
    elif oppose_threshold_met:
        status = "contradicted"
    else:
        status = "insufficient"

    # Normalize directional membership (no dual membership allowed per spec §3)
    supporting_ids = _dedup_sorted(support_out["candidate_finding_ids"])
    opposing_ids = _dedup_sorted(oppose_out["candidate_finding_ids"])
    # Remove any dual-membership (precaution; v1 oppose is always empty)
    dual = set(supporting_ids) & set(opposing_ids)
    if dual:
        supporting_ids = [fid for fid in supporting_ids if fid not in dual]
        opposing_ids = [fid for fid in opposing_ids if fid not in dual]

    out: _StatusResolutionOutput = {
        "rule_id": rule_id,
        "status": status,
        "supporting_finding_ids": supporting_ids,
        "opposing_finding_ids": opposing_ids,
        "guardrail_blocked": guardrail_blocked,
    }

    matched: list[str] = []
    unmatched: list[str] = []
    if support_threshold_met:
        matched.append("support_threshold_met")
    else:
        unmatched.append("support_threshold_met")
    if oppose_threshold_met:
        matched.append("oppose_threshold_met")
    else:
        unmatched.append("oppose_threshold_met")
    if guardrail_blocked:
        matched.append("precondition_guardrail_blocked")

    irec = {
        "inference_record_id": irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": rule_id,
        "rule_version": "v1",
        "result": "hit",
        "input_finding_ids_json": json.dumps(supporting_ids + opposing_ids),
        "input_assessment_ids_json": "[]",
        "opened_gap_ids_json": "[]",
        "resolved_gap_ids_json": "[]",
        "produced_status_transition_json": None,
        "confidence_contribution_json": json.dumps({"direction": "neutral", "magnitude": "small"}),
        "justification_json": json.dumps(
            {"matched_conditions": matched, "unmatched_conditions": unmatched, "notes": []}
        ),
        "schema_version": "v1",
    }
    return out, irec


# ---------------------------------------------------------------------------
# Step 7 — Gap management
# ---------------------------------------------------------------------------


def _run_gap_management(
    ctx: AssessmentEvaluationContext,
    candidate_id: str,
    precond_gate: _GateOutput,
    gap_repo: EvidenceGapRepository,
    now: str,
) -> tuple[_GapManagementOutput, dict[str, Any]]:
    """v1 gap management: manages missing_rule_precondition gaps for finding-presence check."""
    session_id = ctx["session_id"]
    proposition_id = ctx["proposition"]["proposition_id"]
    assessment_type = ctx["assessment_type"]
    rule_id = "gap_management.v1.precondition_gaps"
    irec_id = _make_inference_record_id(session_id, proposition_id, candidate_id, rule_id)
    requirement_key = f"precondition.finding_presence.{assessment_type}"

    precond_miss = precond_gate["result"] == "miss"

    # Find any existing open gap matching our requirement_key
    matching_open_gap_id: str | None = None
    for gid in ctx["open_gap_ids"]:
        gap = gap_repo.get(gid)
        if gap is None:
            continue
        req = gap.get("missing_requirement_json") or {}
        if req.get("requirement_key") == requirement_key:
            matching_open_gap_id = gid
            break

    gap_actions: list[_GapAction] = []
    gap_memberships: list[dict[str, Any]] = []
    opened_gap_ids: list[str] = []
    resolved_gap_ids: list[str] = []

    target_finding_type = _DIRECTIONAL_FINDING_TYPE.get(assessment_type, "observation")

    if precond_miss:
        if matching_open_gap_id is not None:
            # Keep existing gap
            gap_actions.append({"kind": "keep", "gap_id": matching_open_gap_id, "gap_row": None})
            gap_memberships.append(
                {
                    "gap_ref": {"gap_id": matching_open_gap_id, "proposition_id": proposition_id},
                    "blocking": True,
                    "severity": "critical",
                }
            )
        else:
            # Open new gap — include candidate_id so reopens get a fresh gap_id
            new_gap_id = _make_gap_id(session_id, proposition_id, requirement_key, candidate_id)
            gap_row: dict[str, Any] = {
                "gap_id": new_gap_id,
                "session_id": session_id,
                "proposition_id": proposition_id,
                "gap_kind": "missing_rule_precondition",
                "title": f"No {assessment_type} candidate findings",
                "description": (
                    f"Precondition check failed: no findings of type "
                    f"'{target_finding_type}' found in candidate set."
                ),
                "status": "open",
                "missing_requirement_json": json.dumps(
                    {
                        "requirement_type": "rule_precondition",
                        "requirement_key": requirement_key,
                        "requirement_params": {
                            "rule_id": "precondition_gate.v1.finding_presence",
                            "missing_condition": "no_candidate_findings",
                        },
                    }
                ),
                "satisfiable_by_json": json.dumps(
                    [
                        {
                            "kind": "finding_arrival",
                            "finding_type": target_finding_type,
                            "subject": None,
                        }
                    ]
                ),
                "related_finding_ids_json": "[]",
                "opened_by_inference_record_id": irec_id,
                "schema_version": "v1",
            }
            gap_actions.append({"kind": "open", "gap_id": new_gap_id, "gap_row": gap_row})
            opened_gap_ids.append(new_gap_id)
            gap_memberships.append(
                {
                    "gap_ref": {"gap_id": new_gap_id, "proposition_id": proposition_id},
                    "blocking": True,
                    "severity": "critical",
                }
            )
    else:
        # Precondition satisfied — resolve matching open gap if present
        if matching_open_gap_id is not None:
            gap_actions.append({"kind": "resolve", "gap_id": matching_open_gap_id, "gap_row": None})
            resolved_gap_ids.append(matching_open_gap_id)
        # No gap_memberships entry for resolved gap

    out: _GapManagementOutput = {
        "rule_id": rule_id,
        "gap_actions": gap_actions,
        "gap_memberships": gap_memberships,
        "opened_gap_ids": opened_gap_ids,
        "resolved_gap_ids": resolved_gap_ids,
    }

    irec = {
        "inference_record_id": irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": rule_id,
        "rule_version": "v1",
        "result": "miss" if precond_miss else "hit",
        "input_finding_ids_json": "[]",
        "input_assessment_ids_json": "[]",
        "opened_gap_ids_json": json.dumps(opened_gap_ids),
        "resolved_gap_ids_json": json.dumps(resolved_gap_ids),
        "produced_status_transition_json": None,
        "confidence_contribution_json": json.dumps(
            {
                "direction": "decrease" if precond_miss else "neutral",
                "magnitude": "large" if precond_miss else "small",
            }
        ),
        "justification_json": json.dumps(
            {
                "matched_conditions": [] if precond_miss else ["precondition_satisfied"],
                "unmatched_conditions": ["no_candidate_findings"] if precond_miss else [],
                "notes": [],
            }
        ),
        "schema_version": "v1",
    }
    return out, irec


# ---------------------------------------------------------------------------
# Step 8 — Confidence shaping
# ---------------------------------------------------------------------------


def _run_confidence_shaping(
    ctx: AssessmentEvaluationContext,
    candidate_id: str,
    precond_gate: _GateOutput,
    quality_gate: _GateOutput,
    support_out: _DirectionalOutput,
    oppose_out: _DirectionalOutput,
    resolution_out: _StatusResolutionOutput,
    gap_out: _GapManagementOutput,
) -> tuple[_ConfidenceOutput, dict[str, Any]]:
    """v1 confidence shaping: applies global guardrails from the assessment schema.

    Global guardrails (from ``schemas/assessment.md``):
    - ``data_quality_impact="severe"`` → grade ≤ ``"low"``
    - ``evidence_sufficiency="very_weak"`` → grade ≤ ``"low"``
    - ``rule_coverage="minimal"`` and consistency not ``"consistent"`` → grade ≤ ``"medium"``
    - ``evidence_consistency="conflicting"`` → no high-confidence single-direction conclusion
    """
    session_id = ctx["session_id"]
    proposition_id = ctx["proposition"]["proposition_id"]
    rule_id = "confidence_shaping.v1.global_guardrails"
    irec_id = _make_inference_record_id(session_id, proposition_id, candidate_id, rule_id)

    status = resolution_out["status"]
    precond_miss = precond_gate["result"] == "miss"
    data_quality_impact: str = quality_gate.get("data_quality_impact") or "none"

    # evidence_sufficiency
    evidence_sufficiency = (
        "very_weak" if precond_miss else "weak"
    )  # v1: only partial coverage, never "adequate" or above

    # evidence_consistency
    if status == "supported":
        evidence_consistency = "consistent"
    elif status == "contradicted":
        # "consistent" here means evidence consistently points in a single direction
        # (against the proposition).  No support/oppose conflict → not "conflicting".
        evidence_consistency = "consistent"
    elif status == "mixed":
        evidence_consistency = "conflicting"
    else:  # insufficient
        evidence_consistency = "mixed"

    # rule_coverage: v1 runs a subset of the full family set
    rule_coverage = "partial"

    # Confidence grade derivation (v1 heuristic applying guardrails)
    # Base grade from status + sufficiency.
    # v1 deliberate floor: evidence_sufficiency is at most "weak" (see above), so
    # confidence_grade is at most "low".  This is intentional: v1 rule coverage is
    # partial and single-direction only, so "medium" or higher confidence requires
    # richer evidence families not yet implemented.
    if evidence_sufficiency == "very_weak" or status == "insufficient":
        base_grade = "very_low"
    elif status in ("supported", "contradicted"):
        base_grade = "low"
    else:  # mixed
        base_grade = "low"

    # Apply global guardrails (may only lower, never raise)
    _grade_order = ["very_low", "low", "medium", "high", "very_high"]

    def _cap_grade(grade: str, cap: str) -> str:
        gi = _grade_order.index(grade)
        ci = _grade_order.index(cap)
        return _grade_order[min(gi, ci)]

    confidence_grade = base_grade
    if data_quality_impact == "severe":
        confidence_grade = _cap_grade(confidence_grade, "low")
    if evidence_sufficiency == "very_weak":
        confidence_grade = _cap_grade(confidence_grade, "low")
    # rule_coverage="minimal" guardrail does not trigger in v1 (we use "partial")
    # evidence_consistency="conflicting" → no high-confidence single-direction conclusion
    # (already satisfied since base grade for mixed is "low")

    confidence_rationale: dict[str, Any] = {
        "evidence_sufficiency": evidence_sufficiency,
        "evidence_consistency": evidence_consistency,
        "rule_coverage": rule_coverage,
        "data_quality_impact": data_quality_impact,
        "rationale_notes": [],
    }

    out: _ConfidenceOutput = {
        "rule_id": rule_id,
        "confidence_grade": confidence_grade,
        "confidence_rationale": confidence_rationale,
    }

    irec = {
        "inference_record_id": irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": rule_id,
        "rule_version": "v1",
        "result": "hit",
        "input_finding_ids_json": "[]",
        "input_assessment_ids_json": "[]",
        "opened_gap_ids_json": "[]",
        "resolved_gap_ids_json": "[]",
        "produced_status_transition_json": None,
        "confidence_contribution_json": json.dumps({"direction": "neutral", "magnitude": "small"}),
        "justification_json": json.dumps(
            {
                "matched_conditions": [f"grade_set:{confidence_grade}"],
                "unmatched_conditions": [],
                "notes": [],
            }
        ),
        "schema_version": "v1",
    }
    return out, irec


# ---------------------------------------------------------------------------
# Step 9 — Canonical diff detection
# ---------------------------------------------------------------------------


def _canonical_diff_key(
    status: str,
    confidence_grade: str,
    confidence_rationale: dict[str, Any],
    supporting_finding_ids: list[str],
    opposing_finding_ids: list[str],
    gap_memberships: list[dict[str, Any]],
) -> str:
    """Produce a stable string key for canonical diff comparison.

    ``applied_inference_record_ids`` are intentionally excluded: their IDs
    differ across runs (bound to the candidate_assessment_id) even when the
    judgment output is identical, which would incorrectly prevent no-op.
    """
    return json.dumps(
        {
            "status": status,
            "confidence_grade": confidence_grade,
            "confidence_rationale": confidence_rationale,
            "supporting_finding_ids": sorted(supporting_finding_ids),
            "opposing_finding_ids": sorted(opposing_finding_ids),
            "gap_memberships": sorted(
                [
                    {
                        "gap_ref": m["gap_ref"],
                        "blocking": m["blocking"],
                        "severity": m["severity"],
                    }
                    for m in gap_memberships
                ],
                key=lambda m: m["gap_ref"]["gap_id"],
            ),
        },
        sort_keys=True,
    )


def _compute_canonical_diff(
    *,
    candidate_status: str,
    candidate_confidence_grade: str,
    candidate_confidence_rationale: dict[str, Any],
    candidate_supporting_ids: list[str],
    candidate_opposing_ids: list[str],
    candidate_gap_memberships: list[dict[str, Any]],
    prior_latest: dict[str, Any] | None,
) -> bool:
    """Return ``True`` iff the candidate snapshot differs from *prior_latest*.

    Always returns ``True`` when *prior_latest* is ``None`` (first snapshot).
    """
    if prior_latest is None:
        return True

    candidate_key = _canonical_diff_key(
        status=candidate_status,
        confidence_grade=candidate_confidence_grade,
        confidence_rationale=candidate_confidence_rationale,
        supporting_finding_ids=candidate_supporting_ids,
        opposing_finding_ids=candidate_opposing_ids,
        gap_memberships=candidate_gap_memberships,
    )

    # prior_latest has been deserialized by AssessmentRepository.get_latest()
    # JSON fields are already Python objects (lists/dicts)
    prior_rationale = prior_latest.get("confidence_rationale_json") or {}
    prior_supporting = prior_latest.get("supporting_finding_ids_json") or []
    prior_opposing = prior_latest.get("opposing_finding_ids_json") or []
    prior_gap_memberships = prior_latest.get("gap_memberships_json") or []

    prior_key = _canonical_diff_key(
        status=prior_latest["status"],
        confidence_grade=prior_latest["confidence_grade"],
        confidence_rationale=prior_rationale,
        supporting_finding_ids=prior_supporting,
        opposing_finding_ids=prior_opposing,
        gap_memberships=prior_gap_memberships,
    )

    return candidate_key != prior_key


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _dedup_sorted(ids: list[str]) -> list[str]:
    """Return a deduplicated, sorted list of ids."""
    return sorted(set(ids))


def _utc_now() -> str:
    """Return current UTC time as ISO-8601 string (Z suffix)."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Public API — main orchestrator
# ---------------------------------------------------------------------------


def recompute_proposition_assessment(
    *,
    ctx: AssessmentEvaluationContext,
    assessment_repo: AssessmentRepository,
    gap_repo: EvidenceGapRepository,
    inference_record_repo: InferenceRecordRepository,
    finding_repo: FindingRepository,
) -> AssessmentRecomputeResult:
    """Execute the 9-step assessment recompute pipeline for a single proposition.

    Consumes the pre-assembled :class:`AssessmentEvaluationContext` and runs
    all v1 rule families in the fixed evaluation order.  The resulting
    candidate assessment snapshot is committed **only** when the canonical
    judgment output (status, confidence, evidence membership, gap memberships)
    differs from the current latest snapshot.

    Commit order (FK-safe):
      1. ``assessments`` row
      2. ``inference_records`` rows
      3. ``evidence_gaps`` create/resolve

    Parameters
    ----------
    ctx:
        Pre-assembled context from :func:`build_assessment_evaluation_context`.
    assessment_repo:
        Repository for reading prior snapshots and committing the new one.
    gap_repo:
        Repository for reading open gaps and materializing gap lifecycle.
    inference_record_repo:
        Repository for committing candidate inference records.
    finding_repo:
        Repository for resolving candidate finding types (support evidence).

    Returns
    -------
    AssessmentRecomputeResult
        ``created=True`` with the new snapshot details, or ``created=False``
        when the run was a no-op.
    """
    session_id = ctx["session_id"]
    proposition = ctx["proposition"]
    proposition_id = proposition["proposition_id"]
    assessment_type = ctx["assessment_type"]
    candidate_id = ctx["candidate_assessment_id"]

    now = _utc_now()

    # ------------------------------------------------------------------
    # Step 3 — Gate families
    # ------------------------------------------------------------------
    precond_gate, precond_irec = _run_precondition_gate(ctx, candidate_id, finding_repo)
    quality_gate, quality_irec = _run_quality_gate(ctx, candidate_id)
    _compare_gate, compare_irec = _run_comparability_gate(ctx, candidate_id)

    # ------------------------------------------------------------------
    # Step 4–5 — Directional evidence
    # ------------------------------------------------------------------
    support_out, support_irec = _run_support_evidence(ctx, candidate_id, precond_gate, finding_repo)
    oppose_out, oppose_irec = _run_oppose_evidence(ctx, candidate_id)

    # ------------------------------------------------------------------
    # Step 6 — Status resolution
    # ------------------------------------------------------------------
    resolution_out, resolution_irec = _run_status_resolution(
        ctx, candidate_id, precond_gate, support_out, oppose_out
    )

    # ------------------------------------------------------------------
    # Step 7 — Gap management
    # ------------------------------------------------------------------
    gap_out, gap_irec = _run_gap_management(ctx, candidate_id, precond_gate, gap_repo, now)

    # ------------------------------------------------------------------
    # Step 8 — Confidence shaping
    # ------------------------------------------------------------------
    confidence_out, confidence_irec = _run_confidence_shaping(
        ctx,
        candidate_id,
        precond_gate,
        quality_gate,
        support_out,
        oppose_out,
        resolution_out,
        gap_out,
    )

    # ------------------------------------------------------------------
    # Step 9a — Assemble candidate (pre-transition inference records)
    # ------------------------------------------------------------------
    pre_transition_irecs = [
        precond_irec,
        quality_irec,
        compare_irec,
        support_irec,
        oppose_irec,
        resolution_irec,
        gap_irec,
        confidence_irec,
    ]

    candidate_status = resolution_out["status"]
    candidate_confidence_grade = confidence_out["confidence_grade"]
    candidate_confidence_rationale = confidence_out["confidence_rationale"]
    candidate_supporting_ids = resolution_out["supporting_finding_ids"]
    candidate_opposing_ids = resolution_out["opposing_finding_ids"]
    candidate_gap_memberships = gap_out["gap_memberships"]

    # ------------------------------------------------------------------
    # Step 9b — Assessment transition / canonical diff
    # ------------------------------------------------------------------
    prior_latest = assessment_repo.get_latest(proposition_id)

    diff_detected = _compute_canonical_diff(
        candidate_status=candidate_status,
        candidate_confidence_grade=candidate_confidence_grade,
        candidate_confidence_rationale=candidate_confidence_rationale,
        candidate_supporting_ids=candidate_supporting_ids,
        candidate_opposing_ids=candidate_opposing_ids,
        candidate_gap_memberships=candidate_gap_memberships,
        prior_latest=prior_latest,
    )

    if not diff_detected:
        # No-op: discard all candidate objects per spec
        return AssessmentRecomputeResult(
            assessment_id=None,
            created=False,
            snapshot_seq=None,
            status=None,
            candidate_assessment_id=candidate_id,
            schema_version=RECOMPUTE_SCHEMA_VERSION,
        )

    # ------------------------------------------------------------------
    # Build assessment_transition inference record (only on commit)
    # ------------------------------------------------------------------
    prior_latest_id: str | None = (
        prior_latest["assessment_id"] if prior_latest is not None else None
    )
    prior_status: str | None = prior_latest["status"] if prior_latest is not None else None

    transition_rule_id = "assessment_transition.v1.canonical_diff"
    transition_irec_id = _make_inference_record_id(
        session_id, proposition_id, candidate_id, transition_rule_id
    )
    status_transition = {"from_status": prior_status, "to_status": candidate_status}

    transition_irec = {
        "inference_record_id": transition_irec_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_id": candidate_id,
        "rule_id": transition_rule_id,
        "rule_version": "v1",
        "result": "hit",
        "input_finding_ids_json": "[]",
        "input_assessment_ids_json": json.dumps(
            [prior_latest_id] if prior_latest_id is not None else []
        ),
        "opened_gap_ids_json": "[]",
        "resolved_gap_ids_json": "[]",
        "produced_status_transition_json": json.dumps(status_transition),
        "confidence_contribution_json": json.dumps({"direction": "neutral", "magnitude": "small"}),
        "justification_json": json.dumps(
            {
                "matched_conditions": ["canonical_diff_detected"],
                "unmatched_conditions": [],
                "notes": [],
            }
        ),
        "schema_version": "v1",
    }

    # Final ordered list of inference records
    all_irecs = [*pre_transition_irecs, transition_irec]
    all_irec_ids = [r["inference_record_id"] for r in all_irecs]

    # ------------------------------------------------------------------
    # Determine snapshot_seq + build assessment row
    # ------------------------------------------------------------------
    next_seq = assessment_repo.next_snapshot_seq(proposition_id)
    supersedes_id: str | None = prior_latest_id

    assessment_row: dict[str, Any] = {
        "assessment_id": candidate_id,
        "session_id": session_id,
        "proposition_id": proposition_id,
        "assessment_type": assessment_type,
        "snapshot_seq": next_seq,
        "status": candidate_status,
        "confidence_grade": candidate_confidence_grade,
        "confidence_rationale_json": json.dumps(candidate_confidence_rationale, sort_keys=True),
        "supporting_finding_ids_json": json.dumps(sorted(candidate_supporting_ids)),
        "opposing_finding_ids_json": json.dumps(sorted(candidate_opposing_ids)),
        "gap_memberships_json": json.dumps(
            sorted(candidate_gap_memberships, key=lambda m: m["gap_ref"]["gap_id"]),
            sort_keys=True,
        ),
        "applied_inference_record_ids_json": json.dumps(all_irec_ids),
        "supersedes_assessment_id": supersedes_id,
        "payload_json": "{}",
        "schema_version": "v1",
    }

    # ------------------------------------------------------------------
    # Commit (FK-safe order):
    #   1. assessments (assessment_id FK for inference_records)
    #   2. inference_records (inference_record_id FK for evidence_gaps)
    #   3. evidence_gaps create/resolve
    # ------------------------------------------------------------------
    assessment_repo.create(assessment_row)

    for irec in all_irecs:
        inference_record_repo.create(irec)

    for action in gap_out["gap_actions"]:
        if action["kind"] == "open" and action["gap_row"] is not None:
            gap_repo.create(action["gap_row"])
        elif action["kind"] == "resolve":
            gap_repo.resolve(
                action["gap_id"],
                resolved_by_inference_record_id=gap_irec["inference_record_id"],
                resolved_at=now,
            )
        # "keep" → no DB mutation

    return AssessmentRecomputeResult(
        assessment_id=candidate_id,
        created=True,
        snapshot_seq=next_seq,
        status=candidate_status,
        candidate_assessment_id=candidate_id,
        schema_version=RECOMPUTE_SCHEMA_VERSION,
    )
