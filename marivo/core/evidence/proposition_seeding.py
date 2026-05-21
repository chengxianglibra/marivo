"""Pure proposition seeding logic extracted from the evidence pipeline.

Extracted from ``marivo.runtime.evidence.proposition_seeding`` as part of
Phase 3c.  This module contains only the pure computation portions:
- Template materialization logic (T1-T6)
- Segment key parsing and decoding
- Correlation join basis parsing
- Bilateral focus-anchor algorithm
- Canonical subject key derivation

The I/O-bound parts (``SimpleMaterializationContext``, repository access,
``run_system_seeded_propositions``) remain in the original module.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Segment key decode / encode helpers
# ---------------------------------------------------------------------------


def decode_seg_component(s: str) -> str:
    """Reverse the percent-encoding applied by escape_seg_component.

    Decode order: %7C -> |, %3D -> =, %25 -> % (reverse of encode order).
    """
    return s.replace("%7C", "|").replace("%3D", "=").replace("%25", "%")


def parse_segment_key(canonical_item_key: str) -> dict[str, str] | None:
    """Parse the segment portion of a ``rows:k=v|k=v`` canonical_item_key.

    Returns a ``str->str`` dict of dimension key-value pairs, or ``None``
    if the key does not start with ``"rows:"`` or is malformed.
    """
    if not canonical_item_key.startswith("rows:"):
        return None
    seg_part = canonical_item_key[5:]
    if not seg_part:
        return None
    result: dict[str, str] = {}
    for pair in seg_part.split("|"):
        if "=" not in pair:
            return None
        raw_k, raw_v = pair.split("=", 1)
        result[decode_seg_component(raw_k)] = decode_seg_component(raw_v)
    return result


# ---------------------------------------------------------------------------
# Correlation join basis parsing
# ---------------------------------------------------------------------------

_VALID_GRAINS = frozenset({"hour", "day", "week", "month"})
_VALID_JOIN_BASIS_KINDS = frozenset({"time_aligned", "shared_key"})


def parse_correlation_join_basis(raw: Any) -> dict[str, Any] | None:
    """Try to parse *raw* as a structured ``CorrelationJoinBasis``.

    v1 rule: If the join_basis cannot be parsed as a structured
    CorrelationJoinBasis, the creation condition is False.

    A structured join_basis must be a dict with ``kind`` in
    ``{"time_aligned", "shared_key"}``.  A plain string returns ``None``.
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind not in _VALID_JOIN_BASIS_KINDS:
        return None
    key_fields = list(raw.get("key_fields") or [])
    if kind == "time_aligned":
        grain = raw.get("grain")
        if grain not in _VALID_GRAINS:
            return None
        return {"kind": "time_aligned", "grain": grain, "key_fields": key_fields}
    if kind == "shared_key":
        grain = raw.get("grain")
        if grain is not None and grain not in _VALID_GRAINS:
            return None
        return {"kind": "shared_key", "key_fields": key_fields, "grain": grain}
    return None


# ---------------------------------------------------------------------------
# Canonical subject key and bilateral focus anchor
# ---------------------------------------------------------------------------


def canonical_subject_key(subject: dict[str, Any]) -> str:
    """Stable sort key for a proposition subject dict.

    Used by T4/T5 bilateral focus-anchor algorithm.
    """
    return json.dumps(
        {
            "entity": subject.get("entity"),
            "grain": subject.get("grain"),
            "metric": subject.get("metric"),
            "slice": subject.get("slice", {}),
        },
        sort_keys=True,
    )


def bilateral_focus_anchor(
    left_subject: dict[str, Any],
    right_subject: dict[str, Any],
    analysis_axis: str,
) -> dict[str, Any]:
    """Derive the base subject for a bilateral proposition (T4/T5).

    Rule: take the lexically smaller subject key; on equal, take left.
    Set ``analysis_axis`` to the provided value.
    """
    base = (
        left_subject
        if canonical_subject_key(left_subject) <= canonical_subject_key(right_subject)
        else right_subject
    )
    return {**base, "analysis_axis": analysis_axis}


# ---------------------------------------------------------------------------
# Pure template materialization functions (T1-T6)
#
# These take pre-loaded data (finding dicts, artifact payload dicts) instead
# of a MaterializationContext.  The I/O-bound context lookups are handled by
# the caller before invoking these functions.
# ---------------------------------------------------------------------------


