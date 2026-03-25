from __future__ import annotations

from abc import ABC, abstractmethod

from app.evidence_engine.schemas import Claim, Observation


def score_confidence(
    effect_strength: float,
    consistency: float,
    sample_score: float,
    data_quality_score: float,
    contradiction_penalty: float,
    **_ignored: float,
) -> float:
    raw = (
        0.30 * effect_strength
        + 0.25 * consistency
        + 0.20 * sample_score
        + 0.25 * data_quality_score
        - contradiction_penalty
    )
    return round(max(0.0, min(0.99, raw)), 2)


class ConfidenceScorer(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def score(
        self,
        observations: list[Observation],
        claims: list[Claim],
    ) -> list[Claim]:
        raise NotImplementedError


class DefaultConfidenceScorer(ConfidenceScorer):
    name = "default"

    def score(
        self,
        observations: list[Observation],
        claims: list[Claim],
    ) -> list[Claim]:
        scored_claims: list[Claim] = []
        for claim in claims:
            breakdown = dict(claim.get("confidence_breakdown", {}))
            if breakdown.get("_model") == "counter_hypothesis":
                ctr_delta = float(breakdown.pop("_ctr_delta_pct", 0.0))
                breakdown.pop("_model", None)
                confidence = round(min(0.95, 0.65 + min(max(ctr_delta, 0.0) / 10.0, 0.20)), 2)
            elif {
                "effect_strength",
                "consistency",
                "sample_score",
                "data_quality_score",
                "contradiction_penalty",
            }.issubset(breakdown):
                confidence = score_confidence(
                    float(breakdown["effect_strength"]),
                    float(breakdown["consistency"]),
                    float(breakdown["sample_score"]),
                    float(breakdown["data_quality_score"]),
                    float(breakdown["contradiction_penalty"]),
                )
            else:
                scored_claims.append(claim)
                continue

            scored_claims.append(
                {
                    **claim,
                    "confidence": confidence,
                    "confidence_breakdown": breakdown,
                }
            )
        return scored_claims
