"""metric_frame artifact -> observation finding extractor."""

from __future__ import annotations

from typing import Any, cast

from marivo.core.evidence.canonical_finding import (
    AnyFinding,
    FindingExtractionResult,
    StepRef,
)
from marivo.core.evidence.finding_extraction import extract_observe_findings
from marivo.runtime.evidence.finding_extractor_registry import FindingExtractor


class ObserveArtifactExtractor(FindingExtractor):
    """Extract observation findings from observe ``metric_frame`` artifacts."""

    artifact_type = "metric_frame"
    artifact_schema_version = None
    family = "observe"
    extractor_name = "observe_metric_frame_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        findings = cast(
            "list[AnyFinding]",
            extract_observe_findings(
                artifact_id,
                artifact_payload,
                cast("dict[str, Any]", step_ref),
            ),
        )
        return FindingExtractionResult(
            findings=findings,
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            finding_count=len(findings),
        )
