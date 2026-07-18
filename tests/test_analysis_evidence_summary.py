"""Pure rendering for closed digest and issue values."""

from marivo.analysis.evidence.summary import render_artifact_issue
from marivo.analysis.evidence.types import (
    AnalysisScope,
    DataQualityIssue,
    EvidenceAvailabilityIssue,
    RawFallback,
)


def test_data_quality_issue_prose_is_derived_from_typed_fields():
    issue = DataQualityIssue(
        issue_id="iss_1",
        kind="null_rate_high",
        severity="blocking",
        source_refs=("art_1",),
        check_id="null_ratio:value",
        observed_value=0.75,
        expectation="null_ratio <= 0.5",
        evaluated_scope=AnalysisScope(metric_ids=("sales.revenue",)),
    )
    assert "observed=0.75" in render_artifact_issue(issue)
    assert "null_ratio <= 0.5" in render_artifact_issue(issue)
    payload = issue.model_dump()
    assert "message" not in payload
    assert "payload" not in payload


def test_evidence_issue_rendering_exposes_exact_failure_state():
    issue = EvidenceAvailabilityIssue(
        issue_id="iss_2",
        kind="evidence_digest_unavailable",
        severity="blocking",
        source_refs=("art_1",),
        failed_stage="digest",
        findings_available=True,
        fallback=RawFallback(
            artifact_ref="art_1",
            findings_available=True,
            rows_available=True,
        ),
        stable_error_category="DigestBuildError",
    )
    rendered = render_artifact_issue(issue)
    assert rendered == "stage=digest error=DigestBuildError findings_available=true"
