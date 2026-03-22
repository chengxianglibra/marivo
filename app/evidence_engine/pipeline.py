from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from app.evidence_engine.extractors import AggregateRowExtractor, ComparisonRowExtractor, ObservationExtractor
from app.evidence_engine.recommendation_policy import (
    DefaultRecommendationPolicy,
    RecommendationPolicy,
)
from app.evidence_engine.schemas import Claim, Observation, Recommendation
from app.evidence_engine.scoring import ConfidenceScorer, DefaultConfidenceScorer
from app.evidence_engine.synthesizers import ClaimSynthesizer, DefaultClaimSynthesizer


Synthesizer = Callable[[list[Observation]], tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]]


class SynthesisResult(TypedDict):
    claims: list[Claim]
    recommendations: list[Recommendation]
    edges: list[dict[str, Any]]
    summary: str


class EvidencePipeline:
    """Evidence extraction + synthesis pipeline with pluggable strategy seams."""

    def __init__(
        self,
        synthesizer: Synthesizer | ClaimSynthesizer,
        *,
        extractors: Mapping[str, ObservationExtractor] | None = None,
        synthesizers: Mapping[str, ClaimSynthesizer] | None = None,
        confidence_scorers: Mapping[str, ConfidenceScorer] | None = None,
        recommendation_policies: Mapping[str, RecommendationPolicy] | None = None,
    ) -> None:
        default_synthesizer = _coerce_synthesizer(synthesizer)
        default_confidence_scorer = DefaultConfidenceScorer()
        default_recommendation_policy = DefaultRecommendationPolicy()
        default_extractors = {
            extractor.name: extractor
            for extractor in [ComparisonRowExtractor(), AggregateRowExtractor()]
        }
        if extractors:
            default_extractors.update(extractors)
        self._extractors = default_extractors

        default_synthesizers = {default_synthesizer.name: default_synthesizer}
        if synthesizers:
            default_synthesizers.update(synthesizers)
        self._synthesizers = default_synthesizers
        self._default_synthesizer_name = default_synthesizer.name

        default_confidence_scorers = {default_confidence_scorer.name: default_confidence_scorer}
        if confidence_scorers:
            default_confidence_scorers.update(confidence_scorers)
        self._confidence_scorers = default_confidence_scorers
        self._default_confidence_scorer_name = default_confidence_scorer.name

        default_recommendation_policies = {
            default_recommendation_policy.name: default_recommendation_policy
        }
        if recommendation_policies:
            default_recommendation_policies.update(recommendation_policies)
        self._recommendation_policies = default_recommendation_policies
        self._default_recommendation_policy_name = default_recommendation_policy.name

    def extract_observations(
        self,
        extractor_name: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        if extractor_name not in self._extractors:
            raise KeyError(f"Unknown evidence extractor: {extractor_name}")
        return self._extractors[extractor_name].extract(rows, context=context)

    def synthesize(
        self,
        observations: list[Observation],
        *,
        synthesizer_name: str | None = None,
    ) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]:
        resolved_name = synthesizer_name or self._default_synthesizer_name
        if resolved_name not in self._synthesizers:
            raise KeyError(f"Unknown claim synthesizer: {resolved_name}")
        return self._synthesizers[resolved_name].synthesize(observations)

    def build_synthesis(
        self,
        observations: list[Observation],
        *,
        existing_claims: list[Claim] | None = None,
        synthesizer_name: str | None = None,
        confidence_scorer_name: str | None = None,
        recommendation_policy_name: str | None = None,
    ) -> SynthesisResult:
        if existing_claims is not None:
            # M-03 promotion mode: skip fresh claim synthesis; use already-promoted claims.
            claims: list[Claim] = existing_claims
            recommendations: list[Recommendation] = []
            edges: list[dict[str, Any]] = []
        else:
            claims, recommendations, edges = self.synthesize(
                observations,
                synthesizer_name=synthesizer_name,
            )
        claims = self.score_claims(
            observations,
            claims,
            confidence_scorer_name=confidence_scorer_name,
        )
        recommendations = self.derive_recommendations(
            observations,
            claims,
            recommendations,
            recommendation_policy_name=recommendation_policy_name,
        )

        for claim in claims:
            for observation_id in claim["supporting_observations"]:
                edges.append(
                    {
                        "from_node_id": observation_id,
                        "from_node_type": "observation",
                        "to_node_id": claim["claim_id"],
                        "to_node_type": "claim",
                        "edge_type": "supports",
                        "weight": claim["confidence"],
                        "explanation": "Observation strengthens the claim.",
                    }
                )
            for observation_id in claim["contradicting_observations"]:
                edges.append(
                    {
                        "from_node_id": observation_id,
                        "from_node_type": "observation",
                        "to_node_id": claim["claim_id"],
                        "to_node_type": "claim",
                        "edge_type": "contradicts",
                        "weight": 0.35,
                        "explanation": "Observation weakens the claim.",
                    }
                )

        for recommendation in recommendations:
            edges.append(
                {
                    "from_node_id": recommendation["claim_id"],
                    "from_node_type": "claim",
                    "to_node_id": recommendation["rec_id"],
                    "to_node_type": "recommendation",
                    "edge_type": "justifies",
                    "weight": 0.9,
                    "explanation": "Claim justifies the recommendation.",
                }
            )

        return {
            "claims": claims,
            "recommendations": recommendations,
            "edges": edges,
            "summary": claims[0]["text"] if claims else "No supported claims were generated.",
        }

    def score_claims(
        self,
        observations: list[Observation],
        claims: list[Claim],
        *,
        confidence_scorer_name: str | None = None,
    ) -> list[Claim]:
        resolved_name = confidence_scorer_name or self._default_confidence_scorer_name
        if resolved_name not in self._confidence_scorers:
            raise KeyError(f"Unknown confidence scorer: {resolved_name}")
        return self._confidence_scorers[resolved_name].score(observations, claims)

    def derive_recommendations(
        self,
        observations: list[Observation],
        claims: list[Claim],
        recommendations: list[Recommendation],
        *,
        recommendation_policy_name: str | None = None,
    ) -> list[Recommendation]:
        resolved_name = recommendation_policy_name or self._default_recommendation_policy_name
        if resolved_name not in self._recommendation_policies:
            raise KeyError(f"Unknown recommendation policy: {resolved_name}")
        return self._recommendation_policies[resolved_name].derive(
            observations,
            claims,
            recommendations,
        )


def _coerce_synthesizer(synthesizer: Synthesizer | ClaimSynthesizer) -> ClaimSynthesizer:
    if isinstance(synthesizer, ClaimSynthesizer):
        return synthesizer
    return _CallableClaimSynthesizer(synthesizer)


class _CallableClaimSynthesizer(ClaimSynthesizer):
    name = DefaultClaimSynthesizer.name

    def __init__(self, synthesizer: Synthesizer) -> None:
        self._synthesizer = synthesizer

    def synthesize(
        self,
        observations: list[Observation],
    ) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]:
        return self._synthesizer(observations)
