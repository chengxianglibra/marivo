"""Ambiguity classifier and ask-budget engine (single-session core).

Implements the deterministic classification rule and ask-budget mechanics from
docs/specs/semantic/2026-05-31-agent-semantic-discovery-and-clarification-contracts.md
(sections 1-3). Proposal-engine candidate generation, conflict detection from live
metadata, the evidence ledger, and cross-session dedup are separate later phases.

This module is pure: it never imports SemanticProject or a backend. Blast radius is
injected as a callable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

Materiality = Literal["low", "medium", "high"]
AgreementConfidence = Literal["low", "high"]
AuthorityLevel = Literal["establishes", "validates", "candidate_only"]
EvidenceType = Literal[
    "user_confirmation",
    "knowledge",
    "source_sql",
    "comment",
    "metadata",
    "sample",
    "structural",
    "view_definition",
]
ObjectKind = Literal["entity", "dimension", "time_dimension", "metric", "relationship"]
DecisionKind = Literal[
    "entity_identity",
    "entity_primary_key",
    "time_dimension_identity",
    "time_dimension_format",
    "time_dimension_granularity",
    "dimension_meaning",
    "amount_unit",
    "dimension_vs_metric",
    "metric_decomposition",
    "metric_additivity",
    "metric_exclusion_rule",
    "metric_provenance_status",
    "relationship_existence",
    "relationship_join_keys",
    "relationship_semantics",
    "equivalent_column_choice",
    "authoring_abandoned",
]

_MATERIALITY_RANK: dict[Materiality, int] = {"low": 1, "medium": 2, "high": 3}

# decision_kind -> materiality floor. dangerous == (floor == "high").
_FLOOR_TABLE: dict[DecisionKind, Materiality] = {
    "entity_identity": "low",
    "entity_primary_key": "medium",
    "time_dimension_identity": "high",
    "time_dimension_format": "medium",
    "time_dimension_granularity": "medium",
    "dimension_meaning": "high",
    "amount_unit": "high",
    "dimension_vs_metric": "low",
    "metric_decomposition": "high",
    "metric_additivity": "medium",
    "metric_exclusion_rule": "high",
    "metric_provenance_status": "high",
    "relationship_existence": "medium",
    "relationship_join_keys": "medium",
    "relationship_semantics": "high",
    "equivalent_column_choice": "low",
    "authoring_abandoned": "low",
}


def floor_for(kind: DecisionKind) -> Materiality:
    return _FLOOR_TABLE[kind]


def is_dangerous(kind: DecisionKind) -> bool:
    return _FLOOR_TABLE[kind] == "high"


def effective_materiality(kind: DecisionKind, agent: Materiality) -> Materiality:
    floor = _FLOOR_TABLE[kind]
    return floor if _MATERIALITY_RANK[floor] >= _MATERIALITY_RANK[agent] else agent


_AUTHORITY_OF: dict[EvidenceType, AuthorityLevel] = {
    "user_confirmation": "establishes",
    "knowledge": "establishes",
    "source_sql": "establishes",
    "comment": "establishes",
    "metadata": "validates",
    "sample": "validates",
    "structural": "candidate_only",
    "view_definition": "candidate_only",
}

_AUTHORITY_WEIGHT: dict[EvidenceType, float] = {
    "user_confirmation": 4.0,
    "knowledge": 3.0,
    "source_sql": 3.0,
    "comment": 2.0,
    "metadata": 1.5,
    "sample": 1.5,
    "structural": 0.5,
    "view_definition": 0.5,
}

_SATURATION = 4.0


def candidate_confidence(evidence_types: Sequence[EvidenceType]) -> float:
    total = sum(_AUTHORITY_WEIGHT[t] for t in evidence_types)
    return min(1.0, total / _SATURATION)


def qualifying_source_count(evidence_types: Sequence[EvidenceType]) -> int:
    distinct = {t for t in evidence_types if _AUTHORITY_OF[t] in ("establishes", "validates")}
    return len(distinct)


def effective_agreement_confidence(
    agent_verdict: AgreementConfidence, qualifying_sources: int
) -> AgreementConfidence:
    if agent_verdict == "high" and qualifying_sources >= 2:
        return "high"
    return "low"


@dataclass(frozen=True)
class EvidenceRef:
    evidence_type: EvidenceType
    locator: str
    excerpt: str | None = None
    fingerprint: str = ""

    @property
    def authority(self) -> AuthorityLevel:
        return _AUTHORITY_OF[self.evidence_type]


@dataclass(frozen=True)
class Candidate:
    object_kind: ObjectKind
    proposed_id: str
    decision_kind: DecisionKind
    slot_values: Mapping[str, object]
    evidence: tuple[EvidenceRef, ...]
    semantic_delta: str

    @property
    def candidate_confidence(self) -> float:
        return candidate_confidence([e.evidence_type for e in self.evidence])


@dataclass(frozen=True)
class DecisionInput:
    decision_kind: DecisionKind
    subject_refs: tuple[str, ...]
    candidates: tuple[Candidate, ...]
    agent_materiality: Materiality
    agent_verdict: AgreementConfidence
    conflict: bool = False
    gated_by: str | None = None


@dataclass(frozen=True)
class Enrichment:
    decision_kind: DecisionKind
    subject_ref: str
    materiality: Materiality = "low"
    agreement_confidence: AgreementConfidence = "low"
    chosen: object | None = None


def to_decision_inputs(
    candidates: Sequence[Candidate],
    enrichments: Sequence[Enrichment] = (),
    *,
    conflicts: Mapping[tuple[DecisionKind, str], bool] | None = None,
) -> tuple[DecisionInput, ...]:
    """Group candidates by (decision_kind, proposed_id), attach the matching
    enrichment, and build DecisionInputs. A candidate group with no enrichment is
    treated conservatively (low materiality, low verdict) so it still surfaces."""
    enr_by_key: dict[tuple[DecisionKind, str], Enrichment] = {
        (e.decision_kind, e.subject_ref): e for e in enrichments
    }
    conflict_map = conflicts or {}
    groups: dict[tuple[DecisionKind, str], list[Candidate]] = {}
    order: list[tuple[DecisionKind, str]] = []
    for cand in candidates:
        key = (cand.decision_kind, cand.proposed_id)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(cand)

    out: list[DecisionInput] = []
    for key in order:
        kind, subject = key
        enr = enr_by_key.get(key)
        out.append(
            DecisionInput(
                decision_kind=kind,
                subject_refs=(subject,),
                candidates=tuple(groups[key]),
                agent_materiality=enr.materiality if enr is not None else "low",
                agent_verdict=enr.agreement_confidence if enr is not None else "low",
                conflict=bool(conflict_map.get(key, False)),
                gated_by=None,
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class OpenQuestion:
    id: str
    subject_refs: tuple[str, ...]
    decision_kind: DecisionKind
    gated_by: str | None
    candidates: tuple[Candidate, ...]
    materiality: Materiality
    blast_radius: int
    agreement_confidence: AgreementConfidence
    default_if_unanswered: object | None
    severity: Literal["blocker", "optional"]
    blocker_reason: Literal["conflict", "high_materiality_low_confidence", "fail_closed", None]


def question_id(decision_kind: str, subject_refs: Sequence[str], evidence_fp: str) -> str:
    h = hashlib.sha256()
    h.update(decision_kind.encode())
    for ref in sorted(subject_refs):
        h.update(b"\x00")
        h.update(ref.encode())
    h.update(b"\x00")
    h.update(evidence_fp.encode())
    return h.hexdigest()[:16]


def _classify_one(
    di: DecisionInput,
    *,
    agreement: AgreementConfidence | None = None,
) -> tuple[
    Literal["blocker", "optional"],
    Literal["conflict", "high_materiality_low_confidence", "fail_closed", None],
    object | None,
]:
    """Return (severity, blocker_reason, default_if_unanswered) for one decision.

    `agreement` is the EFFECTIVE confidence (post evidence-count floor). When None,
    it is computed from the decision's own evidence (used by direct unit tests).
    """
    if di.conflict:
        return "blocker", "conflict", None

    if agreement is None:
        ev_types = [e.evidence_type for c in di.candidates for e in c.evidence]
        agreement = effective_agreement_confidence(
            di.agent_verdict, qualifying_source_count(ev_types)
        )

    materiality = effective_materiality(di.decision_kind, di.agent_materiality)

    if agreement == "low":
        if materiality == "high":
            return "blocker", "high_materiality_low_confidence", None
        default = di.candidates[0].slot_values if di.candidates else None
        return "optional", None, default

    # high confidence -> auto-decided; choice is candidates[0], not a fallback
    return "optional", None, None


def _evidence_fingerprint(candidates: Sequence[Candidate]) -> str:
    locators = sorted(e.locator for c in candidates for e in c.evidence)
    return "|".join(locators)


def classify(
    inputs: Sequence[DecisionInput],
    *,
    blast_radius_of: Callable[[tuple[str, ...]], int],
    round_index: int = 0,
) -> tuple[OpenQuestion, ...]:
    """Run the deterministic classification rule + ask-budget mechanics.

    blast_radius_of maps a tuple of subject refs to the transitive dependent count
    (injected; this module never touches the semantic graph directly).
    """
    by_id: dict[str, OpenQuestion] = {}
    for di in inputs:
        ev_types = [e.evidence_type for c in di.candidates for e in c.evidence]
        agreement = effective_agreement_confidence(
            di.agent_verdict, qualifying_source_count(ev_types)
        )
        severity, reason, default = _classify_one(di, agreement=agreement)
        qid = question_id(di.decision_kind, di.subject_refs, _evidence_fingerprint(di.candidates))
        if round_index > 0 and di.gated_by is None:
            raise ValueError(
                f"round_index={round_index} requires gated_by on every question; "
                f"decision {di.decision_kind} on {di.subject_refs} is missing it "
                "(agent omission: it should have appeared in round one)."
            )
        if qid in by_id:  # coalesce duplicates
            continue
        by_id[qid] = OpenQuestion(
            id=qid,
            subject_refs=di.subject_refs,
            decision_kind=di.decision_kind,
            gated_by=di.gated_by,
            candidates=di.candidates,
            materiality=effective_materiality(di.decision_kind, di.agent_materiality),
            blast_radius=blast_radius_of(di.subject_refs),
            agreement_confidence=agreement,
            default_if_unanswered=default,
            severity=severity,
            blocker_reason=reason,
        )

    def sort_key(q: OpenQuestion) -> tuple[int, int]:
        blocker_first = 0 if q.severity == "blocker" else 1
        weight = _MATERIALITY_RANK[q.materiality] * max(q.blast_radius, 1)
        return (blocker_first, -weight)

    return tuple(sorted(by_id.values(), key=sort_key))


def select_for_user(
    questions: Sequence[OpenQuestion], *, k: int
) -> tuple[tuple[OpenQuestion, ...], tuple[OpenQuestion, ...], int]:
    """Split classified questions into (blockers, top-K optional confirmations,
    assumption_count). Blockers are never capped. Optionals beyond K become silent
    recorded assumptions (counted, not shown)."""
    blockers = tuple(q for q in questions if q.severity == "blocker")
    optionals = tuple(q for q in questions if q.severity == "optional")
    shown = optionals[:k]
    assumption_count = len(optionals) - len(shown)
    return blockers, shown, assumption_count
