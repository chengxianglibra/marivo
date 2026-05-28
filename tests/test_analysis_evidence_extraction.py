"""Finding extractors: metric_value and delta."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from marivo.analysis.evidence.extraction.delta import extract_delta_findings
from marivo.analysis.evidence.extraction.observation import extract_metric_value_findings
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
