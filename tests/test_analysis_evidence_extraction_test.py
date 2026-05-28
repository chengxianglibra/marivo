from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis.errors import FindingExtractionFailedError
from marivo.analysis.evidence.extraction.test import extract_test_result_findings
from marivo.analysis.evidence.types import Subject


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
