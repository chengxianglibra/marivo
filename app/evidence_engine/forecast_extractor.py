"""forecast_series artifact → forecast_point finding extractor (Phase 4d-4).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as 4d-1/4d-2/4d-3.

Artifact type: ``"forecast_series"``   Schema version: ``"v1"``   Family: ``"forecast"``

Maps each bucket in the ``forecast`` list to one :class:`ForecastPointFinding`:

- N buckets → N findings (1 per future bucket)
- empty ``forecast`` list → 0 findings (D4 rejects this at commit boundary)

Canonical item key
------------------
Each bucket's stable key is ``"{bucket_start}/{bucket_end}"``.  This binds the
canonical item boundary to the time window of the forecast point, matching the
``forecast bucket boundary key`` rule in artifact-finding-generation-rules.md.

The ``horizon_index`` field (1-based, from ``bucket["bucket_index"]``) is stored in
the payload but does NOT enter the canonical item key or ``finding_id``; only the
bucket window boundary determines identity.

Empty semantics (D4):
---------------------
``forecast`` does NOT allow success-empty.  ``validate_for_commit("forecast", result)``
raises :class:`FamilyEmptyError` if ``finding_count == 0``.  The runner already
raises before writing a zero-bucket artifact (``horizon >= 1`` is enforced), so
this gate is a belt-and-suspenders check at the commit boundary.

observed_window:
----------------
Each forecast finding's ``observed_window`` is the future bucket's own time window
(``kind="range"``, ``start``, ``end``).  This documents *when* the predicted value
is expected to materialize, not when the source history was collected.
"""

from __future__ import annotations

from typing import Any

from app.evidence_engine.canonical_finding import (
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingSubject,
    ForecastPointFinding,
    ForecastPointPayload,
    PredictionInterval,
    StepRef,
    make_finding_id,
    make_item_identity,
)
from app.evidence_engine.finding_extractor_registry import FindingExtractor

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


def _build_prediction_interval(raw: Any) -> PredictionInterval | None:
    """Convert a raw prediction_interval dict to :class:`PredictionInterval`.

    Returns ``None`` when the input is not a dict (e.g. ``None`` for level-only
    forecasts that carry no interval).
    """
    if not isinstance(raw, dict):
        return None
    return PredictionInterval(
        lower=_to_float_or_none(raw.get("lower")),
        upper=_to_float_or_none(raw.get("upper")),
        level=_to_float_or_none(raw.get("level")),
    )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class ForecastArtifactExtractor(FindingExtractor):
    """Extract :class:`ForecastPointFinding`\\s from ``forecast_series`` artifacts.

    One finding per forecast bucket (D4: non-empty required).
    """

    artifact_type = "forecast_series"
    artifact_schema_version = "v1"
    family = "forecast"
    extractor_name = "forecast_artifact_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        metric: str | None = artifact_payload.get("metric") or None
        buckets: list[dict[str, Any]] = artifact_payload.get("forecast") or []

        findings: list[ForecastPointFinding] = []
        for i, bucket in enumerate(buckets):
            window: dict[str, Any] = bucket.get("window") or {}
            bucket_start: str = str(window.get("start") or "")
            bucket_end: str = str(window.get("end") or "")

            # Stable canonical key from the bucket boundary (D2 priority: stable key).
            stable_key = f"{bucket_start}/{bucket_end}"
            canonical_item_key, item_ref = make_item_identity("points", key=stable_key)
            finding_id = make_finding_id(artifact_id, "forecast_point", canonical_item_key)

            # horizon_index: prefer the stored 1-based bucket_index; fall back to i+1.
            horizon_index_raw = bucket.get("bucket_index")
            if horizon_index_raw is not None:
                try:
                    horizon_index = int(horizon_index_raw)
                except (TypeError, ValueError):
                    horizon_index = i + 1
            else:
                horizon_index = i + 1

            observed_window = (
                {"kind": "range", "start": bucket_start, "end": bucket_end}
                if bucket_start and bucket_end
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

            finding = ForecastPointFinding(
                finding_id=finding_id,
                finding_type="forecast_point",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice={},
                    grain=None,
                    analysis_axis="forecast",
                ),
                observed_window=observed_window,  # type: ignore[typeddict-item]
                quality=_empty_quality(),
                provenance=provenance,
                payload=ForecastPointPayload(
                    bucket_start=bucket_start,
                    bucket_end=bucket_end,
                    predicted_value=_to_float_or_none(bucket.get("point_forecast")),
                    prediction_interval=_build_prediction_interval(
                        bucket.get("prediction_interval")
                    ),
                    horizon_index=horizon_index,
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
