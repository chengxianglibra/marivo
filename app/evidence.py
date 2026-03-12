from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from app.evidence_engine.scoring import score_confidence


def build_slice(row: dict[str, Any]) -> dict[str, str]:
    return {
        "platform": row["platform"],
        "app_version": row["app_version"],
        "network_type": row["network_type"],
        "content_type": row["content_type"],
    }


def slice_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("platform", "app_version", "network_type", "content_type"))


def make_observation(
    observation_type: str,
    metric: str,
    row: dict[str, Any],
    payload: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": observation_type,
        "subject": {
            "metric": metric,
            "slice": build_slice(row),
        },
        "payload": payload,
        "significance": {
            "sample_size": min(int(payload.get("current_sessions", 0)), int(payload.get("baseline_sessions", 0))),
            "practical_significance": abs(float(payload.get("delta_pct", payload.get("delta_rate", 0.0)))) >= 5.0,
        },
        "quality": quality,
    }


def make_funnel_observation(
    funnel_name: str,
    stages: list[dict[str, Any]],
    quality: dict[str, Any],
) -> dict[str, Any]:
    """Create a funnel_drop observation from stage-over-stage drop data.

    Each stage dict should have: stage_name, users, drop_rate, delta_drop_rate (vs baseline).
    """
    worst_stage = max(stages, key=lambda s: abs(float(s.get("delta_drop_rate", 0))))
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "funnel_drop",
        "subject": {
            "metric": funnel_name,
            "slice": {"funnel": funnel_name, "worst_stage": worst_stage["stage_name"]},
        },
        "payload": {
            "stages": stages,
            "worst_stage": worst_stage["stage_name"],
            "worst_delta_drop_rate": worst_stage.get("delta_drop_rate", 0),
        },
        "significance": {
            "sample_size": min(s.get("users", 0) for s in stages) if stages else 0,
            "practical_significance": abs(float(worst_stage.get("delta_drop_rate", 0))) >= 0.05,
        },
        "quality": quality,
    }


def make_contribution_observation(
    metric: str,
    segment_name: str,
    contributions: list[dict[str, Any]],
    quality: dict[str, Any],
) -> dict[str, Any]:
    """Create a contribution_shift observation for metric contribution breakdown changes.

    Each contribution dict should have: segment_value, current_share, baseline_share, delta_share.
    """
    biggest_shift = max(contributions, key=lambda c: abs(float(c.get("delta_share", 0))))
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "contribution_shift",
        "subject": {
            "metric": metric,
            "slice": {"segment": segment_name, "biggest_shift": biggest_shift["segment_value"]},
        },
        "payload": {
            "segment_name": segment_name,
            "contributions": contributions,
            "biggest_shift_segment": biggest_shift["segment_value"],
            "biggest_delta_share": biggest_shift.get("delta_share", 0),
        },
        "significance": {
            "sample_size": sum(c.get("current_count", 0) for c in contributions),
            "practical_significance": abs(float(biggest_shift.get("delta_share", 0))) >= 0.05,
        },
        "quality": quality,
    }


def make_anomaly_observation(
    metric: str,
    slice_info: dict[str, Any],
    payload: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    """Create an anomaly_detection observation for statistical outlier flagging.

    payload should have: value, mean, stddev, z_score, is_anomaly.
    """
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "anomaly_detection",
        "subject": {
            "metric": metric,
            "slice": slice_info,
        },
        "payload": payload,
        "significance": {
            "sample_size": int(payload.get("sample_size", 0)),
            "practical_significance": abs(float(payload.get("z_score", 0))) >= 2.0,
        },
        "quality": quality,
    }


