from datetime import UTC, datetime

from marivo.analysis.evidence.seeding import seed_driver_proposition
from marivo.analysis.evidence.types import Finding, Subject


def _finding(payload: dict) -> Finding:
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
    finding = _finding(
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
    finding = _finding(
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
