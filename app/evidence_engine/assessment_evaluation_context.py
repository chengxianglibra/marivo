"""Assessment Evaluation Context builder (Phase 4f-1).

Assembles the deterministic, canonical input bundle required before any
assessment recompute.  The resulting :class:`AssessmentEvaluationContext`
is the *only* structured input the rule engine may consume.

Design contract: ``docs/analysis/evidence-engine/assessment-evaluation-context.md``

Authority boundary (§Fixed Design Decision 1)
----------------------------------------------
Only committed canonical objects are allowed as inputs:

- target ``proposition``
- same-proposition committed ``assessment`` snapshots
- same-proposition current ``open`` ``EvidenceGap`` objects
- same-session committed canonical ``finding`` layer
- caller-supplied ``trigger_finding_ids``

Not allowed: UI projections, uncommitted findings, other propositions'
assessments/gaps, black-box model outputs.

Assembly algorithm (8 phases)
------------------------------
1.  Proposition anchor load — extract ``assessment_type``, ``origin_kind``,
    ``seed_finding_refs`` from the pre-loaded proposition row.
2.  Prior assessment / open gap load — derive ``prior_assessment_ids``,
    ``current_latest_assessment_id``, ``open_gap_ids``.
3.  Seed hydration — resolve seed finding refs to committed findings in the
    same session → ``resolved_seed_finding_ids``.
4.  Trigger normalization — dedup + sort caller-supplied trigger ids.
5.  Carry-forward closure replay — carry forward finding inputs from the
    latest assessment's support/oppose sets, inference record input sets, and
    open-gap related finding sets.
6.  Proposition-compatible expansion — filter (trigger ∪ carry-forward) to
    findings whose family and subject are compatible with this proposition.
7.  Authored proposition discovery fallback — when an ``agent_authored``
    proposition has no seeds, no prior assessment, and no triggers, scan the
    session's committed findings for compatible candidates.
8.  Candidate set finalization — stable dedup union of all collected sets;
    sort by ``finding_id ASC``.

Phase: 4f-1
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.storage.evidence_repositories import (
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

EVALUATION_CONTEXT_SCHEMA_VERSION = "assessment_evaluation_context.v1"

# v1 finding-type → assessment_type compatibility mapping.
# ``observation`` findings are compatible with every assessment type.
_COMPATIBLE_FINDING_TYPES: dict[str, str] = {
    "delta": "change_assessment",
    "decomposition_item": "decomposition_assessment",
    "anomaly_candidate": "anomaly_assessment",
    "correlation_result": "correlation_assessment",
    "test_result": "test_hypothesis_assessment",
    "forecast_point": "forecast_assessment",
}
_OBSERVATION_FINDING_TYPE = "observation"


# ---------------------------------------------------------------------------
# Public TypedDict
# ---------------------------------------------------------------------------


class AssessmentEvaluationContext(TypedDict):
    """Canonical input boundary for a single assessment recompute.

    Fields
    ------
    session_id:
        The session this context is scoped to.
    proposition:
        Full deserialized proposition row (from PropositionRepository).
    assessment_type:
        Derived from ``proposition.assessment_anchor_json["assessment_type"]``.
    candidate_assessment_id:
        Pre-allocated by the caller; not yet committed.  Binds candidate
        InferenceRecord / EvidenceGap objects produced during the recompute.
    current_latest_assessment_id:
        assessment_id of the current latest committed snapshot, or ``None``
        if the proposition has never been assessed.
    prior_assessment_ids:
        All committed assessment snapshot ids, ordered by ``snapshot_seq ASC``.
    open_gap_ids:
        gap_ids of currently ``open`` EvidenceGaps for this proposition.
    resolved_seed_finding_ids:
        Seed finding ids that could be resolved to committed findings in the
        same session, in seed-ref order.
    trigger_finding_ids:
        Normalised (deduped + sorted) trigger finding ids passed in by the
        caller.
    candidate_finding_ids:
        Stable dedup union of all finding input sources; the rule engine's
        sole finding input boundary.
    schema_version:
        Fixed at ``"assessment_evaluation_context.v1"``.
    """

    session_id: str
    proposition: dict[str, Any]
    assessment_type: str
    candidate_assessment_id: str
    current_latest_assessment_id: str | None
    prior_assessment_ids: list[str]
    open_gap_ids: list[str]
    resolved_seed_finding_ids: list[str]
    trigger_finding_ids: list[str]
    candidate_finding_ids: list[str]
    schema_version: str


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def build_assessment_evaluation_context(
    *,
    session_id: str,
    proposition_id: str,
    proposition: dict[str, Any],
    candidate_assessment_id: str,
    trigger_finding_ids: list[str],
    assessment_repo: AssessmentRepository,
    gap_repo: EvidenceGapRepository,
    finding_repo: FindingRepository,
    inference_record_repo: InferenceRecordRepository,
) -> AssessmentEvaluationContext:
    """Assemble the evaluation context for a single assessment recompute.

    Parameters
    ----------
    session_id:
        Session scope for this recompute.
    proposition_id:
        The proposition being assessed.
    proposition:
        Pre-loaded, fully deserialized proposition row.  Must belong to
        ``session_id`` and match ``proposition_id`` (validated on entry).
    candidate_assessment_id:
        Pre-allocated candidate assessment id supplied by the caller.
    trigger_finding_ids:
        Finding ids that triggered this recompute (may be empty).
    assessment_repo:
        Repository for reading prior assessment snapshots.
    gap_repo:
        Repository for reading open evidence gaps.
    finding_repo:
        Repository for resolving finding refs and fallback scans.
    inference_record_repo:
        Repository for reading inference records linked to the latest
        assessment (carry-forward, Phase 5).

    Returns
    -------
    AssessmentEvaluationContext
        The assembled, deterministic context.

    Raises
    ------
    ValueError
        If ``proposition`` belongs to a different ``session_id`` or a
        different ``proposition_id`` than supplied.
    """
    # ------------------------------------------------------------------
    # Phase 1 — proposition anchor load
    # ------------------------------------------------------------------
    if proposition["session_id"] != session_id:
        raise ValueError(
            f"proposition.session_id {proposition['session_id']!r} != session_id {session_id!r}"
        )
    if proposition["proposition_id"] != proposition_id:
        raise ValueError(
            f"proposition.proposition_id {proposition['proposition_id']!r} != "
            f"proposition_id {proposition_id!r}"
        )

    assessment_anchor = proposition["assessment_anchor_json"]
    assessment_type: str = assessment_anchor["assessment_type"]

    origin_json = proposition["origin_json"]
    origin_kind: str = origin_json["kind"]

    # seed_finding_refs_json is already deserialized by the repository
    seed_finding_refs: list[dict[str, Any]] = proposition.get("seed_finding_refs_json") or []

    proposition_subject: dict[str, Any] = proposition.get("subject_json") or {}

    # ------------------------------------------------------------------
    # Phase 2 — prior assessment / open gap load
    # ------------------------------------------------------------------
    prior_snapshots = assessment_repo.list_by_proposition(proposition_id)
    # list_by_proposition already returns snapshot_seq ASC
    prior_assessment_ids: list[str] = [a["assessment_id"] for a in prior_snapshots]
    current_latest_assessment_id: str | None = (
        prior_snapshots[-1]["assessment_id"] if prior_snapshots else None
    )

    open_gaps = gap_repo.list_by_proposition(proposition_id, status="open")
    open_gap_ids: list[str] = [g["gap_id"] for g in open_gaps]

    # ------------------------------------------------------------------
    # Phase 3 — seed hydration
    # ------------------------------------------------------------------
    resolved_seed_finding_ids: list[str] = []
    for ref in seed_finding_refs:
        finding_ref = ref.get("finding_ref", {})
        fid = finding_ref.get("finding_id", "")
        if not fid:
            continue
        # Ref must be same-session; check before DB lookup
        ref_session = finding_ref.get("session_id", "")
        if ref_session and ref_session != session_id:
            continue
        f = finding_repo.get(fid)
        if f is not None and f["session_id"] == session_id:
            resolved_seed_finding_ids.append(fid)

    # ------------------------------------------------------------------
    # Phase 4 — trigger normalization
    # ------------------------------------------------------------------
    normalized_triggers: list[str] = _stable_dedup(trigger_finding_ids)

    # ------------------------------------------------------------------
    # Phase 5 — carry-forward closure replay
    # ------------------------------------------------------------------
    # Fetch once here; reused in Phase 7 to avoid a second round-trip.
    latest_assessment: dict[str, Any] | None = (
        assessment_repo.get(current_latest_assessment_id)
        if current_latest_assessment_id is not None
        else None
    )

    carry_forward_raw: list[str] = []
    if latest_assessment is not None:
        carry_forward_raw.extend(latest_assessment.get("supporting_finding_ids_json") or [])
        carry_forward_raw.extend(latest_assessment.get("opposing_finding_ids_json") or [])
        for record_id in latest_assessment.get("applied_inference_record_ids_json") or []:
            rec = inference_record_repo.get(record_id)
            if rec is not None:
                carry_forward_raw.extend(rec.get("input_finding_ids_json") or [])

    # Open-gap related findings (also part of Phase 5 per spec §Phase 5)
    for gap in open_gaps:
        carry_forward_raw.extend(gap.get("related_finding_ids_json") or [])

    # Filter carry-forward: finding must exist and be in same session
    carry_forward_finding_ids: list[str] = []
    for fid in carry_forward_raw:
        if fid in carry_forward_finding_ids:
            continue
        f = finding_repo.get(fid)
        if f is not None and f["session_id"] == session_id:
            carry_forward_finding_ids.append(fid)

    # ------------------------------------------------------------------
    # Phase 6 — proposition-compatible trigger expansion
    # (applied to both trigger and carry-forward sets)
    # ------------------------------------------------------------------
    compatible_triggers: list[str] = _filter_compatible(
        normalized_triggers, assessment_type, proposition_subject, session_id, finding_repo
    )
    compatible_carry_forward: list[str] = _filter_compatible(
        carry_forward_finding_ids, assessment_type, proposition_subject, session_id, finding_repo
    )

    # ------------------------------------------------------------------
    # Phase 7 — authored proposition discovery fallback
    # ------------------------------------------------------------------
    discovery_fallback_ids: list[str] = []
    if _should_run_discovery_fallback(
        origin_kind=origin_kind,
        resolved_seed_finding_ids=resolved_seed_finding_ids,
        current_latest_assessment_id=current_latest_assessment_id,
        latest_assessment=latest_assessment,
        normalized_triggers=normalized_triggers,
    ):
        all_session_findings = finding_repo.list_by_session(session_id)
        for f in all_session_findings:
            fid = f["finding_id"]
            if _is_compatible(f, assessment_type, proposition_subject, session_id):
                discovery_fallback_ids.append(fid)

    # ------------------------------------------------------------------
    # Phase 8 — candidate set finalization
    # ------------------------------------------------------------------
    # Union of: resolved seeds, compatible triggers, compatible carry-forward,
    # discovery fallback.  Stable dedup + sort by finding_id ASC.
    all_candidate_ids = (
        resolved_seed_finding_ids
        + compatible_triggers
        + compatible_carry_forward
        + discovery_fallback_ids
    )
    candidate_finding_ids = _stable_dedup(all_candidate_ids)

    return AssessmentEvaluationContext(
        session_id=session_id,
        proposition=proposition,
        assessment_type=assessment_type,
        candidate_assessment_id=candidate_assessment_id,
        current_latest_assessment_id=current_latest_assessment_id,
        prior_assessment_ids=prior_assessment_ids,
        open_gap_ids=open_gap_ids,
        resolved_seed_finding_ids=resolved_seed_finding_ids,
        trigger_finding_ids=normalized_triggers,
        candidate_finding_ids=candidate_finding_ids,
        schema_version=EVALUATION_CONTEXT_SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compatible_finding_types(assessment_type: str) -> set[str]:
    """Return the set of finding_types compatible with *assessment_type*.

    ``observation`` findings are always included (compatible with any type).
    Unknown assessment_types return the empty set (conservative).
    """
    result: set[str] = {_OBSERVATION_FINDING_TYPE}
    for ft, at in _COMPATIBLE_FINDING_TYPES.items():
        if at == assessment_type:
            result.add(ft)
    return result


def _subject_compatible(
    proposition_subject: dict[str, Any],
    finding_subject: dict[str, Any],
) -> bool:
    """Return True if *finding_subject* does not conflict with *proposition_subject*.

    v1 rules: for each of metric, entity, grain — if the proposition subject
    declares a non-null value, the finding's value on that axis must either
    match or be null (i.e. a finding with a null axis is always compatible).

    Slice filtering is deferred to a future gate; always passes in v1.
    """
    for axis in ("metric", "entity", "grain"):
        prop_val = proposition_subject.get(axis)
        find_val = finding_subject.get(axis)
        if prop_val is None:
            # Proposition doesn't constrain this axis → any finding value is ok
            continue
        if find_val is None:
            # Finding is unconstrained on this axis → compatible
            continue
        if prop_val != find_val:
            return False
    return True


def _is_compatible(
    finding: dict[str, Any],
    assessment_type: str,
    proposition_subject: dict[str, Any],
    session_id: str,
) -> bool:
    """Return True iff *finding* passes all v1 compatibility checks."""
    if finding["session_id"] != session_id:
        return False
    compatible_types = _compatible_finding_types(assessment_type)
    finding_type = finding.get("finding_type", "")
    if finding_type not in compatible_types:
        return False
    finding_subject: dict[str, Any] = finding.get("subject_json") or {}
    return _subject_compatible(proposition_subject, finding_subject)


def _filter_compatible(
    finding_ids: list[str],
    assessment_type: str,
    proposition_subject: dict[str, Any],
    session_id: str,
    finding_repo: FindingRepository,
) -> list[str]:
    """Return the subset of *finding_ids* that pass the v1 compatibility check.

    Preserves order; unresolvable ids are silently excluded.
    """
    result: list[str] = []
    for fid in finding_ids:
        f = finding_repo.get(fid)
        if f is None:
            continue
        if _is_compatible(f, assessment_type, proposition_subject, session_id):
            result.append(fid)
    return result


def _should_run_discovery_fallback(
    *,
    origin_kind: str,
    resolved_seed_finding_ids: list[str],
    current_latest_assessment_id: str | None,
    latest_assessment: dict[str, Any] | None,
    normalized_triggers: list[str],
) -> bool:
    """Return True iff the authored-proposition discovery fallback should run.

    Conditions (all must hold per spec §Phase 7):
    1. ``origin_kind == "agent_authored"``
    2. ``resolved_seed_finding_ids == []``
    3. No prior latest assessment, or the latest assessment's support+oppose
       closure is empty.
    4. ``normalized_triggers == []``
    """
    if origin_kind != "agent_authored":
        return False
    if resolved_seed_finding_ids:
        return False
    if normalized_triggers:
        return False
    if current_latest_assessment_id is None:
        return True
    # latest_assessment exists — check whether its closure is empty
    if latest_assessment is None:
        return True
    support = latest_assessment.get("supporting_finding_ids_json") or []
    oppose = latest_assessment.get("opposing_finding_ids_json") or []
    return len(support) == 0 and len(oppose) == 0


def _stable_dedup(ids: list[str]) -> list[str]:
    """Return a stable deduplicated + sorted list of finding ids.

    Deduplication preserves first occurrence; the result is then sorted
    ascending by ``finding_id`` for canonical stable order.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for fid in ids:
        if fid not in seen:
            seen.add(fid)
            unique.append(fid)
    return sorted(unique)