def materialize_change_from_delta(
    *,
    finding: dict[str, Any],
    session_id: str,
    template: dict[str, Any],
    artifact_payload: dict[str, Any],
) -> dict[str, Any] | None:
    """T1: delta finding -> change proposition (pure computation).

    Returns None when the creation condition fails.

    Parameters
    ----------
    finding:
        Deserialized finding row.  Must include ``payload_json`` and
        ``subject_json`` keys.
    session_id:
        Session scope for seed finding refs.
    template:
        Seed template spec dict with at least ``template_id``,
        ``template_version``, ``assessment_type``, ``derivation_version``.
    artifact_payload:
        Pre-loaded compare artifact content (the caller handles I/O).
    """
    payload = finding["payload_json"]
    direction: str = payload.get("direction") or "undefined"
    presence: str | None = payload.get("presence")
    delta_kind: str = payload.get("delta_kind") or ""

    # Creation condition: direction check
    if direction == "flat":
        return None
    if direction == "undefined" and presence not in ("current_only", "baseline_only"):
        return None

    # direction_of_interest mapping
    if direction == "increase":
        direction_of_interest = "increase"
    elif direction == "decrease":
        direction_of_interest = "decrease"
    else:
        direction_of_interest = "any_non_flat"

    # change_kind mapping — delta_kind is now derived from shape instead of
    # comparison_type; panel_delta maps to panel_change.
    change_kind_map = {
        "scalar_delta": "scalar_change",
        "segmented_delta": "segment_change",
        "time_series_delta": None,  # no proposition for time-series bucket deltas
        "panel_delta": "panel_change",
    }
    change_kind = change_kind_map.get(delta_kind)
    if change_kind is None:
        return None

    # comparison_window from artifact payload
    resolved = artifact_payload.get("resolved_input_summary") or {}
    current_time_scope = resolved.get("current_time_scope")
    baseline_time_scope = resolved.get("baseline_time_scope")
    if not current_time_scope or not baseline_time_scope:
        return None
    comparison_window = {"current": current_time_scope, "baseline": baseline_time_scope}

    comparison_basis = artifact_payload.get("comparison_basis") or "left_vs_right"

    # dimension_keys for segmented_delta / panel_delta
    dimension_keys: dict[str, Any] | None = None
    if delta_kind in ("segmented_delta", "panel_delta"):
        provenance = finding.get("provenance_json") or {}
        canonical_item_key: str = provenance.get("canonical_item_key") or ""
        dimension_keys = parse_segment_key(canonical_item_key)
        if dimension_keys is None:
            return None

    subject_base = finding["subject_json"]
    subject: dict[str, Any] = {**subject_base, "analysis_axis": "change"}

    prop_payload: dict[str, Any] = {
        "change_kind": change_kind,
        "comparison_window": comparison_window,
        "direction_of_interest": direction_of_interest,
        "comparison_basis": comparison_basis,
        "unit": payload.get("unit"),
        "dimension_keys": dimension_keys,
    }

    return _build_proposition_spec(
        proposition_type="change",
        subject=subject,
        template=template,
        finding=finding,
        session_id=session_id,
        payload=prop_payload,
    )


def materialize_anomaly_from_candidate(
    *,
    finding: dict[str, Any],
    session_id: str,
    template: dict[str, Any],
) -> dict[str, Any] | None:
    """T3: anomaly_candidate finding -> anomaly proposition (pure computation)."""
    payload = finding["payload_json"]
    candidate_ref = payload.get("candidate_ref")
    observed_window = finding.get("observed_window_json")

    if not candidate_ref or not candidate_ref.get("artifact_id"):
        return None
    if not observed_window:
        return None

    subject_base = finding["subject_json"]
    subject: dict[str, Any] = {**subject_base, "analysis_axis": "anomaly"}

    prop_payload: dict[str, Any] = {
        "anomaly_kind": "candidate",
        "candidate_ref": candidate_ref,
        "expected_behavior_ref": None,
        "observed_window": observed_window,
        "validation_goal": "validate_candidate",
    }

    return _build_proposition_spec(
        proposition_type="anomaly",
        subject=subject,
        template=template,
        finding=finding,
        session_id=session_id,
        payload=prop_payload,
    )


