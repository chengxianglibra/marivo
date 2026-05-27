from datetime import UTC, datetime

from marivo.analysis_py.evidence.seeding import seed_forecast_proposition
from marivo.analysis_py.evidence.types import Finding, Subject


def _finding(payload: dict) -> Finding:
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
    finding = _finding(
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
    finding = _finding(
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
    finding = _finding(
        {
            "bucket_start": "x",
            "bucket_end": "y",
            "predicted_value": 1.0,
            "horizon_index": None,
        }
    )

    assert seed_forecast_proposition(finding=finding) is None
