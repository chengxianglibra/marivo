from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.evidence_engine.recommendation_templates import RecommendationTemplateRegistry
from app.evidence_engine.schemas import (
    EDGE_TYPE_CORRELATES_WITH,
    EDGE_TYPE_ELIMINATES_ALTERNATIVE,
    EDGE_TYPE_EXPERIMENTALLY_CONFIRMS,
    EDGE_TYPE_MECHANISTICALLY_EXPLAINS,
    EDGE_TYPE_TEMPORALLY_PRECEDES,
    REC_TYPE_ACTION,
    REC_TYPE_NO_ACTION,
    Claim,
    ClaimRelation,
    Observation,
    Recommendation,
)


class RecommendationPolicy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def derive(
        self,
        observations: list[Observation],
        claims: list[Claim],
        recommendations: list[Recommendation],
        relations: list[ClaimRelation] | None = None,
    ) -> list[Recommendation]:
        raise NotImplementedError


_DIRECTIONAL_EDGE_PRIORITY: dict[str, int] = {
    EDGE_TYPE_MECHANISTICALLY_EXPLAINS: 4,
    EDGE_TYPE_TEMPORALLY_PRECEDES: 3,
    EDGE_TYPE_ELIMINATES_ALTERNATIVE: 2,
    EDGE_TYPE_EXPERIMENTALLY_CONFIRMS: 1,
}
_PATH_EDGE_PRIORITY: dict[str, int] = {
    **_DIRECTIONAL_EDGE_PRIORITY,
    EDGE_TYPE_CORRELATES_WITH: 0,
}


def _slice_key(claim: Claim) -> tuple[tuple[str, Any], ...]:
    slice_dict = claim.get("scope", {}).get("slice", {})
    return tuple(sorted(slice_dict.items()))


def _slice_desc(claim: Claim) -> str:
    slice_dict = claim.get("scope", {}).get("slice", {})
    return " / ".join(f"{k}={v}" for k, v in slice_dict.items()) if slice_dict else "overall"


def _get_claim_delta(claim: Claim) -> float | None:
    delta = claim.get("confidence_breakdown", {}).get("primary_delta_pct")
    return float(delta) if delta is not None else None


def _claim_current_value(claim: Claim) -> Any:
    return claim.get("confidence_breakdown", {}).get("current_value")


def _relation_types_for_group(
    group: list[Claim],
    relations: list[ClaimRelation] | None,
) -> set[str]:
    if not relations or len(group) < 2:
        return set()

    claim_ids = {claim["claim_id"] for claim in group}
    relation_types: set[str] = set()
    for relation in relations:
        if relation["from_claim_id"] in claim_ids and relation["to_claim_id"] in claim_ids:
            relation_types.add(relation["relation_type"])
    return relation_types


def attach_causal_chain_metadata(
    recommendations: list[Recommendation],
    claims: list[Claim],
    relations: list[ClaimRelation] | None,
    edges: list[dict[str, Any]] | None,
) -> list[Recommendation]:
    """Attach deterministic claim-path metadata to recommendation causal_basis.

    Narrative generation is intentionally conservative:
    - search only within the recommendation-local claim subgraph
    - allow `correlates_with` only as a connector, never as sufficient evidence
    - require at least one directional claim-to-claim edge in the selected path
    """

    if not recommendations:
        return recommendations

    claim_index = {claim["claim_id"]: claim for claim in claims}
    relation_rows = relations or []
    edge_rows = edges or []
    enriched: list[Recommendation] = []

    for recommendation in recommendations:
        causal_basis = recommendation.get("causal_basis")
        if causal_basis is None:
            enriched.append(recommendation)
            continue

        local_claim_ids = recommendation.get("supporting_claims") or [recommendation["claim_id"]]
        local_claim_ids = [
            claim_id
            for claim_id in local_claim_ids
            if claim_id in claim_index
        ]
        path_claim_ids = _select_causal_path(
            local_claim_ids=local_claim_ids,
            primary_claim_id=recommendation["claim_id"],
            claims_by_id=claim_index,
            relations=relation_rows,
            edges=edge_rows,
        )

        enriched.append(
            {
                **recommendation,
                "causal_basis": {
                    **causal_basis,
                    "causal_chain": (
                        _render_causal_chain(path_claim_ids, claim_index)
                        if path_claim_ids
                        else None
                    ),
                    "causal_path_claim_ids": path_claim_ids,
                },
            }
        )
    return enriched


