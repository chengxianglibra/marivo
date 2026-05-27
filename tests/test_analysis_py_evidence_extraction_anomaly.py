from datetime import UTC, datetime

import pandas as pd

from marivo.analysis_py.evidence.extraction.anomaly import (
    extract_anomaly_candidate_findings,
)
from marivo.analysis_py.evidence.types import Subject


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
