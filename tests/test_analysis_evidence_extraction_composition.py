from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis.errors import FindingExtractionFailedError
from marivo.analysis.evidence.extraction.composition import (
    extract_decomposition_findings,
)
from marivo.analysis.evidence.types import Subject


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


def test_decomposition_findings_empty_df_raises() -> None:
    subject = Subject(metric="dau", analysis_axis="decomposition")

    with pytest.raises(FindingExtractionFailedError):
        extract_decomposition_findings(
            df=pd.DataFrame(columns=["dimension", "contribution_value"]),
            artifact_id="art_decomp_empty",
            session_id="sess_1",
            subject=subject,
            committed_at=datetime.now(UTC),
            scope_delta_ref="art_delta_parent",
        )
