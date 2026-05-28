from datetime import UTC, datetime

from marivo.analysis.evidence.seeding import seed_correlation_proposition
from marivo.analysis.evidence.types import Finding, Subject


def _finding(payload: dict) -> Finding:
    return Finding(
        finding_id="fnd_c1",
        finding_type="correlation_result",
        artifact_id="art_c1",
        session_id="sess_1",
        subject=Subject(metric=None, analysis_axis="correlation"),
        canonical_item_key="result",
        payload=payload,
        committed_at=datetime.now(UTC),
    )


def test_seed_correlation_proposition_basic() -> None:
    finding = _finding(
        {
            "left_ref": "art_left",
            "right_ref": "art_right",
            "method": "pearson",
            "coefficient": 0.71,
            "p_value": 0.03,
            "n": 42,
            "join_basis": "window_bucket",
        }
    )

    prop = seed_correlation_proposition(
        finding=finding,
        aligned_window={"field": "ds", "start": "2025-01-01", "end": "2025-02-01"},
        left_subject={"metric": "dau"},
        right_subject={"metric": "revenue"},
    )

    assert prop is not None
    assert prop.proposition_type == "association"
    assert prop.payload["method_family"] == "pearson"
    assert prop.payload["join_basis"] == "window_bucket"
    assert prop.payload["relationship_of_interest"] == "any_non_zero"


def test_seed_correlation_skips_when_join_basis_missing() -> None:
    finding = _finding(
        {
            "left_ref": "a",
            "right_ref": "b",
            "method": "pearson",
            "coefficient": 0.5,
            "p_value": 0.1,
            "n": 10,
            "join_basis": None,
        }
    )

    assert (
        seed_correlation_proposition(
            finding=finding,
            aligned_window=None,
            left_subject={"metric": "x"},
            right_subject={"metric": "y"},
        )
        is None
    )
