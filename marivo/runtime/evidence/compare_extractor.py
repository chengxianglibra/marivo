"""delta_frame artifact → delta finding extractor (Phase 4d-3).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as 4d-1/4d-2.

Artifact type: ``"delta_frame"``   Schema version: ``"v1"``   Family: ``"compare"``

Maps compare ``shape`` variants to :class:`DeltaFinding`:

- ``scalar_delta``    → 1 finding (:class:`DeltaPayload`, ``delta_kind="scalar_delta"``)
- ``segmented_delta`` → 1 finding per row (:class:`DeltaPayload`, ``delta_kind="segmented_delta"``)
- ``time_series_delta`` → 1 finding per bucket row
- ``panel_delta`` → 1 finding per series per dimension key combination

Dispatch uses ``shape`` from the delta_frame artifact contract.

Empty semantics (D4):
---------------------
``compare`` does NOT allow success-empty.  ``validate_for_commit("compare", result)``
raises :class:`FamilyEmptyError` if ``finding_count == 0``.  The runner already
fails with ``NOT_COMPARABLE`` before writing an empty segmented artifact, so this
gate is a belt-and-suspenders check at the commit boundary.

current_ref / baseline_ref artifact_id:
---------------------------------
The compare runner embeds upstream observation artifact IDs in its lineage refs.
The extractor copies those IDs into delta findings and never resolves session
state itself.
"""

from __future__ import annotations

from typing import Any, cast

