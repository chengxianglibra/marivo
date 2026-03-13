from __future__ import annotations

from typing import Any
from uuid import uuid4


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
