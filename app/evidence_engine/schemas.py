"""Evidence schemas for Factum's signal/decision model.

Design principle: Factum produces **signals** (deterministically extracted facts),
not **decisions** (choices about what the agent should do next).

Signal/Decision boundary
------------------------
- Observation  — a typed factual finding extracted by code from query results.
                 Always a signal. Never embeds "next step" intent.
- Claim        — a synthesized conclusion backed by one or more observations.
                 Still a signal. Confidence is computed deterministically.
- Recommendation — an action proposal backed by a claim.
                 A signal with priority/risk metadata; the *agent* decides
                 whether to act on it.

Governance decisions (policy enforcement, budget hard-stops) are system-enforced
constraints, not suggestions. Agents must respect them but do not produce them.

inference_level classification (Claim.inference_level)
-------------------------------------------------------
- L0 : Correlation / association only (default). No causal claim is made.
- L1 : Statistical correlation across slices (correlates_with edge).
- L2 : Temporal precedence established (temporally_precedes edge).
- L3 : Causal mechanism identified (mechanistically_explains edge).
- L4 : Confounders ruled out (eliminates_alternative edge).
- L5 : Experimental confirmation (experimentally_confirms edge).
"""

from __future__ import annotations

from typing import Any, TypedDict


# ── Edge type constants ──────────────────────────────────────────────────────
# Basic layer (existing, semantics unchanged)
EDGE_TYPE_SUPPORTS = "supports"
EDGE_TYPE_CONTRADICTS = "contradicts"
EDGE_TYPE_JUSTIFIES = "justifies"

# Causal enhancement layer (M-07)
EDGE_TYPE_CORRELATES_WITH = "correlates_with"                    # L1 — statistical correlation across slices
EDGE_TYPE_TEMPORALLY_PRECEDES = "temporally_precedes"            # L2 — temporal order established
EDGE_TYPE_MECHANISTICALLY_EXPLAINS = "mechanistically_explains"  # L3 — causal pathway identified
EDGE_TYPE_ELIMINATES_ALTERNATIVE = "eliminates_alternative"      # L4 — confounders ruled out
EDGE_TYPE_EXPERIMENTALLY_CONFIRMS = "experimentally_confirms"    # L5 — A/B or natural experiment

BASIC_EDGE_TYPES: frozenset[str] = frozenset({
    EDGE_TYPE_SUPPORTS, EDGE_TYPE_CONTRADICTS, EDGE_TYPE_JUSTIFIES,
})
CAUSAL_EDGE_TYPES: frozenset[str] = frozenset({
    EDGE_TYPE_CORRELATES_WITH, EDGE_TYPE_TEMPORALLY_PRECEDES,
    EDGE_TYPE_MECHANISTICALLY_EXPLAINS, EDGE_TYPE_ELIMINATES_ALTERNATIVE,
    EDGE_TYPE_EXPERIMENTALLY_CONFIRMS,
})
ALL_EDGE_TYPES: frozenset[str] = BASIC_EDGE_TYPES | CAUSAL_EDGE_TYPES

# Maps each causal edge type → the inference level it implies on the connected claim.
# Basic edge types are absent — they do not advance inference level.
CAUSAL_EDGE_TO_INFERENCE_LEVEL: dict[str, str] = {
    EDGE_TYPE_CORRELATES_WITH: "L1",
    EDGE_TYPE_TEMPORALLY_PRECEDES: "L2",
    EDGE_TYPE_MECHANISTICALLY_EXPLAINS: "L3",
    EDGE_TYPE_ELIMINATES_ALTERNATIVE: "L4",
    EDGE_TYPE_EXPERIMENTALLY_CONFIRMS: "L5",
}

# Ordered list for numeric comparison (index = rank)
INFERENCE_LEVEL_ORDER: list[str] = ["L0", "L1", "L2", "L3", "L4", "L5"]


class ObservationSubject(TypedDict):
    metric: str
    slice: dict[str, Any]


class Observation(TypedDict):
    observation_id: str          # Signal: unique fact identifier
    type: str                    # Signal: observation category (metric_change, funnel_drop, …)
    subject: ObservationSubject  # Signal: what entity/metric was observed
    payload: dict[str, Any]      # Signal: raw extracted values
    significance: dict[str, Any] # Signal: statistical and practical significance flags
    quality: dict[str, Any]      # Signal: data quality metadata


class Claim(TypedDict):
    claim_id: str                        # Signal: unique claim identifier
    type: str                            # Signal: claim category
    text: str                            # Signal: human-readable summary (code-generated)
    scope: dict[str, Any]                # Signal: dimensional scope of the claim
    confidence: float                    # Signal: deterministically scored confidence
    status: str                          # Signal: supported / contradicted / uncertain
    supporting_observations: list[str]   # Signal: observation_ids that support this claim
    contradicting_observations: list[str] # Signal: observation_ids that contradict
    confidence_breakdown: dict[str, Any] # Signal: per-factor confidence components
    inference_level: str                 # Signal: L0=correlation; L1-L3=causal (Phase 2)
    inference_justification: list[str]   # Signal: provenance tokens justifying the level


class ClaimRelation(TypedDict):
    """Stable intermediate relation discovered between two claims before edge materialization."""

    from_claim_id: str
    to_claim_id: str
    relation_type: str
    weight: float
    match_basis: dict[str, Any]
    score_components: dict[str, Any]
    supporting_observation_ids: list[str]
    explanation: str


REC_TYPE_ACTION = "action_required"
REC_TYPE_NO_ACTION = "no_action_required"


class Recommendation(TypedDict):
    rec_id: str                        # Signal: unique recommendation identifier
    type: str                          # Signal: "action_required" or "no_action_required"
    claim_id: str                      # Signal: primary backing claim (highest confidence)
    action_text: str                   # Signal: proposed action (agent decides whether to take it)
    priority: str                      # Signal: P0/P1/P2/P3 — agent uses to triage, not a command
    expected_impact: str               # Signal: estimated outcome if action is taken
    risk: str                          # Signal: risk level of the action
    validation_metric: dict[str, Any]  # Signal: how to verify the action worked
    causal_basis: dict[str, Any] | None  # Signal: M-10 causal evidence summary; None for old rows
    supporting_claims: list[str] | None  # Signal: all claim_ids backing this rec (multi-claim aggregation)
