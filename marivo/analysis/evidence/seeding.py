"""Proposition seed templates for evidence finding families."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from marivo.analysis.evidence.identity import (
    canonical_subject_key,
    make_proposition_id,
)
from marivo.analysis.evidence.types import Finding, Proposition, Subject

DERIVATION_VERSION = "v1"

_CHANGE_KIND_MAP: dict[str, str | None] = {
    "scalar_delta": "scalar_change",
    "segmented_delta": "segment_change",
    "time_series_delta": None,
    "panel_delta": "panel_change",
}


def _direction_of_interest(direction: str) -> str:
    if direction == "increase":
        return "increase"
    if direction == "decrease":
        return "decrease"
    return "any_non_flat"


def seed_change_proposition(
    *,
    finding: Finding,
    comparison_window: dict[str, Any],
    comparison_basis: str,
) -> Proposition | None:
    """Seed a T1 change proposition from a delta finding.

    Returns None when the finding does not warrant a change proposition
    (flat direction, undefined without presence, or unsupported delta_kind).
    """
    if finding.finding_type != "delta":
        return None

    payload = finding.payload
    direction = payload.get("direction") or "undefined"
    presence = payload.get("presence")
    delta_kind = payload.get("delta_kind") or ""

    if direction == "flat":
        return None
    if direction == "undefined" and presence not in ("current_only", "baseline_only"):
        return None

    change_kind = _CHANGE_KIND_MAP.get(delta_kind)
    if change_kind is None:
        return None

    subject = Subject(
        metric=finding.subject.metric,
        entity=finding.subject.entity,
        slice=dict(finding.subject.slice),
        grain=finding.subject.grain,
        analysis_axis="change",
    )
    subject_key = canonical_subject_key(subject)

    prop_payload: dict[str, Any] = {
        "change_kind": change_kind,
        "comparison_window": comparison_window,
        "direction_of_interest": _direction_of_interest(direction),
        "comparison_basis": comparison_basis,
        "unit": payload.get("unit"),
    }
    if delta_kind in ("segmented_delta", "panel_delta"):
        dimension_keys = payload.get("dimension_keys")
        if not isinstance(dimension_keys, dict):
            return None
        prop_payload["dimension_keys"] = dict(dimension_keys)

    proposition_id = make_proposition_id(
        proposition_type="change",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
    )

    return Proposition(
        proposition_id=proposition_id,
        session_id=finding.session_id,
        proposition_type="change",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
        seed_finding_refs=[finding.finding_id],
        created_at=datetime.now(UTC),
    )


def _classify_contribution_role(share: float | None, direction: str) -> str:
    if share is None:
        return "material_component"
    if (direction == "decrease" and share < 0) or (direction == "increase" and share < 0):
        return "offsetting_factor"
    if abs(share) >= 0.5:
        return "primary_driver"
    if abs(share) >= 0.2:
        return "secondary_driver"
    return "material_component"


def seed_driver_proposition(
    *,
    finding: Finding,
    observed_window: dict[str, Any] | None,
) -> Proposition | None:
    """Seed a T2 driver proposition from a decomposition_item finding."""
    if finding.finding_type != "decomposition_item":
        return None
    payload = finding.payload
    scope_delta_ref = payload.get("scope_delta_ref")
    dimension_keys = payload.get("dimension_keys") or {}
    dimension = payload.get("dimension")
    if not scope_delta_ref or not dimension_keys or not dimension:
        return None

    subject = Subject(
        metric=finding.subject.metric,
        entity=finding.subject.entity,
        slice=dict(finding.subject.slice),
        grain=finding.subject.grain,
        analysis_axis="decomposition",
    )
    subject_key = canonical_subject_key(subject)
    role = _classify_contribution_role(
        payload.get("contribution_share"), payload.get("direction") or "undefined"
    )
    prop_payload: dict[str, Any] = {
        "dimension": dimension,
        "dimension_keys": dict(dimension_keys),
        "scope_delta_ref": scope_delta_ref,
        "contribution_role": role,
        "observed_window": observed_window,
    }
    proposition_id = make_proposition_id(
        proposition_type="driver",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
    )
    return Proposition(
        proposition_id=proposition_id,
        session_id=finding.session_id,
        proposition_type="driver",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
        seed_finding_refs=[finding.finding_id],
        created_at=datetime.now(UTC),
    )


def seed_anomaly_proposition(
    *,
    finding: Finding,
    observed_window: dict[str, Any] | None,
) -> Proposition | None:
    """Seed a T3 anomaly proposition from an anomaly_candidate finding."""
    if finding.finding_type != "anomaly_candidate":
        return None
    candidate_ref = finding.payload.get("candidate_ref")
    if not candidate_ref or not observed_window:
        return None

    subject = finding.subject
    subject_key = canonical_subject_key(subject)
    prop_payload: dict[str, Any] = {
        "candidate_ref": candidate_ref,
        "anomaly_kind": "candidate",
        "observed_window": observed_window,
    }
    proposition_id = make_proposition_id(
        proposition_type="anomaly",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
    )
    return Proposition(
        proposition_id=proposition_id,
        session_id=finding.session_id,
        proposition_type="anomaly",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
        seed_finding_refs=[finding.finding_id],
        created_at=datetime.now(UTC),
    )


def seed_correlation_proposition(
    *,
    finding: Finding,
    aligned_window: dict[str, Any] | None,
    left_subject: dict[str, Any],
    right_subject: dict[str, Any],
) -> Proposition | None:
    """Seed a T4 association proposition from a correlation_result finding."""
    if finding.finding_type != "correlation_result":
        return None
    payload = finding.payload
    if not payload.get("join_basis") or not aligned_window:
        return None
    if not left_subject.get("metric") or not right_subject.get("metric"):
        return None

    subject = finding.subject
    subject_key = canonical_subject_key(subject)
    prop_payload: dict[str, Any] = {
        "left_subject": dict(left_subject),
        "right_subject": dict(right_subject),
        "method_family": payload.get("method"),
        "relationship_of_interest": "any_non_zero",
        "join_basis": payload.get("join_basis"),
        "aligned_window": aligned_window,
    }
    proposition_id = make_proposition_id(
        proposition_type="association",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
    )
    return Proposition(
        proposition_id=proposition_id,
        session_id=finding.session_id,
        proposition_type="association",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
        seed_finding_refs=[finding.finding_id],
        created_at=datetime.now(UTC),
    )


def seed_test_hypothesis_proposition(
    *,
    finding: Finding,
    left_subject: dict[str, Any],
    right_subject: dict[str, Any],
    alternative: str = "two_sided",
) -> Proposition | None:
    """Seed a T5 tested_hypothesis proposition from a test_result finding."""
    if finding.finding_type != "test_result":
        return None
    payload = finding.payload
    alpha = payload.get("alpha")
    if alpha is None:
        return None
    if not left_subject.get("metric") or not right_subject.get("metric"):
        return None

    subject = finding.subject
    subject_key = canonical_subject_key(subject)
    prop_payload: dict[str, Any] = {
        "left_subject": dict(left_subject),
        "right_subject": dict(right_subject),
        "hypothesis_family": "difference",
        "alternative": alternative,
        "method_family": payload.get("method"),
        "alpha": alpha,
    }
    proposition_id = make_proposition_id(
        proposition_type="tested_hypothesis",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
    )
    return Proposition(
        proposition_id=proposition_id,
        session_id=finding.session_id,
        proposition_type="tested_hypothesis",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
        seed_finding_refs=[finding.finding_id],
        created_at=datetime.now(UTC),
    )


def seed_forecast_proposition(*, finding: Finding) -> Proposition | None:
    """Seed a T6 forecast proposition from a forecast_point finding."""
    if finding.finding_type != "forecast_point":
        return None
    payload = finding.payload
    bucket_start = payload.get("bucket_start")
    bucket_end = payload.get("bucket_end")
    horizon_index = payload.get("horizon_index")
    if not bucket_start or not bucket_end or horizon_index is None:
        return None

    forecast_kind = "interval" if payload.get("prediction_interval") else "point"
    subject = finding.subject
    subject_key = canonical_subject_key(subject)
    prop_payload: dict[str, Any] = {
        "forecast_kind": forecast_kind,
        "forecast_window": {"start": bucket_start, "end": bucket_end},
        "horizon_index": int(horizon_index),
        "expectation_direction": "open",
    }
    proposition_id = make_proposition_id(
        proposition_type="forecast",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
    )
    return Proposition(
        proposition_id=proposition_id,
        session_id=finding.session_id,
        proposition_type="forecast",
        origin_kind="system_seeded",
        derivation_version=DERIVATION_VERSION,
        subject_key=subject_key,
        payload=prop_payload,
        seed_finding_refs=[finding.finding_id],
        created_at=datetime.now(UTC),
    )


__all__ = [
    "DERIVATION_VERSION",
    "seed_anomaly_proposition",
    "seed_change_proposition",
    "seed_correlation_proposition",
    "seed_driver_proposition",
    "seed_forecast_proposition",
    "seed_test_hypothesis_proposition",
]
