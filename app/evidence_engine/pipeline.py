from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from app.evidence_engine.causal_checkers import CausalCheckerRegistry, get_default_registry
from app.evidence_engine.claim_relations import (
    ClaimRelationDiscovery,
    DefaultClaimRelationDiscovery,
    materialize_relations_as_edges,
)
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
    ClaimRelation,
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


class CausalPromotionResult(TypedDict):
    claims: list[Claim]
    edges: list[dict[str, Any]]


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
    """Evidence extraction + synthesis pipeline with explicit layered seams.

    The synthesis path is intentionally modeled as five layers:
    1. Claim Synthesis
    2. Claim Relation Discovery
    3. Causal Promotion
    4. Recommendation Derivation
    5. Edge Materialization

    Observation extraction is exposed separately via ``extract_observations()``.
    """

    def __init__(
        self,
        synthesizer: Synthesizer | ClaimSynthesizer,
        *,
        extractors: Mapping[str, ObservationExtractor] | None = None,
        synthesizers: Mapping[str, ClaimSynthesizer] | None = None,
        confidence_scorers: Mapping[str, ConfidenceScorer] | None = None,
        recommendation_policies: Mapping[str, RecommendationPolicy] | None = None,
        relation_discoveries: Mapping[str, ClaimRelationDiscovery] | None = None,
        causal_checker_registry: CausalCheckerRegistry | None = None,
        metric_direction_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        from app.evidence_engine.registry import ExtractorRegistry, _default_registry

        default_synthesizer = _coerce_synthesizer(synthesizer)
        default_confidence_scorer = DefaultConfidenceScorer()
        default_recommendation_policy = DefaultRecommendationPolicy(
            metric_direction_resolver=metric_direction_resolver,
        )
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

        default_relation_discovery = DefaultClaimRelationDiscovery()
        default_relation_discoveries = {default_relation_discovery.name: default_relation_discovery}
        if relation_discoveries:
            default_relation_discoveries.update(relation_discoveries)
        self._relation_discoveries = default_relation_discoveries
        self._default_relation_discovery_name = default_relation_discovery.name

        self._causal_checker_registry = causal_checker_registry or get_default_registry()

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
        relation_discovery_name: str | None = None,
        confidence_scorer_name: str | None = None,
        recommendation_policy_name: str | None = None,
    ) -> SynthesisResult:
        claims, recommendations, edges = self._synthesize_claims_layer(
            observations,
            existing_claims=existing_claims,
            synthesizer_name=synthesizer_name,
        )
        claims = self.score_claims(
            observations,
            claims,
            confidence_scorer_name=confidence_scorer_name,
        )
        relations = self.discover_relations(
            observations,
            claims,
            edges,
            relation_discovery_name=relation_discovery_name,
        )
        promotion = self.promote_causality(observations, claims, edges, relations)
        claims = promotion["claims"]
        edges.extend(promotion["edges"])
        recommendations = self.derive_recommendations(
            observations,
            claims,
            relations,
            recommendations,
            recommendation_policy_name=recommendation_policy_name,
        )
        edges.extend(self.materialize_relation_edges(relations))
        edges.extend(self.materialize_support_edges(claims))
        edges.extend(self.materialize_recommendation_edges(recommendations))

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
            "edges": _dedupe_edges(edges),
            "summary": claims[0]["text"] if claims else "No supported claims were generated.",
        }

    def _synthesize_claims_layer(
        self,
        observations: list[Observation],
        *,
        existing_claims: list[Claim] | None,
        synthesizer_name: str | None,
    ) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]:
        # Any explicit existing_claims value, including [], means the caller is
        # supplying the claim layer and wants synthesis skipped.
        if existing_claims is not None:
            return existing_claims, [], []
        return self.synthesize(observations, synthesizer_name=synthesizer_name)

    def discover_relations(
        self,
        observations: list[Observation],
        claims: list[Claim],
        edges: list[dict[str, Any]],
        *,
        relation_discovery_name: str | None = None,
    ) -> list[ClaimRelation]:
        resolved_name = relation_discovery_name or self._default_relation_discovery_name
        if resolved_name not in self._relation_discoveries:
            raise KeyError(f"Unknown claim relation discovery: {resolved_name}")
        return self._relation_discoveries[resolved_name].discover(claims, observations, edges)

    def promote_causality(
        self,
        observations: list[Observation],
        claims: list[Claim],
        edges: list[dict[str, Any]],
        relations: list[ClaimRelation],
    ) -> CausalPromotionResult:
        upgrades = self._causal_checker_registry.run_all(
            claims,
            observations,
            edges,
            relations=relations,
        )
        promoted_edges: list[dict[str, Any]] = []
        for upgrade in upgrades:
            for edge in upgrade.causal_edges:
                promoted_edges.append(
                    {
                        "from_node_id": edge.from_node_id,
                        "from_node_type": edge.from_node_type,
                        "to_node_id": edge.to_node_id,
                        "to_node_type": edge.to_node_type,
                        "edge_type": edge.edge_type,
                        "weight": edge.weight,
                        "explanation": edge.explanation,
                    }
                )

        combined_edges = edges + promoted_edges
        level_updates = _derive_inference_level_from_edges(combined_edges, claims)
        if not level_updates:
            return {"claims": claims, "edges": promoted_edges}
        promoted_claims = [
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
        return {"claims": promoted_claims, "edges": promoted_edges}

    def materialize_support_edges(self, claims: list[Claim]) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
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
        return edges

    def materialize_relation_edges(self, relations: list[ClaimRelation]) -> list[dict[str, Any]]:
        return materialize_relations_as_edges(relations)

    def materialize_recommendation_edges(
        self,
        recommendations: list[Recommendation],
    ) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        for recommendation in recommendations:
            backing_claim_ids = recommendation.get("supporting_claims") or [recommendation["claim_id"]]
            for backing_id in backing_claim_ids:
                edges.append(
                    {
                        "from_node_id": backing_id,
                        "from_node_type": "claim",
                        "to_node_id": recommendation["rec_id"],
                        "to_node_type": "recommendation",
                        "edge_type": "justifies",
                        "weight": 0.9,
                        "explanation": "Claim justifies the recommendation.",
                    }
                )
        return edges

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
        relations: list[ClaimRelation],
        recommendations: list[Recommendation],
        *,
        recommendation_policy_name: str | None = None,
    ) -> list[Recommendation]:
        resolved_name = recommendation_policy_name or self._default_recommendation_policy_name
        if resolved_name not in self._recommendation_policies:
            raise KeyError(f"Unknown recommendation policy: {resolved_name}")
        policy = self._recommendation_policies[resolved_name]
        try:
            return policy.derive(
                observations,
                claims,
                recommendations,
                relations=relations,
            )
        except TypeError:
            # Backward compatibility for custom policies still implementing the
            # pre-Phase-1.4 signature.
            return policy.derive(
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


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for edge in edges:
        key = (
            str(edge.get("from_node_id")),
            str(edge.get("from_node_type")),
            str(edge.get("to_node_id")),
            str(edge.get("to_node_type")),
            str(edge.get("edge_type")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped
