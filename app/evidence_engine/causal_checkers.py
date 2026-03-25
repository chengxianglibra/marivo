"""Deterministic causal checkers for Factum's evidence engine (M-09).

Each checker inspects accumulated observations and tentative claims to
determine whether the evidence warrants upgrading a claim's inference_level.
All logic is purely deterministic — no LLMs involved.

Checker chain (executed in order by CausalCheckerRegistry.run_all):
  1. CrossSliceConsistencyChecker  — L0 → L1  (directional consistency across slices)
  2. CrossScopeCorrelationChecker  — L0 → L1  (cross-scope temporal correlation)
  3. CrossMetricCorrelationChecker — L0 → L1  (multi-metric consistency via claim relations)
  4. MechanisticExplanationChecker — L0/L1/L2 → L3  (contribution_shift explanation)
  5. TemporalPrecedenceChecker     — L1/L1-like relation → L2  (strict claim-level temporal ordering)
  6. DoseResponseChecker           — L1+ bonus (Spearman ρ ≥ 0.7 on numeric dimension)
  7. ReversalChecker               — L2+ bonus (sustained direction reversal ≥ 2 periods)
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.evidence_engine.schemas import (
    EDGE_TYPE_CORRELATES_WITH,
    EDGE_TYPE_MECHANISTICALLY_EXPLAINS,
    ClaimRelation,
    INFERENCE_LEVEL_ORDER,
)


# ── CausalEdge ───────────────────────────────────────────────────────────────


@dataclass
class CausalEdge:
    """A causal edge to be materialized in the evidence graph."""

    from_node_id: str
    from_node_type: str
    to_node_id: str
    to_node_type: str
    edge_type: str
    weight: float
    explanation: str


# ── LevelUpgrade ─────────────────────────────────────────────────────────────


@dataclass
class LevelUpgrade:
    """A proposed upgrade to a claim's inference_level from a single checker."""

    claim_id: str
    new_level: str                         # "L1", "L2", etc.
    justification_tokens: list[str] = field(default_factory=list)
    confidence_boost: float = 0.0
    causal_edges: list[CausalEdge] = field(default_factory=list)


# ── Base class ────────────────────────────────────────────────────────────────