from marivo.core.evidence.canonical_finding import (
    ArtifactItemRefRef,
    DeltaDirection,
    DeltaFinding,
    DeltaPayload,
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingSubject,
    StepRef,
    make_finding_id,
    make_item_identity,
)
from marivo.runtime.evidence.finding_extractor_registry import FindingExtractor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_DIRECTIONS = frozenset({"increase", "decrease", "flat", "undefined"})
_VALID_PRESENCES = frozenset({"both", "current_only", "baseline_only"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _empty_quality() -> FindingQuality:
    return FindingQuality(
        data_complete=None,
        sample_size=None,
        row_count=None,
        null_rate=None,
        quality_status=None,
        quality_warnings=[],
    )


def _source_artifact_id(payload: dict[str, Any], side: str) -> str:
    ref = payload.get(f"{side}_ref") or {}
    return str(ref.get("artifact_id") or "")


def _extract_comparability_payload(payload: dict[str, Any]) -> dict[str, Any]:
    comparability = payload.get("comparability")
    resolved = payload.get("resolved_input_summary") or {}
    calendar_alignment = resolved.get("calendar_alignment")

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
    delta_payload: DeltaPayload,
    comparability_payload: dict[str, Any],
) -> DeltaPayload:
    comparability = comparability_payload.get("comparability")
    if comparability is not None:
        delta_payload["comparability"] = comparability
    calendar_alignment = comparability_payload.get("calendar_alignment")
    if calendar_alignment is not None:
        delta_payload["calendar_alignment"] = calendar_alignment
    return delta_payload


def _delta_frame_payload_body(payload: dict[str, Any]) -> dict[str, Any]:
    payload_inner = payload.get("payload")
    if not isinstance(payload_inner, dict):
        raise ValueError("CompareArtifactExtractor: delta_frame payload must be an object")
    return payload_inner


def _delta_frame_series(payload: dict[str, Any]) -> list[dict[str, Any]]:
    series = _delta_frame_payload_body(payload).get("series")
    if not isinstance(series, list):
        raise ValueError("CompareArtifactExtractor: delta_frame payload.series must be a list")
    return series


def _delta_frame_scope(payload: dict[str, Any]) -> dict[str, Any]:
    scope = _delta_frame_payload_body(payload).get("scope")
    return scope if isinstance(scope, dict) else {}


def _escape_seg_component(s: str) -> str:
    """Percent-encode segment key separators to prevent key collisions.

    Mirrors the same encoding used in observe_extractor and detect_extractor:
    the stable segment key format is ``k=v|k=v``; values containing ``|`` or
    ``=`` must be escaped to avoid collisions between distinct segments.
    Escaping order matters: ``%`` must be escaped first to avoid double-encoding.
    """
    return s.replace("%", "%25").replace("|", "%7C").replace("=", "%3D")


def _segment_stable_key(keys: dict[str, Any]) -> str:
    """Derive a stable segment key from a dimension key-value dict.

    Produces a deterministic ``k=v|k=v`` string from sorted dimension KV pairs,
    with each component percent-encoded.  Mirrors observe_extractor behaviour so
    that delta findings for a given segment are stable under re-extraction.
    """
    return "|".join(
        f"{_escape_seg_component(str(k))}={_escape_seg_component(str(v))}"
        for k, v in sorted(keys.items())
    )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class CompareArtifactExtractor(FindingExtractor):
    """Extract :class:`DeltaFinding`\\s from ``delta_frame`` artifacts."""

    artifact_type = "delta_frame"
    artifact_schema_version = "v1"
    family = "compare"
    extractor_name = "delta_frame_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        shape: str = artifact_payload.get("shape") or ""

        if shape == "scalar_delta":
            findings = self._extract_scalar_delta(artifact_id, artifact_payload, step_ref)
        elif shape == "segmented_delta":
            findings = self._extract_segmented_delta(artifact_id, artifact_payload, step_ref)
        elif shape == "time_series_delta":
            findings = self._extract_time_series_delta(artifact_id, artifact_payload, step_ref)
        elif shape == "panel_delta":
            findings = self._extract_panel_delta(artifact_id, artifact_payload, step_ref)
        else:
            raise ValueError(
                f"CompareArtifactExtractor: unknown shape={shape!r}. "
                "Expected one of: scalar_delta, segmented_delta, time_series_delta, panel_delta."
            )

        return FindingExtractionResult(
            findings=findings,  # type: ignore[typeddict-item]
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            finding_count=len(findings),
        )

    # ------------------------------------------------------------------
    # Private per-mode helpers
    # ------------------------------------------------------------------

    def _make_provenance(
        self,
        step_ref: StepRef,
        canonical_item_key: str,
        item_ref: Any,
    ) -> FindingProvenance:
        return FindingProvenance(
            source_step_type=step_ref["step_type"],
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            canonical_item_key=canonical_item_key,
            artifact_item_ref=item_ref,
            projection_ref=None,
        )

    def _extract_scalar_delta(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[DeltaFinding]:
        canonical_item_key, item_ref = make_item_identity("result")
        finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

        resolved = payload.get("resolved_input_summary") or {}
        current_scope: dict[str, Any] = resolved.get("current_scope") or {}
        current_time_scope = resolved.get("current_time_scope")
        scope = _delta_frame_scope(payload)

        direction_raw = scope.get("direction") or "undefined"
        direction = cast(
            "DeltaDirection",
            direction_raw if direction_raw in _VALID_DIRECTIONS else "undefined",
        )

        _, obs_item_ref = make_item_identity("value")
        current_ref = ArtifactItemRefRef(
            artifact_id=_source_artifact_id(payload, "current"), item_ref=obs_item_ref
        )
        baseline_ref = ArtifactItemRefRef(
            artifact_id=_source_artifact_id(payload, "baseline"), item_ref=obs_item_ref
        )
        comparability_payload = _extract_comparability_payload(payload)

        delta_payload = DeltaPayload(
            delta_kind="scalar_delta",
            current_ref=current_ref,
            baseline_ref=baseline_ref,
            current_value=_to_float_or_none(scope.get("current_value")),
            baseline_value=_to_float_or_none(scope.get("baseline_value")),
            absolute_delta=_to_float_or_none(scope.get("delta_abs")),
            relative_delta=_to_float_or_none(scope.get("delta_pct")),
            direction=direction,
            presence="both",  # scalar_delta always compares two defined scopes
            unit=payload.get("unit"),
        )
        delta_payload = _attach_comparability_payload(delta_payload, comparability_payload)

        finding = DeltaFinding(
            finding_id=finding_id,
            finding_type="delta",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject=FindingSubject(
                metric=payload.get("metric"),
                entity=None,
                slice=current_scope,
                grain=None,
                analysis_axis="scalar",
            ),
            observed_window=current_time_scope,
            quality=_empty_quality(),
            provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
            payload=delta_payload,
        )
        return [finding]

    def _extract_segmented_delta(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[DeltaFinding]:
        series_entries = _delta_frame_series(payload)
        metric: str | None = payload.get("metric")
        unit: str | None = payload.get("unit")
        resolved = payload.get("resolved_input_summary") or {}
        current_time_scope = resolved.get("current_time_scope")
        comparability_payload = _extract_comparability_payload(payload)

        findings: list[DeltaFinding] = []
        for entry in series_entries:
            keys: dict[str, Any] = entry.get("keys") or {}
            stable_key = _segment_stable_key(keys)
            point = (entry.get("points") or [{}])[0]

            canonical_item_key, item_ref = make_item_identity("rows", key=stable_key)
            finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

            direction_raw = point.get("direction") or "undefined"
            direction = cast(
                "DeltaDirection",
                direction_raw if direction_raw in _VALID_DIRECTIONS else "undefined",
            )

            presence_raw = point.get("presence")
            presence = presence_raw if presence_raw in _VALID_PRESENCES else None

            _, left_item_ref = make_item_identity("rows", key=stable_key)
            _, right_item_ref = make_item_identity("rows", key=stable_key)
            current_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "current"), item_ref=left_item_ref
            )
            baseline_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "baseline"), item_ref=right_item_ref
            )

            delta_payload = DeltaPayload(
                delta_kind="segmented_delta",
                current_ref=current_ref,
                baseline_ref=baseline_ref,
                current_value=_to_float_or_none(point.get("current_value")),
                baseline_value=_to_float_or_none(point.get("baseline_value")),
                absolute_delta=_to_float_or_none(point.get("delta_abs")),
                relative_delta=_to_float_or_none(point.get("delta_pct")),
                direction=direction,
                presence=presence,
                unit=unit,
            )
            delta_payload = _attach_comparability_payload(delta_payload, comparability_payload)

            finding = DeltaFinding(
                finding_id=finding_id,
                finding_type="delta",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice=dict(keys),
                    grain=None,
                    analysis_axis="segment",
                ),
                observed_window=current_time_scope,
                quality=_empty_quality(),
                provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
                payload=delta_payload,
            )
            findings.append(finding)

        return findings

    def _extract_time_series_delta(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[DeltaFinding]:
        series_entries = _delta_frame_series(payload)
        rows: list[dict[str, Any]] = series_entries[0].get("points") or [] if series_entries else []
        metric: str | None = payload.get("metric")
        unit: str | None = payload.get("unit")
        # Read granularity from axes in v2.0 format
        axes: list[dict[str, str]] = payload.get("axes") or []
        granularity: str | None = None
        for a in axes:
            if a.get("kind") == "time":
                granularity = a.get("grain")
        comparability_payload = _extract_comparability_payload(payload)

        findings: list[DeltaFinding] = []
        scope = _delta_frame_scope(payload)
        has_summary_delta = bool(scope)
        if has_summary_delta:
            summary_key, summary_item_ref = make_item_identity("summary")
            summary_finding_id = make_finding_id(artifact_id, "delta", summary_key)
            summary_direction_raw = scope.get("direction") or "undefined"
            summary_direction = cast(
                "DeltaDirection",
                summary_direction_raw
                if summary_direction_raw in _VALID_DIRECTIONS
                else "undefined",
            )
            current_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "current"), item_ref=summary_item_ref
            )
            baseline_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "baseline"), item_ref=summary_item_ref
            )
            summary_payload = DeltaPayload(
                delta_kind="time_series_delta",
                current_ref=current_ref,
                baseline_ref=baseline_ref,
                current_value=_to_float_or_none(scope.get("current_value")),
                baseline_value=_to_float_or_none(scope.get("baseline_value")),
                absolute_delta=_to_float_or_none(scope.get("delta_abs")),
                relative_delta=_to_float_or_none(scope.get("delta_pct")),
                direction=summary_direction,
                presence=None,
                unit=unit,
            )
            summary_payload = _attach_comparability_payload(summary_payload, comparability_payload)
            analytical = payload.get("analytical_metadata") or {}
            matched_time_scope = analytical.get("matched_time_scope")
            observed_window: dict[str, str] | None = (
                {
                    "field": str(matched_time_scope.get("field") or "time").strip() or "time",
                    "start": str(matched_time_scope["start"]),
                    "end": str(matched_time_scope["end"]),
                }
                if isinstance(matched_time_scope, dict)
                and matched_time_scope.get("start")
                and matched_time_scope.get("end")
                else None
            )
            findings.append(
                DeltaFinding(
                    finding_id=summary_finding_id,
                    finding_type="delta",
                    artifact_id=artifact_id,
                    step_ref=step_ref,
                    subject=FindingSubject(
                        metric=metric,
                        entity=None,
                        slice={},
                        grain=cast("Any", granularity),
                        analysis_axis="time",
                    ),
                    observed_window=cast("Any", observed_window),
                    quality=_empty_quality(),
                    provenance=self._make_provenance(step_ref, summary_key, summary_item_ref),
                    payload=summary_payload,
                )
            )

        for row in rows:
            window = row.get("window") or {}
            bucket_start = str(window.get("start") or "")
            bucket_end = str(window.get("end") or bucket_start)
            if not bucket_start:
                raise ValueError(
                    "CompareArtifactExtractor: time_series_delta row is missing window.start"
                )

            canonical_item_key, item_ref = make_item_identity("buckets", key=bucket_start)
            finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

            direction_raw = row.get("direction") or "undefined"
            direction = cast(
                "DeltaDirection",
                direction_raw if direction_raw in _VALID_DIRECTIONS else "undefined",
            )

            presence_raw = row.get("presence")
            presence = presence_raw if presence_raw in _VALID_PRESENCES else None

            _, bucket_item_ref = make_item_identity("buckets", key=bucket_start)
            current_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "current"), item_ref=bucket_item_ref
            )
            baseline_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "baseline"), item_ref=bucket_item_ref
            )

            delta_payload = DeltaPayload(
                delta_kind="time_series_delta",
                current_ref=current_ref,
                baseline_ref=baseline_ref,
                current_value=_to_float_or_none(row.get("current_value")),
                baseline_value=_to_float_or_none(row.get("baseline_value")),
                absolute_delta=_to_float_or_none(row.get("delta_abs")),
                relative_delta=_to_float_or_none(row.get("delta_pct")),
                direction=direction,
                presence=presence,
                unit=unit,
            )
            delta_payload = _attach_comparability_payload(delta_payload, comparability_payload)

            finding = DeltaFinding(
                finding_id=finding_id,
                finding_type="delta",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice={},
                    grain=cast("Any", granularity),
                    analysis_axis="time",
                ),
                observed_window={
                    "field": str(
                        (
                            (payload.get("resolved_input_summary") or {}).get("current_time_scope")
                            or {}
                        ).get("field")
                        or "time"
                    ).strip()
                    or "time",
                    "start": bucket_start,
                    "end": bucket_end,
                },
                quality=_empty_quality(),
                provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
                payload=delta_payload,
            )
            findings.append(finding)

        return findings

    def _extract_panel_delta(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[DeltaFinding]:
        """Extract findings from panel_delta compare artifacts.

        panel_delta produces 1 finding per series (per dimension key combination),
        iterating over each time bucket within that series.  Each series-level
        finding aggregates all bucket points for that dimension key set.
        """
        series_entries = _delta_frame_series(payload)
        metric: str | None = payload.get("metric")
        unit: str | None = payload.get("unit")
        axes: list[dict[str, str]] = payload.get("axes") or []

        # Read granularity from time axis
        granularity: str | None = None
        for a in axes:
            if a.get("kind") == "time":
                granularity = a.get("grain")

        comparability_payload = _extract_comparability_payload(payload)

        findings: list[DeltaFinding] = []
        for entry in series_entries:
            keys: dict[str, Any] = entry.get("keys") or {}
            points: list[dict[str, Any]] = entry.get("points") or []
            if not points:
                continue

            # Use the first point's window as the canonical scope window
            first_point = points[0]
            first_window = first_point.get("window") or {}
            scope_start = str(first_window.get("start") or "")
            scope_end = str(first_window.get("end") or scope_start)

            stable_key = _segment_stable_key(keys)
            canonical_item_key, item_ref = make_item_identity("rows", key=stable_key)
            finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

            direction_raw = first_point.get("direction") or "undefined"
            direction = cast(
                "DeltaDirection",
                direction_raw if direction_raw in _VALID_DIRECTIONS else "undefined",
            )

            presence_raw = first_point.get("presence")
            presence = presence_raw if presence_raw in _VALID_PRESENCES else None

            _, series_item_ref = make_item_identity("rows", key=stable_key)
            current_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "current"), item_ref=series_item_ref
            )
            baseline_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "baseline"), item_ref=series_item_ref
            )

            delta_payload = DeltaPayload(
                delta_kind="panel_delta",
                current_ref=current_ref,
                baseline_ref=baseline_ref,
                current_value=_to_float_or_none(first_point.get("current_value")),
                baseline_value=_to_float_or_none(first_point.get("baseline_value")),
                absolute_delta=_to_float_or_none(first_point.get("delta_abs")),
                relative_delta=_to_float_or_none(first_point.get("delta_pct")),
                direction=direction,
                presence=presence,
                unit=unit,
            )
            delta_payload = _attach_comparability_payload(delta_payload, comparability_payload)

            finding = DeltaFinding(
                finding_id=finding_id,
                finding_type="delta",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice=dict(keys),
                    grain=cast("Any", granularity),
                    analysis_axis="panel",
                ),
                observed_window={
                    "field": str(
                        (
                            (payload.get("resolved_input_summary") or {}).get("current_time_scope")
                            or {}
                        ).get("field")
                        or "time"
                    ).strip()
                    or "time",
                    "start": scope_start,
                    "end": scope_end,
                },
                quality=_empty_quality(),
                provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
                payload=delta_payload,
            )
            findings.append(finding)

        scope = _delta_frame_scope(payload)
        has_summary = bool(scope)
        if has_summary:
            summary_key, summary_item_ref = make_item_identity("summary")
            summary_finding_id = make_finding_id(artifact_id, "delta", summary_key)
            summary_direction_raw = scope.get("direction") or "undefined"
            summary_direction = cast(
                "DeltaDirection",
                summary_direction_raw
                if summary_direction_raw in _VALID_DIRECTIONS
                else "undefined",
            )
            current_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "current"), item_ref=summary_item_ref
            )
            baseline_ref = ArtifactItemRefRef(
                artifact_id=_source_artifact_id(payload, "baseline"), item_ref=summary_item_ref
            )
            summary_payload = DeltaPayload(
                delta_kind="panel_delta",
                current_ref=current_ref,
                baseline_ref=baseline_ref,
                current_value=_to_float_or_none(scope.get("current_value")),
                baseline_value=_to_float_or_none(scope.get("baseline_value")),
                absolute_delta=_to_float_or_none(scope.get("delta_abs")),
                relative_delta=_to_float_or_none(scope.get("delta_pct")),
                direction=summary_direction,
                presence=None,
                unit=unit,
            )
            summary_payload = _attach_comparability_payload(summary_payload, comparability_payload)

            findings.append(
                DeltaFinding(
                    finding_id=summary_finding_id,
                    finding_type="delta",
                    artifact_id=artifact_id,
                    step_ref=step_ref,
                    subject=FindingSubject(
                        metric=metric,
                        entity=None,
                        slice={},
                        grain=cast("Any", granularity),
                        analysis_axis="panel",
                    ),
                    observed_window=cast("Any", None),
                    quality=_empty_quality(),
                    provenance=self._make_provenance(step_ref, summary_key, summary_item_ref),
                    payload=summary_payload,
                )
            )

        return findings
