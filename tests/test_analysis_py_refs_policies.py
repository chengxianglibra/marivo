import pytest
from pydantic import ValidationError

import marivo.analysis_py as mv
from marivo.analysis_py.errors import (
    AlignmentPolicyValidationError,
    LagPolicyValidationError,
    PromotionFailedError,
)
from marivo.analysis_py.policies import (
    AlignmentKind,
    AlignmentPolicy,
    LagPolicy,
    PromotionPolicy,
    PromotionSemanticAnchors,
)
from marivo.analysis_py.refs import ArtifactRef, CalendarRef, DimensionRef, MetricRef


def test_refs_are_exported_and_preserve_ids():
    assert mv.AlignmentKind is AlignmentKind
    assert mv.MetricRef("sales.revenue").id == "sales.revenue"
    assert mv.DimensionRef("region").id == "region"
    assert mv.CalendarRef("cn_holidays").id == "cn_holidays"
    assert MetricRef("sales.revenue").id == "sales.revenue"
    assert DimensionRef("region").id == "region"
    assert CalendarRef("cn_holidays").id == "cn_holidays"


def test_artifact_ref_is_exported_and_preserves_id():
    assert mv.ArtifactRef is ArtifactRef
    assert ArtifactRef("frame_abc123").id == "frame_abc123"
    assert str(ArtifactRef("frame_abc123")) == "frame_abc123"


def test_refs_reject_empty_ids():
    for ref_cls in (MetricRef, DimensionRef, CalendarRef, ArtifactRef):
        with pytest.raises(ValidationError):
            ref_cls(" ")


def test_refs_reject_extra_fields_with_validation_error():
    with pytest.raises(ValidationError):
        CalendarRef(id="cn", extra=1)


def test_metric_ref_requires_model_and_metric():
    with pytest.raises(ValidationError):
        MetricRef("revenue")


def test_alignment_policy_requires_calendar_for_calendar_backed_modes():
    assert AlignmentPolicy(kind="calendar_bucket").calendar is None

    with pytest.raises(AlignmentPolicyValidationError):
        AlignmentPolicy(kind="calendar_bucket", calendar=CalendarRef("cn"))

    with pytest.raises(AlignmentPolicyValidationError):
        AlignmentPolicy(kind="dow_aligned")

    with pytest.raises(ValidationError):
        AlignmentPolicy(kind="dow_aligned", calendar={"id": "cn", "extra": 1})

    policy = AlignmentPolicy(kind="holiday_and_dow_aligned", calendar=CalendarRef("cn"))
    assert policy.kind == "holiday_and_dow_aligned"
    assert policy.calendar == CalendarRef("cn")
    assert policy.period == "month"
    assert policy.fallback == "drop"


def test_alignment_policy_validation_error_renders_fix_snippet():
    with pytest.raises(AlignmentPolicyValidationError) as missing_cal:
        AlignmentPolicy(kind="dow_aligned")
    rendered = str(missing_cal.value)
    assert 'mv.AlignmentPolicy(kind="dow_aligned"' in rendered
    assert 'mv.CalendarRef("cn_holidays")' in rendered

    with pytest.raises(AlignmentPolicyValidationError) as unexpected_cal:
        AlignmentPolicy(kind="calendar_bucket", calendar=CalendarRef("cn"))
    rendered_unexpected = str(unexpected_cal.value)
    assert 'mv.AlignmentPolicy(kind="calendar_bucket")' in rendered_unexpected


def test_lag_policy_supports_only_single_zero_offset_for_now():
    assert LagPolicy(mode="single", offset=0).offset == 0

    with pytest.raises(LagPolicyValidationError):
        LagPolicy(mode="single", offset=1)

    with pytest.raises(ValidationError):
        LagPolicy(mode="sweep", offset=0)


def test_lag_policy_validation_error_renders_fix_snippet():
    with pytest.raises(LagPolicyValidationError) as nonzero:
        LagPolicy(mode="single", offset=2)
    rendered = str(nonzero.value)
    assert 'mv.LagPolicy(mode="single", offset=0)' in rendered


def test_sampling_policy_defaults_and_forbids_extra():
    from marivo.analysis_py import SamplingPolicy

    policy = SamplingPolicy()
    assert policy.unit == "bucket"
    assert policy.method == "paired_numeric_summary"
    assert policy.pairing == "calendar_bucket"
    assert policy.null_handling == "drop_pair"
    assert policy.min_n == 3

    with pytest.raises(ValidationError):
        SamplingPolicy(extra_field=True)  # type: ignore[call-arg]


def test_promotion_policy_defaults_and_forbids_extra_fields():
    policy = PromotionPolicy()

    assert policy.auto_infer is True
    assert policy.on_missing == "fail_closed"
    assert policy.required_fields == []
    assert policy.semantic_anchors == PromotionSemanticAnchors()

    with pytest.raises(ValidationError):
        PromotionPolicy(on_missing="warn")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        PromotionPolicy(extra_field=True)  # type: ignore[call-arg]


def test_promotion_policy_accepts_typed_anchors_only():
    policy = PromotionPolicy(
        semantic_anchors=PromotionSemanticAnchors(
            metric=MetricRef("sales.revenue"),
            subject=DimensionRef("account"),
            time_axis=DimensionRef("order_day"),
            source_metric=ArtifactRef("frame_metric"),
            source_delta=ArtifactRef("frame_delta"),
            current=ArtifactRef("frame_current"),
            baseline=ArtifactRef("frame_baseline"),
            axis=DimensionRef("country"),
        ),
        required_fields=["measure_column", "semantic_model"],
    )

    assert policy.semantic_anchors.metric == MetricRef("sales.revenue")
    assert policy.semantic_anchors.current == ArtifactRef("frame_current")
    assert policy.required_fields == ["measure_column", "semantic_model"]

    with pytest.raises(ValidationError):
        PromotionSemanticAnchors(metric={"id": "sales.revenue", "extra": 1})


def test_promotion_failed_error_uses_metric_snippet_when_metric_field_is_later():
    error = PromotionFailedError(
        message="missing promotion metadata",
        details={"target_kind": "metric_frame", "missing": ["subject", "metric"]},
    )

    assert "session.promote_metric_frame(" in str(error)
