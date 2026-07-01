"""Evidence seeding: T1 change, T2 driver, T3 anomaly, T4 correlation, T5 test, T6 forecast."""

from __future__ import annotations

from datetime import UTC, datetime

from marivo.analysis.evidence.seeding import (
    DERIVATION_VERSION,
    seed_anomaly_proposition,
    seed_change_proposition,
    seed_correlation_proposition,
    seed_driver_proposition,
    seed_forecast_proposition,
    seed_test_hypothesis_proposition,
)
from marivo.analysis.evidence.types import Finding, Subject


def _delta_finding(
    *,
    direction: str = "increase",
    delta_kind: str = "scalar_delta",
    presence: str | None = None,
    canonical_item_key: str = "value",
    dimension_keys: dict[str, str] | None = None,
    unit: str | None = None,
) -> Finding:
    payload: dict[str, object] = {
        "delta_kind": delta_kind,
        "direction": direction,
        "presence": presence,
        "magnitude": 20.0,
        "current": 120.0,
        "baseline": 100.0,
        "unit": unit,
    }
    if dimension_keys is not None:
        payload["dimension_keys"] = dimension_keys
    return Finding(
        finding_id="fnd_x",
        finding_type="delta",
        artifact_id="art_delta_1",
        session_id="sess_1",
        subject=Subject(metric="sales.revenue", slice={}, analysis_axis="change"),
        canonical_item_key=canonical_item_key,
        payload=payload,
        committed_at=datetime.now(UTC),
    )


def _comparison_window() -> dict:
    return {
        "current": {"field": "order_date", "start": "2026-05-01", "end": "2026-05-07"},
        "baseline": {"field": "order_date", "start": "2026-04-24", "end": "2026-04-30"},
    }


def test_seed_change_for_increase_emits_proposition() -> None:
    finding = _delta_finding(direction="increase", delta_kind="scalar_delta")
    prop = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    assert prop is not None
    assert prop.proposition_type == "change"
    assert prop.payload["change_kind"] == "scalar_change"
    assert prop.payload["direction_of_interest"] == "increase"
    assert prop.derivation_version == DERIVATION_VERSION
    assert prop.seed_finding_refs == [finding.finding_id]


def test_seed_change_skips_flat_direction() -> None:
    finding = _delta_finding(direction="flat", delta_kind="scalar_delta")
    prop = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    assert prop is None


def test_seed_change_skips_undefined_without_presence() -> None:
    finding = _delta_finding(direction="undefined", delta_kind="scalar_delta", presence=None)
    prop = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    assert prop is None


def test_seed_change_emits_for_undefined_with_presence() -> None:
    finding = _delta_finding(
        direction="undefined", delta_kind="scalar_delta", presence="current_only"
    )
    prop = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    assert prop is not None
    assert prop.payload["direction_of_interest"] == "any_non_flat"


def test_seed_change_skips_time_series_delta() -> None:
    finding = _delta_finding(direction="increase", delta_kind="time_series_delta")
    prop = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    assert prop is None


def test_seed_change_segmented_carries_dimension_keys() -> None:
    finding = _delta_finding(
        direction="decrease",
        delta_kind="segmented_delta",
        canonical_item_key="rows:region=us",
        dimension_keys={"region": "us"},
    )
    prop = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    assert prop is not None
    assert prop.payload["change_kind"] == "segment_change"
    assert prop.payload["dimension_keys"] == {"region": "us"}


def test_seed_change_proposition_id_replay_stable() -> None:
    finding = _delta_finding(direction="increase", delta_kind="scalar_delta")
    p1 = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    p2 = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    assert p1 is not None and p2 is not None
    assert p1.proposition_id == p2.proposition_id


def test_seed_change_passes_unit_through() -> None:
    finding = _delta_finding(direction="increase", delta_kind="scalar_delta", unit="CNY")
    prop = seed_change_proposition(
        finding=finding,
        comparison_window=_comparison_window(),
        comparison_basis="left_vs_right",
    )
    assert prop is not None
    assert prop.payload["unit"] == "CNY"


# ---------------------------------------------------------------------------
# T2: driver proposition seeding
# ---------------------------------------------------------------------------


def _decomposition_finding(payload: dict) -> Finding:
    return Finding(
        finding_id="fnd_1",
        finding_type="decomposition_item",
        artifact_id="art_d1",
        session_id="sess_1",
        subject=Subject(metric="dau", analysis_axis="decomposition"),
        canonical_item_key="country|country=us",
        payload=payload,
        committed_at=datetime.now(UTC),
    )


