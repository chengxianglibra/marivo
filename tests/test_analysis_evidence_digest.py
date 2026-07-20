"""Operator-local bounded digest construction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from marivo.analysis.evidence.digest import build_artifact_digest
from marivo.analysis.evidence.types import (
    AnalysisScope,
    AnomalyCandidateFindingValue,
    AssociationFindingValue,
    DeltaFindingValue,
    DerivationRule,
    Finding,
    OperatorSemantics,
    Subject,
    TestFindingValue,
)
from tests.shared_fixtures import make_test_analysis_scope, make_test_subject


def _finding(*, key: str, value: DeltaFindingValue, committed_at: datetime) -> Finding:
    return Finding(
        finding_id=f"fnd_{key}",
        finding_type="delta",
        epistemic_kind="algebraic",
        artifact_id="art_compare",
        session_id="sess_1",
        subject=make_test_subject(metric_id="revenue", analysis_axis="change"),
        canonical_item_key=key,
        value=value,
        derivation=DerivationRule(
            rule_id="extract.delta",
            rule_version="v2",
            operator="compare",
            source_fields=("current", "baseline", "delta", "pct_change"),
            source_finding_refs=(),
        ),
        committed_at=committed_at,
    )


def _compare_digest(findings: list[Finding]):
    return build_artifact_digest(
        artifact_ref="art_compare",
        operator=OperatorSemantics(
            operator="compare",
            operator_version="v1",
            artifact_family="delta_frame",
            semantic_shape="segmented",
        ),
        subject=make_test_subject(metric_id="revenue", analysis_axis="change"),
        scope=make_test_analysis_scope("revenue"),
        findings=findings,
        quality=None,
        rows_available=True,
    )


def test_digest_is_bounded_and_reports_exact_omissions() -> None:
    now = datetime.now(UTC)
    digest = _compare_digest(
        [
            _finding(
                key=f"segment:{index}",
                value=DeltaFindingValue(
                    delta_kind="segmented_delta",
                    magnitude=float(index),
                    direction="increase",
                ),
                committed_at=now,
            )
            for index in range(8)
        ]
    )
    assert len(digest.items) == 5
    assert digest.omissions.retained_items == 5
    assert digest.omissions.omitted_items == 3
    assert digest.omissions.omitted_kinds == ("change",)
    assert digest.boundaries[0].kind == "full_distribution_not_in_digest"
    assert "unregistered_question" in digest.fallback.recommended_when


def test_digest_fingerprint_ignores_finding_commit_time() -> None:
    now = datetime.now(UTC)
    value = DeltaFindingValue(delta_kind="scalar_delta", magnitude=2.0, direction="increase")
    first = _compare_digest([_finding(key="value", value=value, committed_at=now)])
    second = _compare_digest(
        [_finding(key="value", value=value, committed_at=now + timedelta(days=1))]
    )
    assert first.fingerprint == second.fingerprint


def test_correlation_digest_states_missing_inference_without_upgrading_it() -> None:
    finding = Finding(
        finding_id="fnd_assoc",
        finding_type="correlation_result",
        epistemic_kind="estimated",
        artifact_id="art_assoc",
        session_id="sess_1",
        subject=Subject(analysis_axis="correlation"),
        canonical_item_key="result",
        value=AssociationFindingValue(
            left_ref="art_a",
            right_ref="art_b",
            method="pearson",
            coefficient=0.7,
            sample_size=30,
            join_basis="window_bucket",
        ),
        derivation=DerivationRule(
            rule_id="extract.association",
            rule_version="v2",
            operator="correlate",
            source_fields=("coefficient", "n"),
            source_finding_refs=(),
        ),
        committed_at=datetime.now(UTC),
    )
    digest = build_artifact_digest(
        artifact_ref="art_assoc",
        operator=OperatorSemantics(
            operator="correlate",
            operator_version="v1",
            artifact_family="association_result",
        ),
        subject=finding.subject,
        scope=make_test_analysis_scope("a", "b"),
        findings=[finding],
        quality=None,
        rows_available=True,
    )
    assert {boundary.kind for boundary in digest.boundaries} == {
        "significance_not_computed",
        "interval_not_computed",
        "causal_effect_not_estimated",
    }
    rendered = digest.render()
    assert "metric_definition_compatibility" not in rendered
    assert "primary_driver" not in rendered
    assert "root cause" not in rendered
    assert "validated" not in rendered


def test_unregistered_operator_fails_closed() -> None:
    with pytest.raises(ValueError, match="no digest rule"):
        build_artifact_digest(
            artifact_ref="art_x",
            operator=OperatorSemantics(
                operator="unknown",
                operator_version="v1",
                artifact_family="unknown",
            ),
            subject=Subject(analysis_axis="scalar"),
            scope=AnalysisScope(),
            findings=[],
            quality=None,
            rows_available=True,
        )


def test_digest_projection_does_not_drop_test_or_candidate_facts() -> None:
    now = datetime.now(UTC)
    subject = make_test_subject(metric_id="rate", analysis_axis="scalar")
    derivation = DerivationRule(
        rule_id="extract",
        rule_version="v2",
        operator="test",
        source_fields=(),
        source_finding_refs=(),
    )
    test_finding = Finding(
        finding_id="fnd_test",
        finding_type="test_result",
        epistemic_kind="tested",
        artifact_id="art_test",
        session_id="sess_1",
        subject=subject,
        canonical_item_key="result",
        value=TestFindingValue(
            null_predicate="difference_equals_zero",
            alternative="two_sided",
            method="paired_t",
            alpha=0.05,
            effect_estimate=-0.3,
            confidence_interval=(-0.4, -0.2),
            reject_null=True,
            sample_size=7,
        ),
        derivation=derivation,
        committed_at=now,
    )
    test_digest = build_artifact_digest(
        artifact_ref="art_test",
        operator=OperatorSemantics(
            operator="hypothesis_test",
            operator_version="v1",
            artifact_family="hypothesis_test_result",
        ),
        subject=subject,
        scope=make_test_analysis_scope("rate"),
        findings=[test_finding],
        quality=None,
        rows_available=True,
    )
    test_item = test_digest.items[0]
    assert test_item.kind == "test_decision"
    assert test_item.sample_size == 7
    assert "interval=[-0.4,-0.2]" in test_digest.render()
    assert "n=7" in test_digest.render()

    candidate_finding = Finding(
        finding_id="fnd_candidate",
        finding_type="anomaly_candidate",
        epistemic_kind="candidate",
        artifact_id="art_candidates",
        session_id="sess_1",
        subject=make_test_subject(metric_id="rate", analysis_axis="anomaly"),
        canonical_item_key="2026-01-01",
        value=AnomalyCandidateFindingValue(
            candidate_ref="2026-01-01",
            score=2.8,
            detector="zscore",
            rank=1,
            current_value=0.36,
            baseline_value=0.17,
            deviation_absolute=0.19,
            deviation_relative=1.12,
            flag_level="high",
        ),
        derivation=derivation,
        committed_at=now,
    )
    candidate_digest = build_artifact_digest(
        artifact_ref="art_candidates",
        operator=OperatorSemantics(
            operator="discover",
            operator_version="v1",
            artifact_family="candidate_set",
        ),
        subject=candidate_finding.subject,
        scope=make_test_analysis_scope("rate"),
        findings=[candidate_finding],
        quality=None,
        rows_available=True,
    )
    candidate_item = candidate_digest.items[0]
    assert candidate_item.kind == "anomaly_candidate"
    assert candidate_item.current_value == 0.36
    assert "baseline=0.17" in candidate_digest.render()
