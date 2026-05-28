from datetime import UTC, datetime

from marivo.analysis.evidence.seeding import seed_anomaly_proposition
from marivo.analysis.evidence.types import Finding, Subject


def _finding(payload: dict, axis: str = "time") -> Finding:
    return Finding(
        finding_id="fnd_a1",
        finding_type="anomaly_candidate",
        artifact_id="art_a1",
        session_id="sess_1",
        subject=Subject(metric="dau", analysis_axis=axis),
        canonical_item_key="cand_1",
        payload=payload,
        committed_at=datetime.now(UTC),
    )


def test_seed_anomaly_proposition_basic() -> None:
    finding = _finding({"candidate_ref": "cand_1", "score": 0.92})

    prop = seed_anomaly_proposition(
        finding=finding,
        observed_window={"field": "ds", "start": "2025-01-05", "end": "2025-01-05"},
    )

    assert prop is not None
    assert prop.proposition_type == "anomaly"
    assert prop.payload["candidate_ref"] == "cand_1"
    assert prop.payload["anomaly_kind"] == "candidate"
    assert prop.payload["observed_window"]["start"] == "2025-01-05"


def test_seed_anomaly_proposition_skips_when_window_missing() -> None:
    finding = _finding({"candidate_ref": "cand_1", "score": 0.92})

    assert seed_anomaly_proposition(finding=finding, observed_window=None) is None


def test_seed_anomaly_proposition_skips_when_candidate_ref_missing() -> None:
    finding = _finding({"candidate_ref": None, "score": 0.5})

    assert (
        seed_anomaly_proposition(
            finding=finding,
            observed_window={"field": "ds", "start": "x", "end": "y"},
        )
        is None
    )
