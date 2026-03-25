"""Claim relation discovery for the layered evidence engine.

This module intentionally separates two concerns:
- discovering stable in-memory relations between claims
- materializing those relations into graph edges later in the pipeline

Phase 2.1 introduces conservative claim-to-claim relation mining for the final
``synthesize_findings`` path.  Discovery only emits weak ``correlates_with``
relations between confirmed claims; it does not directly promote inference
levels or emit stronger causal edge types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.evidence_engine.schemas import Claim, ClaimRelation


class ClaimRelationDiscovery(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def discover(
        self,
        claims: list[Claim],
        observations: list[dict[str, Any]],
        existing_edges: list[dict[str, Any]],
    ) -> list[ClaimRelation]:
        raise NotImplementedError


class DefaultClaimRelationDiscovery(ClaimRelationDiscovery):
    """Conservative relation discovery for confirmed claims.

    Discovery rules in Phase 2.1:
    - exact_match: different metrics, identical slice, same direction
    - subset_or_overlap: different metrics, overlapping slices, same direction
    - complementary_dimension: same metric, same scope shape, exactly one slice
      dimension value differs and the claims are complementary
    """

    name = "default"

    EXACT_MATCH_SCORE = 0.92
    SUBSET_OR_OVERLAP_SCORE = 0.78
    COMPLEMENTARY_DIMENSION_SCORE = 0.64

    def discover(
        self,
        claims: list[Claim],
        observations: list[dict[str, Any]],
        existing_edges: list[dict[str, Any]],
    ) -> list[ClaimRelation]:
        confirmed_claims = [claim for claim in claims if claim.get("status") == "confirmed"]
        if len(confirmed_claims) < 2:
            return []

        observation_by_id = {
            str(obs.get("observation_id")): obs
            for obs in observations
            if obs.get("observation_id")
        }
        relations: list[ClaimRelation] = []
        seen_pairs: set[tuple[str, str]] = set()

        for left_index in range(len(confirmed_claims)):
            for right_index in range(left_index + 1, len(confirmed_claims)):
                claim_a = confirmed_claims[left_index]
                claim_b = confirmed_claims[right_index]
                relation = self._build_relation(claim_a, claim_b, observation_by_id)
                if relation is None:
                    continue
                key = (relation["from_claim_id"], relation["to_claim_id"])
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                relations.append(relation)

        return relations

    def _build_relation(
        self,
        claim_a: Claim,
        claim_b: Claim,
        observation_by_id: dict[str, dict[str, Any]],
    ) -> ClaimRelation | None:
        scope_a = claim_a.get("scope", {}) or {}
        scope_b = claim_b.get("scope", {}) or {}
        metric_a = str(scope_a.get("metric", "") or "")
        metric_b = str(scope_b.get("metric", "") or "")
        slice_a = _slice_dict(scope_a)
        slice_b = _slice_dict(scope_b)
        direction_a = _claim_direction(claim_a, observation_by_id)
        direction_b = _claim_direction(claim_b, observation_by_id)
        if direction_a is None or direction_b is None or direction_a != direction_b:
            return None

        category: str | None = None
        score = 0.0
        shared_keys = sorted(set(slice_a).intersection(slice_b))
        exact = slice_a == slice_b
        subset = _is_subset(slice_a, slice_b) or _is_subset(slice_b, slice_a)
        overlap = bool(shared_keys) and _shared_values(slice_a, slice_b)
        complementary = _complementary_dimension(slice_a, slice_b)

        if metric_a and metric_b and metric_a != metric_b and exact:
            category = "exact_match"
            score = self.EXACT_MATCH_SCORE
        elif metric_a and metric_b and metric_a != metric_b and (subset or overlap):
            category = "subset_or_overlap"
            score = self.SUBSET_OR_OVERLAP_SCORE
        elif metric_a and metric_a == metric_b and complementary:
            category = "complementary_dimension"
            score = self.COMPLEMENTARY_DIMENSION_SCORE
        else:
            return None

        ordered_a, ordered_b = _ordered_claim_pair(claim_a, claim_b)
        supporting_observation_ids = sorted(
            {
                *[str(obs_id) for obs_id in claim_a.get("supporting_observations", [])],
                *[str(obs_id) for obs_id in claim_b.get("supporting_observations", [])],
            }
        )
        different_keys = sorted(
            {
                key
                for key in set(slice_a).union(slice_b)
                if slice_a.get(key) != slice_b.get(key)
            }
        )
        score_components = {
            "scope_match": round(score, 2),
            "direction_match": 1.0,
            "observation_support": min(1.0, len(supporting_observation_ids) / 4.0),
        }
        explanation = _build_explanation(
            category=category,
            claim_a=claim_a,
            claim_b=claim_b,
            direction=direction_a,
            shared_keys=shared_keys,
            different_keys=different_keys,
        )

        return {
            "from_claim_id": ordered_a["claim_id"],
            "to_claim_id": ordered_b["claim_id"],
            "relation_type": "correlates_with",
            "weight": round(score, 2),
            "match_basis": {
                "category": category,
                "shared_scope_keys": shared_keys,
                "different_scope_keys": different_keys,
                "shared_scope_values": _shared_values(slice_a, slice_b),
                "left_metric": metric_a,
                "right_metric": metric_b,
                "direction": direction_a,
            },
            "score_components": score_components,
            "supporting_observation_ids": supporting_observation_ids,
            "explanation": explanation,
        }


def materialize_relations_as_edges(relations: list[ClaimRelation]) -> list[dict[str, Any]]:
    """Convert claim relations into evidence-edge rows without persisting them."""

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for relation in relations:
        key = (
            relation["from_claim_id"],
            relation["to_claim_id"],
            relation["relation_type"],
        )
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "from_node_id": relation["from_claim_id"],
                "from_node_type": "claim",
                "to_node_id": relation["to_claim_id"],
                "to_node_type": "claim",
                "edge_type": relation["relation_type"],
                "weight": relation["weight"],
                "explanation": relation["explanation"],
                "match_basis": relation.get("match_basis", {}),
                "score_components": relation.get("score_components", {}),
                "supporting_observation_ids": relation.get("supporting_observation_ids", []),
            }
        )
    return edges


def _slice_dict(scope: dict[str, Any]) -> dict[str, Any]:
    slice_dict = scope.get("slice", {}) or {}
    return slice_dict if isinstance(slice_dict, dict) else {}


def _claim_direction(
    claim: Claim,
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


def _is_subset(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return bool(left) and all(right.get(key) == value for key, value in left.items())


def _shared_values(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {
        key: left[key]
        for key in set(left).intersection(right)
        if left.get(key) == right.get(key)
    }


def _complementary_dimension(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if set(left.keys()) != set(right.keys()):
        return False
    different_keys = [key for key in left if left.get(key) != right.get(key)]
    return len(different_keys) == 1 and len(left) >= 1


def _ordered_claim_pair(claim_a: Claim, claim_b: Claim) -> tuple[Claim, Claim]:
    pair = sorted(
        [claim_a, claim_b],
        key=lambda claim: (
            str(claim.get("scope", {}).get("metric", "")),
            sorted((claim.get("scope", {}).get("slice", {}) or {}).items()),
            str(claim.get("claim_id", "")),
        ),
    )
    return pair[0], pair[1]


def _build_explanation(
    *,
    category: str,
    claim_a: Claim,
    claim_b: Claim,
    direction: str,
    shared_keys: list[str],
    different_keys: list[str],
) -> str:
    metric_a = str(claim_a.get("scope", {}).get("metric", "unknown"))
    metric_b = str(claim_b.get("scope", {}).get("metric", "unknown"))
    direction_text = "in the same direction" if direction in {"up", "down"} else "consistently"
    if category == "exact_match":
        return (
            f"Claims for {metric_a} and {metric_b} share the same slice and move {direction_text}."
        )
    if category == "subset_or_overlap":
        joined = ", ".join(shared_keys) if shared_keys else "overlapping dimensions"
        return (
            f"Claims for {metric_a} and {metric_b} overlap on {joined} and move {direction_text}."
        )
    changed = ", ".join(different_keys) if different_keys else "one complementary dimension"
    return (
        f"Claims for {metric_a} differ only on {changed} and form a complementary structure."
    )
