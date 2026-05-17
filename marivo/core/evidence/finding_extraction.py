"""Pure finding extraction functions for the evidence pipeline.

Extracted from ``marivo.evidence_engine.*_extractor`` modules as part of
Phase 3c.  Each ``extract_*`` function takes an artifact payload dict and
produces a list of finding dicts without any I/O side effects.

The original extractor *classes* (which implement ``FindingExtractor`` and
are registered in the ``FindingExtractorRegistry``) still live in
``marivo.evidence_engine`` and delegate to these pure functions.  This keeps
the registry / bootstrap wiring intact while making the core computation
testable and reusable without database access.

Shared helpers
--------------
- ``to_float_or_none`` — safe numeric coercion (duplicated across 6 extractors)
- ``escape_seg_component`` — percent-encode segment key separators
- ``segment_stable_key`` — deterministic ``k=v|k=v`` from dimension dict
- ``make_finding_id`` — stable, deterministic finding ID from SHA-256
- ``make_item_identity`` — co-generate canonical_item_key + ArtifactItemRef
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal, cast

# ---------------------------------------------------------------------------
# Shared type aliases (TypedDict-free for pure computation)
# ---------------------------------------------------------------------------

ArtifactItemRefCollection = Literal[
    "value", "rows", "buckets", "candidates", "points", "result", "summary"
]

# ---------------------------------------------------------------------------
# Identity helpers (from canonical_finding.py)
# ---------------------------------------------------------------------------

_FINDING_ID_PREFIX = "fnd_"
_FINDING_ID_HASH_LEN = 24


def make_canonical_item_key(
    collection: ArtifactItemRefCollection,
    key: str | None = None,
    index: int | None = None,
) -> str:
    """Build the canonical item key for a single artifact item.

    Priority rules (D2):
    1. If ``key`` is not None -> ``f"{collection}:{key}"``
    2. If ``index`` is not None -> ``f"{collection}:{index}"``
    3. Otherwise -> ``collection``
    """
    if key is not None:
        return f"{collection}:{key}"
    if index is not None:
        return f"{collection}:{index}"
    return collection


def make_finding_id(
    artifact_id: str,
    finding_type: str,
    canonical_item_key: str,
) -> str:
    """Generate a stable, deterministic finding_id.

    Formula: ``"fnd_" + sha256(f"{artifact_id}|{finding_type}|{canonical_item_key}")[:24]``
    """
    raw = f"{artifact_id}|{finding_type}|{canonical_item_key}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"{_FINDING_ID_PREFIX}{digest[:_FINDING_ID_HASH_LEN]}"


def make_artifact_item_ref(
    collection: ArtifactItemRefCollection,
    key: str | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """Build an ArtifactItemRef dict applying D2 priority rules."""
    if key is not None:
        return {"collection": collection, "index": None, "key": key}
    if index is not None:
        return {"collection": collection, "index": index, "key": None}
    return {"collection": collection, "index": None, "key": None}


def make_item_identity(
    collection: ArtifactItemRefCollection,
    key: str | None = None,
    index: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Co-generate canonical_item_key and ArtifactItemRef atomically."""
    return (
        make_canonical_item_key(collection, key=key, index=index),
        make_artifact_item_ref(collection, key=key, index=index),
    )


# ---------------------------------------------------------------------------
# Shared numeric / quality helpers
# ---------------------------------------------------------------------------