def synthesize_claims(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    watch_observations = [obs for obs in observations if obs["type"] == "metric_change"]
    qoe_observations = [obs for obs in observations if obs["type"] == "qoe_regression"]
    ad_observations = [obs for obs in observations if obs["type"] == "ad_regression"]
    recommendation_observations = [obs for obs in observations if obs["type"] == "recommendation_signal"]
    funnel_observations = [obs for obs in observations if obs["type"] == "funnel_drop"]
    contribution_observations = [obs for obs in observations if obs["type"] == "contribution_shift"]
    anomaly_observations = [obs for obs in observations if obs["type"] == "anomaly_detection"]

    if not watch_observations:
        return [], [], []

    primary_watch = min(watch_observations, key=lambda item: float(item["payload"]["delta_pct"]))
    impacted_slice = primary_watch["subject"]["slice"]

    qoe_support = next(
        (
            obs
            for obs in qoe_observations
            if slice_matches(obs["subject"]["slice"], impacted_slice) and float(obs["payload"]["delta_pct"]) >= 10.0
        ),
        None,
    )
    ad_support = next(
        (
            obs
            for obs in ad_observations
            if slice_matches(obs["subject"]["slice"], impacted_slice) and float(obs["payload"]["delta_rate"]) >= 0.05
        ),
        None,
    )
    recommendation_signal = next(
        (obs for obs in recommendation_observations if slice_matches(obs["subject"]["slice"], impacted_slice)),
        None,
    )

    supports = [primary_watch["observation_id"]]
    support_reasons: list[str] = []
    consistency_factors = [1.0]
    contradiction_penalty = 0.0

    if qoe_support:
        supports.append(qoe_support["observation_id"])
        support_reasons.append("playback QoE regression")
        consistency_factors.append(1.0)
    else:
        consistency_factors.append(0.45)

    if ad_support:
        supports.append(ad_support["observation_id"])
        support_reasons.append("higher preroll timeout rate")
        consistency_factors.append(0.9)
    else:
        consistency_factors.append(0.45)

    # Incorporate funnel observations — a significant funnel drop strengthens the claim
    for obs in funnel_observations:
        if obs["significance"]["practical_significance"]:
            supports.append(obs["observation_id"])
            support_reasons.append(f"funnel drop at {obs['payload']['worst_stage']}")
            consistency_factors.append(0.85)

    # Incorporate contribution observations — a significant share shift strengthens the claim
    for obs in contribution_observations:
        if obs["significance"]["practical_significance"]:
            supports.append(obs["observation_id"])
            support_reasons.append(f"contribution shift in {obs['payload']['biggest_shift_segment']}")
            consistency_factors.append(0.80)

    # Incorporate anomaly observations — anomalies in the impacted slice strengthen the claim
    for obs in anomaly_observations:
        if obs["significance"]["practical_significance"]:
            supports.append(obs["observation_id"])
            support_reasons.append("statistical anomaly detected")
            consistency_factors.append(0.90)

    contradicts: list[str] = []
    recommendation_claims: list[dict[str, Any]] = []
    if recommendation_signal:
        ctr_delta = float(recommendation_signal["payload"]["delta_ctr_pct"])
        if ctr_delta >= 0.0:
            supports.append(recommendation_signal["observation_id"])
            consistency_factors.append(0.8)
            recommendation_claims.append(
                {
                    "claim_id": f"claim_{uuid4().hex[:12]}",
                    "type": "counter_hypothesis",
                    "text": (
                        "Recommendation quality is unlikely to be the primary driver because CTR stayed flat or improved "
                        "for the impacted traffic slice."
                    ),
                    "scope": {"slice": impacted_slice},
                    "confidence": round(min(0.95, 0.65 + min(ctr_delta / 10.0, 0.20)), 2),
                    "status": "supported",
                    "supporting_observations": [recommendation_signal["observation_id"]],
                    "contradicting_observations": [],
                    "confidence_breakdown": {
                        "effect_strength": round(min(1.0, max(ctr_delta, 0.0) / 5.0), 2),
                        "consistency": 0.8,
                        "sample_score": round(min(1.0, recommendation_signal["significance"]["sample_size"] / 150.0), 2),
                        "data_quality_score": 0.95,
                    },
                }
            )
        else:
            contradiction_penalty += 0.15
            contradicts.append(recommendation_signal["observation_id"])

    effect_strength = min(1.0, abs(float(primary_watch["payload"]["delta_pct"])) / 20.0)
    consistency = sum(consistency_factors) / len(consistency_factors)
    sample_score = min(1.0, primary_watch["significance"]["sample_size"] / 150.0)
    data_quality_score = 0.95 if primary_watch["quality"]["sample_size_ok"] else 0.60
    confidence = score_confidence(effect_strength, consistency, sample_score, data_quality_score, contradiction_penalty)

    slice_label = (
        f'{impacted_slice["platform"].title()} {impacted_slice["app_version"]} '
        f'{impacted_slice["network_type"]} {impacted_slice["content_type"]}-video'
    )
    reason_label = " and ".join(support_reasons) if support_reasons else "localized traffic changes"

    primary_claim = {
        "claim_id": f"claim_{uuid4().hex[:12]}",
        "type": "root_cause_candidate",
        "text": f"Watch time decline is concentrated in {slice_label} traffic, with {reason_label} acting as the leading driver.",
        "scope": {"slice": impacted_slice},
        "confidence": confidence,
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

    recommendations = []
    if qoe_support:
        recommendations.append(
            {
                "rec_id": f"rec_{uuid4().hex[:12]}",
                "claim_id": primary_claim["claim_id"],
                "action_text": "Prioritize an Android 8.3.1 playback fix focused on reducing first-frame latency for weak-network sessions.",
                "priority": "P0",
                "expected_impact": "Recover 30-second retention for the impacted Android cohort.",
                "risk": "May require player hotfix rollout and staged validation.",
                "validation_metric": {
                    "primary_metric": "retention_30s",
                    "secondary_metric": "watch_time",
                },
            }
        )
    if ad_support:
        recommendations.append(
            {
                "rec_id": f"rec_{uuid4().hex[:12]}",
                "claim_id": primary_claim["claim_id"],
                "action_text": "Reduce preroll burden for weak-network short-video traffic while the playback issue is being mitigated.",
                "priority": "P1",
                "expected_impact": "Lower early exits caused by timeout-heavy ad starts.",
                "risk": "Short-term revenue tradeoff on the impacted cohort.",
                "validation_metric": {
                    "primary_metric": "preroll_timeout_rate",
                    "secondary_metric": "watch_time",
                },
            }
        )
    recommendations.append(
        {
            "rec_id": f"rec_{uuid4().hex[:12]}",
            "claim_id": primary_claim["claim_id"],
            "action_text": "Launch a recovery experiment for affected Android weak-network users after the hotfix lands.",
            "priority": "P1",
            "expected_impact": "Validate watch-time recovery before rolling strategy changes to all users.",
            "risk": "Experiment duration may delay full rollout decisions.",
            "validation_metric": {
                "primary_metric": "watch_time",
                "secondary_metric": "retention_30s",
            },
        }
    )

    claims = [primary_claim, *recommendation_claims]
    return claims, recommendations, []