def test_seed_driver_proposition_primary() -> None:
    finding = _decomposition_finding(
        {
            "dimension": "country",
            "dimension_keys": {"country": "us"},
            "contribution_value": 12.0,
            "contribution_share": 0.6,
            "direction": "increase",
            "scope_delta_ref": "art_delta_parent",
        }
    )

    prop = seed_driver_proposition(
        finding=finding,
        observed_window={"field": "ds", "start": "2025-01-01", "end": "2025-01-08"},
    )

    assert prop is not None
    assert prop.proposition_type == "driver"
    assert prop.payload["dimension"] == "country"
    assert prop.payload["contribution_role"] == "primary_driver"
    assert prop.payload["scope_delta_ref"] == "art_delta_parent"


def test_seed_driver_proposition_skips_when_dimension_keys_empty() -> None:
    finding = _decomposition_finding(
        {
            "dimension": "country",
            "dimension_keys": {},
            "contribution_value": 1.0,
            "contribution_share": 0.1,
            "direction": "increase",
            "scope_delta_ref": "art_delta_parent",
        }
    )

    assert seed_driver_proposition(finding=finding, observed_window=None) is None


# ---------------------------------------------------------------------------
# T3: anomaly proposition seeding
# ---------------------------------------------------------------------------


def _anomaly_finding(payload: dict, axis: str = "time") -> Finding:
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
    finding = _anomaly_finding({"candidate_ref": "cand_1", "score": 0.92})

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
    finding = _anomaly_finding({"candidate_ref": "cand_1", "score": 0.92})

    assert seed_anomaly_proposition(finding=finding, observed_window=None) is None


def test_seed_anomaly_proposition_skips_when_candidate_ref_missing() -> None:
    finding = _anomaly_finding({"candidate_ref": None, "score": 0.5})

    assert (
        seed_anomaly_proposition(
            finding=finding,
            observed_window={"field": "ds", "start": "x", "end": "y"},
        )
        is None
    )


# ---------------------------------------------------------------------------
# T4: correlation proposition seeding
# ---------------------------------------------------------------------------


def _correlation_finding(payload: dict) -> Finding:
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
    finding = _correlation_finding(
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
    finding = _correlation_finding(
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


# ---------------------------------------------------------------------------
# T5: hypothesis test proposition seeding
# ---------------------------------------------------------------------------


def _test_result_finding(payload: dict) -> Finding:
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
    finding = _test_result_finding(
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
    finding = _test_result_finding(
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


# ---------------------------------------------------------------------------
# T6: forecast proposition seeding
# ---------------------------------------------------------------------------


def _forecast_finding(payload: dict) -> Finding:
    return Finding(
        finding_id="fnd_fc1",
        finding_type="forecast_point",
        artifact_id="art_fc1",
        session_id="sess_1",
        subject=Subject(metric="dau", analysis_axis="forecast"),
        canonical_item_key="2025-01-08|2025-01-08",
        payload=payload,
        committed_at=datetime.now(UTC),
    )


def test_seed_forecast_with_interval_is_interval_kind() -> None:
    finding = _forecast_finding(
        {
            "bucket_start": "2025-01-08",
            "bucket_end": "2025-01-08",
            "predicted_value": 1100.0,
            "prediction_interval": [1050.0, 1150.0],
            "horizon_index": 1,
        }
    )

    prop = seed_forecast_proposition(finding=finding)

    assert prop is not None
    assert prop.proposition_type == "forecast"
    assert prop.payload["forecast_kind"] == "interval"
    assert prop.payload["expectation_direction"] == "open"
    assert prop.payload["horizon_index"] == 1


def test_seed_forecast_without_interval_is_point_kind() -> None:
    finding = _forecast_finding(
        {
            "bucket_start": "2025-01-08",
            "bucket_end": "2025-01-08",
            "predicted_value": 1100.0,
            "prediction_interval": None,
            "horizon_index": 1,
        }
    )

    prop = seed_forecast_proposition(finding=finding)

    assert prop is not None
    assert prop.payload["forecast_kind"] == "point"


def test_seed_forecast_skips_when_horizon_missing() -> None:
    finding = _forecast_finding(
        {
            "bucket_start": "x",
            "bucket_end": "y",
            "predicted_value": 1.0,
            "horizon_index": None,
        }
    )

    assert seed_forecast_proposition(finding=finding) is None
