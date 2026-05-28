from datetime import UTC, datetime

from marivo.analysis.evidence.assessment import (
    recompute_anomaly_assessment,
    recompute_association_assessment,
    recompute_driver_assessment,
    recompute_forecast_assessment,
    recompute_test_hypothesis_assessment,
)
from marivo.analysis.evidence.types import Finding, Proposition, Subject


def _driver_seed(direction: str, share: float | None) -> Finding:
    return Finding(
        finding_id="fnd_drv1",
        finding_type="decomposition_item",
        artifact_id="art_d1",
        session_id="sess_1",
        subject=Subject(metric="dau", analysis_axis="decomposition"),
        canonical_item_key="country|country=us",
        payload={
            "direction": direction,
            "contribution_share": share,
            "contribution_value": 12.0,
            "dimension_keys": {"country": "us"},
        },
        committed_at=datetime.now(UTC),
    )


def _driver_prop(role: str) -> Proposition:
    return Proposition(
        proposition_id="prop_drv1",
        session_id="sess_1",
        proposition_type="driver",
        derivation_version="v1",
        subject_key="abc123",
        payload={
            "contribution_role": role,
            "dimension": "country",
            "dimension_keys": {"country": "us"},
            "scope_delta_ref": "art_delta_parent",
        },
        seed_finding_refs=["fnd_drv1"],
        created_at=datetime.now(UTC),
    )


def test_driver_assessment_validated_when_role_matches() -> None:
    snap, edges = recompute_driver_assessment(
        proposition=_driver_prop("primary_driver"),
        seed_findings=[_driver_seed("increase", 0.6)],
        snapshot_seq=1,
    )

    assert snap.status == "validated"
    assert snap.confidence == 0.9
    assert edges == [("fnd_drv1", "support")]


def test_driver_assessment_inconclusive_when_share_missing() -> None:
    snap, _edges = recompute_driver_assessment(
        proposition=_driver_prop("primary_driver"),
        seed_findings=[_driver_seed("increase", None)],
        snapshot_seq=1,
    )

    assert snap.status == "inconclusive"


def _anomaly_seed(score: float | None, flag: str = "high") -> Finding:
    return Finding(
        finding_id="fnd_anom1",
        finding_type="anomaly_candidate",
        artifact_id="art_a1",
        session_id="sess_1",
        subject=Subject(metric="dau", analysis_axis="time"),
        canonical_item_key="cand_1",
        payload={"candidate_ref": "cand_1", "score": score, "flag_level": flag},
        committed_at=datetime.now(UTC),
    )


def _anomaly_prop() -> Proposition:
    return Proposition(
        proposition_id="prop_anom1",
        session_id="sess_1",
        proposition_type="anomaly",
        derivation_version="v1",
        subject_key="abc",
        payload={
            "candidate_ref": "cand_1",
            "anomaly_kind": "candidate",
            "observed_window": {
                "field": "ds",
                "start": "2025-01-05",
                "end": "2025-01-05",
            },
        },
        seed_finding_refs=["fnd_anom1"],
        created_at=datetime.now(UTC),
    )


def test_anomaly_assessment_pending_until_human_judgment() -> None:
    snap, edges = recompute_anomaly_assessment(
        proposition=_anomaly_prop(),
        seed_findings=[_anomaly_seed(score=0.92)],
        snapshot_seq=1,
    )

    assert snap.status == "pending"
    assert snap.confidence_basis == "anomaly_candidate_pending_review"
    assert edges == [("fnd_anom1", "support")]


def _assoc_seed(coef: float | None, p_value: float | None) -> Finding:
    return Finding(
        finding_id="fnd_as1",
        finding_type="correlation_result",
        artifact_id="art_a1",
        session_id="sess_1",
        subject=Subject(metric=None, analysis_axis="correlation"),
        canonical_item_key="result",
        payload={"coefficient": coef, "p_value": p_value, "n": 30},
        committed_at=datetime.now(UTC),
    )


