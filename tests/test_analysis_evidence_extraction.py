"""Finding extractors: metric_value, delta, anomaly, composition, correlation, forecast, test."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis.errors import FindingExtractionFailedError
from marivo.analysis.evidence.extraction.anomaly import extract_anomaly_candidate_findings
from marivo.analysis.evidence.extraction.composition import extract_decomposition_findings
from marivo.analysis.evidence.extraction.correlation import extract_correlation_findings
from marivo.analysis.evidence.extraction.delta import extract_delta_findings
from marivo.analysis.evidence.extraction.forecast import extract_forecast_point_findings
from marivo.analysis.evidence.extraction.observation import extract_metric_value_findings
from marivo.analysis.evidence.extraction.test import extract_test_result_findings
from marivo.analysis.evidence.types import Subject


def _now() -> datetime:
    return datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)


def test_metric_value_scalar_emits_one_finding() -> None:
    df = pd.DataFrame({"revenue": [100.0]})
    subject = Subject(metric="sales.revenue", slice={}, analysis_axis="scalar")
    findings = extract_metric_value_findings(
        df=df,
        artifact_id="art_obs_1",
        session_id="sess_1",
        subject=subject,
        semantic_kind="scalar",
        measure_column="revenue",
        committed_at=_now(),
    )
    assert len(findings) == 1
    assert findings[0].finding_type == "metric_value"
    assert findings[0].canonical_item_key == "value"
    assert findings[0].payload["value"] == 100.0


def test_metric_value_time_series_emits_per_bucket_finding() -> None:
    df = pd.DataFrame({"bucket_start": ["2026-05-01", "2026-05-02"], "revenue": [10.0, 20.0]})
    subject = Subject(metric="sales.revenue", slice={}, grain="day", analysis_axis="time")
    findings = extract_metric_value_findings(
        df=df,
        artifact_id="art_obs_2",
        session_id="sess_1",
        subject=subject,
        semantic_kind="time_series",
        measure_column="revenue",
        committed_at=_now(),
        time_column="bucket_start",
    )
    assert len(findings) == 2
    assert {f.canonical_item_key for f in findings} == {
        "buckets:2026-05-01",
        "buckets:2026-05-02",
    }


def test_delta_scalar_emits_change_finding() -> None:
    df = pd.DataFrame(
        {"current": [120.0], "baseline": [100.0], "delta": [20.0], "pct_change": [0.2]}
    )
    subject = Subject(metric="sales.revenue", slice={}, analysis_axis="change")
    findings = extract_delta_findings(
        df=df,
        artifact_id="art_delta_1",
        session_id="sess_1",
        subject=subject,
        semantic_kind="scalar",
        committed_at=_now(),
    )
    assert len(findings) == 1
    assert findings[0].finding_type == "delta"
    assert findings[0].canonical_item_key == "value"
    assert findings[0].payload["direction"] == "increase"
    assert findings[0].payload["delta_kind"] == "scalar_delta"
    assert findings[0].payload["magnitude"] == 20.0


def test_delta_segmented_emits_per_segment_finding() -> None:
    df = pd.DataFrame(
        {
            "region": ["us", "eu"],
            "current": [120.0, 80.0],
            "baseline": [100.0, 90.0],
            "delta": [20.0, -10.0],
            "pct_change": [0.2, -0.111],
        }
    )
    subject = Subject(metric="sales.revenue", slice={}, analysis_axis="change")
    findings = extract_delta_findings(
        df=df,
        artifact_id="art_delta_2",
        session_id="sess_1",
        subject=subject,
        semantic_kind="segmented",
        committed_at=_now(),
        dimension_columns=["region"],
    )
    assert len(findings) == 2
    keys = {f.canonical_item_key for f in findings}
    assert keys == {"rows:region=us", "rows:region=eu"}
    by_key = {f.canonical_item_key: f for f in findings}
    assert by_key["rows:region=us"].payload["direction"] == "increase"
    assert by_key["rows:region=eu"].payload["direction"] == "decrease"
    assert by_key["rows:region=us"].payload["delta_kind"] == "segmented_delta"


def test_delta_flat_direction_when_zero() -> None:
    df = pd.DataFrame(
        {"current": [100.0], "baseline": [100.0], "delta": [0.0], "pct_change": [0.0]}
    )
    subject = Subject(metric="sales.revenue", slice={}, analysis_axis="change")
    findings = extract_delta_findings(
        df=df,
        artifact_id="art_delta_3",
        session_id="sess_1",
        subject=subject,
        semantic_kind="scalar",
        committed_at=_now(),
    )
    assert findings[0].payload["direction"] == "flat"


def test_delta_finding_id_is_replay_stable() -> None:
    df = pd.DataFrame(
        {"current": [120.0], "baseline": [100.0], "delta": [20.0], "pct_change": [0.2]}
    )
    subject = Subject(metric="sales.revenue", slice={}, analysis_axis="change")
    f1 = extract_delta_findings(
        df=df,
        artifact_id="art_delta_4",
        session_id="sess_1",
        subject=subject,
        semantic_kind="scalar",
        committed_at=_now(),
    )[0]
    f2 = extract_delta_findings(
        df=df,
        artifact_id="art_delta_4",
        session_id="sess_1",
        subject=subject,
        semantic_kind="scalar",
        committed_at=_now(),
    )[0]
    assert f1.finding_id == f2.finding_id


def test_delta_scalar_payload_carries_unit() -> None:
    df = pd.DataFrame(
        {"current": [120.0], "baseline": [100.0], "delta": [20.0], "pct_change": [0.2]}
    )
    subject = Subject(metric="sales.revenue", slice={}, analysis_axis="change")
    findings = extract_delta_findings(
        df=df,
        artifact_id="art_delta_u1",
        session_id="sess_1",
        subject=subject,
        semantic_kind="scalar",
        committed_at=_now(),
        unit="CNY",
    )
    assert findings[0].payload["unit"] == "CNY"


def test_delta_segmented_payload_carries_unit() -> None:
    df = pd.DataFrame(
        {
            "region": ["us"],
            "current": [120.0],
            "baseline": [100.0],
            "delta": [20.0],
            "pct_change": [0.2],
        }
    )
    subject = Subject(metric="sales.revenue", slice={}, analysis_axis="change")
    findings = extract_delta_findings(
        df=df,
        artifact_id="art_delta_u2",
        session_id="sess_1",
        subject=subject,
        semantic_kind="segmented",
        committed_at=_now(),
        dimension_columns=["region"],
        unit="CNY",
    )
    assert findings[0].payload["unit"] == "CNY"


def test_delta_payload_unit_defaults_to_none() -> None:
    df = pd.DataFrame(
        {"current": [120.0], "baseline": [100.0], "delta": [20.0], "pct_change": [0.2]}
    )
    subject = Subject(metric="sales.revenue", slice={}, analysis_axis="change")
    findings = extract_delta_findings(
        df=df,
        artifact_id="art_delta_u3",
        session_id="sess_1",
        subject=subject,
        semantic_kind="scalar",
        committed_at=_now(),
    )
    assert findings[0].payload["unit"] is None


# ---------------------------------------------------------------------------
# Anomaly candidate extraction
# ---------------------------------------------------------------------------


def test_anomaly_candidate_findings_one_per_row() -> None:
    df = pd.DataFrame(
        [
            {
                "candidate_ref": "cand_1",
                "score": 0.92,
                "flag_level": "high",
                "current_value": 1200.0,
                "baseline_value": 800.0,
                "deviation_absolute": 400.0,
                "deviation_relative": 0.5,
            },
            {
                "candidate_ref": "cand_2",
                "score": 0.81,
                "flag_level": "medium",
                "current_value": 600.0,
                "baseline_value": 800.0,
                "deviation_absolute": -200.0,
                "deviation_relative": -0.25,
            },
        ]
    )
    subject = Subject(metric="dau", analysis_axis="time")

    findings = extract_anomaly_candidate_findings(
        df=df,
        artifact_id="art_anom1",
        session_id="sess_1",
        subject=subject,
        committed_at=datetime.now(UTC),
    )

    assert len(findings) == 2
    assert findings[0].finding_type == "anomaly_candidate"
    assert findings[0].subject.analysis_axis == "time"
    assert findings[0].canonical_item_key == "cand_1"
    assert findings[0].payload["candidate_ref"] == "cand_1"
    assert findings[0].payload["score"] == 0.92


def test_anomaly_candidate_empty_allowed() -> None:
    subject = Subject(metric="dau", analysis_axis="time")

    findings = extract_anomaly_candidate_findings(
        df=pd.DataFrame(columns=["candidate_ref", "score"]),
        artifact_id="art_anom_empty",
        session_id="sess_1",
        subject=subject,
        committed_at=datetime.now(UTC),
    )

    assert findings == []


def test_anomaly_candidate_falls_back_to_index_when_no_ref() -> None:
    df = pd.DataFrame([{"score": 0.7, "current_value": 100.0}])
    subject = Subject(metric="dau", analysis_axis="time")

    findings = extract_anomaly_candidate_findings(
        df=df,
        artifact_id="art_x",
        session_id="sess_1",
        subject=subject,
        committed_at=datetime.now(UTC),
    )

    assert findings[0].canonical_item_key == "row:0"


# ---------------------------------------------------------------------------
# Decomposition finding extraction
# ---------------------------------------------------------------------------


def test_decomposition_findings_one_per_row() -> None:
    df = pd.DataFrame(
        [
            {
                "dimension": "country",
                "country": "us",
                "contribution_value": 12.0,
                "contribution_share": 0.6,
                "direction": "increase",
            },
            {
                "dimension": "country",
                "country": "jp",
                "contribution_value": -4.0,
                "contribution_share": -0.2,
                "direction": "decrease",
            },
        ]
    )
    subject = Subject(metric="dau", analysis_axis="decomposition")

    findings = extract_decomposition_findings(
        df=df,
        artifact_id="art_decomp1",
        session_id="sess_1",
        subject=subject,
        committed_at=datetime.now(UTC),
        scope_delta_ref="art_delta_parent",
    )

    assert len(findings) == 2
    assert findings[0].finding_type == "decomposition_item"
    assert findings[0].canonical_item_key.startswith("country|")
    assert findings[0].payload["scope_delta_ref"] == "art_delta_parent"
    assert findings[0].payload["dimension"] == "country"
    assert findings[0].payload["dimension_keys"] == {"country": "us"}
    assert findings[0].payload["contribution_value"] == 12.0
    assert findings[0].payload["contribution_share"] == 0.6


def test_decomposition_findings_empty_df_returns_empty() -> None:
    subject = Subject(metric="dau", analysis_axis="decomposition")

    findings = extract_decomposition_findings(
        df=pd.DataFrame(columns=["dimension", "contribution_value"]),
        artifact_id="art_decomp_empty",
        session_id="sess_1",
        subject=subject,
        committed_at=datetime.now(UTC),
        scope_delta_ref="art_delta_parent",
    )

    assert findings == []


# ---------------------------------------------------------------------------
# Correlation finding extraction
# ---------------------------------------------------------------------------


def test_correlation_finding_one_per_artifact() -> None:
    df = pd.DataFrame(
        [
            {
                "left_ref": "art_left",
                "right_ref": "art_right",
                "method": "pearson",
                "coefficient": 0.71,
                "p_value": 0.03,
                "n": 42,
                "join_basis": "window_bucket",
            }
        ]
    )
    subject = Subject(metric=None, analysis_axis="correlation")

    findings = extract_correlation_findings(
        df=df,
        artifact_id="art_corr1",
        session_id="sess_1",
        subject=subject,
        committed_at=datetime.now(UTC),
    )

    assert len(findings) == 1
    assert findings[0].finding_type == "correlation_result"
    assert findings[0].canonical_item_key == "result"
    assert findings[0].payload["coefficient"] == 0.71
    assert findings[0].payload["join_basis"] == "window_bucket"


def test_correlation_extractor_empty_raises() -> None:
    subject = Subject(metric=None, analysis_axis="correlation")

    with pytest.raises(FindingExtractionFailedError):
        extract_correlation_findings(
            df=pd.DataFrame(columns=["coefficient"]),
            artifact_id="art_empty",
            session_id="sess_1",
            subject=subject,
            committed_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Forecast point finding extraction
# ---------------------------------------------------------------------------


def test_forecast_point_findings_one_per_bucket() -> None:
    df = pd.DataFrame(
        [
            {
                "bucket_start": "2025-01-08",
                "bucket_end": "2025-01-08",
                "predicted_value": 1100.0,
                "lower": 1050.0,
                "upper": 1150.0,
                "horizon_index": 1,
            },
            {
                "bucket_start": "2025-01-09",
                "bucket_end": "2025-01-09",
                "predicted_value": 1120.0,
                "lower": 1060.0,
                "upper": 1180.0,
                "horizon_index": 2,
            },
        ]
    )
    subject = Subject(metric="dau", analysis_axis="forecast")

    findings = extract_forecast_point_findings(
        df=df,
        artifact_id="art_f1",
        session_id="sess_1",
        subject=subject,
        committed_at=datetime.now(UTC),
    )

    assert len(findings) == 2
    assert findings[0].finding_type == "forecast_point"
    assert findings[0].canonical_item_key == "2025-01-08|2025-01-08"
    assert findings[0].payload["predicted_value"] == 1100.0
    assert findings[0].payload["prediction_interval"] == [1050.0, 1150.0]


def test_forecast_point_extractor_empty_raises() -> None:
    subject = Subject(metric="dau", analysis_axis="forecast")

    with pytest.raises(FindingExtractionFailedError):
        extract_forecast_point_findings(
            df=pd.DataFrame(columns=["predicted_value"]),
            artifact_id="art_empty",
            session_id="sess_1",
            subject=subject,
            committed_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Test result finding extraction
# ---------------------------------------------------------------------------


def test_test_result_finding_one_per_artifact() -> None:
    df = pd.DataFrame(
        [
            {
                "current_ref": "art_cur",
                "baseline_ref": "art_bas",
                "method": "welch_t",
                "estimate_value": 5.0,
                "statistic_name": "t",
                "statistic_value": 2.4,
                "p_value": 0.02,
                "reject_null": True,
                "alpha": 0.05,
            }
        ]
    )
    subject = Subject(metric="dau", analysis_axis="scalar")

    findings = extract_test_result_findings(
        df=df,
        artifact_id="art_t1",
        session_id="sess_1",
        subject=subject,
        committed_at=datetime.now(UTC),
    )

    assert len(findings) == 1
    assert findings[0].finding_type == "test_result"
    assert findings[0].canonical_item_key == "result"
    assert findings[0].payload["reject_null"] is True
    assert findings[0].payload["p_value"] == 0.02


def test_test_result_extractor_empty_raises() -> None:
    subject = Subject(metric="dau", analysis_axis="scalar")

    with pytest.raises(FindingExtractionFailedError):
        extract_test_result_findings(
            df=pd.DataFrame(columns=["p_value"]),
            artifact_id="art_empty",
            session_id="sess_1",
            subject=subject,
            committed_at=datetime.now(UTC),
        )
