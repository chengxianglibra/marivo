"""Replay-stable finding, digest-item, and fingerprint identities."""

from __future__ import annotations

from marivo.analysis.evidence.identity import (
    canonical_json,
    canonical_subject_key,
    make_artifact_id,
    make_digest_fingerprint,
    make_digest_item_id,
    make_finding_id,
)
from marivo.analysis.evidence.types import Subject
from marivo.semantic.metric_graph import RuntimeExpressionSubjectV1


def test_canonical_json_and_subject_key_are_order_stable() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    first = Subject(metric="revenue", slice={"region": "us"}, analysis_axis="change")
    second = Subject(metric="revenue", slice={"region": "us"}, analysis_axis="change")
    assert canonical_subject_key(first) == canonical_subject_key(second)
    assert len(canonical_subject_key(first)) == 32


def test_artifact_finding_and_item_ids_are_deterministic() -> None:
    artifact = make_artifact_id("compare", ["b", "a"], {"alignment": "window"}, {})
    assert artifact == make_artifact_id("compare", ["a", "b"], {"alignment": "window"}, {})
    finding = make_finding_id(artifact, "delta", "value")
    assert finding == make_finding_id(artifact, "delta", "value")
    item = make_digest_item_id(
        artifact_ref=artifact,
        item_kind="change",
        source_finding_refs=(finding,),
    )
    assert item.startswith("itm_")


def test_runtime_subject_key_never_merges_across_sessions() -> None:
    def subject(session_id: str) -> Subject:
        return Subject(
            typed_metric_subject=RuntimeExpressionSubjectV1(
                kind="runtime_expression",
                session_id=session_id,
                expression_fingerprint="expr_same",
                artifact_id="art_same",
                scope_fingerprint="scope_same",
            ),
            metric=None,
            analysis_axis="scalar",
        )

    assert canonical_subject_key(subject("sess_a")) != canonical_subject_key(subject("sess_b"))


def test_digest_fingerprint_excludes_its_self_field() -> None:
    first = make_digest_fingerprint({"artifact_ref": "art_x", "fingerprint": "first"})
    second = make_digest_fingerprint({"artifact_ref": "art_x", "fingerprint": "second"})
    assert first == second
