from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import uuid4

from app.evidence_engine.factories import slice_matches
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
                if claim["type"] == "root_cause_candidate" and claim["status"] == "supported"
            ),
            None,
        )
        if not primary_claim:
            return recommendations

        impacted_slice = primary_claim.get("scope", {}).get("slice", {})
        qoe_support = next(
            (
                obs
                for obs in observations
                if obs["type"] == "qoe_regression"
                and slice_matches(obs["subject"]["slice"], impacted_slice)
                and float(obs["payload"]["delta_pct"]) >= 10.0
            ),
            None,
        )
        ad_support = next(
            (
                obs
                for obs in observations
                if obs["type"] == "ad_regression"
                and slice_matches(obs["subject"]["slice"], impacted_slice)
                and float(obs["payload"]["delta_rate"]) >= 0.05
            ),
            None,
        )

        derived: list[Recommendation] = []
        if qoe_support:
            derived.append(
                {
                    "rec_id": f"rec_{uuid4().hex[:12]}",
                    "claim_id": primary_claim["claim_id"],
                    "action_text": "Prioritize an Android 8.3.1 playback fix focused on reducing first-frame latency for weak-network sessions.",
                    "priority": "P0",
                    "expected_impact": "Recover 30-second retention for the impacted Android cohort.",
                    "risk": "May require player hotfix rollout and staged validation.",
                    "validation_metric": {
                        "primary_metric": "retention_30s",
                        "secondary_metric": "watch_time",
                    },
                }
            )
        if ad_support:
            derived.append(
                {
                    "rec_id": f"rec_{uuid4().hex[:12]}",
                    "claim_id": primary_claim["claim_id"],
                    "action_text": "Reduce preroll burden for weak-network short-video traffic while the playback issue is being mitigated.",
                    "priority": "P1",
                    "expected_impact": "Lower early exits caused by timeout-heavy ad starts.",
                    "risk": "Short-term revenue tradeoff on the impacted cohort.",
                    "validation_metric": {
                        "primary_metric": "preroll_timeout_rate",
                        "secondary_metric": "watch_time",
                    },
                }
            )
        derived.append(
            {
                "rec_id": f"rec_{uuid4().hex[:12]}",
                "claim_id": primary_claim["claim_id"],
                "action_text": "Launch a recovery experiment for affected Android weak-network users after the hotfix lands.",
                "priority": "P1",
                "expected_impact": "Validate watch-time recovery before rolling strategy changes to all users.",
                "risk": "Experiment duration may delay full rollout decisions.",
                "validation_metric": {
                    "primary_metric": "watch_time",
                    "secondary_metric": "retention_30s",
                },
            }
        )
        return derived
