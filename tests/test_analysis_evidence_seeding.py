"""T1 change proposition seeding from delta findings."""

from __future__ import annotations

from datetime import UTC, datetime

from marivo.analysis.evidence.seeding import (
    DERIVATION_VERSION,
    seed_change_proposition,
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
