from __future__ import annotations

from typing import Any
from uuid import uuid4


_AGGREGATE_FIELDS = frozenset({
    "current_value", "baseline_value", "delta_pct",
    "current_sessions", "baseline_sessions",
    "metric_value", "session_count", "row_count",
    "period",
})


def build_slice(row: dict[str, Any], dimensions: list[str] | None = None) -> dict[str, str]:
    if dimensions is not None:
        return {k: row[k] for k in dimensions if k in row}
    # Fallback: derive slice keys from row data minus known aggregate fields
    return {k: v for k, v in row.items() if k not in _AGGREGATE_FIELDS and v is not None}


def slice_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    common_keys = set(left) & set(right)
    if not common_keys:
        return not left and not right
    return all(left.get(key) == right.get(key) for key in common_keys)


def make_observation(
    observation_type: str,
    metric: str,
    row: dict[str, Any],
    payload: dict[str, Any],
    quality: dict[str, Any],
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": observation_type,
        "subject": {
            "metric": metric,
            "slice": build_slice(row, dimensions),
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


def make_causal_candidate_observation(
    metric: str,
    slice_info: dict[str, Any],
    candidate_cause_observation_id: str,
    payload: dict[str, Any],
    quality: dict[str, Any],
    observed_window: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a causal_candidate observation linking to a potential cause.

    This observation type enables cross-step causal chain linking. The
    candidate_cause_observation_id points to an observation from a prior step
    that is hypothesized to be the cause of the current observation.

    Args:
        metric: Metric name for the observation.
        slice_info: Slice dimensions for the observation.
        candidate_cause_observation_id: ID of the observation that is a candidate cause.
        payload: Additional payload data.
        quality: Data quality metadata.
        observed_window: Optional temporal window for the observation.

    Returns:
        A causal_candidate observation dict.
    """
    obs: dict[str, Any] = {
        "observation_id": f"obs_{uuid4().hex[:12]}",
        "type": "causal_candidate",
        "subject": {
            "metric": metric,
            "slice": slice_info,
        },
        "payload": {
            **payload,
            "candidate_cause_observation_id": candidate_cause_observation_id,
        },
        "significance": {
            "sample_size": int(payload.get("sample_size", 0)),
            "practical_significance": True,  # Candidate cause is always notable
        },
        "quality": quality,
    }
    if observed_window is not None:
        obs["observed_window"] = observed_window
    return obs
