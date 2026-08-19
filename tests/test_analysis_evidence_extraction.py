"""All extractors return validated closed finding variants."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from marivo._compat import UTC
from marivo.analysis.errors import FindingExtractionFailedError
from marivo.analysis.evidence.extraction.anomaly import extract_anomaly_candidate_findings
from marivo.analysis.evidence.extraction.composition import (
    DecompositionExtractionContract,
    extract_decomposition_findings,
)
from marivo.analysis.evidence.extraction.correlation import extract_correlation_findings
from marivo.analysis.evidence.extraction.delta import extract_delta_findings
from marivo.analysis.evidence.extraction.forecast import extract_forecast_point_findings
from marivo.analysis.evidence.extraction.observation import (
    extract_metric_value_findings,
    extract_observation_digest_finding,
)
from marivo.analysis.evidence.extraction.test import extract_test_result_findings
from marivo.analysis.evidence.types import AnalysisScope, Subject
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from tests.shared_fixtures import make_test_analysis_scope, make_test_subject


def _now() -> datetime:
    return datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def test_observation_extracts_full_volume_values_and_bounded_aggregate() -> None:
    data = pd.DataFrame({"region": [f"r{index}" for index in range(7)], "value": range(7)})
    subject = make_test_subject(metric_id="revenue", analysis_axis="segment")
    values = extract_metric_value_findings(
        df=data,
        artifact_id="art_observe",
        session_id="sess_1",
        subject=subject,
        semantic_kind="segmented",
        measure_column="value",
        dimension_columns=["region"],
        committed_at=_now(),
        unit="USD",
    )
    aggregate = extract_observation_digest_finding(
        df=data,
        artifact_id="art_observe",
        session_id="sess_1",
        subject=subject,
        semantic_kind="segmented",
        measure_column="value",
        dimension_columns=["region"],
        committed_at=_now(),
        additive=True,
        unit="USD",
    )
    assert len(values) == 7
    assert values[0].value.kind == "metric_value"
    assert values[0].value.unit == "USD"
    assert aggregate.value.kind == "observation"
    assert aggregate.value.value.shape == "segmented"
    assert len(aggregate.value.value.top_segments) == 5
    assert aggregate.value.value.total_value == 21.0


def test_panel_observation_retains_additive_total_and_top_segments() -> None:
    finding = extract_observation_digest_finding(
        df=pd.DataFrame(
            {
                "day": ["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-02"],
                "region": ["us", "us", "eu", "eu"],
                "value": [4.0, 6.0, 1.0, 2.0],
            }
        ),
        artifact_id="art_panel",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="panel"),
        semantic_kind="panel",
        measure_column="value",
        time_column="day",
        dimension_columns=["region"],
        committed_at=_now(),
        additive=True,
    )
    value = finding.value.value
    assert value.shape == "panel"
    assert value.total_value == 13.0
    assert value.top_segments[0].keys == {"region": "us"}
    assert value.top_segments[0].value == 10.0


def test_time_series_uses_endpoint_direction_not_generic_trend() -> None:
    finding = extract_observation_digest_finding(
        df=pd.DataFrame({"bucket_start": ["2026-01-01", "2026-01-02"], "value": [1.0, 3.0]}),
        artifact_id="art_time",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="time"),
        semantic_kind="time_series",
        measure_column="value",
        time_column="bucket_start",
        committed_at=_now(),
    )
    value = finding.value.value
    assert value.shape == "time_series"
    assert value.endpoint_change_direction == "increase"
    assert not hasattr(value, "trend")


def test_time_series_partial_tail_bucket_suppresses_direction() -> None:
    finding = extract_observation_digest_finding(
        df=pd.DataFrame(
            {
                "bucket_start": ["2026-01-01", "2026-02-01"],
                "value": [10.0, 5.0],
                "has_full_data": [True, False],
            }
        ),
        artifact_id="art_partial_tail",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="time"),
        semantic_kind="time_series",
        measure_column="value",
        time_column="bucket_start",
        committed_at=_now(),
    )
    value = finding.value.value
    assert value.shape == "time_series"
    assert value.partial_tail_bucket is True
    assert value.endpoint_change_direction == "undefined"


def test_time_series_complete_tail_bucket_derives_direction() -> None:
    finding = extract_observation_digest_finding(
        df=pd.DataFrame(
            {
                "bucket_start": ["2026-01-01", "2026-02-01"],
                "value": [10.0, 5.0],
                "has_full_data": [True, True],
            }
        ),
        artifact_id="art_complete_tail",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="time"),
        semantic_kind="time_series",
        measure_column="value",
        time_column="bucket_start",
        committed_at=_now(),
    )
    value = finding.value.value
    assert value.shape == "time_series"
    assert value.partial_tail_bucket is False
    assert value.endpoint_change_direction == "decrease"


def test_delta_preserves_undefined_relative_delta_reason_and_unit() -> None:
    finding = extract_delta_findings(
        df=pd.DataFrame(
            {"current": [5.0], "baseline": [0.0], "delta": [5.0], "pct_change": [None]}
        ),
        artifact_id="art_delta",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="change"),
        semantic_kind="scalar",
        committed_at=_now(),
        unit="USD",
    )[0]
    assert finding.value.kind == "delta"
    assert finding.value.relative_delta is None
    assert finding.value.relative_delta_undefined_reason == "baseline_zero_or_missing"
    assert finding.value.unit == "USD"
    assert finding.epistemic_kind == "algebraic"


def test_time_series_delta_identity_uses_both_comparison_coordinates() -> None:
    findings = extract_delta_findings(
        df=pd.DataFrame(
            {
                "bucket_start_a": [pd.NaT, pd.NaT],
                "bucket_start_b": pd.to_datetime(["2025-06-01", "2025-06-02"]),
                "current": [None, None],
                "baseline": [4.0, 5.0],
                "delta": [None, None],
                "pct_change": [None, None],
            }
        ),
        artifact_id="art_delta_time",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="time"),
        semantic_kind="time_series",
        time_column="bucket_start_a",
        baseline_time_column="bucket_start_b",
        committed_at=_now(),
    )

    assert len({finding.canonical_item_key for finding in findings}) == 2
    assert all(
        "bucket=|baseline_bucket=2025-06-0" in finding.canonical_item_key for finding in findings
    )
    assert all(finding.value.bucket is None for finding in findings)
    assert all(
        finding.derivation.source_fields[:2] == ("bucket_start_a", "bucket_start_b")
        for finding in findings
    )


def test_time_series_delta_rejects_missing_declared_coordinate() -> None:
    with pytest.raises(ValueError, match="time_column 'bucket_start' is absent"):
        extract_delta_findings(
            df=pd.DataFrame(
                {
                    "bucket_start_a": pd.to_datetime(["2026-06-01"]),
                    "current": [5.0],
                    "baseline": [4.0],
                    "delta": [1.0],
                    "pct_change": [0.25],
                }
            ),
            artifact_id="art_delta_time",
            session_id="sess_1",
            subject=make_test_subject(metric_id="revenue", analysis_axis="time"),
            semantic_kind="time_series",
            time_column="bucket_start",
            committed_at=_now(),
        )


def test_panel_delta_identity_preserves_segment_and_paired_coordinates() -> None:
    findings = extract_delta_findings(
        df=pd.DataFrame(
            {
                "region": ["north", "north"],
                "bucket_start_a": [pd.NaT, pd.NaT],
                "bucket_start_b": pd.to_datetime(["2025-06-01", "2025-06-02"]),
                "current": [None, None],
                "baseline": [4.0, 5.0],
                "delta": [None, None],
                "pct_change": [None, None],
            }
        ),
        artifact_id="art_delta_panel",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="panel"),
        semantic_kind="panel",
        dimension_columns=["region"],
        time_column="bucket_start_a",
        baseline_time_column="bucket_start_b",
        committed_at=_now(),
    )

    assert len({finding.canonical_item_key for finding in findings}) == 2
    assert all(
        finding.canonical_item_key.startswith("rows:region=north|bucket=|") for finding in findings
    )


def test_contribution_has_rank_and_method_but_no_driver_role() -> None:
    findings = extract_decomposition_findings(
        df=pd.DataFrame(
            {
                "dimension": ["region", "region"],
                "region": ["us", "eu"],
                "contribution_value": [2.0, 8.0],
                "contribution_share": [0.2, 0.8],
            }
        ),
        artifact_id="art_attr",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="decomposition"),
        committed_at=_now(),
        scope_delta_ref="art_delta",
    )
    assert [finding.value.contribution_rank for finding in findings] == [1, 2]
    assert findings[0].value.contribution_value == 8.0
    assert findings[0].value.decomposition_method == "algebraic_decomposition"
    assert not hasattr(findings[0].value, "contribution_role")


def test_typed_contribution_findings_preserve_scope_local_rank() -> None:
    findings = extract_decomposition_findings(
        df=pd.DataFrame(
            {
                "bucket_start": ["a", "a", "b", "b"],
                "region": ["us", "eu", "us", "eu"],
                "contribution": [10.0, 2.0, 9.0, 1.0],
                "rank": [1, 2, 1, 2],
            }
        ),
        artifact_id="art_attr",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="decomposition"),
        committed_at=_now(),
        scope_delta_ref="art_delta",
        contract=DecompositionExtractionContract(
            dimension_name="region",
            key_columns=("bucket_start", "region"),
            contribution_column="contribution",
            contribution_share_column=None,
            direction="increase",
            decomposition_method="sum",
            reconciliation_residual=0.0,
            ordered_axis_refs=(
                RefPayloadV1.from_ref(ref_factory.dimension("sales.orders.region")),
            ),
        ),
    )

    assert [finding.value.contribution_rank for finding in findings] == [1, 1, 2, 2]


def test_association_keeps_missing_significance_explicit() -> None:
    finding = extract_correlation_findings(
        df=pd.DataFrame(
            {
                "left_ref": ["art_a"],
                "right_ref": ["art_b"],
                "method": ["pearson"],
                "coefficient": [0.7],
                "n": [20],
                "join_basis": ["window_bucket"],
            }
        ),
        artifact_id="art_assoc",
        session_id="sess_1",
        subject=Subject(analysis_axis="correlation"),
        committed_at=_now(),
    )[0]
    assert finding.value.kind == "correlation_result"
    assert finding.value.p_value is None
    assert finding.value.confidence_interval is None
    assert finding.epistemic_kind == "estimated"


def test_test_result_keeps_exact_predicate_and_decision() -> None:
    finding = extract_test_result_findings(
        df=pd.DataFrame(
            {
                "current_ref": ["art_a"],
                "baseline_ref": ["art_b"],
                "method": ["welch_t"],
                "statistic_value": [2.1],
                "p_value": [0.03],
                "reject_null": [True],
                "alpha": [0.05],
            }
        ),
        artifact_id="art_test",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="scalar"),
        committed_at=_now(),
        alternative="greater",
    )[0]
    assert finding.value.null_predicate == "current_minus_baseline_equals_zero"
    assert finding.value.alternative == "greater"
    assert finding.value.reject_null is True
    assert not hasattr(finding.value, "status")


def test_forecast_retains_model_scope_and_actual_absence() -> None:
    finding = extract_forecast_point_findings(
        df=pd.DataFrame(
            {
                "bucket_start": ["2026-08-01"],
                "bucket_end": ["2026-08-02"],
                "predicted_value": [12.0],
                "horizon_index": [1],
            }
        ),
        artifact_id="art_forecast",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="forecast"),
        committed_at=_now(),
        model="naive",
        training_scope=make_test_analysis_scope("revenue"),
    )[0]
    assert finding.value.model == "naive"
    assert finding.value.training_scope.metric_ids == ("sales.revenue",)
    assert finding.value.observed_actual is None
    assert finding.epistemic_kind == "predicted"


def test_anomaly_candidates_are_stably_ranked_and_remain_candidates() -> None:
    findings = extract_anomaly_candidate_findings(
        df=pd.DataFrame({"candidate_ref": ["low", "high"], "score": [0.2, 0.9]}),
        artifact_id="art_candidates",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="anomaly"),
        committed_at=_now(),
    )
    assert [finding.value.candidate_ref for finding in findings] == ["high", "low"]
    assert [finding.value.rank for finding in findings] == [1, 2]
    assert all(finding.epistemic_kind == "candidate" for finding in findings)


@pytest.mark.parametrize(
    "extractor",
    [
        lambda: extract_correlation_findings(
            df=pd.DataFrame(),
            artifact_id="art_empty",
            session_id="sess_1",
            subject=Subject(analysis_axis="correlation"),
            committed_at=_now(),
        ),
        lambda: extract_forecast_point_findings(
            df=pd.DataFrame(),
            artifact_id="art_empty",
            session_id="sess_1",
            subject=Subject(analysis_axis="forecast"),
            committed_at=_now(),
            model="naive",
            training_scope=AnalysisScope(),
        ),
        lambda: extract_test_result_findings(
            df=pd.DataFrame(),
            artifact_id="art_empty",
            session_id="sess_1",
            subject=Subject(analysis_axis="scalar"),
            committed_at=_now(),
        ),
    ],
)
def test_extractors_that_require_results_fail_on_empty(extractor) -> None:
    with pytest.raises(FindingExtractionFailedError):
        extractor()
