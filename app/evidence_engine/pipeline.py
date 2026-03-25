from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from app.evidence_engine.extractors.base import ObservationExtractor
from app.evidence_engine.recommendation_policy import (
    DefaultRecommendationPolicy,
    RecommendationPolicy,
)
from app.evidence_engine.causal_basis import (
    SessionSummary,
    build_causal_basis,
    derive_session_summary,
)
from app.evidence_engine.confounder_resolution import resolve_confounders
from app.evidence_engine.schemas import (
    CAUSAL_EDGE_TO_INFERENCE_LEVEL,
    INFERENCE_LEVEL_ORDER,
    Claim,
    Observation,
    Recommendation,
)
from app.evidence_engine.scoring import ConfidenceScorer, DefaultConfidenceScorer
from app.evidence_engine.synthesizers import ClaimSynthesizer, DefaultClaimSynthesizer


Synthesizer = Callable[[list[Observation]], tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]]


class SynthesisResult(TypedDict):
    claims: list[Claim]
    recommendations: list[Recommendation]
    edges: list[dict[str, Any]]
    summary: str


def _derive_inference_level_from_edges(
    edges: list[dict[str, Any]],
    claims: list[Claim],
) -> dict[str, tuple[str, list[str], float]]:
    """Return per-claim inference level upgrades implied by causal edges.

    Returns: claim_id → (new_level, justification_tokens, confidence_boost)
    Only includes entries for claims where level would change from L0.
    """
    claim_causal_edges: dict[str, list[str]] = {}
    for edge in edges:
        if edge["to_node_type"] == "claim" and edge["edge_type"] in CAUSAL_EDGE_TO_INFERENCE_LEVEL:
            claim_causal_edges.setdefault(edge["to_node_id"], []).append(edge["edge_type"])

    result: dict[str, tuple[str, list[str], float]] = {}
    for claim in claims:
        causal_types = claim_causal_edges.get(claim["claim_id"], [])
        if not causal_types:
            continue
        max_level = max(
            (CAUSAL_EDGE_TO_INFERENCE_LEVEL[et] for et in causal_types),
            key=lambda lvl: INFERENCE_LEVEL_ORDER.index(lvl),
        )
        justification = sorted(
            {f"{et}→{CAUSAL_EDGE_TO_INFERENCE_LEVEL[et]}" for et in causal_types}
        )
        # +0.03 per additional distinct causal edge type beyond the first, capped at +0.12
        boost = min(0.12, (len(set(causal_types)) - 1) * 0.03)
        result[claim["claim_id"]] = (max_level, justification, boost)
    return result


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
        from app.evidence_engine.registry import ExtractorRegistry, _default_registry

        default_synthesizer = _coerce_synthesizer(synthesizer)
        default_confidence_scorer = DefaultConfidenceScorer()
        default_recommendation_policy = DefaultRecommendationPolicy()
        default_extractors = dict(_default_registry.as_mapping())
        if extractors is not None:
            if isinstance(extractors, ExtractorRegistry):
                default_extractors.update(extractors.as_mapping())
            else:
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

        # M-07: upgrade claim inference_level based on connected causal edges
        level_updates = _derive_inference_level_from_edges(edges, claims)
        if level_updates:
            claims = [
                {
                    **claim,
                    "inference_level": level_updates[claim["claim_id"]][0]
                        if claim["claim_id"] in level_updates
                        else claim.get("inference_level", "L0"),
                    "inference_justification": level_updates[claim["claim_id"]][1]
                        if claim["claim_id"] in level_updates
                        else claim.get("inference_justification", []),
                    "confidence": min(
                        0.99,
                        claim["confidence"] + level_updates[claim["claim_id"]][2],
                    ) if claim["claim_id"] in level_updates else claim["confidence"],
                }
                for claim in claims
            ]

        # M-10: attach causal_basis using final (post-upgrade) inference_level.
        # Pass supporting observations and a session summary so that the rule engine
        # can generate scope-aware confounders (G-3a/G-3b).
        if recommendations:
            _claim_idx: dict[str, Any] = {c["claim_id"]: c for c in claims}
            _obs_map: dict[str, Any] = {o["observation_id"]: o for o in observations}
            recommendations = [
                {
                    **rec,
                    "causal_basis": build_causal_basis(
                        _claim_idx[rec["claim_id"]],
                        [
                            _obs_map[oid]
                            for oid in _claim_idx[rec["claim_id"]].get("supporting_observations", [])
                            if oid in _obs_map
                        ],
                        derive_session_summary(
                            _claim_idx[rec["claim_id"]].get("scope", {}),
                            observations,
                        ),
                    )
                    if rec["claim_id"] in _claim_idx
                    else None,
                }
                for rec in recommendations
            ]

        # 1.1: auto-resolve confounders against confirmed claims in the session.
        if recommendations:
            _confirmed = [c for c in claims if c.get("status") == "confirmed"]
            recommendations = resolve_confounders(recommendations, _confirmed)

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
