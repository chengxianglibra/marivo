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
- L1 : Temporal precedence established (cause precedes effect in time).
- L2 : Mechanism identified (a plausible causal pathway is described).
- L3 : Counterfactual / experimental evidence (A/B test, natural experiment).

Phase 1 only produces L0. Higher levels are reserved for Phase 2 causal reasoning.
"""

from __future__ import annotations

from typing import Any, TypedDict


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


class Recommendation(TypedDict):
    rec_id: str                   # Signal: unique recommendation identifier
    claim_id: str                 # Signal: backing claim
    action_text: str              # Signal: proposed action (agent decides whether to take it)
    priority: str                 # Signal: P0/P1/P2 — agent uses to triage, not a command
    expected_impact: str          # Signal: estimated outcome if action is taken
    risk: str                     # Signal: risk level of the action
    validation_metric: dict[str, Any]  # Signal: how to verify the action worked