def to_float_or_none(v: Any) -> float | None:
    """Safely coerce *v* to float, returning None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_bool_or_none(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return None


def _time_scope_field(value: Any) -> str:
    if isinstance(value, dict):
        field = str(value.get("field") or "").strip()
        if field:
            return field
    return "time"


def _time_window(start: Any, end: Any, *, field: Any = None) -> dict[str, str]:
    return {
        "field": str(field or "time").strip() or "time",
        "start": str(start),
        "end": str(end),
    }


def _quality_from_am(am: dict[str, Any]) -> dict[str, Any]:
    """Build quality dict from analytical_metadata."""
    qs_raw = am.get("quality_status")
    _valid = frozenset({"ready", "needs_attention", "not_ready"})
    return {
        "data_complete": am.get("data_complete"),
        "sample_size": am.get("sample_size"),
        "row_count": am.get("row_count"),
        "null_rate": am.get("null_rate"),
        "quality_status": qs_raw if qs_raw in _valid else None,
        "quality_warnings": [],
    }


def _empty_quality() -> dict[str, Any]:
    return {
        "data_complete": None,
        "sample_size": None,
        "row_count": None,
        "null_rate": None,
        "quality_status": None,
        "quality_warnings": [],
    }


# ---------------------------------------------------------------------------
# Segment key helpers
# ---------------------------------------------------------------------------


def escape_seg_component(s: str) -> str:
    """Percent-encode characters that are structural separators in segment keys.

    Escaping order matters: ``%`` must be escaped first to avoid double-encoding.
    """
    return s.replace("%", "%25").replace("|", "%7C").replace("=", "%3D")


def _escape_decompose_component(s: str) -> str:
    """Percent-encode for decompose item key format ``dim:key``."""
    return s.replace("%", "%25").replace(":", "%3A")


def segment_stable_key(keys: dict[str, Any]) -> str:
    """Derive a stable segment key from a dimension key-value dict.

    Produces a deterministic ``k=v|k=v`` string from sorted dimension KV pairs,
    with each component percent-encoded.
    """
    return "|".join(
        f"{escape_seg_component(str(k))}={escape_seg_component(str(v))}"
        for k, v in sorted(keys.items())
    )


# ---------------------------------------------------------------------------
# Direction / presence validation
# ---------------------------------------------------------------------------

_VALID_DIRECTIONS = frozenset({"increase", "decrease", "flat", "undefined"})
_VALID_PRESENCES = frozenset({"both", "current_only", "baseline_only"})


def _validate_direction(raw: Any) -> str:
    raw_str = str(raw) if raw is not None else "undefined"
    return raw_str if raw_str in _VALID_DIRECTIONS else "undefined"


def _validate_presence(raw: Any) -> str | None:
    return raw if raw in _VALID_PRESENCES else None


# ---------------------------------------------------------------------------
# Comparability payload helpers
# ---------------------------------------------------------------------------


def extract_comparability_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract comparability and calendar_alignment dicts from an artifact payload.

    Pure helper shared by compare and test extractors.
    """
    comparability = payload.get("comparability")
    resolved = payload.get("resolved_input_summary") or {}
    source_lineage = payload.get("source_lineage") or {}
    calendar_alignment = resolved.get("calendar_alignment") or source_lineage.get(
        "calendar_alignment"
    )

    extracted: dict[str, Any] = {}
    if isinstance(comparability, dict):
        extracted["comparability"] = {
            "status": comparability.get("status") or "needs_attention",
            "issues": list(comparability.get("issues") or []),
        }
    if isinstance(calendar_alignment, dict):
        extracted["calendar_alignment"] = dict(calendar_alignment)
    return extracted


def _attach_comparability_payload(
    target: dict[str, Any],
    comparability_payload: dict[str, Any],
) -> dict[str, Any]:
    """Mutably attach comparability/calendar fields to a payload dict and return it."""
    comparability = comparability_payload.get("comparability")
    if comparability is not None:
        target["comparability"] = comparability
    calendar_alignment = comparability_payload.get("calendar_alignment")
    if calendar_alignment is not None:
        target["calendar_alignment"] = calendar_alignment
    return target


# ---------------------------------------------------------------------------
# Provenance builder
# ---------------------------------------------------------------------------


def _make_provenance(
    *,
    step_ref: dict[str, Any],
    extractor_name: str,
    extractor_version: str,
    artifact_schema_version: str,
    canonical_item_key: str,
    item_ref: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_step_type": step_ref["step_type"],
        "extractor_name": extractor_name,
        "extractor_version": extractor_version,
        "artifact_schema_version": artifact_schema_version,
        "canonical_item_key": canonical_item_key,
        "artifact_item_ref": item_ref,
        "projection_ref": None,
    }


# ---------------------------------------------------------------------------
# Finding construction helper
# ---------------------------------------------------------------------------


