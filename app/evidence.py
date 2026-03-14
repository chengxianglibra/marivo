from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from app.evidence_engine.factories import (
    build_slice,
    make_anomaly_observation,
    make_contribution_observation,
    make_funnel_observation,
    make_observation,
    slice_matches,
)
from app.evidence_engine.scoring import score_confidence
from app.evidence_engine.schemas import Claim, Recommendation


def synthesize_claims(
    observations: list[dict[str, Any]],
) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]:
    metric_observations = [obs for obs in observations if obs["type"] == "metric_change"]
    funnel_observations = [obs for obs in observations if obs["type"] == "funnel_drop"]
    contribution_observations = [obs for obs in observations if obs["type"] == "contribution_shift"]
    anomaly_observations = [obs for obs in observations if obs["type"] == "anomaly_detection"]

    if not metric_observations:
        return [], [], []

    primary_metric = min(metric_observations, key=lambda item: float(item["payload"]["delta_pct"]))
    impacted_slice = primary_metric["subject"]["slice"]

    supports = [primary_metric["observation_id"]]
    support_reasons: list[str] = []
    consistency_factors = [1.0]
    contradiction_penalty = 0.0

    # Incorporate additional metric_change observations that match the impacted slice
    for obs in metric_observations:
        if obs is primary_metric:
            continue
        if slice_matches(obs["subject"]["slice"], impacted_slice):
            supports.append(obs["observation_id"])
            metric_name = obs.get("subject", {}).get("metric", "metric")
            support_reasons.append(f"{metric_name} change")
            consistency_factors.append(0.9)

    # Incorporate funnel observations -- a significant funnel drop strengthens the claim
    for obs in funnel_observations:
        if obs["significance"]["practical_significance"]:
            supports.append(obs["observation_id"])
            support_reasons.append(f"funnel drop at {obs['payload']['worst_stage']}")
            consistency_factors.append(0.85)

    # Incorporate contribution observations -- a significant share shift strengthens the claim
    for obs in contribution_observations:
        if obs["significance"]["practical_significance"]:
            supports.append(obs["observation_id"])
            support_reasons.append(f"contribution shift in {obs['payload']['biggest_shift_segment']}")
            consistency_factors.append(0.80)

    # Incorporate anomaly observations -- anomalies in the impacted slice strengthen the claim
    for obs in anomaly_observations:
        if obs["significance"]["practical_significance"]:
            supports.append(obs["observation_id"])
            support_reasons.append("statistical anomaly detected")
            consistency_factors.append(0.90)

    contradicts: list[str] = []

    effect_strength = min(1.0, abs(float(primary_metric["payload"]["delta_pct"])) / 20.0)
    consistency = sum(consistency_factors) / len(consistency_factors)
    sample_score = min(1.0, primary_metric["significance"]["sample_size"] / 150.0)
    data_quality_score = 0.95 if primary_metric["quality"]["sample_size_ok"] else 0.60
    if impacted_slice:
        slice_label = " / ".join(f"{k}={v}" for k, v in impacted_slice.items())
    else:
        slice_label = "overall"
    reason_label = " and ".join(support_reasons) if support_reasons else "localized traffic changes"

    primary_claim = {
        "claim_id": f"claim_{uuid4().hex[:12]}",
        "type": "root_cause_candidate",
        "text": f"Metric decline is concentrated in {slice_label} traffic, with {reason_label} acting as the leading driver.",
        "scope": {"slice": impacted_slice},
        "confidence": 0.0,
        "status": "supported",
        "supporting_observations": supports,
        "contradicting_observations": contradicts,
        "confidence_breakdown": {
            "effect_strength": round(effect_strength, 2),
            "consistency": round(consistency, 2),
            "sample_score": round(sample_score, 2),
            "data_quality_score": round(data_quality_score, 2),
            "contradiction_penalty": round(contradiction_penalty, 2),
        },
    }

    claims = [primary_claim]
    return claims, [], []
