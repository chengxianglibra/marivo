# DEPRECATED: Pure extraction logic extracted to app.core.evidence.finding_extraction.extract_correlate_findings.

"""pairwise_time_series_association artifact → correlation_result finding extractor (Phase 4d-4).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as 4d-1/4d-2/4d-3.

Artifact type: ``"pairwise_time_series_association"``   Schema version: ``"v1"``
Family: ``"correlate"``

D5 (approved): v1 is 1 artifact → 1 finding.

Empty semantics (D4):
---------------------
``correlate`` does NOT allow success-empty.  ``validate_for_commit("correlate", result)``
raises :class:`FamilyEmptyError` if ``finding_count == 0``.  The runner already
fails with ``INSUFFICIENT_DATA`` before writing an empty artifact, so this gate is
a belt-and-suspenders check at the commit boundary.

left_ref / right_ref artifact_id:
----------------------------------
The v1 ``pairwise_time_series_association`` artifact embeds the upstream observation
``artifact_id`` directly inside its ``left_ref`` / ``right_ref`` sub-objects
(set by the correlate runner via ``svc._resolve_artifact_id_for_step``).  Both refs
therefore carry the real artifact_id at extraction time.  The item_ref uses
``collection="result"`` as a stable whole-artifact ref (the upstream observation is
consumed as a whole series; no individual bucket is the canonical input boundary).
"""

from __future__ import annotations

from typing import Any

from marivo.evidence_engine.canonical_finding import (
    ArtifactItemRefRef,
    CorrelationResultFinding,
    CorrelationResultPayload,
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingSubject,
    StepRef,
    make_finding_id,
    make_item_identity,
)
from marivo.evidence_engine.finding_extractor_registry import FindingExtractor

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


def _to_int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
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


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class CorrelateArtifactExtractor(FindingExtractor):
    """Extract a single :class:`CorrelationResultFinding` from a
    ``pairwise_time_series_association`` artifact (D5: 1 artifact → 1 finding).
    """

    artifact_type = "pairwise_time_series_association"
    artifact_schema_version = "v1"
    family = "correlate"
    extractor_name = "correlate_artifact_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        canonical_item_key, item_ref = make_item_identity("result")
        finding_id = make_finding_id(artifact_id, "correlation_result", canonical_item_key)

        statistic: dict[str, Any] = artifact_payload.get("statistic") or {}
        analytical: dict[str, Any] = artifact_payload.get("analytical_metadata") or {}

        method_raw: str = str(statistic.get("method") or "")
        method = method_raw

        # left_ref / right_ref: the runner embeds artifact_id directly.
        left_src: dict[str, Any] = artifact_payload.get("left_ref") or {}
        right_src: dict[str, Any] = artifact_payload.get("right_ref") or {}

        _, left_obs_item_ref = make_item_identity("result")
        left_ref = ArtifactItemRefRef(
            artifact_id=str(left_src.get("artifact_id") or ""),
            item_ref=left_obs_item_ref,
        )
        _, right_obs_item_ref = make_item_identity("result")
        right_ref = ArtifactItemRefRef(
            artifact_id=str(right_src.get("artifact_id") or ""),
            item_ref=right_obs_item_ref,
        )

        # observed_window from matched_time_scope when available.
        matched_time_scope: dict[str, Any] | None = analytical.get("matched_time_scope")
        observed_window = (
            {
                "kind": "range",
                "start": matched_time_scope["start"],
                "end": matched_time_scope["end"],
            }
            if isinstance(matched_time_scope, dict)
            and matched_time_scope.get("start")
            and matched_time_scope.get("end")
            else None
        )

        provenance = FindingProvenance(
            source_step_type=step_ref["step_type"],
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            canonical_item_key=canonical_item_key,
            artifact_item_ref=item_ref,
            projection_ref=None,
        )

        finding = CorrelationResultFinding(
            finding_id=finding_id,
            finding_type="correlation_result",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject=FindingSubject(
                metric=artifact_payload.get("left_metric"),
                entity=None,
                slice={},
                grain=None,
                analysis_axis="correlation",
            ),
            observed_window=observed_window,  # type: ignore[typeddict-item]
            quality=_empty_quality(),
            provenance=provenance,
            payload=CorrelationResultPayload(
                left_ref=left_ref,
                right_ref=right_ref,
                method=method,  # type: ignore[typeddict-item]
                coefficient=_to_float_or_none(statistic.get("coefficient")),
                p_value=_to_float_or_none(statistic.get("p_value")),
                n=_to_int_or_none(statistic.get("n_pairs")),
                join_basis=analytical.get("pairing_rule"),
            ),
        )

        return FindingExtractionResult(
            findings=[finding],
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            finding_count=1,
        )