def _assoc_prop() -> Proposition:
    return Proposition(
        proposition_id="prop_as1",
        session_id="sess_1",
        proposition_type="association",
        derivation_version="v1",
        subject_key="abc",
        payload={
            "relationship_of_interest": "any_non_zero",
            "method_family": "pearson",
            "join_basis": "window_bucket",
        },
        seed_finding_refs=["fnd_as1"],
        created_at=datetime.now(UTC),
    )


def test_association_validated_when_p_lt_alpha() -> None:
    snap, _edges = recompute_association_assessment(
        proposition=_assoc_prop(),
        seed_findings=[_assoc_seed(0.71, 0.03)],
        snapshot_seq=1,
        alpha=0.05,
    )

    assert snap.status == "validated"
    assert snap.confidence_basis == "association_p_lt_alpha"


def test_association_inconclusive_when_p_high() -> None:
    snap, _edges = recompute_association_assessment(
        proposition=_assoc_prop(),
        seed_findings=[_assoc_seed(0.05, 0.4)],
        snapshot_seq=1,
        alpha=0.05,
    )

    assert snap.status == "inconclusive"


def _test_seed(reject: bool | None, p_value: float | None) -> Finding:
    return Finding(
        finding_id="fnd_th1",
        finding_type="test_result",
        artifact_id="art_th1",
        session_id="sess_1",
        subject=Subject(metric="dau", analysis_axis="scalar"),
        canonical_item_key="result",
        payload={
            "reject_null": reject,
            "p_value": p_value,
            "estimate_value": 5.0,
            "statistic_value": 2.4,
        },
        committed_at=datetime.now(UTC),
    )


def _test_prop() -> Proposition:
    return Proposition(
        proposition_id="prop_th1",
        session_id="sess_1",
        proposition_type="tested_hypothesis",
        derivation_version="v1",
        subject_key="abc",
        payload={
            "hypothesis_family": "difference",
            "alpha": 0.05,
            "alternative": "two_sided",
        },
        seed_finding_refs=["fnd_th1"],
        created_at=datetime.now(UTC),
    )


def test_tested_hypothesis_validated_when_reject_null_true() -> None:
    snap, _edges = recompute_test_hypothesis_assessment(
        proposition=_test_prop(),
        seed_findings=[_test_seed(True, 0.02)],
        snapshot_seq=1,
    )

    assert snap.status == "validated"
    assert snap.confidence_basis == "test_p_lt_alpha"


def test_tested_hypothesis_refuted_when_reject_null_false() -> None:
    snap, _edges = recompute_test_hypothesis_assessment(
        proposition=_test_prop(),
        seed_findings=[_test_seed(False, 0.4)],
        snapshot_seq=1,
    )

    assert snap.status == "refuted"


def test_forecast_pending_until_actual_observed() -> None:
    seed = Finding(
        finding_id="fnd_fc1",
        finding_type="forecast_point",
        artifact_id="art_fc1",
        session_id="sess_1",
        subject=Subject(metric="dau", analysis_axis="forecast"),
        canonical_item_key="2025-01-08|2025-01-08",
        payload={
            "predicted_value": 1100.0,
            "prediction_interval": [1050.0, 1150.0],
            "horizon_index": 1,
            "bucket_start": "2025-01-08",
            "bucket_end": "2025-01-08",
        },
        committed_at=datetime.now(UTC),
    )
    prop = Proposition(
        proposition_id="prop_fc1",
        session_id="sess_1",
        proposition_type="forecast",
        derivation_version="v1",
        subject_key="abc",
        payload={
            "forecast_kind": "interval",
            "forecast_window": {"start": "2025-01-08", "end": "2025-01-08"},
            "horizon_index": 1,
            "expectation_direction": "open",
        },
        seed_finding_refs=["fnd_fc1"],
        created_at=datetime.now(UTC),
    )

    snap, edges = recompute_forecast_assessment(
        proposition=prop,
        seed_findings=[seed],
        snapshot_seq=1,
    )

    assert snap.status == "pending"
    assert snap.confidence_basis == "forecast_pending_actual"
    assert edges == [("fnd_fc1", "support")]
