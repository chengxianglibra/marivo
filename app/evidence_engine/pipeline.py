from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from app.evidence_engine.extractors import ComparisonRowExtractor, ObservationExtractor
from app.evidence_engine.schemas import Claim, Observation, Recommendation
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
    ) -> None:
        default_synthesizer = _coerce_synthesizer(synthesizer)
        default_extractors = {
            extractor.name: extractor
            for extractor in [ComparisonRowExtractor()]
        }
        if extractors:
            default_extractors.update(extractors)
        self._extractors = default_extractors

        default_synthesizers = {default_synthesizer.name: default_synthesizer}
        if synthesizers:
            default_synthesizers.update(synthesizers)
        self._synthesizers = default_synthesizers
        self._default_synthesizer_name = default_synthesizer.name

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
    ) -> SynthesisResult:
        claims, recommendations, edges = self.synthesize(observations)

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
