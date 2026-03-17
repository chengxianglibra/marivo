"""Evidence-engine seams for the incremental refactor."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AggregateRowExtractor",
    "ClaimSynthesizer",
    "ComparisonRowExtractor",
    "ConfidenceScorer",
    "DefaultConfidenceScorer",
    "DefaultClaimSynthesizer",
    "DefaultRecommendationPolicy",
    "EvidencePipeline",
    "ObservationExtractor",
    "RecommendationPolicy",
    "score_confidence",
]


def __getattr__(name: str) -> Any:
    if name == "EvidencePipeline":
        from app.evidence_engine.pipeline import EvidencePipeline

        return EvidencePipeline
    if name == "score_confidence":
        from app.evidence_engine.scoring import score_confidence

        return score_confidence
    if name in {"ConfidenceScorer", "DefaultConfidenceScorer"}:
        from app.evidence_engine.scoring import ConfidenceScorer, DefaultConfidenceScorer

        return {
            "ConfidenceScorer": ConfidenceScorer,
            "DefaultConfidenceScorer": DefaultConfidenceScorer,
        }[name]
    if name in {"ObservationExtractor", "ComparisonRowExtractor", "AggregateRowExtractor"}:
        from app.evidence_engine.extractors import AggregateRowExtractor, ComparisonRowExtractor, ObservationExtractor

        return {
            "ObservationExtractor": ObservationExtractor,
            "ComparisonRowExtractor": ComparisonRowExtractor,
            "AggregateRowExtractor": AggregateRowExtractor,
        }[name]
    if name in {"RecommendationPolicy", "DefaultRecommendationPolicy"}:
        from app.evidence_engine.recommendation_policy import (
            DefaultRecommendationPolicy,
            RecommendationPolicy,
        )

        return {
            "RecommendationPolicy": RecommendationPolicy,
            "DefaultRecommendationPolicy": DefaultRecommendationPolicy,
        }[name]
    if name in {"ClaimSynthesizer", "DefaultClaimSynthesizer"}:
        from app.evidence_engine.synthesizers import ClaimSynthesizer, DefaultClaimSynthesizer

        return {
            "ClaimSynthesizer": ClaimSynthesizer,
            "DefaultClaimSynthesizer": DefaultClaimSynthesizer,
        }[name]
    raise AttributeError(name)
