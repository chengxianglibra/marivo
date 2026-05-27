"""Latest-snapshot change assessment recompute."""

from __future__ import annotations

from datetime import UTC, datetime

from marivo.analysis_py.evidence.assessment import recompute_change_assessment
from marivo.analysis_py.evidence.types import Assessment, Finding, Proposition, Subject


def _proposition() -> Proposition:
    return Proposition(
        proposition_id="prop_change_1",
        session_id="sess_1",
        proposition_type="change",
        origin_kind="system_seeded",
        derivation_version="v1",
        subject_key="abc",
        payload={
            "change_kind": "scalar_change",
            "direction_of_interest": "increase",
            "comparison_basis": "left_vs_right",
        },
        seed_finding_refs=["fnd_seed"],
        created_at=datetime(2026, 5, 27, tzinfo=UTC),
    )


def _delta_finding(*, direction: str, magnitude: float = 20.0, finding_id: str = "fnd_seed") -> Finding:
    return Finding(
        finding_id=finding_id,
        finding_type="delta",
        artifact_id="art_delta_1",
        session_id="sess_1",
        subject=Subject(metric="sales.revenue", slice={}, analysis_axis="change"),
        canonical_item_key="value",
        payload={
            "direction": direction,
            "magnitude": magnitude,
            "delta_kind": "scalar_delta",
            "presence": None,
        },
        committed_at=datetime(2026, 5, 27, tzinfo=UTC),
    )


def test_recompute_validates_when_seed_direction_matches_interest() -> None:
    prop = _proposition()
    seed = _delta_finding(direction="increase")
    snapshot, edges = recompute_change_assessment(
        proposition=prop,
        seed_findings=[seed],
        snapshot_seq=1,
    )
    assert isinstance(snapshot, Assessment)
    assert snapshot.status == "validated"
    assert snapshot.confidence == 0.9
    assert snapshot.confidence_basis == "seed_delta_direction_matches"
    assert snapshot.is_latest is True
    assert snapshot.payload["magnitude"] == 20.0
    assert {(role, fid) for fid, role in edges} == {("support", "fnd_seed")}


def test_recompute_refutes_when_seed_direction_opposes_interest() -> None:
    prop = _proposition()
    seed = _delta_finding(direction="decrease")
    snapshot, edges = recompute_change_assessment(
        proposition=prop,
        seed_findings=[seed],
        snapshot_seq=1,
    )
    assert snapshot.status == "refuted"
    assert {(role, fid) for fid, role in edges} == {("oppose", "fnd_seed")}


def test_recompute_inconclusive_when_seed_direction_is_undefined() -> None:
    prop = _proposition()
    seed = _delta_finding(direction="undefined")
    snapshot, _edges = recompute_change_assessment(
        proposition=prop,
        seed_findings=[seed],
        snapshot_seq=1,
    )
    assert snapshot.status == "inconclusive"


def test_recompute_assigns_supersede_metadata() -> None:
    prop = _proposition()
    seed = _delta_finding(direction="increase")
    s1, _ = recompute_change_assessment(
        proposition=prop,
        seed_findings=[seed],
        snapshot_seq=1,
    )
    s2, _ = recompute_change_assessment(
        proposition=prop,
        seed_findings=[seed],
        snapshot_seq=2,
        previous=s1,
    )
    assert s2.supersedes_id == s1.snapshot_id
    assert s2.is_latest is True


def test_recompute_any_non_flat_validates_either_direction() -> None:
    prop = _proposition().model_copy(
        update={"payload": {"change_kind": "scalar_change", "direction_of_interest": "any_non_flat", "comparison_basis": "left_vs_right"}}
    )
    seed_inc = _delta_finding(direction="increase")
    snap_inc, _ = recompute_change_assessment(proposition=prop, seed_findings=[seed_inc], snapshot_seq=1)
    seed_dec = _delta_finding(direction="decrease", finding_id="fnd_dec")
    snap_dec, _ = recompute_change_assessment(proposition=prop, seed_findings=[seed_dec], snapshot_seq=2)
    assert snap_inc.status == "validated"
    assert snap_dec.status == "validated"
