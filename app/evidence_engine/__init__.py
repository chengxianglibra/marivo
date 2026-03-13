"""Evidence-engine seams for the incremental refactor."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ClaimSynthesizer",
    "ComparisonRowExtractor",
    "DefaultClaimSynthesizer",
    "EvidencePipeline",
    "ObservationExtractor",
    "score_confidence",
]


def __getattr__(name: str) -> Any:
    if name == "EvidencePipeline":
        from app.evidence_engine.pipeline import EvidencePipeline

        return EvidencePipeline
    if name == "score_confidence":
        from app.evidence_engine.scoring import score_confidence

        return score_confidence
    if name in {"ObservationExtractor", "ComparisonRowExtractor"}:
        from app.evidence_engine.extractors import ComparisonRowExtractor, ObservationExtractor

        return {
            "ObservationExtractor": ObservationExtractor,
            "ComparisonRowExtractor": ComparisonRowExtractor,
        }[name]
    if name in {"ClaimSynthesizer", "DefaultClaimSynthesizer"}:
        from app.evidence_engine.synthesizers import ClaimSynthesizer, DefaultClaimSynthesizer

        return {
            "ClaimSynthesizer": ClaimSynthesizer,
            "DefaultClaimSynthesizer": DefaultClaimSynthesizer,
        }[name]
    raise AttributeError(name)
