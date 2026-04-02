"""hypothesis_test artifact → test_result finding extractor (Phase 4d-4).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as 4d-1/4d-2/4d-3.

Artifact type: ``"hypothesis_test"``   Schema version: ``"v1"``   Family: ``"test"``

D5 (approved): v1 is 1 artifact → 1 finding.

Empty semantics (D4):
---------------------
``test`` does NOT allow success-empty.  ``validate_for_commit("test", result)``
raises :class:`FamilyEmptyError` if ``finding_count == 0``.  The runner already
fails with validation errors before writing an empty artifact, so this gate is
a belt-and-suspenders check at the commit boundary.

subject.metric:
---------------
The ``hypothesis_test`` artifact does not embed the metric name directly in its
top-level payload; the metric is only reachable via the upstream observe artifacts.
``subject.metric`` is set to ``None`` in v1.  This is intentional — the metric
anchor will be added when the runner is updated to embed it in the artifact.

left_ref / right_ref artifact_id:
----------------------------------
The v1 runner embeds the upstream observation ``artifact_id`` inside
``left_ref`` / ``right_ref`` sub-objects.  Both refs carry the real artifact_id at
extraction time.  The item_ref uses ``collection="result"`` as a stable
whole-artifact ref (same approach as the correlate extractor).

observed_window:
----------------
``hypothesis_test`` artifacts carry no time scope at the top level; the test
operates on sample summaries, not on a time window.  ``observed_window`` is
always ``None`` in v1.
"""

from __future__ import annotations

from typing import Any

from app.evidence_engine.canonical_finding import (
    ArtifactItemRefRef,
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingSubject,
    StepRef,
    TestResultFinding,
    TestResultPayload,
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


def _to_bool_or_none(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
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


class TestArtifactExtractor(FindingExtractor):
    """Extract a single :class:`TestResultFinding` from a ``hypothesis_test``
    artifact (D5: 1 artifact → 1 finding).
    """

    artifact_type = "hypothesis_test"
    artifact_schema_version = "v1"
    family = "test"
    extractor_name = "test_artifact_v1"
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
        finding_id = make_finding_id(artifact_id, "test_result", canonical_item_key)

        statistic: dict[str, Any] = artifact_payload.get("statistic") or {}
        estimate: dict[str, Any] = artifact_payload.get("estimate") or {}
        hypothesis: dict[str, Any] = artifact_payload.get("hypothesis") or {}
        decision: dict[str, Any] = artifact_payload.get("decision") or {}

        method_raw: str = str(artifact_payload.get("method") or "")
        method = method_raw

        stat_name_raw: str = str(statistic.get("name") or "")
        stat_name = stat_name_raw

        alpha_raw = hypothesis.get("alpha")
        try:
            alpha = float(alpha_raw) if alpha_raw is not None else 0.05
        except (TypeError, ValueError):
            alpha = 0.05

        # left_ref / right_ref: the runner embeds artifact_id in the sub-objects.
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

        provenance = FindingProvenance(
            source_step_type=step_ref["step_type"],
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            canonical_item_key=canonical_item_key,
            artifact_item_ref=item_ref,
            projection_ref=None,
        )

        finding = TestResultFinding(
            finding_id=finding_id,
            finding_type="test_result",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject=FindingSubject(
                metric=None,
                entity=None,
                slice={},
                grain=None,
                analysis_axis="test",
            ),
            observed_window=None,
            quality=_empty_quality(),
            provenance=provenance,
            payload=TestResultPayload(
                left_ref=left_ref,
                right_ref=right_ref,
                method=method,  # type: ignore[typeddict-item]
                estimate_value=_to_float_or_none(estimate.get("value")),
                statistic_name=stat_name,  # type: ignore[typeddict-item]
                statistic_value=_to_float_or_none(statistic.get("value")),
                p_value=_to_float_or_none(artifact_payload.get("p_value")),
                reject_null=_to_bool_or_none(decision.get("reject_null")),
                alpha=alpha,
            ),
        )

        return FindingExtractionResult(
            findings=[finding],
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            finding_count=1,
        )
