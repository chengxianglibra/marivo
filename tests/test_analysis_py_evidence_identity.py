"""Replay-stable IDs and canonical keys."""

from __future__ import annotations

from marivo.analysis_py.evidence.identity import (
    canonical_json,
    canonical_subject_key,
    make_action_id,
    make_artifact_id,
    make_finding_id,
    make_proposition_id,
)
from marivo.analysis_py.evidence.types import Subject


def test_canonical_json_sorts_keys() -> None:
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b
    assert a == '{"a":2,"b":1}'


def test_canonical_subject_key_stable() -> None:
    s1 = Subject(metric="dau", slice={"region": "us"}, analysis_axis="change")
    s2 = Subject(metric="dau", slice={"region": "us"}, analysis_axis="change")
    assert canonical_subject_key(s1) == canonical_subject_key(s2)
    assert len(canonical_subject_key(s1)) == 32  # SHA-256 first 16 bytes hex


def test_canonical_subject_key_differs_on_content() -> None:
    s1 = Subject(metric="dau", slice={}, analysis_axis="change")
    s2 = Subject(metric="mau", slice={}, analysis_axis="change")
    assert canonical_subject_key(s1) != canonical_subject_key(s2)


def test_make_artifact_id_deterministic() -> None:
    aid1 = make_artifact_id(
        step_type="compare",
        normalized_inputs=["art_a", "art_b"],
        normalized_params={"alignment": "window_bucket"},
        semantic_anchors={"metric": "dau@v1"},
    )
    aid2 = make_artifact_id(
        step_type="compare",
        normalized_inputs=["art_a", "art_b"],
        normalized_params={"alignment": "window_bucket"},
        semantic_anchors={"metric": "dau@v1"},
    )
    assert aid1 == aid2
    assert aid1.startswith("art_")


def test_make_artifact_id_differs_on_inputs() -> None:
    aid1 = make_artifact_id("compare", ["a"], {}, {})
    aid2 = make_artifact_id("compare", ["b"], {}, {})
    assert aid1 != aid2


def test_make_finding_id_deterministic() -> None:
    fid1 = make_finding_id("art_xyz", "delta", "value")
    fid2 = make_finding_id("art_xyz", "delta", "value")
    assert fid1 == fid2
    assert fid1.startswith("fnd_")


def test_make_proposition_id_deterministic() -> None:
    pid1 = make_proposition_id(
        proposition_type="change",
        origin_kind="system_seeded",
        derivation_version="v1",
        subject_key="abc",
        payload={"change_kind": "scalar_change"},
    )
    pid2 = make_proposition_id(
        proposition_type="change",
        origin_kind="system_seeded",
        derivation_version="v1",
        subject_key="abc",
        payload={"change_kind": "scalar_change"},
    )
    assert pid1 == pid2
    assert pid1.startswith("prop_")


def test_make_action_id_deterministic() -> None:
    a1 = make_action_id(
        source_artifact_id="art_xyz",
        category="dag_continuation",
        operator="discover",
        input_refs=["art_xyz"],
        params={"objective": "point_anomalies"},
    )
    a2 = make_action_id(
        source_artifact_id="art_xyz",
        category="dag_continuation",
        operator="discover",
        input_refs=["art_xyz"],
        params={"objective": "point_anomalies"},
    )
    assert a1 == a2
    assert a1.startswith("act_")
