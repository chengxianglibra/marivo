"""Independent exact v3 Finding value and closed body-codec contracts."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import (
    decode_finding_body,
    encode_finding_body,
    finding_identity,
)
from marivo.analysis.materialization.contracts import canonical_json, parse_json
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.refs import ArtifactRef
from marivo.refs import RefPayloadV1, SemanticKind


def _finding(value: t.FindingValueV1) -> t.Finding:
    subject: t.FindingSubjectV1 = t.MetricFindingSubjectV1(
        metric=d._catalog_identity("metric:sales.revenue")
    )
    if isinstance(value, t.AssociationFindingValueV1):
        subject = t.AssociationFindingSubjectV1(
            metric_a=d._catalog_identity("metric:sales.revenue"),
            metric_b=d._catalog_identity("metric:sales.order_count"),
        )
    elif isinstance(value, t.FunnelDeltaFindingValueV1) or (
        isinstance(value, t.ContributionFindingValueV1) and value.contribution_kind != "metric"
    ):
        subject = t.FunnelFindingSubjectV1(
            subject_entity_ref=RefPayloadV1(
                schema="marivo.semantic_ref/v1", kind=SemanticKind.ENTITY, path="sales.customers"
            ),
            pattern_fingerprint="a" * 64,
        )
    item = t.Finding(
        finding_id="pending",
        artifact_ref=ArtifactRef(ref="artifact"),
        session_id="session",
        finding_type=value.kind,
        epistemic_kind="estimated"
        if value.kind == "association"
        else ("predicted" if value.kind == "forecast_point" else "algebraic"),
        subject=subject,
        coordinates=(),
        canonical_item_key='["row",0]',
        value=value,
        derivation=t.FindingDerivationV1(
            producer_id="test.finding",
            extractor_contract_id="test_finding",
            extractor_contract_version="1",
            source_artifact_refs=(ArtifactRef(ref="source"),),
            source_fields=(d._make_field_id("metric.revenue"),),
        ),
        committed_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    return replace(item, finding_id=finding_identity(item))


def _delta() -> t.DeltaFindingValueV1:
    return t.DeltaFindingValueV1(
        coordinate_presence="matched",
        current_value=Decimal("12.30"),
        baseline_value=Decimal("10.00"),
        delta=Decimal("2.30"),
        relative_delta=t.DefinedFindingRatioV1(value=0.23),
    )


def test_finite_delta_remains_eligible_when_relative_arithmetic_is_unavailable() -> None:
    value = replace(_delta(), relative_delta=t.UndefinedRelativeDeltaV1(reason="delta_unavailable"))
    finding = _finding(value)
    restored = decode_finding_body(
        encode_finding_body(finding),
        finding_id=finding.finding_id,
        artifact_ref=finding.artifact_ref.ref,
        session_id=finding.session_id,
        committed_at=finding.committed_at,
    )
    assert restored == finding
    with pytest.raises(IntegrityError):
        replace(value, baseline_value=Decimal("0"))
    with pytest.raises(IntegrityError):
        replace(value, relative_delta=t.UndefinedRelativeDeltaV1(reason="baseline_zero"))


def _contribution() -> t.ContributionFindingValueV1:
    return t.ContributionFindingValueV1(
        method="additive_difference@v1",
        active_axis_mask=(True,),
        other_mask=(False,),
        contribution_kind="metric",
        current_value=12,
        baseline_value=10,
        overall_delta=2,
        contribution=2,
        share_of_total_delta=t.DefinedFindingRatioV1(value=1.0),
        share_of_positive_pool=t.DefinedFindingRatioV1(value=1.0),
        share_of_negative_pool=t.UndefinedFindingShareV1(reason="empty_negative_pool"),
        contribution_rank=1,
        status="ok",
    )


def _funnel() -> t.FunnelDeltaFindingValueV1:
    return t.FunnelDeltaFindingValueV1(
        step_key="payment",
        coordinate_presence="matched",
        current_cohort_count=10,
        baseline_cohort_count=10,
        current_resolved_cohort_count=10,
        baseline_resolved_cohort_count=10,
        current_entry_count=10,
        baseline_entry_count=10,
        current_resolved_entry_count=10,
        baseline_resolved_entry_count=10,
        current_reached_count=8,
        baseline_reached_count=7,
        current_lost_count=2,
        baseline_lost_count=3,
        current_coverage_censored_count=0,
        baseline_coverage_censored_count=0,
        current_loss_rate_from_previous=0.2,
        baseline_loss_rate_from_previous=0.3,
        loss_rate_delta=-0.1,
    )


@pytest.mark.parametrize(
    "value",
    [
        t.AssociationFindingValueV1(
            method="pearson",
            coefficient=0.8,
            input_observation_count=11,
            null_pair_count=1,
            complete_pair_count=10,
            lag=t.NoAssociationLagV1(),
        ),
        _delta(),
        _contribution(),
        t.ForecastPointFindingValueV1(
            model="drift@v1",
            interval_level=0.95,
            horizon_ordinal=1,
            forecast_value=12.0,
            interval_lower=10.0,
            interval_upper=14.0,
            training_row_count=20,
        ),
        _funnel(),
    ],
)
def test_five_closed_variants_round_trip_and_exclude_store_envelope(
    value: t.FindingValueV1,
) -> None:
    original = _finding(value)
    payload = encode_finding_body(original)
    body = parse_json(payload)
    assert isinstance(body, dict)
    assert set(body) == {
        "finding_type",
        "epistemic_kind",
        "subject",
        "coordinates",
        "canonical_item_key",
        "value",
        "derivation",
    }
    restored = decode_finding_body(
        payload,
        finding_id=original.finding_id,
        artifact_ref="artifact",
        session_id="session",
        committed_at=original.committed_at,
    )
    assert restored == original
    assert "show()" in repr(restored)
    assert "\n" not in repr(restored)
    assert len(restored.render().encode()) <= 8000
    with pytest.raises(FrozenInstanceError):
        attribute = "session_id"
        setattr(restored, attribute, "different")


@pytest.mark.parametrize(
    "scalar",
    [
        None,
        True,
        False,
        0,
        2**80,
        -0.0,
        1.25,
        "",
        "member\nsecond line",
        "member",
        Decimal("1.2300"),
        date(2026, 9, 8),
        datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
    ],
)
def test_coordinate_scalar_type_and_value_are_lossless(scalar: t.Scalar) -> None:
    original = replace(
        _finding(_delta()),
        coordinates=(
            t.FindingCoordinateV1(
                field_id=d._make_field_id("dimension.region"),
                identity=d._catalog_identity("dimension:sales.region"),
                value=scalar,
            ),
        ),
    )
    restored = decode_finding_body(
        encode_finding_body(original),
        finding_id=original.finding_id,
        artifact_ref="artifact",
        session_id="session",
        committed_at=original.committed_at,
    )
    assert type(restored.coordinates[0].value) is type(scalar)
    assert restored.coordinates[0].value == scalar
    if isinstance(scalar, Decimal):
        assert str(restored.coordinates[0].value) == str(scalar)


@pytest.mark.parametrize(
    "member,unexpected",
    [
        ("compatibility", {}),
        ("source_artifact_ref", "legacy"),
        ("session_id", "forged"),
        ("committed_at", "2026-09-09"),
    ],
)
def test_body_codec_rejects_unknown_and_duplicated_ownership(
    member: str, unexpected: object
) -> None:
    original = _finding(_delta())
    body = parse_json(encode_finding_body(original))
    assert isinstance(body, dict)
    body[member] = unexpected
    with pytest.raises(IntegrityError):
        decode_finding_body(
            canonical_json(body),
            finding_id=original.finding_id,
            artifact_ref="artifact",
            session_id="session",
            committed_at=original.committed_at,
        )


def test_typed_values_reject_unknown_kind_nonfinite_counts_masks_and_mixed_numeric_types() -> None:
    with pytest.raises(IntegrityError):
        replace(_delta(), current_value=float("nan"))
    with pytest.raises(IntegrityError):
        replace(_delta(), delta=2.3)
    with pytest.raises(IntegrityError):
        replace(_contribution(), active_axis_mask=(False,), other_mask=(True,))
    with pytest.raises(IntegrityError):
        replace(_contribution(), contribution_rank=True)
    with pytest.raises(IntegrityError):
        replace(_funnel(), current_coverage_censored_count=1)
    with pytest.raises(IntegrityError):
        replace(_finding(_delta()), epistemic_kind="predicted")
    with pytest.raises(IntegrityError):
        replace(_finding(_delta()), committed_at=datetime(2026, 9, 8))
    with pytest.raises(IntegrityError):
        t.FindingPage(items=(), limit=True, has_more=False, next_cursor=None)


def test_digest_and_three_axis_results_have_exact_private_shape() -> None:
    result = t.ArtifactDigest(
        artifact_ref=ArtifactRef(ref="artifact"),
        quality_summary_digest="a" * 64,
        typed_issue_digest="b" * 64,
        evidence_digest="c" * 64,
        finding_count=0,
        finding_set_digest="d" * 64,
        extractor_contract_versions=("none@v1",),
    )
    assert result.digest_version == "v3"
    assert not hasattr(result, "items")
    audit = t.ArtifactRevalidation(
        artifact_ref=result.artifact_ref,
        checked_at=datetime.now(timezone.utc),
        artifact_integrity="invalid",
        storage_authority="unauthorized",
        evidence_integrity="valid",
        issues=(
            t.ArtifactRevalidationIssue(
                axis="artifact_integrity",
                kind="metadata_invalid",
                safe_message="Invalid descriptor.",
            ),
        ),
    )
    assert audit.revalidation_version == "v2"
    assert len(audit.render()) < 8000