def _select_causal_path(
    *,
    local_claim_ids: list[str],
    primary_claim_id: str,
    claims_by_id: dict[str, Claim],
    relations: list[ClaimRelation],
    edges: list[dict[str, Any]],
) -> list[str]:
    if primary_claim_id not in local_claim_ids or len(local_claim_ids) < 2:
        return []

    local_set = set(local_claim_ids)
    adjacency: dict[str, list[tuple[str, str, float, bool]]] = defaultdict(list)

    for relation in relations:
        if relation.get("relation_type") != EDGE_TYPE_CORRELATES_WITH:
            continue
        left = relation.get("from_claim_id")
        right = relation.get("to_claim_id")
        if left not in local_set or right not in local_set:
            continue
        weight = float(relation.get("weight", 0.0) or 0.0)
        adjacency[str(left)].append((str(right), EDGE_TYPE_CORRELATES_WITH, weight, False))
        adjacency[str(right)].append((str(left), EDGE_TYPE_CORRELATES_WITH, weight, False))

    for edge in edges:
        if edge.get("from_node_type") != "claim" or edge.get("to_node_type") != "claim":
            continue
        edge_type = str(edge.get("edge_type", "") or "")
        if edge_type not in _DIRECTIONAL_EDGE_PRIORITY:
            continue
        left = str(edge.get("from_node_id", "") or "")
        right = str(edge.get("to_node_id", "") or "")
        if left not in local_set or right not in local_set:
            continue
        weight = float(edge.get("weight", 0.0) or 0.0)
        adjacency[left].append((right, edge_type, weight, True))

    if not any(is_directional for neighbors in adjacency.values() for _, _, _, is_directional in neighbors):
        return []

    candidate_paths: list[tuple[tuple[Any, ...], list[str]]] = []
    for start_claim_id in local_claim_ids:
        if start_claim_id == primary_claim_id:
            continue
        _walk_paths(
            current=start_claim_id,
            target=primary_claim_id,
            adjacency=adjacency,
            claims_by_id=claims_by_id,
            visited={start_claim_id},
            path=[start_claim_id],
            path_edges=[],
            out=candidate_paths,
        )

    if not candidate_paths:
        return []
    candidate_paths.sort(key=lambda item: item[0], reverse=True)
    return candidate_paths[0][1]


def _walk_paths(
    *,
    current: str,
    target: str,
    adjacency: dict[str, list[tuple[str, str, float, bool]]],
    claims_by_id: dict[str, Claim],
    visited: set[str],
    path: list[str],
    path_edges: list[tuple[str, float, bool]],
    out: list[tuple[tuple[Any, ...], list[str]]],
) -> None:
    if current == target:
        if any(is_directional for _, _, is_directional in path_edges):
            out.append((_score_path(path, path_edges, claims_by_id), list(path)))
        return

    for neighbor, edge_type, weight, is_directional in adjacency.get(current, []):
        if neighbor in visited:
            continue
        visited.add(neighbor)
        path.append(neighbor)
        path_edges.append((edge_type, weight, is_directional))
        _walk_paths(
            current=neighbor,
            target=target,
            adjacency=adjacency,
            claims_by_id=claims_by_id,
            visited=visited,
            path=path,
            path_edges=path_edges,
            out=out,
        )
        path_edges.pop()
        path.pop()
        visited.remove(neighbor)