def materialize_forecast_from_point(
    *,
    finding: dict[str, Any],
    session_id: str,
    template: dict[str, Any],
) -> dict[str, Any] | None:
    """T6: forecast_point finding -> forecast proposition (pure computation)."""
    payload = finding["payload_json"]

    bucket_start: str = payload.get("bucket_start") or ""
    bucket_end: str = payload.get("bucket_end") or ""
    if not bucket_start or not bucket_end:
        return None

    horizon_index = payload.get("horizon_index")
    if horizon_index is None:
        return None
    try:
        horizon_int = int(horizon_index)
    except (TypeError, ValueError):
        return None
    if horizon_int < 0:
        return None

    prediction_interval = payload.get("prediction_interval")
    forecast_kind = "interval_forecast" if prediction_interval is not None else "point_forecast"

    subject_base = finding["subject_json"]
    subject: dict[str, Any] = {**subject_base, "analysis_axis": "forecast"}
    observed_window = finding.get("observed_window_json") or {}
    time_field = str(observed_window.get("field") or "time").strip() or "time"

    prop_payload: dict[str, Any] = {
        "forecast_kind": forecast_kind,
        "forecast_window": {"field": time_field, "start": bucket_start, "end": bucket_end},
        "horizon_index": horizon_int,
        "expectation_direction": "open",
        "forecast_basis_ref": {"session_id": session_id, "finding_id": finding["finding_id"]},
    }

    return _build_proposition_spec(
        proposition_type="forecast",
        subject=subject,
        template=template,
        finding=finding,
        session_id=session_id,
        payload=prop_payload,
    )


# ---------------------------------------------------------------------------
# Internal spec builder
# ---------------------------------------------------------------------------


def _build_proposition_spec(
    *,
    proposition_type: str,
    subject: dict[str, Any],
    template: dict[str, Any],
    finding: dict[str, Any],
    session_id: str,
    payload: dict[str, Any],
    extra_artifact_ids: list[str] | None = None,
    extra_step_refs: list[dict[str, Any]] | None = None,
    extra_seed_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a proposition spec dict from components."""

    # Origin
    origin = {
        "kind": "system_seeded",
        "template_id": template["template_id"],
        "template_version": template["template_version"],
    }

    # Lineage
    lineage = _build_lineage(
        finding,
        template,
        extra_artifact_ids=extra_artifact_ids,
        extra_step_refs=extra_step_refs,
    )

    # Seed finding refs
    seed_refs = [
        {
            "finding_ref": {"session_id": session_id, "finding_id": finding["finding_id"]},
            "role": "primary",
        }
    ]
    if extra_seed_refs:
        seed_refs.extend(extra_seed_refs)

    return {
        "proposition_type": proposition_type,
        "subject": subject,
        "assessment_anchor": {"assessment_type": template["assessment_type"]},
        "origin": origin,
        "lineage": lineage,
        "payload": payload,
        "seed_finding_refs": seed_refs,
    }


def _build_lineage(
    finding: dict[str, Any],
    template: dict[str, Any],
    extra_artifact_ids: list[str] | None = None,
    extra_step_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the proposition lineage from the trigger finding and template."""
    artifact_ids: list[str] = [finding["artifact_id"]]
    if extra_artifact_ids:
        for aid in extra_artifact_ids:
            if aid and aid not in artifact_ids:
                artifact_ids.append(aid)
    artifact_ids.sort()

    source_artifact_lineages = [
        {"artifact_id": aid, "artifact_schema_version": None, "extractor_version": None}
        for aid in artifact_ids
    ]

    step_ref = finding.get("step_ref_json")
    step_refs: list[dict[str, Any]] = [step_ref] if step_ref else []
    if extra_step_refs:
        seen_step_ids: set[str] = {s["step_id"] for s in step_refs if s.get("step_id")}
        for sr in extra_step_refs:
            if sr and sr.get("step_id") and sr["step_id"] not in seen_step_ids:
                step_refs.append(sr)
                seen_step_ids.add(sr["step_id"])
    step_refs.sort(key=lambda s: s.get("step_id", ""))

    return {
        "creation_mode": "seeded",
        "source_artifact_lineages": source_artifact_lineages,
        "source_step_refs": step_refs,
        "derived_from_proposition_ref": None,
        "derivation_version": template["derivation_version"],
    }