def _build_finding(
    *,
    finding_id: str,
    finding_type: str,
    artifact_id: str,
    step_ref: dict[str, Any],
    subject: dict[str, Any],
    observed_window: dict[str, Any] | None,
    quality: dict[str, Any],
    provenance: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Construct a canonical finding dict."""
    return {
        "finding_id": finding_id,
        "finding_type": finding_type,
        "artifact_id": artifact_id,
        "step_ref": step_ref,
        "subject": subject,
        "observed_window": observed_window,
        "quality": quality,
        "provenance": provenance,
        "payload": payload,
    }


# ===========================================================================
# OBSERVE extraction
# ===========================================================================


def extract_observe_findings(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract observation findings from an observation artifact payload.

    Pure computation: maps the ``observation_type`` variants to finding
    dicts.  Returns an empty list for empty time_series / segmented artifacts.
    """
    obs_type: str = payload.get("observation_type") or ""

    if obs_type == "scalar":
        return _extract_observe_scalar(artifact_id, payload, step_ref)
    elif obs_type == "time_series":
        return _extract_observe_time_series(artifact_id, payload, step_ref)
    elif obs_type == "segmented":
        return _extract_observe_segmented(artifact_id, payload, step_ref)
    else:
        raise ValueError(
            f"Unknown observation_type={obs_type!r}. "
            "Expected one of: scalar, time_series, segmented."
        )


def _extract_observe_scalar(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    canonical_item_key, item_ref = make_item_identity("value")
    finding_id = make_finding_id(artifact_id, "observation", canonical_item_key)
    am = payload.get("analytical_metadata") or {}

    return [
        _build_finding(
            finding_id=finding_id,
            finding_type="observation",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject={
                "metric": payload.get("metric"),
                "entity": None,
                "slice": payload.get("scope") or {},
                "grain": None,
                "analysis_axis": "scalar",
            },
            observed_window=payload.get("time_scope"),
            quality=_quality_from_am(am),
            provenance=_make_provenance(
                step_ref=step_ref,
                extractor_name="observe_artifact_v1",
                extractor_version="1.0.0",
                artifact_schema_version="v1",
                canonical_item_key=canonical_item_key,
                item_ref=item_ref,
            ),
            payload={
                "observation_kind": "scalar",
                "value": to_float_or_none(payload.get("value")),
                "unit": payload.get("unit"),
            },
        )
    ]


def _extract_observe_time_series(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = payload.get("series") or []
    if not series:
        return []

    grain_raw = payload.get("granularity")
    grain = grain_raw if grain_raw in {"hour", "day", "week", "month"} else None
    unit = payload.get("unit")
    metric = payload.get("metric")
    scope = payload.get("scope") or {}
    am = payload.get("analytical_metadata") or {}
    quality = _quality_from_am(am)
    time_field = _time_scope_field(payload.get("time_scope"))

    findings: list[dict[str, Any]] = []
    for bucket in series:
        window = bucket.get("window") or {}
        bucket_start: str = str(window.get("start", ""))
        bucket_end: str = str(window.get("end", ""))
        stable_key = f"{bucket_start}/{bucket_end}"

        canonical_item_key, item_ref = make_item_identity("buckets", key=stable_key)
        finding_id = make_finding_id(artifact_id, "observation", canonical_item_key)

        findings.append(
            _build_finding(
                finding_id=finding_id,
                finding_type="observation",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject={
                    "metric": metric,
                    "entity": None,
                    "slice": scope,
                    "grain": grain,
                    "analysis_axis": "time",
                },
                observed_window=_time_window(bucket_start, bucket_end, field=time_field),
                quality=quality,
                provenance=_make_provenance(
                    step_ref=step_ref,
                    extractor_name="observe_artifact_v1",
                    extractor_version="1.0.0",
                    artifact_schema_version="v1",
                    canonical_item_key=canonical_item_key,
                    item_ref=item_ref,
                ),
                payload={
                    "observation_kind": "time_bucket",
                    "bucket_start": bucket_start,
                    "bucket_end": bucket_end,
                    "value": to_float_or_none(bucket.get("value")),
                    "unit": unit,
                },
            )
        )
    return findings


def _extract_observe_segmented(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = payload.get("segments") or []
    if not segments:
        return []

    unit = payload.get("unit")
    metric = payload.get("metric")
    time_scope = payload.get("time_scope")

    findings: list[dict[str, Any]] = []
    for seg in segments:
        keys: dict[str, Any] = seg.get("keys") or {}
        stable_key = segment_stable_key(keys)

        canonical_item_key, item_ref = make_item_identity("rows", key=stable_key)
        finding_id = make_finding_id(artifact_id, "observation", canonical_item_key)

        findings.append(
            _build_finding(
                finding_id=finding_id,
                finding_type="observation",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject={
                    "metric": metric,
                    "entity": None,
                    "slice": dict(keys),
                    "grain": None,
                    "analysis_axis": "segment",
                },
                observed_window=time_scope,
                quality=_empty_quality(),
                provenance=_make_provenance(
                    step_ref=step_ref,
                    extractor_name="observe_artifact_v1",
                    extractor_version="1.0.0",
                    artifact_schema_version="v1",
                    canonical_item_key=canonical_item_key,
                    item_ref=item_ref,
                ),
                payload={
                    "observation_kind": "segment",
                    "keys": dict(keys),
                    "value": to_float_or_none(seg.get("value")),
                    "unit": unit,
                    "rank": None,
                },
            )
        )
    return findings


# ===========================================================================
# COMPARE extraction
# ===========================================================================


def extract_compare_findings(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract delta findings from a compare artifact payload.

    Pure computation: maps ``comparison_type`` variants to finding dicts.
    """
    comparison_type: str = payload.get("comparison_type") or ""

    if comparison_type == "scalar_delta":
        return _extract_compare_scalar_delta(artifact_id, payload, step_ref)
    elif comparison_type == "segmented_delta":
        return _extract_compare_segmented_delta(artifact_id, payload, step_ref)
    elif comparison_type == "time_series_delta":
        return _extract_compare_time_series_delta(artifact_id, payload, step_ref)
    else:
        raise ValueError(
            f"Unknown comparison_type={comparison_type!r}. "
            "Expected one of: scalar_delta, segmented_delta, time_series_delta."
        )


def _extract_compare_scalar_delta(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    canonical_item_key, item_ref = make_item_identity("result")
    finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

    resolved = payload.get("resolved_input_summary") or {}
    current_scope: dict[str, Any] = resolved.get("current_scope") or {}
    current_time_scope = resolved.get("current_time_scope")

    direction = _validate_direction(payload.get("direction"))

    _, obs_item_ref = make_item_identity("value")
    current_ref = {"artifact_id": "", "item_ref": obs_item_ref}
    baseline_ref = {"artifact_id": "", "item_ref": obs_item_ref}
    comp_payload = extract_comparability_payload(payload)

    delta_payload = _attach_comparability_payload(
        {
            "delta_kind": "scalar_delta",
            "current_ref": current_ref,
            "baseline_ref": baseline_ref,
            "current_value": to_float_or_none(payload.get("current_value")),
            "baseline_value": to_float_or_none(payload.get("baseline_value")),
            "absolute_delta": to_float_or_none(payload.get("absolute_delta")),
            "relative_delta": to_float_or_none(payload.get("relative_delta")),
            "direction": direction,
            "presence": "both",
            "unit": payload.get("unit"),
        },
        comp_payload,
    )

    return [
        _build_finding(
            finding_id=finding_id,
            finding_type="delta",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject={
                "metric": payload.get("metric"),
                "entity": None,
                "slice": current_scope,
                "grain": None,
                "analysis_axis": "scalar",
            },
            observed_window=current_time_scope,
            quality=_empty_quality(),
            provenance=_make_provenance(
                step_ref=step_ref,
                extractor_name="compare_artifact_v1",
                extractor_version="1.0.0",
                artifact_schema_version="v1",
                canonical_item_key=canonical_item_key,
                item_ref=item_ref,
            ),
            payload=delta_payload,
        )
    ]


def _extract_compare_segmented_delta(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = payload.get("rows") or []
    metric: str | None = payload.get("metric")
    unit: str | None = payload.get("unit")
    resolved = payload.get("resolved_input_summary") or {}
    current_time_scope = resolved.get("current_time_scope")
    comp_payload = extract_comparability_payload(payload)

    findings: list[dict[str, Any]] = []
    for row in rows:
        keys: dict[str, Any] = row.get("keys") or {}
        stable_key = segment_stable_key(keys)

        canonical_item_key, item_ref = make_item_identity("rows", key=stable_key)
        finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

        direction = _validate_direction(row.get("direction"))
        presence = _validate_presence(row.get("presence"))

        _, current_item_ref = make_item_identity("rows", key=stable_key)
        _, baseline_item_ref = make_item_identity("rows", key=stable_key)

        delta_payload = _attach_comparability_payload(
            {
                "delta_kind": "segmented_delta",
                "current_ref": {"artifact_id": "", "item_ref": current_item_ref},
                "baseline_ref": {"artifact_id": "", "item_ref": baseline_item_ref},
                "current_value": to_float_or_none(row.get("current_value")),
                "baseline_value": to_float_or_none(row.get("baseline_value")),
                "absolute_delta": to_float_or_none(row.get("absolute_delta")),
                "relative_delta": to_float_or_none(row.get("relative_delta")),
                "direction": direction,
                "presence": presence,
                "unit": unit,
            },
            comp_payload,
        )

        findings.append(
            _build_finding(
                finding_id=finding_id,
                finding_type="delta",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject={
                    "metric": metric,
                    "entity": None,
                    "slice": dict(keys),
                    "grain": None,
                    "analysis_axis": "segment",
                },
                observed_window=current_time_scope,
                quality=_empty_quality(),
                provenance=_make_provenance(
                    step_ref=step_ref,
                    extractor_name="compare_artifact_v1",
                    extractor_version="1.0.0",
                    artifact_schema_version="v1",
                    canonical_item_key=canonical_item_key,
                    item_ref=item_ref,
                ),
                payload=delta_payload,
            )
        )
    return findings


def _extract_compare_time_series_delta(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = payload.get("rows") or []
    metric: str | None = payload.get("metric")
    unit: str | None = payload.get("unit")
    granularity: str | None = payload.get("granularity")
    comp_payload = extract_comparability_payload(payload)

    findings: list[dict[str, Any]] = []

    # Summary delta
    has_summary = any(
        key in payload
        for key in (
            "summary_current_value",
            "summary_baseline_value",
            "summary_absolute_delta",
            "summary_relative_delta",
            "summary_direction",
        )
    )
    if has_summary:
        summary_key, summary_item_ref = make_item_identity("summary")
        summary_finding_id = make_finding_id(artifact_id, "delta", summary_key)
        summary_direction = _validate_direction(payload.get("summary_direction"))

        summary_payload = _attach_comparability_payload(
            {
                "delta_kind": "time_series_delta",
                "current_ref": {"artifact_id": "", "item_ref": summary_item_ref},
                "baseline_ref": {"artifact_id": "", "item_ref": summary_item_ref},
                "current_value": to_float_or_none(payload.get("summary_current_value")),
                "baseline_value": to_float_or_none(payload.get("summary_baseline_value")),
                "absolute_delta": to_float_or_none(payload.get("summary_absolute_delta")),
                "relative_delta": to_float_or_none(payload.get("summary_relative_delta")),
                "direction": summary_direction,
                "presence": None,
                "unit": unit,
            },
            comp_payload,
        )

        analytical = payload.get("analytical_metadata") or {}
        matched_time_scope = analytical.get("matched_time_scope")
        observed_window: dict[str, str] | None = (
            _time_window(
                matched_time_scope["start"],
                matched_time_scope["end"],
                field=matched_time_scope.get("field"),
            )
            if isinstance(matched_time_scope, dict)
            and matched_time_scope.get("start")
            and matched_time_scope.get("end")
            else None
        )
        findings.append(
            _build_finding(
                finding_id=summary_finding_id,
                finding_type="delta",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject={
                    "metric": metric,
                    "entity": None,
                    "slice": {},
                    "grain": granularity,
                    "analysis_axis": "time",
                },
                observed_window=observed_window,
                quality=_empty_quality(),
                provenance=_make_provenance(
                    step_ref=step_ref,
                    extractor_name="compare_artifact_v1",
                    extractor_version="1.0.0",
                    artifact_schema_version="v1",
                    canonical_item_key=summary_key,
                    item_ref=summary_item_ref,
                ),
                payload=summary_payload,
            )
        )

    # Bucket-level deltas
    for row in rows:
        window = row.get("window") or {}
        bucket_start = str(window.get("start") or "")
        bucket_end = str(window.get("end") or bucket_start)
        if not bucket_start:
            raise ValueError("time_series_delta row is missing window.start")

        canonical_item_key, item_ref = make_item_identity("buckets", key=bucket_start)
        finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

        direction = _validate_direction(row.get("direction"))
        presence = _validate_presence(row.get("presence"))

        _, bucket_item_ref = make_item_identity("buckets", key=bucket_start)

        delta_payload = _attach_comparability_payload(
            {
                "delta_kind": "time_series_delta",
                "current_ref": {"artifact_id": "", "item_ref": bucket_item_ref},
                "baseline_ref": {"artifact_id": "", "item_ref": bucket_item_ref},
                "current_value": to_float_or_none(row.get("current_value")),
                "baseline_value": to_float_or_none(row.get("baseline_value")),
                "absolute_delta": to_float_or_none(row.get("absolute_delta")),
                "relative_delta": to_float_or_none(row.get("relative_delta")),
                "direction": direction,
                "presence": presence,
                "unit": unit,
            },
            comp_payload,
        )

        findings.append(
            _build_finding(
                finding_id=finding_id,
                finding_type="delta",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject={
                    "metric": metric,
                    "entity": None,
                    "slice": {},
                    "grain": granularity,
                    "analysis_axis": "time",
                },
                observed_window=_time_window(
                    bucket_start,
                    bucket_end,
                    field=(payload.get("resolved_input_summary") or {})
                    .get("current_time_scope", {})
                    .get("field"),
                ),
                quality=_empty_quality(),
                provenance=_make_provenance(
                    step_ref=step_ref,
                    extractor_name="compare_artifact_v1",
                    extractor_version="1.0.0",
                    artifact_schema_version="v1",
                    canonical_item_key=canonical_item_key,
                    item_ref=item_ref,
                ),
                payload=delta_payload,
            )
        )
    return findings


# ===========================================================================
# DETECT extraction
# ===========================================================================

_VALID_FLAG_LEVELS = frozenset({"high", "medium", "low"})
_VALID_GRAINS = frozenset({"hour", "day", "week", "month"})


def _derive_analysis_axis(candidate: dict[str, Any]) -> str:
    """Choose analysis_axis from the candidate's shape."""
    window = candidate.get("window")
    if candidate.get("slice") is not None:
        return "segment"
    if isinstance(window, dict) and window:
        return "time"
    return "scalar"


def extract_detect_findings(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract anomaly_candidate findings from a detect artifact payload.

    Pure computation: maps each candidate item to a finding dict.
    Returns an empty list for an empty ``candidates`` list.
    """
    metric: str | None = payload.get("metric")
    scope: dict[str, Any] = payload.get("scope") or {}
    candidates: list[dict[str, Any]] = payload.get("candidates") or []

    grain_raw: str | None = payload.get("granularity")
    grain = grain_raw if grain_raw in _VALID_GRAINS else None

    findings: list[dict[str, Any]] = []
    for i, candidate in enumerate(candidates):
        window = candidate.get("window") or {}
        window_start: str = str(window.get("start", "")).strip()
        window_end: str = str(window.get("end", "")).strip()
        candidate_slice: dict[str, Any] | None = candidate.get("slice")

        analysis_axis = _derive_analysis_axis(candidate)

        # Stable canonical item key (D2 priority)
        if window_start and analysis_axis == "segment" and candidate_slice:
            stable_key = f"{window_start}|{segment_stable_key(candidate_slice)}"
            canonical_item_key, item_ref = make_item_identity("candidates", key=stable_key)
        elif window_start:
            canonical_item_key, item_ref = make_item_identity("candidates", key=window_start)
        elif analysis_axis == "segment" and candidate_slice:
            stable_key = segment_stable_key(candidate_slice)
            canonical_item_key, item_ref = make_item_identity("candidates", key=stable_key)
        else:
            canonical_item_key, item_ref = make_item_identity("candidates", index=i)

        finding_id = make_finding_id(artifact_id, "anomaly_candidate", canonical_item_key)

        candidate_ref = {"artifact_id": artifact_id, "item_ref": item_ref}

        flag_raw = candidate.get("flag_level")
        flag_level = flag_raw if flag_raw in _VALID_FLAG_LEVELS else None

        observed_window = (
            _time_window(
                window_start, window_end, field=(payload.get("time_scope") or {}).get("field")
            )
            if window_start and window_end
            else None
        )

        subject_slice: dict[str, Any] = (
            dict(candidate_slice) if analysis_axis == "segment" and candidate_slice else dict(scope)
        )

        findings.append(
            _build_finding(
                finding_id=finding_id,
                finding_type="anomaly_candidate",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject={
                    "metric": metric,
                    "entity": None,
                    "slice": subject_slice,
                    "grain": grain,
                    "analysis_axis": analysis_axis,
                },
                observed_window=observed_window,
                quality=_empty_quality(),
                provenance=_make_provenance(
                    step_ref=step_ref,
                    extractor_name="detect_artifact_v1",
                    extractor_version="1.0.0",
                    artifact_schema_version="v1",
                    canonical_item_key=canonical_item_key,
                    item_ref=item_ref,
                ),
                payload={
                    "candidate_ref": candidate_ref,
                    "score": to_float_or_none(candidate.get("candidate_score")),
                    "flag_level": flag_level,
                    "current_value": to_float_or_none(candidate.get("current_value")),
                    "baseline_value": to_float_or_none(candidate.get("baseline_value")),
                    "deviation_absolute": to_float_or_none(candidate.get("deviation_abs")),
                    "deviation_relative": to_float_or_none(candidate.get("deviation_pct")),
                },
            )
        )
    return findings


# ===========================================================================
# DECOMPOSE extraction
# ===========================================================================


def extract_decompose_findings(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
    session_id: str,
) -> list[dict[str, Any]]:
    """Extract decomposition_item findings from a decompose artifact payload.

    Pure computation with ``session_id`` needed only for scope_delta_ref
    (a cross-reference within the same session, no DB access required).
    """
    dimension: str = payload.get("dimension") or ""
    if not dimension:
        raise ValueError("Decompose artifact payload is missing required 'dimension' field.")

    # Resolve scope_delta_ref from compare_ref
    compare_ref: dict[str, Any] = payload.get("compare_ref") or {}
    compare_artifact_id: str = compare_ref.get("artifact_id") or ""
    if not compare_artifact_id:
        raise ValueError(
            "Decompose artifact: compare_ref.artifact_id is required to compute "
            "scope_delta_ref.finding_id but is absent or empty."
        )

    compare_type: str = compare_ref.get("comparison_type") or ""
    if compare_type == "time_series_delta":
        delta_collection = "summary"
    elif compare_type in ("", "scalar_delta"):
        delta_collection = "result"
    else:
        raise ValueError(
            f"Decompose artifact: compare_ref.comparison_type={compare_type!r} "
            "is not supported for scope_delta_ref derivation."
        )

    delta_canonical_key, _ = make_item_identity(cast("Any", delta_collection))
    delta_finding_id = make_finding_id(compare_artifact_id, "delta", delta_canonical_key)
    scope_delta_ref = {"session_id": session_id, "finding_id": delta_finding_id}

    metric: str | None = payload.get("metric")
    rows: list[dict[str, Any]] = payload.get("rows") or []

    findings: list[dict[str, Any]] = []
    for rank_0, row in enumerate(rows):
        key: Any = row.get("key")
        key_str: str = "" if key is None else str(key)
        key_typed: str | int | float | bool | None = (
            key if isinstance(key, (str, int, float, bool)) or key is None else str(key)
        )

        stable_key = (
            f"{_escape_decompose_component(dimension)}:{_escape_decompose_component(key_str)}"
        )
        canonical_item_key, item_ref = make_item_identity("rows", key=stable_key)
        finding_id = make_finding_id(artifact_id, "decomposition_item", canonical_item_key)

        direction = _validate_direction(row.get("direction"))

        findings.append(
            _build_finding(
                finding_id=finding_id,
                finding_type="decomposition_item",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject={
                    "metric": metric,
                    "entity": None,
                    "slice": {dimension: key_typed},
                    "grain": None,
                    "analysis_axis": "decomposition",
                },
                observed_window=None,
                quality=_empty_quality(),
                provenance=_make_provenance(
                    step_ref=step_ref,
                    extractor_name="decompose_artifact_v1",
                    extractor_version="1.0.0",
                    artifact_schema_version="v1",
                    canonical_item_key=canonical_item_key,
                    item_ref=item_ref,
                ),
                payload={
                    "dimension": dimension,
                    "keys": {dimension: key_typed},
                    "contribution_value": to_float_or_none(row.get("absolute_contribution")),
                    "contribution_share": to_float_or_none(row.get("contribution_share")),
                    "rank": rank_0 + 1,
                    "direction": direction,
                    "scope_delta_ref": scope_delta_ref,
                },
            )
        )
    return findings


# ===========================================================================
# CORRELATE extraction
# ===========================================================================


def extract_correlate_findings(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract a single correlation_result finding from a correlate artifact payload.

    Pure computation: 1 artifact -> 1 finding (D5).
    """
    canonical_item_key, item_ref = make_item_identity("result")
    finding_id = make_finding_id(artifact_id, "correlation_result", canonical_item_key)

    statistic: dict[str, Any] = payload.get("statistic") or {}
    analytical: dict[str, Any] = payload.get("analytical_metadata") or {}

    method_raw: str = str(statistic.get("method") or "")
    method = method_raw

    left_src: dict[str, Any] = payload.get("left_ref") or {}
    right_src: dict[str, Any] = payload.get("right_ref") or {}

    _, left_obs_item_ref = make_item_identity("result")
    left_ref = {
        "artifact_id": str(left_src.get("artifact_id") or ""),
        "item_ref": left_obs_item_ref,
    }
    _, right_obs_item_ref = make_item_identity("result")
    right_ref = {
        "artifact_id": str(right_src.get("artifact_id") or ""),
        "item_ref": right_obs_item_ref,
    }

    matched_time_scope: dict[str, Any] | None = analytical.get("matched_time_scope")
    observed_window = (
        _time_window(
            matched_time_scope["start"],
            matched_time_scope["end"],
            field=matched_time_scope.get("field"),
        )
        if isinstance(matched_time_scope, dict)
        and matched_time_scope.get("start")
        and matched_time_scope.get("end")
        else None
    )

    return [
        _build_finding(
            finding_id=finding_id,
            finding_type="correlation_result",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject={
                "metric": payload.get("left_metric"),
                "entity": None,
                "slice": {},
                "grain": None,
                "analysis_axis": "correlation",
            },
            observed_window=observed_window,
            quality=_empty_quality(),
            provenance=_make_provenance(
                step_ref=step_ref,
                extractor_name="correlate_artifact_v1",
                extractor_version="1.0.0",
                artifact_schema_version="v1",
                canonical_item_key=canonical_item_key,
                item_ref=item_ref,
            ),
            payload={
                "left_ref": left_ref,
                "right_ref": right_ref,
                "method": method,
                "coefficient": to_float_or_none(statistic.get("coefficient")),
                "p_value": to_float_or_none(statistic.get("p_value")),
                "n": _to_int_or_none(statistic.get("n_pairs")),
                "join_basis": analytical.get("pairing_rule"),
            },
        )
    ]


# ===========================================================================
# FORECAST extraction
# ===========================================================================


def _build_prediction_interval(raw: Any) -> dict[str, Any] | None:
    """Convert a raw prediction_interval dict to a structured dict."""
    if not isinstance(raw, dict):
        return None
    return {
        "lower": to_float_or_none(raw.get("lower")),
        "upper": to_float_or_none(raw.get("upper")),
        "level": to_float_or_none(raw.get("level")),
    }


def extract_forecast_findings(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract forecast_point findings from a forecast artifact payload.

    Pure computation: 1 finding per forecast bucket.
    """
    metric: str | None = payload.get("metric") or None
    buckets: list[dict[str, Any]] = payload.get("forecast") or []

    findings: list[dict[str, Any]] = []
    for i, bucket in enumerate(buckets):
        window: dict[str, Any] = bucket.get("window") or {}
        bucket_start: str = str(window.get("start") or "")
        bucket_end: str = str(window.get("end") or "")

        stable_key = f"{bucket_start}/{bucket_end}"
        canonical_item_key, item_ref = make_item_identity("points", key=stable_key)
        finding_id = make_finding_id(artifact_id, "forecast_point", canonical_item_key)

        horizon_index_raw = bucket.get("bucket_index")
        if horizon_index_raw is not None:
            try:
                horizon_index = int(horizon_index_raw)
            except (TypeError, ValueError):
                horizon_index = i + 1
        else:
            horizon_index = i + 1

        observed_window = (
            _time_window(
                bucket_start,
                bucket_end,
                field=(payload.get("source_time_scope") or {}).get("field"),
            )
            if bucket_start and bucket_end
            else None
        )

        findings.append(
            _build_finding(
                finding_id=finding_id,
                finding_type="forecast_point",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject={
                    "metric": metric,
                    "entity": None,
                    "slice": {},
                    "grain": None,
                    "analysis_axis": "forecast",
                },
                observed_window=observed_window,
                quality=_empty_quality(),
                provenance=_make_provenance(
                    step_ref=step_ref,
                    extractor_name="forecast_artifact_v1",
                    extractor_version="1.0.0",
                    artifact_schema_version="v1",
                    canonical_item_key=canonical_item_key,
                    item_ref=item_ref,
                ),
                payload={
                    "bucket_start": bucket_start,
                    "bucket_end": bucket_end,
                    "predicted_value": to_float_or_none(bucket.get("point_forecast")),
                    "prediction_interval": _build_prediction_interval(
                        bucket.get("prediction_interval")
                    ),
                    "horizon_index": horizon_index,
                },
            )
        )
    return findings


# ===========================================================================
# TEST extraction
# ===========================================================================


def extract_test_findings(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract a single test_result finding from a test artifact payload.

    Pure computation: 1 artifact -> 1 finding (D5).
    """
    canonical_item_key, item_ref = make_item_identity("result")
    finding_id = make_finding_id(artifact_id, "test_result", canonical_item_key)

    statistic: dict[str, Any] = payload.get("statistic") or {}
    estimate: dict[str, Any] = payload.get("estimate") or {}
    hypothesis: dict[str, Any] = payload.get("hypothesis") or {}
    decision: dict[str, Any] = payload.get("decision") or {}

    method = str(payload.get("method") or "")
    stat_name = str(statistic.get("name") or "")

    alpha_raw = hypothesis.get("alpha")
    try:
        alpha = float(alpha_raw) if alpha_raw is not None else 0.05
    except (TypeError, ValueError):
        alpha = 0.05

    current_src: dict[str, Any] = payload.get("current_ref") or {}
    baseline_src: dict[str, Any] = payload.get("baseline_ref") or {}

    _, current_obs_item_ref = make_item_identity("result")
    current_ref = {
        "artifact_id": str(current_src.get("artifact_id") or ""),
        "item_ref": current_obs_item_ref,
    }
    _, baseline_obs_item_ref = make_item_identity("result")
    baseline_ref = {
        "artifact_id": str(baseline_src.get("artifact_id") or ""),
        "item_ref": baseline_obs_item_ref,
    }

    comp_payload = extract_comparability_payload(payload)

    test_payload = _attach_comparability_payload(
        {
            "current_ref": current_ref,
            "baseline_ref": baseline_ref,
            "method": method,
            "estimate_value": to_float_or_none(estimate.get("value")),
            "statistic_name": stat_name,
            "statistic_value": to_float_or_none(statistic.get("value")),
            "p_value": to_float_or_none(payload.get("p_value")),
            "reject_null": _to_bool_or_none(decision.get("reject_null")),
            "alpha": alpha,
        },
        comp_payload,
    )

    return [
        _build_finding(
            finding_id=finding_id,
            finding_type="test_result",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject={
                "metric": None,
                "entity": None,
                "slice": {},
                "grain": None,
                "analysis_axis": "test",
            },
            observed_window=None,
            quality=_empty_quality(),
            provenance=_make_provenance(
                step_ref=step_ref,
                extractor_name="test_artifact_v1",
                extractor_version="1.0.0",
                artifact_schema_version="v1",
                canonical_item_key=canonical_item_key,
                item_ref=item_ref,
            ),
            payload=test_payload,
        )
    ]
