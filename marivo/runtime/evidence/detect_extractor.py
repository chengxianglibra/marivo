"""candidate_set artifact -> anomaly_candidate finding extractor."""

from __future__ import annotations

from typing import Any

from marivo.core.evidence.canonical_finding import (
    AnomalyCandidateFinding,
    AnomalyCandidatePayload,
    ArtifactItemRefRef,
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingSubject,
    StepRef,
    make_finding_id,
    make_item_identity,
)
from marivo.runtime.evidence.finding_extractor_registry import FindingExtractor

_VALID_GRAINS = frozenset({"hour", "day", "week", "month"})
_VALID_DIRECTIONS = frozenset({"increase", "decrease", "unknown"})


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _escape_seg_component(s: str) -> str:
    return s.replace("%", "%25").replace("|", "%7C").replace("=", "%3D")


def _segment_stable_key(keys: dict[str, Any]) -> str:
    return "|".join(
        f"{_escape_seg_component(str(k))}={_escape_seg_component(str(v))}"
        for k, v in sorted(keys.items())
    )


def _extract_grain(artifact_payload: dict[str, Any]) -> str | None:
    axes = artifact_payload.get("axes")
    if not isinstance(axes, list):
        return None
    for axis in axes:
        if not isinstance(axis, dict) or axis.get("kind") != "time":
            continue
        grain = axis.get("grain")
        return grain if grain in _VALID_GRAINS else None
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


class DetectArtifactExtractor(FindingExtractor):
    """Extract anomaly_candidate findings from candidate_set artifacts."""

    artifact_type = "candidate_set"
    artifact_schema_version = None
    family = "detect"
    extractor_name = "detect_candidate_set_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        subject = artifact_payload.get("subject") or {}
        payload = artifact_payload.get("payload") or {}
        items: list[dict[str, Any]] = payload.get("items") or []
        metric: str | None = subject.get("metric_ref")
        grain = _extract_grain(artifact_payload)

        findings: list[AnomalyCandidateFinding] = []
        for i, item in enumerate(items):
            item_id = str(item.get("item_id") or "").strip()
            window = item.get("window") or {}
            window_start = str(window.get("start", "")).strip()
            window_end = str(window.get("end", "")).strip()
            keys = item.get("keys") if isinstance(item.get("keys"), dict) else None

            if item_id:
                canonical_item_key, item_ref = make_item_identity("candidates", key=item_id)
            elif window_start and keys:
                canonical_item_key, item_ref = make_item_identity(
                    "candidates", key=f"{window_start}|{_segment_stable_key(keys)}"
                )
            elif window_start:
                canonical_item_key, item_ref = make_item_identity("candidates", key=window_start)
            else:
                canonical_item_key, item_ref = make_item_identity("candidates", index=i)

            candidate_ref = ArtifactItemRefRef(
                artifact_id=artifact_id,
                item_ref=item_ref,
            )
            analysis_axis = (
                "panel" if keys and window_start else ("time" if window_start else "scalar")
            )
            subject_slice = dict(keys or {})
            observed_window = (
                {"field": "time", "start": window_start, "end": window_end}
                if window_start and window_end
                else None
            )
            direction_raw = item.get("direction")
            direction = direction_raw if direction_raw in _VALID_DIRECTIONS else None

            finding = AnomalyCandidateFinding(
                finding_id=make_finding_id(
                    artifact_id,
                    "anomaly_candidate",
                    canonical_item_key,
                ),
                finding_type="anomaly_candidate",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice=subject_slice,
                    grain=grain,  # type: ignore[typeddict-item]
                    analysis_axis=analysis_axis,  # type: ignore[typeddict-item]
                ),
                observed_window=observed_window,  # type: ignore[typeddict-item]
                quality=_empty_quality(),
                provenance=FindingProvenance(
                    source_step_type=step_ref["step_type"],
                    extractor_name=self.extractor_name,
                    extractor_version=self.extractor_version,
                    artifact_schema_version=self.artifact_schema_version,
                    canonical_item_key=canonical_item_key,
                    artifact_item_ref=item_ref,
                    projection_ref=None,
                ),
                payload=AnomalyCandidatePayload(
                    candidate_ref=candidate_ref,
                    source_point_ref=item.get("source_point_ref"),
                    source_delta_point_ref=item.get("source_delta_point_ref"),
                    score=_to_float_or_none(item.get("score")),
                    flag_level=None,
                    current_value=_to_float_or_none(item.get("value")),
                    baseline_value=_to_float_or_none(item.get("baseline_value")),
                    deviation_absolute=_to_float_or_none(item.get("delta_abs")),
                    deviation_relative=_to_float_or_none(item.get("delta_pct")),
                    direction=direction,
                ),
            )
            findings.append(finding)

        return FindingExtractionResult(
            findings=findings,  # type: ignore[typeddict-item]
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            finding_count=len(findings),
        )
