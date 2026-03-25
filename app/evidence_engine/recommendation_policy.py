from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.evidence_engine.recommendation_templates import RecommendationTemplateRegistry
from app.evidence_engine.schemas import (
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
