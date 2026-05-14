# DEPRECATED: Pure extraction logic extracted to app.core.evidence.finding_extraction.extract_observe_findings.

"""observe artifact → observation finding extractor (Phase 4d-1).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as
``registry.py:_bootstrap()``.

Artifact type: ``"observation"``   Schema version: ``"v1"``   Family: ``"observe"``

Maps the three ``observation_type`` variants to :class:`ObservationFinding`:

- ``scalar``               → 1 finding (:class:`ScalarObservationPayload`)
- ``time_series``          → 1 finding per bucket (:class:`TimeBucketObservationPayload`);
                             empty series → 0 findings (success-empty)
- ``segmented``            → 1 finding per segment (:class:`SegmentObservationPayload`);
                             empty segments → 0 findings (success-empty)
"""

from __future__ import annotations

from typing import Any

from marivo.core.evidence.canonical_finding import (
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingSubject,
    ObservationFinding,
    ScalarObservationPayload,
    SegmentObservationPayload,
    StepRef,
    TimeBucketObservationPayload,
    make_finding_id,
    make_item_identity,
)
from marivo.runtime.evidence.finding_extractor_registry import FindingExtractor

# ---------------------------------------------------------------------------
# Quality helpers
# ---------------------------------------------------------------------------

_VALID_QUALITY_STATUSES = frozenset({"ready", "needs_attention", "not_ready"})


def _quality_from_am(am: dict[str, Any]) -> FindingQuality:
    """Build FindingQuality from an ``analytical_metadata`` dict."""
    qs_raw = am.get("quality_status")
    return FindingQuality(
        data_complete=am.get("data_complete"),
        sample_size=am.get("sample_size"),
        row_count=am.get("row_count"),
        null_rate=am.get("null_rate"),
        quality_status=qs_raw if qs_raw in _VALID_QUALITY_STATUSES else None,
        quality_warnings=[],
    )


def _empty_quality() -> FindingQuality:
    """Return a per-item quality dict with all fields set to not-applicable."""
    return FindingQuality(
        data_complete=None,
        sample_size=None,
        row_count=None,
        null_rate=None,
        quality_status=None,
        quality_warnings=[],
    )


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _escape_seg_component(s: str) -> str:
    """Percent-encode characters that are structural separators in the segment key format.

    The segment stable key is ``k=v|k=v``.  Without escaping, a dimension
    value that contains ``|`` or ``=`` can produce the same key string as a
    different set of dimension key-value pairs, causing a ``finding_id``
    collision between distinct segments.

    Escaping order matters: ``%`` must be escaped first to avoid double-encoding.
    """
    return s.replace("%", "%25").replace("|", "%7C").replace("=", "%3D")


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class ObserveArtifactExtractor(FindingExtractor):
    """Extract :class:`ObservationFinding`\\s from ``observation`` artifacts."""

    artifact_type = "observation"
    artifact_schema_version = "v1"
    family = "observe"
    extractor_name = "observe_artifact_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        obs_type: str = artifact_payload.get("observation_type") or ""

        if obs_type == "scalar":
            findings = self._extract_scalar(artifact_id, artifact_payload, step_ref)
        elif obs_type == "time_series":
            findings = self._extract_time_series(artifact_id, artifact_payload, step_ref)
        elif obs_type == "segmented":
            findings = self._extract_segmented(artifact_id, artifact_payload, step_ref)
        else:
            raise ValueError(
                f"ObserveArtifactExtractor: unknown observation_type={obs_type!r}. "
                "Expected one of: scalar, time_series, segmented."
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

    def _extract_scalar(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[ObservationFinding]:
        canonical_item_key, item_ref = make_item_identity("value")
        finding_id = make_finding_id(artifact_id, "observation", canonical_item_key)
        am = payload.get("analytical_metadata") or {}

        finding = ObservationFinding(
            finding_id=finding_id,
            finding_type="observation",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject=FindingSubject(
                metric=payload.get("metric"),
                entity=None,
                slice=payload.get("scope") or {},
                grain=None,
                analysis_axis="scalar",
            ),
            observed_window=payload.get("time_scope"),
            quality=_quality_from_am(am),
            provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
            payload=ScalarObservationPayload(
                observation_kind="scalar",
                value=_to_float_or_none(payload.get("value")),
                unit=payload.get("unit"),
            ),
        )
        return [finding]

    def _extract_time_series(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[ObservationFinding]:
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

        findings: list[ObservationFinding] = []
        for bucket in series:
            window = bucket.get("window") or {}
            bucket_start: str = str(window.get("start", ""))
            bucket_end: str = str(window.get("end", ""))
            stable_key = f"{bucket_start}/{bucket_end}"

            canonical_item_key, item_ref = make_item_identity("buckets", key=stable_key)
            finding_id = make_finding_id(artifact_id, "observation", canonical_item_key)

            finding = ObservationFinding(
                finding_id=finding_id,
                finding_type="observation",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice=scope,
                    grain=grain,
                    analysis_axis="time",
                ),
                observed_window={"kind": "range", "start": bucket_start, "end": bucket_end},
                quality=quality,
                provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
                payload=TimeBucketObservationPayload(
                    observation_kind="time_bucket",
                    bucket_start=bucket_start,
                    bucket_end=bucket_end,
                    value=_to_float_or_none(bucket.get("value")),
                    unit=unit,
                ),
            )
            findings.append(finding)
        return findings

    def _extract_segmented(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[ObservationFinding]:
        segments: list[dict[str, Any]] = payload.get("segments") or []
        if not segments:
            return []

        unit = payload.get("unit")
        metric = payload.get("metric")
        time_scope = payload.get("time_scope")

        findings: list[ObservationFinding] = []
        for seg in segments:
            keys: dict[str, Any] = seg.get("keys") or {}
            # Stable normalized key from sorted dimension key-value pairs.
            # Components are percent-encoded so that values containing "|" or
            # "=" cannot produce the same key as a different segment.
            stable_key = "|".join(
                f"{_escape_seg_component(str(k))}={_escape_seg_component(str(v))}"
                for k, v in sorted(keys.items())
            )

            canonical_item_key, item_ref = make_item_identity("rows", key=stable_key)
            finding_id = make_finding_id(artifact_id, "observation", canonical_item_key)

            finding = ObservationFinding(
                finding_id=finding_id,
                finding_type="observation",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice=dict(keys),
                    grain=None,
                    analysis_axis="segment",
                ),
                observed_window=time_scope,
                quality=_empty_quality(),
                provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
                payload=SegmentObservationPayload(
                    observation_kind="segment",
                    keys=dict(keys),
                    value=_to_float_or_none(seg.get("value")),
                    unit=unit,
                    rank=None,
                ),
            )
            findings.append(finding)
        return findings
