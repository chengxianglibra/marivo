from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import uuid4

from app.evidence_engine.schemas import Claim, Observation, Recommendation


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
    ) -> list[Recommendation]:
        raise NotImplementedError


class DefaultRecommendationPolicy(RecommendationPolicy):
    name = "default"

    def derive(
        self,
        observations: list[Observation],
        claims: list[Claim],
        recommendations: list[Recommendation],
    ) -> list[Recommendation]:
        if recommendations:
            return recommendations

        primary_claim = next(
            (
                claim
                for claim in claims
                if claim["type"] == "root_cause_candidate" and claim["status"] in {"supported", "confirmed"}
            ),
            None,
        )
        if not primary_claim:
            return recommendations

        impacted_slice = primary_claim.get("scope", {}).get("slice", {})

        derived: list[Recommendation] = []
        derived.append(
            {
                "rec_id": f"rec_{uuid4().hex[:12]}",
                "claim_id": primary_claim["claim_id"],
                "action_text": "Investigate the root cause for the impacted traffic slice and launch a recovery experiment.",
                "priority": "P1",
                "expected_impact": "Validate metric recovery before rolling strategy changes to all users.",
                "risk": "Experiment duration may delay full rollout decisions.",
                "validation_metric": {
                    "primary_metric": "metric_under_investigation",
                },
            }
        )
        return derived