def _score_path(
    path: list[str],
    path_edges: list[tuple[str, float, bool]],
    claims_by_id: dict[str, Claim],
) -> tuple[Any, ...]:
    directional_edges = [edge_type for edge_type, _, is_directional in path_edges if is_directional]
    directional_count = len(directional_edges)
    highest_priority = max((_PATH_EDGE_PRIORITY.get(edge_type, 0) for edge_type in directional_edges), default=0)
    total_priority = sum(_PATH_EDGE_PRIORITY.get(edge_type, 0) for edge_type, _, _ in path_edges)
    total_weight = round(sum(weight for _, weight, _ in path_edges), 6)
    total_confidence = round(
        sum(float(claims_by_id[claim_id].get("confidence", 0.0) or 0.0) for claim_id in path),
        6,
    )
    return (
        directional_count,
        highest_priority,
        total_priority,
        -len(path),
        total_weight,
        total_confidence,
        tuple(path),
    )


def _render_causal_chain(path_claim_ids: list[str], claims_by_id: dict[str, Claim]) -> str:
    return " -> ".join(_render_claim_label(claims_by_id[claim_id]) for claim_id in path_claim_ids)


def _render_claim_label(claim: Claim) -> str:
    metric = str(claim.get("scope", {}).get("metric", "") or "metric")
    delta = _get_claim_delta(claim)
    if delta is None:
        return metric
    sign = "+" if delta >= 0 else ""
    return f"{metric} {sign}{delta:.1f}%"


