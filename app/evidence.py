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
    metric_observations = [
        obs
        for obs in observations
        if obs["type"] == "metric_observation" and obs.get("payload", {}).get("delta_pct") is not None
    ]
    metric_window_observations = [
        obs
        for obs in observations
        if obs["type"] == "metric_observation" and obs.get("payload", {}).get("delta_pct") is None
    ]
    funnel_observations = [obs for obs in observations if obs["type"] == "funnel_drop"]
    contribution_observations = [obs for obs in observations if obs["type"] == "contribution_shift"]
    anomaly_observations = [obs for obs in observations if obs["type"] == "anomaly_detection"]

    if not metric_observations:
        # No delta-bearing metric_observation rows — try to synthesize from other types
        all_typed = metric_window_observations + funnel_observations + contribution_observations + anomaly_observations
        if not all_typed:
            return [], [], []
        return _synthesize_non_metric_claims(all_typed, funnel_observations, contribution_observations, anomaly_observations)

    primary_metric = max(
        metric_observations,
        key=lambda item: abs(float(item["payload"]["delta_pct"])) * math.log1p(item["significance"]["sample_size"]),
    )
    impacted_slice = primary_metric["subject"]["slice"]

    supports = [primary_metric["observation_id"]]
    support_reasons: list[str] = []
    consistency_factors = [1.0]
    contradiction_penalty = 0.0

    # Incorporate additional metric_observation rows that match the impacted slice
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
        "inference_level": "L0",
        "inference_justification": [],
    }

    claims = [primary_claim]

    # Generate overall_trend claim when multiple distinct metrics are observed
    distinct_metrics = {obs.get("subject", {}).get("metric") for obs in metric_observations}
    if len(distinct_metrics) > 1:
        declining = [obs for obs in metric_observations if float(obs["payload"]["delta_pct"]) < 0]
        improving = [obs for obs in metric_observations if float(obs["payload"]["delta_pct"]) > 0]
        trend_parts = []
        if declining:
            trend_parts.append(f"{len(declining)} declining")
        if improving:
            trend_parts.append(f"{len(improving)} improving")
        trend_text = f"Across {len(distinct_metrics)} metrics ({', '.join(trend_parts)}), the overall pattern suggests a broad {'decline' if len(declining) >= len(improving) else 'shift'}."
        overall_claim = {
            "claim_id": f"claim_{uuid4().hex[:12]}",
            "type": "overall_trend",
            "text": trend_text,
            "scope": {"slice": {}},
            "confidence": 0.0,
            "status": "supported",
            "supporting_observations": [obs["observation_id"] for obs in metric_observations],
            "contradicting_observations": [],
            "confidence_breakdown": {},
            "inference_level": "L0",
            "inference_justification": [],
        }
        claims.append(overall_claim)

    return claims, [], []


def _synthesize_non_metric_claims(
    all_observations: list[dict[str, Any]],
    funnel_observations: list[dict[str, Any]],
    contribution_observations: list[dict[str, Any]],
    anomaly_observations: list[dict[str, Any]],
) -> tuple[list[Claim], list[Recommendation], list[dict[str, Any]]]:
    """Synthesize claims when only non-delta metric observations are present."""

    # Pick the most significant observation as primary
    primary = max(all_observations, key=lambda o: o["significance"]["sample_size"])
    obs_type = primary["type"]
    impacted_slice = primary["subject"].get("slice", {})

    supports = [primary["observation_id"]]
    support_reasons: list[str] = []
    consistency_factors = [1.0]

    # Build claim text based on observation type
    if obs_type == "funnel_drop":
        text = f"Funnel drop detected at stage '{primary['payload'].get('worst_stage', 'unknown')}' with significant conversion loss."
    elif obs_type == "contribution_shift":
        text = f"Contribution shift detected in segment '{primary['payload'].get('biggest_shift_segment', 'unknown')}' indicating redistribution."
    elif obs_type == "anomaly_detection":
        z_score = primary["payload"].get("z_score", 0)
        text = f"Statistical anomaly detected (z-score: {z_score}) indicating abnormal behavior."
    elif obs_type == "metric_observation":
        current_value = primary["payload"].get("current_value")
        if impacted_slice:
            slice_label = " / ".join(f"{k}={v}" for k, v in impacted_slice.items())
        else:
            slice_label = "overall"
        if isinstance(current_value, (int, float)):
            text = f"Current window observation for {slice_label}: value={current_value}."
        else:
            text = f"Current window observation recorded for {slice_label}."
    else:
        text = f"Finding of type '{obs_type}' detected with practical significance."

    # Incorporate other observations as supporting evidence
    for obs in all_observations:
        if obs is primary:
            continue
        if obs["significance"].get("practical_significance"):
            supports.append(obs["observation_id"])
            if obs["type"] == "funnel_drop":
                support_reasons.append(f"funnel drop at {obs['payload'].get('worst_stage', 'unknown')}")
            elif obs["type"] == "contribution_shift":
                support_reasons.append(f"contribution shift in {obs['payload'].get('biggest_shift_segment', 'unknown')}")
            elif obs["type"] == "anomaly_detection":
                support_reasons.append("statistical anomaly detected")
            else:
                support_reasons.append(f"{obs['type']} finding")
            consistency_factors.append(0.85)

    consistency = sum(consistency_factors) / len(consistency_factors)
    sample_score = min(1.0, primary["significance"]["sample_size"] / 150.0)
    data_quality_score = 0.95 if primary["quality"].get("sample_size_ok", True) else 0.60

    if impacted_slice:
        slice_label = " / ".join(f"{k}={v}" for k, v in impacted_slice.items())
    else:
        slice_label = "overall"

    if support_reasons:
        text += f" Corroborated by {' and '.join(support_reasons)} in {slice_label}."

    claim = {
        "claim_id": f"claim_{uuid4().hex[:12]}",
        "type": "finding",
        "text": text,
        "scope": {"slice": impacted_slice},
        "confidence": 0.0,
        "status": "supported",
        "supporting_observations": supports,
        "contradicting_observations": [],
        "confidence_breakdown": {
            "effect_strength": 0.50,
            "consistency": round(consistency, 2),
            "sample_score": round(sample_score, 2),
            "data_quality_score": round(data_quality_score, 2),
            "contradiction_penalty": 0.0,
        },
        "inference_level": "L0",
        "inference_justification": [],
    }

    return [claim], [], []
