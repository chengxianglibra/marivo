from datetime import UTC, datetime

from marivo.analysis_py.evidence.seeding import seed_test_hypothesis_proposition
from marivo.analysis_py.evidence.types import Finding, Subject


def _finding(payload: dict) -> Finding:
    return Finding(
        finding_id="fnd_t1",
        finding_type="test_result",
        artifact_id="art_t1",
        session_id="sess_1",
        subject=Subject(metric="dau", analysis_axis="scalar"),
        canonical_item_key="result",
        payload=payload,
        committed_at=datetime.now(UTC),
    )


def test_seed_test_hypothesis_basic() -> None:
    finding = _finding(
        {
            "current_ref": "art_cur",
            "baseline_ref": "art_bas",
            "method": "welch_t",
            "p_value": 0.02,
            "alpha": 0.05,
        }
    )

    prop = seed_test_hypothesis_proposition(
        finding=finding,
        left_subject={"metric": "dau", "window": "current"},
        right_subject={"metric": "dau", "window": "baseline"},
    )

    assert prop is not None
    assert prop.proposition_type == "tested_hypothesis"
    assert prop.payload["hypothesis_family"] == "difference"
    assert prop.payload["alpha"] == 0.05
    assert prop.payload["alternative"] == "two_sided"


def test_seed_test_hypothesis_skips_when_alpha_missing() -> None:
    finding = _finding(
        {
            "current_ref": "a",
            "baseline_ref": "b",
            "method": "t",
            "p_value": 0.02,
            "alpha": None,
        }
    )

    assert (
        seed_test_hypothesis_proposition(
            finding=finding,
            left_subject={"metric": "x"},
            right_subject={"metric": "y"},
        )
        is None
    )
