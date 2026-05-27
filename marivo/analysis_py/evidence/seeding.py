"""Proposition seed templates. Slice-1 implements only T1 (change)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from marivo.analysis_py.evidence.identity import (
    canonical_subject_key,
    make_proposition_id,
)
from marivo.analysis_py.evidence.types import Finding, Proposition, Subject

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


__all__ = ["DERIVATION_VERSION", "seed_change_proposition"]