class CausalChecker(ABC):
    """Abstract base for deterministic causal checkers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for logging / debugging."""

    @abstractmethod
    def check(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        """Inspect claims + observations and return upgrade proposals."""


# ── Registry ──────────────────────────────────────────────────────────────────


class CausalCheckerRegistry:
    """Ordered registry that runs all checkers and merges their outputs.

    Merge strategy (per claim_id):
    - Level: keep highest proposed level (never downgrade from current).
    - Justification tokens: union of all tokens across checkers.
    - Confidence boost: sum of all boosts, capped at +0.12 for consistency
      with pipeline-level causal-edge-derived boosts.
    """

    def __init__(self) -> None:
        self._checkers: list[CausalChecker] = []

    def register(self, checker: CausalChecker) -> None:
        self._checkers.append(checker)

    def run_all(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        """Run all checkers and return merged upgrades keyed by claim_id."""
        merged: dict[str, LevelUpgrade] = {}

        for checker in self._checkers:
            checker_upgrades = checker.check(
                claims,
                observations,
                edges,
                relations=relations,
            )
            for upgrade in checker_upgrades:
                cid = upgrade.claim_id
                if cid not in merged:
                    merged[cid] = LevelUpgrade(
                        claim_id=cid,
                        new_level=upgrade.new_level,
                        justification_tokens=list(upgrade.justification_tokens),
                        confidence_boost=upgrade.confidence_boost,
                        causal_edges=list(upgrade.causal_edges),
                    )
                else:
                    existing = merged[cid]
                    # Keep highest level
                    if (INFERENCE_LEVEL_ORDER.index(upgrade.new_level) >
                            INFERENCE_LEVEL_ORDER.index(existing.new_level)):
                        existing.new_level = upgrade.new_level
                    # Union tokens (preserve order, deduplicate)
                    for token in upgrade.justification_tokens:
                        if token not in existing.justification_tokens:
                            existing.justification_tokens.append(token)
                    existing.confidence_boost = min(
                        0.12,
                        existing.confidence_boost + upgrade.confidence_boost,
                    )
                    # Merge causal edges (deduplicate by from+to+type)
                    existing_edge_keys = {
                        (e.from_node_id, e.to_node_id, e.edge_type)
                        for e in existing.causal_edges
                    }
                    for edge in upgrade.causal_edges:
                        key = (edge.from_node_id, edge.to_node_id, edge.edge_type)
                        if key not in existing_edge_keys:
                            existing.causal_edges.append(edge)
                            existing_edge_keys.add(key)

        return list(merged.values())


# ── Checker implementations ───────────────────────────────────────────────────


class CrossSliceConsistencyChecker(CausalChecker):
    """L0 → L1: directional consistency of delta_pct across metric slices.

    If >80% of observations for a metric share the same delta sign, the
    effect is consistent across slices → upgrade to L1 (correlates_with).
    """

    CONSISTENCY_THRESHOLD = 0.80
    MIN_OBSERVATIONS = 2

    @property
    def name(self) -> str:
        return "cross_slice_consistency"

    def check(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        # Index observations by metric
        obs_by_metric: dict[str, list[dict[str, Any]]] = {}
        for obs in observations:
            metric = obs.get("subject", {}).get("metric", "")
            if metric:
                obs_by_metric.setdefault(metric, []).append(obs)

        upgrades: list[LevelUpgrade] = []
        for claim in claims:
            if claim.get("inference_level", "L0") != "L0":
                continue

            claim_metric = claim.get("scope", {}).get("metric", "")
            if not claim_metric:
                continue

            # Cross-slice consistency: look at all observations for this metric
            # across the session (not just those supporting this specific claim slice).
            metric_obs = obs_by_metric.get(claim_metric, [])

            deltas = [
                float(o["payload"]["delta_pct"])
                for o in metric_obs
                if o.get("payload", {}).get("delta_pct") is not None
            ]

            if len(deltas) < self.MIN_OBSERVATIONS:
                continue

            positive = sum(1 for d in deltas if d > 0)
            negative = sum(1 for d in deltas if d < 0)
            majority = max(positive, negative)
            consistency_rate = majority / len(deltas)

            if consistency_rate > self.CONSISTENCY_THRESHOLD:
                direction = "positive" if positive >= negative else "negative"
                token = (
                    f"cross_slice_consistency:{majority}/{len(deltas)}"
                    f"_slices_{direction}→L1"
                )
                upgrades.append(LevelUpgrade(
                    claim_id=claim["claim_id"],
                    new_level="L1",
                    justification_tokens=[token],
                    confidence_boost=0.02,
                ))

        return upgrades


class TemporalPrecedenceChecker(CausalChecker):
    """Promote relation-backed claims to L2 when real time windows establish precedence.

    The checker consumes existing claim relations as the entry point for pairing:
    it never mines raw claim pairs on its own. A claim relation is eligible when:
    - relation_type is ``correlates_with``
    - relation category is an overlapping-scope variant (exact/subset/overlap)
    - both claims have supporting observations with real ``observed_window`` values

    Promotion is conservative:
    - only strict, non-overlapping claim windows qualify
    - step execution order is ignored as causal evidence
    - only the temporally later claim is upgraded to L2
    """

    ELIGIBLE_RELATION_CATEGORIES = frozenset({"exact_match", "subset_or_overlap"})

    @property
    def name(self) -> str:
        return "temporal_precedence"

    def check(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        if not relations:
            return []

        obs_by_id = {
            str(observation["observation_id"]): observation
            for observation in observations
            if observation.get("observation_id")
        }
        claim_by_id = {
            str(claim.get("claim_id")): claim
            for claim in claims
            if claim.get("claim_id")
        }
        upgrades: list[LevelUpgrade] = []
        emitted_pairs: set[tuple[str, str]] = set()

        for relation in relations:
            if relation.get("relation_type") != EDGE_TYPE_CORRELATES_WITH:
                continue
            match_basis = relation.get("match_basis", {})
            if str(match_basis.get("category", "")) not in self.ELIGIBLE_RELATION_CATEGORIES:
                continue

            left_claim = claim_by_id.get(str(relation.get("from_claim_id", "")))
            right_claim = claim_by_id.get(str(relation.get("to_claim_id", "")))
            if left_claim is None or right_claim is None:
                continue

            left_window = self._claim_window(left_claim, obs_by_id)
            right_window = self._claim_window(right_claim, obs_by_id)
            if left_window is None or right_window is None:
                continue

            precedence = self._resolve_precedence(left_claim, left_window, right_claim, right_window)
            if precedence is None:
                continue

            earlier_claim, earlier_window, later_claim, later_window = precedence
            pair_key = (earlier_claim["claim_id"], later_claim["claim_id"])
            if pair_key in emitted_pairs:
                continue
            emitted_pairs.add(pair_key)

            lag_days = _date_diff_days(earlier_window["end"], later_window["start"])
            if lag_days < 0:
                continue
            token = (
                "temporal_precedence:"
                f"{earlier_claim['claim_id']}→{later_claim['claim_id']}:"
                f"lag={lag_days}d→L2"
            )
            edge = CausalEdge(
                from_node_id=earlier_claim["claim_id"],
                from_node_type="claim",
                to_node_id=later_claim["claim_id"],
                to_node_type="claim",
                edge_type="temporally_precedes",
                weight=0.8,
                explanation=(
                    f"Claim {earlier_claim['claim_id']} "
                    f"(window end={earlier_window['end']}) precedes "
                    f"claim {later_claim['claim_id']} "
                    f"(window start={later_window['start']}) by {lag_days} days."
                ),
            )
            upgrades.append(LevelUpgrade(
                claim_id=later_claim["claim_id"],
                new_level="L2",
                justification_tokens=[token],
                confidence_boost=0.03,
                causal_edges=[edge],
            ))

        return upgrades

    def _claim_window(
        self,
        claim: dict[str, Any],
        observation_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, str] | None:
        # Use a conservative envelope across all supporting windows. This can
        # yield false negatives for disjoint support, but it avoids claiming
        # precedence when a claim has overlapping evidence in another window.
        windows = [
            observation.get("observed_window")
            for observation_id in claim.get("supporting_observations", [])
            for observation in [observation_by_id.get(str(observation_id))]
            if observation is not None and observation.get("observed_window") is not None
        ]
        if not windows:
            return None
        starts = [str(window["start"]) for window in windows if window.get("start")]
        ends = [str(window["end"]) for window in windows if window.get("end")]
        if not starts or not ends:
            return None
        return {
            "start": min(starts),
            "end": max(ends),
        }

    def _resolve_precedence(
        self,
        left_claim: dict[str, Any],
        left_window: dict[str, str],
        right_claim: dict[str, Any],
        right_window: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any], dict[str, str]] | None:
        if left_window["end"] < right_window["start"]:
            return left_claim, left_window, right_claim, right_window
        if right_window["end"] < left_window["start"]:
            return right_claim, right_window, left_claim, left_window
        return None


class DoseResponseChecker(CausalChecker):
    """L1+ bonus: Spearman correlation between numeric dimension and delta_pct.

    If |ρ| ≥ 0.7 for any numeric slice dimension, adds a dose_response
    justification token without changing the inference level.
    """

    SPEARMAN_THRESHOLD = 0.7
    MIN_OBSERVATIONS = 3

    @property
    def name(self) -> str:
        return "dose_response"

    def check(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        obs_by_id = {o["observation_id"]: o for o in observations}
        upgrades: list[LevelUpgrade] = []

        for claim in claims:
            current_level = claim.get("inference_level", "L0")
            if current_level not in ("L1", "L2", "L3", "L4", "L5"):
                continue

            supporting_obs_ids = set(claim.get("supporting_observations", []))
            claim_scope = claim.get("scope", {})
            claim_metric = str(claim_scope.get("metric", ""))
            for obs_id in supporting_obs_ids:
                obs = obs_by_id.get(obs_id)
                if obs is None or obs.get("type") != "correlation_result":
                    continue
                payload = obs.get("payload", {})
                rho = float(payload.get("rho", 0.0))
                if abs(rho) < self.SPEARMAN_THRESHOLD:
                    continue
                left_metric = str(payload.get("left_metric", ""))
                right_metric = str(payload.get("right_metric", ""))
                if claim_metric not in (left_metric, right_metric):
                    continue
                token = f"dose_response_precomputed:ρ={rho:.3f}"
                upgrades.append(LevelUpgrade(
                    claim_id=claim["claim_id"],
                    new_level=current_level,
                    justification_tokens=[token],
                    confidence_boost=0.02,
                ))
                break  # One match is enough per claim

            if any(u.claim_id == claim["claim_id"] for u in upgrades):
                continue

        upgraded_claim_ids = {u.claim_id for u in upgrades}

        for claim in claims:
            if claim["claim_id"] in upgraded_claim_ids:
                continue
            current_level = claim.get("inference_level", "L0")
            if current_level not in ("L1", "L2", "L3", "L4", "L5"):
                continue

            supporting_obs = [
                obs_by_id[oid]
                for oid in claim.get("supporting_observations", [])
                if oid in obs_by_id
            ]

            if len(supporting_obs) < self.MIN_OBSERVATIONS:
                continue

            slices = [o.get("subject", {}).get("slice", {}) for o in supporting_obs]
            if not slices or not slices[0]:
                continue

            dim_keys = list(slices[0].keys())
            found_bonus = False
            for dim_key in dim_keys:
                if found_bonus:
                    break
                dim_values = [s.get(dim_key) for s in slices]
                deltas = [o["payload"].get("delta_pct") for o in supporting_obs]

                pairs = [
                    (v, d)
                    for v, d in zip(dim_values, deltas)
                    if v is not None and d is not None
                ]

                if len(pairs) < self.MIN_OBSERVATIONS:
                    continue

                try:
                    numeric_values = [float(v) for v, _ in pairs]
                    delta_values = [float(d) for _, d in pairs]
                except (ValueError, TypeError):
                    continue

                rho = _spearman_correlation(numeric_values, delta_values)
                if abs(rho) >= self.SPEARMAN_THRESHOLD:
                    token = f"dose_response:{dim_key}:ρ={rho:.2f}→L1_bonus"
                    upgrades.append(LevelUpgrade(
                        claim_id=claim["claim_id"],
                        new_level=current_level,  # Level unchanged
                        justification_tokens=[token],
                        confidence_boost=0.02,
                    ))
                    found_bonus = True

        return upgrades


class MechanisticExplanationChecker(CausalChecker):
    """Upgrade claims when a contribution_shift observation explains the top slice."""

    @property
    def name(self) -> str:
        return "mechanistic_explanation"

    def check(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        obs_by_metric: dict[str, list[dict[str, Any]]] = {}
        for obs in observations:
            if obs.get("type") != "contribution_shift":
                continue
            metric = str(obs.get("subject", {}).get("metric", ""))
            if metric:
                obs_by_metric.setdefault(metric, []).append(obs)

        upgrades: list[LevelUpgrade] = []
        for claim in claims:
            claim_scope = claim.get("scope", {})
            claim_metric = str(claim_scope.get("metric", ""))
            claim_slice = claim_scope.get("slice", {})
            if not claim_metric or not isinstance(claim_slice, dict):
                continue

            for obs in obs_by_metric.get(claim_metric, []):
                payload = obs.get("payload", {})
                segment_name = str(payload.get("segment_name", ""))
                biggest_shift = payload.get("biggest_shift_segment")
                if not segment_name or biggest_shift is None:
                    continue
                if not _claim_matches_contribution_shift(
                    claim_slice,
                    obs.get("subject", {}).get("slice", {}),
                    segment_name,
                    biggest_shift,
                ):
                    continue

                token = f"mechanistic_explanation:{segment_name}:{biggest_shift}→L3"
                edge = CausalEdge(
                    from_node_id=obs["observation_id"],
                    from_node_type="observation",
                    to_node_id=claim["claim_id"],
                    to_node_type="claim",
                    edge_type=EDGE_TYPE_MECHANISTICALLY_EXPLAINS,
                    weight=0.9,
                    explanation=(
                        f"Contribution shift in {segment_name} points to {biggest_shift} "
                        f"as the strongest contributor for the claim's slice."
                    ),
                )
                upgrades.append(LevelUpgrade(
                    claim_id=claim["claim_id"],
                    new_level="L3",
                    justification_tokens=[token],
                    confidence_boost=0.05,
                    causal_edges=[edge],
                ))
                break

        return upgrades


class ReversalChecker(CausalChecker):
    """L2+ bonus: sustained direction reversal across ≥2 consecutive periods.

    Observations are sorted by temporal_order. A reversal is detected when
    the final ≥2 observations have opposite delta sign to the initial majority.
    """

    MIN_REVERSAL_PERIODS = 2

    @property
    def name(self) -> str:
        return "reversal"

    def check(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        obs_by_id = {o["observation_id"]: o for o in observations}

        upgrades: list[LevelUpgrade] = []
        for claim in claims:
            current_level = claim.get("inference_level", "L0")
            if current_level not in ("L2", "L3", "L4", "L5"):
                continue

            supporting_obs = [
                obs_by_id[oid]
                for oid in claim.get("supporting_observations", [])
                if oid in obs_by_id
            ]

            if len(supporting_obs) < self.MIN_REVERSAL_PERIODS + 1:
                continue

            sorted_obs = sorted(
                supporting_obs,
                key=lambda o: o.get("temporal_order", 0),
            )

            deltas = [
                float(o["payload"]["delta_pct"])
                for o in sorted_obs
                if o.get("payload", {}).get("delta_pct") is not None
            ]

            if len(deltas) < self.MIN_REVERSAL_PERIODS + 1:
                continue

            reversal_count = _detect_reversal(deltas, self.MIN_REVERSAL_PERIODS)
            if reversal_count >= self.MIN_REVERSAL_PERIODS:
                token = f"reversal:sustained_{reversal_count}_periods→L2_bonus"
                upgrades.append(LevelUpgrade(
                    claim_id=claim["claim_id"],
                    new_level=current_level,  # Level unchanged
                    justification_tokens=[token],
                    confidence_boost=0.02,
                ))

        return upgrades


class CrossScopeCorrelationChecker(CausalChecker):
    """L0 → L1: cross-scope temporal correlation.

    Two mechanisms:
    1. Automatic: When observation A's observed_window precedes observation B's window,
       propose a correlates_with edge: A → claim_for_B.
    2. Explicit: When a causal_candidate observation has candidate_cause_observation_id,
       propose a correlates_with edge from that cause observation to the claim.

    This enables cross-step causal chains like:
    - Step A: sys_titan query volume +524%
      → Step B: others RG queue congestion
        → Step C: oneservice RG timeout failures
    """

    MAX_LAG_DAYS = 7  # Maximum temporal lag to consider

    @property
    def name(self) -> str:
        return "cross_scope_correlation"

    def check(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        obs_by_id = {o["observation_id"]: o for o in observations}
        upgrades: list[LevelUpgrade] = []
        upgraded_claim_ids: set[str] = set()

        # 1. Handle explicit causal_candidate observations
        for obs in observations:
            if obs.get("type") != "causal_candidate":
                continue
            cause_id = obs.get("payload", {}).get("candidate_cause_observation_id")
            if not cause_id or cause_id not in obs_by_id:
                continue
            # Find claim that has this observation as supporting
            claim = self._find_claim_for_obs(obs["observation_id"], claims)
            if not claim:
                continue
            if claim["claim_id"] in upgraded_claim_ids:
                continue
            cause_short = cause_id[:12] if len(cause_id) >= 12 else cause_id
            token = f"cross_scope_explicit:{cause_short}→L1"
            edge = CausalEdge(
                from_node_id=cause_id,
                from_node_type="observation",
                to_node_id=claim["claim_id"],
                to_node_type="claim",
                edge_type=EDGE_TYPE_CORRELATES_WITH,
                weight=0.7,
                explanation=f"Explicit causal_candidate link from observation {cause_short}",
            )
            upgrades.append(LevelUpgrade(
                claim_id=claim["claim_id"],
                new_level="L1",
                justification_tokens=[token],
                confidence_boost=0.03,
                causal_edges=[edge],
            ))
            upgraded_claim_ids.add(claim["claim_id"])

        # 2. Automatic temporal predecessor detection
        # Build list of windowed observations
        windowed_obs = [
            o for o in observations
            if o.get("observed_window") is not None
        ]
        if len(windowed_obs) < 2:
            return upgrades

        # For each claim with windowed supporting observations, find predecessors
        for claim in claims:
            if claim["claim_id"] in upgraded_claim_ids:
                continue
            supporting_ids = set(claim.get("supporting_observations", []))
            claim_windowed = [
                o for o in windowed_obs
                if o["observation_id"] in supporting_ids
            ]
            if not claim_windowed:
                continue

            claim_start = min(o["observed_window"]["start"] for o in claim_windowed)

            # Find observations that precede this claim
            for pred_obs in windowed_obs:
                if pred_obs["observation_id"] in supporting_ids:
                    continue  # Skip same-claim observations
                pred_end = pred_obs["observed_window"]["end"]
                lag_days = _date_diff_days(pred_end, claim_start)
                if 0 < lag_days <= self.MAX_LAG_DAYS:
                    token = f"cross_scope_temporal:lag={lag_days}d→L1"
                    edge = CausalEdge(
                        from_node_id=pred_obs["observation_id"],
                        from_node_type="observation",
                        to_node_id=claim["claim_id"],
                        to_node_type="claim",
                        edge_type=EDGE_TYPE_CORRELATES_WITH,
                        weight=0.5,
                        explanation=f"Temporal predecessor detected: {pred_end} precedes {claim_start} by {lag_days} days",
                    )
                    upgrades.append(LevelUpgrade(
                        claim_id=claim["claim_id"],
                        new_level="L1",
                        justification_tokens=[token],
                        confidence_boost=0.02,
                        causal_edges=[edge],
                    ))
                    upgraded_claim_ids.add(claim["claim_id"])
                    break  # One predecessor is enough per claim

        return upgrades

    def _find_claim_for_obs(self, obs_id: str, claims: list[dict[str, Any]]) -> dict[str, Any] | None:
        for claim in claims:
            if obs_id in claim.get("supporting_observations", []):
                return claim
        return None


class CrossMetricCorrelationChecker(CausalChecker):
    """L0 → L1: promote relation-backed multi-metric consistency groups.

    The checker consumes precomputed claim relations and upgrades confirmed
    claims when a connected component contains at least three distinct metrics
    that all move in the same direction. It never discovers relations itself
    and does not emit new causal edges.
    """

    MIN_DISTINCT_METRICS = 3
    ELIGIBLE_RELATION_CATEGORIES = frozenset({"exact_match", "subset_or_overlap"})

    @property
    def name(self) -> str:
        return "cross_metric_correlation"

    def check(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation] | None = None,
    ) -> list[LevelUpgrade]:
        if not relations:
            return []

        claim_by_id = {
            str(claim.get("claim_id")): claim
            for claim in claims
            if claim.get("claim_id")
        }
        observation_by_id = {
            str(obs.get("observation_id")): obs
            for obs in observations
            if obs.get("observation_id")
        }
        adjacency = self._build_eligible_adjacency(
            relations,
            claim_by_id,
            observation_by_id,
        )
        if not adjacency:
            return []

        upgrades: list[LevelUpgrade] = []
        for component in _connected_components(adjacency):
            component_claims = [
                claim_by_id[claim_id]
                for claim_id in component
                if claim_id in claim_by_id
            ]
            component_claims = [
                claim for claim in component_claims
                if claim.get("inference_level", "L0") == "L0"
            ]
            if len(component_claims) < self.MIN_DISTINCT_METRICS:
                continue

            metrics = {
                str((claim.get("scope", {})).get("metric", ""))
                for claim in component_claims
                if (claim.get("scope", {})).get("metric")
            }
            if len(metrics) < self.MIN_DISTINCT_METRICS:
                continue

            directions = {
                claim["claim_id"]: _claim_support_direction(claim, observation_by_id)
                for claim in component_claims
            }
            if any(direction is None for direction in directions.values()):
                continue
            if len(set(directions.values())) != 1:
                continue

            token = (
                f"cross_metric_consistency:{len(metrics)}_metrics:"
                f"{_render_component_scope_label(component_claims)}→L1"
            )
            for claim in component_claims:
                upgrades.append(
                    LevelUpgrade(
                        claim_id=claim["claim_id"],
                        new_level="L1",
                        justification_tokens=[token],
                        confidence_boost=0.02,
                    )
                )

        return upgrades

    def _build_eligible_adjacency(
        self,
        relations: list[ClaimRelation],
        claim_by_id: dict[str, dict[str, Any]],
        observation_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for relation in relations:
            if relation.get("relation_type") != EDGE_TYPE_CORRELATES_WITH:
                continue
            from_claim_id = str(relation.get("from_claim_id", ""))
            to_claim_id = str(relation.get("to_claim_id", ""))
            if not from_claim_id or not to_claim_id or from_claim_id == to_claim_id:
                continue

            left_claim = claim_by_id.get(from_claim_id)
            right_claim = claim_by_id.get(to_claim_id)
            if left_claim is None or right_claim is None:
                continue
            if left_claim.get("status") != "confirmed" or right_claim.get("status") != "confirmed":
                continue

            left_metric = str((left_claim.get("scope", {})).get("metric", ""))
            right_metric = str((right_claim.get("scope", {})).get("metric", ""))
            if not left_metric or not right_metric or left_metric == right_metric:
                continue

            match_basis = relation.get("match_basis", {})
            category = str(match_basis.get("category", ""))
            relation_direction = str(match_basis.get("direction", ""))
            if category not in self.ELIGIBLE_RELATION_CATEGORIES:
                continue

            left_direction = _claim_support_direction(left_claim, observation_by_id)
            right_direction = _claim_support_direction(right_claim, observation_by_id)
            if (
                relation_direction not in {"up", "down"}
                or left_direction is None
                or right_direction is None
                or left_direction != right_direction
                or left_direction != relation_direction
            ):
                continue

            adjacency[from_claim_id].add(to_claim_id)
            adjacency[to_claim_id].add(from_claim_id)
        return adjacency

# ── Registry factory ──────────────────────────────────────────────────────────


def build_default_registry() -> CausalCheckerRegistry:
    """Build a registry with all standard checkers registered in execution order."""
    registry = CausalCheckerRegistry()
    registry.register(CrossSliceConsistencyChecker())
    registry.register(CrossScopeCorrelationChecker())
    registry.register(CrossMetricCorrelationChecker())
    registry.register(MechanisticExplanationChecker())
    registry.register(TemporalPrecedenceChecker())
    registry.register(DoseResponseChecker())
    registry.register(ReversalChecker())
    return registry


_default_registry: CausalCheckerRegistry | None = None


def get_default_registry() -> CausalCheckerRegistry:
    """Return the module-level default registry (lazy singleton)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry


# ── Pure helper functions ─────────────────────────────────────────────────────


def _spearman_correlation(x: list[float], y: list[float]) -> float:
    """Compute Spearman rank correlation coefficient without scipy."""
    n = len(x)
    if n < 2:
        return 0.0
    x_ranks = _rank(x)
    y_ranks = _rank(y)
    return _pearson_correlation(x_ranks, y_ranks)


def _rank(values: list[float]) -> list[float]:
    """Convert values to ranks with average-rank tie handling."""
    n = len(values)
    sorted_pairs = sorted(enumerate(values), key=lambda p: p[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        # Find the end of a run of equal values
        while j + 1 < n and sorted_pairs[j + 1][1] == sorted_pairs[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[sorted_pairs[k][0]] = avg_rank
        i = j + 1
    return ranks


def _pearson_correlation(x: list[float], y: list[float]) -> float:
    """Compute Pearson correlation coefficient."""
    n = len(x)
    if n < 2:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    if var_x == 0.0 or var_y == 0.0:
        return 0.0
    return cov / math.sqrt(var_x * var_y)


def _claim_matches_contribution_shift(
    claim_slice: dict[str, Any],
    obs_slice: dict[str, Any],
    segment_name: str,
    biggest_shift: Any,
) -> bool:
    """Return True when a claim slice matches a contribution_shift explanation."""
    expected_shift = str(biggest_shift)
    return (
        obs_slice.get("segment") == segment_name
        and str(obs_slice.get("biggest_shift")) == expected_shift
        and claim_slice == obs_slice
    )


def _claim_support_direction(
    claim: dict[str, Any],
    observation_by_id: dict[str, dict[str, Any]],
) -> str | None:
    deltas: list[float] = []
    for observation_id in claim.get("supporting_observations", []):
        observation = observation_by_id.get(str(observation_id))
        if observation is None:
            continue
        delta = observation.get("payload", {}).get("delta_pct")
        if delta is None:
            continue
        deltas.append(float(delta))
    if not deltas:
        return None
    positive = sum(1 for delta in deltas if delta > 0)
    negative = sum(1 for delta in deltas if delta < 0)
    if positive == negative:
        return None
    return "up" if positive > negative else "down"


def _connected_components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    components: list[set[str]] = []
    visited: set[str] = set()
    for node in adjacency:
        if node in visited:
            continue
        stack = [node]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            stack.extend(sorted(adjacency.get(current, set()) - visited))
        if component:
            components.append(component)
    return components


def _render_component_scope_label(claims: list[dict[str, Any]]) -> str:
    if not claims:
        return "overall"

    slices: list[dict[str, Any]] = []
    for claim in claims:
        scope = claim.get("scope", {})
        slice_dict = scope.get("slice", {})
        if isinstance(slice_dict, dict):
            slices.append(slice_dict)
    if not slices:
        return "overall"

    common_items = sorted(set(slices[0].items()))
    for slice_dict in slices[1:]:
        common_items = [item for item in common_items if slice_dict.get(item[0]) == item[1]]
        if not common_items:
            break
    if common_items:
        return ",".join(f"{key}={value}" for key, value in common_items)

    best_slice = max(slices, key=lambda item: (len(item), sorted(item.items())))
    if not best_slice:
        return "overall"
    return ",".join(f"{key}={value}" for key, value in sorted(best_slice.items()))


def _date_diff_days(start_iso: str, end_iso: str) -> int:
    """Return number of days from start_iso to end_iso (both YYYY-MM-DD)."""
    from datetime import date
    start = date.fromisoformat(start_iso[:10])
    end = date.fromisoformat(end_iso[:10])
    return (end - start).days


def _detect_reversal(deltas: list[float], min_periods: int) -> int:
    """Count consecutive trailing observations that reverse the initial majority direction.

    Returns the number of trailing periods in the reversed direction, or 0 if
    no reversal of at least min_periods is detected.
    """
    if len(deltas) < min_periods + 1:
        return 0

    # Determine initial majority direction from first half
    half = max(1, len(deltas) // 2)
    initial = deltas[:half]
    initial_positive = sum(1 for d in initial if d != 0.0 and d > 0)
    initial_negative = sum(1 for d in initial if d != 0.0 and d < 0)
    if initial_positive == 0 and initial_negative == 0:
        return 0
    initial_is_positive = initial_positive >= initial_negative

    # Count consecutive reversed observations from the end
    reversal_count = 0
    for d in reversed(deltas):
        if d == 0.0:
            continue
        if (d > 0) != initial_is_positive:
            reversal_count += 1
        else:
            break

    return reversal_count
