from __future__ import annotations


def score_confidence(
    effect_strength: float,
    consistency: float,
    sample_score: float,
    data_quality_score: float,
    contradiction_penalty: float,
) -> float:
    raw = (
        0.30 * effect_strength
        + 0.25 * consistency
        + 0.20 * sample_score
        + 0.25 * data_quality_score
        - contradiction_penalty
    )
    return round(max(0.0, min(0.99, raw)), 2)
