from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

from app.evidence_engine.schemas import Claim, Observation, Recommendation


Synthesizer = Callable[[list[Observation]], tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]]


class SynthesisResult(TypedDict):
    claims: list[Claim]
    recommendations: list[Recommendation]
    edges: list[dict[str, Any]]
    summary: str


class EvidencePipeline:
    """Small compatibility seam before the full evidence split lands."""

    def __init__(self, synthesizer: Synthesizer) -> None:
        self._synthesizer = synthesizer

    def synthesize(
        self,
        observations: list[Observation],
    ) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]:
        return self._synthesizer(observations)

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
