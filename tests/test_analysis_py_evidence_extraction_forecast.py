from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis_py.errors import FindingExtractionFailedError
from marivo.analysis_py.evidence.extraction.forecast import (
    extract_forecast_point_findings,
)
from marivo.analysis_py.evidence.types import Subject


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
