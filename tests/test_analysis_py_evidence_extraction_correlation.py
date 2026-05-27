from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis_py.errors import FindingExtractionFailedError
from marivo.analysis_py.evidence.extraction.correlation import (
    extract_correlation_findings,
)
from marivo.analysis_py.evidence.types import Subject


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
                "join_basis": "calendar_bucket",
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
    assert findings[0].payload["join_basis"] == "calendar_bucket"


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