class DefaultRecommendationPolicy(RecommendationPolicy):
    name = "default"

    def __init__(
        self,
        metric_direction_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._resolve_direction = metric_direction_resolver
        self._templates = RecommendationTemplateRegistry()

    def derive(
        self,
        observations: list[Observation],
        claims: list[Claim],
        recommendations: list[Recommendation],
        relations: list[ClaimRelation] | None = None,
    ) -> list[Recommendation]:
        del observations, recommendations

        confirmed_claims = [
            c
            for c in claims
            if c["type"] == "root_cause_candidate" and c["status"] in {"supported", "confirmed"}
        ]
        if not confirmed_claims:
            return []
        return self._derive_from_confirmed(confirmed_claims, relations)

    def _is_no_action(self, metric: str, delta_pct: float | None) -> bool:
        if delta_pct is None:
            return False
        if abs(delta_pct) < 5.0:
            return True
        if self._resolve_direction is None:
            return False
        direction = self._resolve_direction(metric)
        if direction is None or direction == "neutral":
            return False
        if direction == "down" and delta_pct < 0:
            return True
        if direction == "up" and delta_pct > 0:
            return True
        return False

    def _derive_from_confirmed(
        self,
        confirmed_claims: list[Claim],
        relations: list[ClaimRelation] | None,
    ) -> list[Recommendation]:
        action_claims: list[Claim] = []
        no_action_claims: list[Claim] = []
        for claim in confirmed_claims:
            metric = claim.get("scope", {}).get("metric", "")
            if self._is_no_action(metric, _get_claim_delta(claim)):
                no_action_claims.append(claim)
            else:
                action_claims.append(claim)

        result: list[Recommendation] = []
        if action_claims:
            groups: dict[tuple[tuple[str, Any], ...], list[Claim]] = defaultdict(list)
            for claim in action_claims:
                groups[_slice_key(claim)].append(claim)

            for group in groups.values():
                relation_types = _relation_types_for_group(group, relations)
                if len(group) >= 2 and relation_types:
                    result.append(self._aggregated_recommendation(group, relation_types))
                else:
                    for claim in group:
                        result.append(self._single_claim_recommendation(claim))

        for claim in no_action_claims:
            result.append(self._no_action_recommendation(claim))
        return result

    def _no_action_recommendation(self, claim: Claim) -> Recommendation:
        metric = claim.get("scope", {}).get("metric", "") or "metric_under_investigation"
        template = self._templates.resolve(
            entry_type="no_action",
            claim_type=claim["type"],
            inference_level=claim.get("inference_level", "L0"),
            relation_types=set(),
        )
        context = {
            "slice_desc": _slice_desc(claim),
            "primary_metric": metric,
        }
        return {
            "rec_id": f"rec_{uuid4().hex[:12]}",
            "type": REC_TYPE_NO_ACTION,
            "claim_id": claim["claim_id"],
            "supporting_claims": None,
            "template_id": template.template_id,
            "action_text": template.render_action(context),
            "priority": template.fixed_priority or "P3",
            "expected_impact": template.render_expected_impact(context),
            "risk": template.render_risk(context),
            "validation_metric": {"primary_metric": metric},
            "causal_basis": None,
        }

    def _aggregated_recommendation(
        self,
        group: list[Claim],
        relation_types: set[str],
    ) -> Recommendation:
        primary = max(group, key=lambda c: c["confidence"])
        template = self._templates.resolve(
            entry_type="multi_claim",
            claim_type=primary["type"],
            inference_level=primary.get("inference_level", "L0"),
            relation_types=relation_types,
        )
        all_metrics = [c.get("scope", {}).get("metric", "") for c in group]
        context = {
            "slice_desc": _slice_desc(primary),
            "metrics_csv": ", ".join(metric for metric in all_metrics if metric),
            "primary_claim_text": primary["text"],
            "primary_metric": primary.get("scope", {}).get("metric", "") or "metric_under_investigation",
        }
        return {
            "rec_id": f"rec_{uuid4().hex[:12]}",
            "type": REC_TYPE_ACTION,
            "claim_id": primary["claim_id"],
            "supporting_claims": [c["claim_id"] for c in group],
            "template_id": template.template_id,
            "action_text": template.render_action(context),
            "priority": template.fixed_priority or "P1",
            "expected_impact": template.render_expected_impact(context),
            "risk": template.render_risk(context),
            "validation_metric": {
                "primary_metric": all_metrics[0] if all_metrics else "metric_under_investigation",
                "correlated_metrics": all_metrics[1:],
            },
        }

    def _single_claim_recommendation(self, claim: Claim) -> Recommendation:
        metric = claim.get("scope", {}).get("metric", "")
        claim_status = claim.get("status", "")
        scope_desc = _slice_desc(claim)
        metric_desc = f" for {metric}" if metric and metric != "aggregate" else ""

        if claim_status in {"supported", "confirmed"}:
            template = self._templates.resolve(
                entry_type="single_claim",
                claim_type=claim["type"],
                inference_level=claim.get("inference_level", "L0"),
                relation_types=set(),
            )
            context = {
                "slice_desc": scope_desc,
                "primary_claim_text": claim["text"],
                "primary_metric": metric or "metric_under_investigation",
                "current_value": _claim_current_value(claim),
            }
            action_text = template.render_action(context)
            priority = template.fixed_priority or "P1"
            expected_impact = template.render_expected_impact(
                {
                    "slice_desc": scope_desc,
                    "primary_metric": metric or "metric_under_investigation",
                }
            )
            risk = template.render_risk(context)
            template_id = template.template_id
        else:
            action_text = (
                f"Collect more data{metric_desc} in scope [{scope_desc}] "
                "to confirm or refute this signal before drawing conclusions."
            )
            priority = "P2"
            expected_impact = (
                "Determines whether the observed signal is statistically significant "
                "and warrants further investigation."
            )
            risk = "Signal may be noise; avoid acting on insufficient evidence."
            template_id = None

        return {
            "rec_id": f"rec_{uuid4().hex[:12]}",
            "type": REC_TYPE_ACTION,
            "claim_id": claim["claim_id"],
            "supporting_claims": None,
            "template_id": template_id,
            "action_text": action_text,
            "priority": priority,
            "expected_impact": expected_impact,
            "risk": risk,
            "validation_metric": {
                "primary_metric": metric or "metric_under_investigation",
            },
        }
