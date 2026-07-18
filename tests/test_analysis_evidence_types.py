"""Closed typed-evidence model contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from marivo.analysis.evidence.types import (
    AnalysisScope,
    DeltaFindingValue,
    DerivationRule,
    Finding,
    Subject,
)


def _derivation() -> DerivationRule:
    return DerivationRule(
        rule_id="extract.delta",
        rule_version="v2",
        operator="compare",
        source_fields=("current", "baseline", "delta"),
        source_finding_refs=(),
    )


def test_subject_and_scope_are_frozen_and_reject_extra_fields() -> None:
    subject = Subject(metric="revenue", analysis_axis="change")
    scope = AnalysisScope(metric_ids=("revenue",), assumptions=("same currency",))
    assert Subject.model_validate(subject.model_dump(mode="json")) == subject
    assert AnalysisScope.model_validate(scope.model_dump(mode="json")) == scope
    with pytest.raises(ValidationError):
        subject.metric = "orders"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        AnalysisScope(metric_ids=("revenue",), compatible_with=True)  # type: ignore[call-arg]


def test_finding_round_trip_uses_closed_value_union() -> None:
    finding = Finding(
        finding_id="fnd_change",
        finding_type="delta",
        epistemic_kind="algebraic",
        artifact_id="art_compare",
        session_id="sess_1",
        subject=Subject(metric="revenue", analysis_axis="change"),
        canonical_item_key="value",
        value=DeltaFindingValue(
            delta_kind="scalar_delta",
            current=12.0,
            baseline=10.0,
            magnitude=2.0,
            relative_delta=0.2,
            direction="increase",
        ),
        derivation=_derivation(),
        source_refs=("art_current", "art_baseline"),
        committed_at=datetime.now(UTC),
    )
    restored = Finding.model_validate(finding.model_dump(mode="json"))
    assert restored == finding
    assert not hasattr(restored, "payload")


@pytest.mark.parametrize(
    ("finding_type", "epistemic_kind"),
    [("delta", "observed"), ("test_result", "estimated")],
)
def test_finding_rejects_epistemic_upgrades(finding_type: str, epistemic_kind: str) -> None:
    payload = {
        "finding_id": "fnd_bad",
        "finding_type": finding_type,
        "epistemic_kind": epistemic_kind,
        "artifact_id": "art_x",
        "session_id": "sess_1",
        "subject": {"metric": "revenue", "analysis_axis": "change"},
        "canonical_item_key": "value",
        "value": {
            "kind": "delta",
            "delta_kind": "scalar_delta",
            "direction": "flat",
        },
        "derivation": _derivation().model_dump(mode="json"),
        "committed_at": datetime.now(UTC),
    }
    with pytest.raises(ValidationError):
        Finding.model_validate(payload)


def test_public_evidence_namespace_contains_digest_types_and_removes_judgment_types() -> None:
    import marivo.analysis as mv

    for name in (
        "ArtifactDigest",
        "Finding",
        "ObservationFact",
        "ChangeFact",
        "ContributionFact",
        "AssociationFact",
        "TestDecision",
        "ForecastOutput",
        "AnomalyCandidate",
        "QualityCheckResult",
        "AnalysisScope",
    ):
        assert hasattr(mv.evidence, name), name
    for name in (
        "Proposition",
        "Assessment",
        "SessionKnowledge",
        "OpenQuestion",
        "BlockedFollowup",
        "ConfidenceScope",
        "InvocationOption",
        "CandidateAffordance",
        "CandidateConstraint",
    ):
        assert not hasattr(mv.evidence, name), name
