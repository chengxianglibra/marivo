import pytest
from pydantic import ValidationError

import marivo.analysis as mv
from marivo.analysis.errors import AlignmentPolicyValidationError
from marivo.analysis.policies import (
    AlignmentKind,
    AlignmentPolicy,
    dow_aligned,
    holiday_aligned,
    holiday_and_dow_aligned,
    window_bucket,
)
from marivo.analysis.refs import ArtifactRef, CalendarRef
from marivo.semantic.catalog import SemanticKind, SemanticRef
from marivo.semantic.refs import make_ref


def test_refs_are_exported_and_preserve_ids():
    assert mv.AlignmentKind is AlignmentKind
    assert make_ref("sales.revenue", SemanticKind.METRIC).id == "sales.revenue"
    assert make_ref("region", SemanticKind.DIMENSION).id == "region"
    assert mv.SemanticRef is SemanticRef
    assert mv.CalendarRef("cn_holidays").id == "cn_holidays"
    assert make_ref("sales.revenue", SemanticKind.METRIC).id == "sales.revenue"
    assert make_ref("region", SemanticKind.DIMENSION).id == "region"
    assert CalendarRef("cn_holidays").id == "cn_holidays"


def test_artifact_ref_is_exported_and_preserves_id():
    assert mv.ArtifactRef is ArtifactRef
    assert ArtifactRef("frame_abc123").id == "frame_abc123"
    assert str(ArtifactRef("frame_abc123")) == "frame_abc123"


def test_refs_reject_empty_ids():
    for ref_cls in (CalendarRef, ArtifactRef):
        with pytest.raises(ValidationError):
            ref_cls(" ")


def test_refs_reject_extra_fields_with_validation_error():
    with pytest.raises(ValidationError):
        CalendarRef(id="cn", extra=1)


def test_alignment_policy_requires_calendar_for_calendar_backed_modes():
    assert AlignmentPolicy(kind="window_bucket").calendar is None

    with pytest.raises(AlignmentPolicyValidationError):
        AlignmentPolicy(kind="window_bucket", calendar=CalendarRef("cn"))

    with pytest.raises(AlignmentPolicyValidationError) as legacy:
        AlignmentPolicy(kind="calendar_bucket")  # type: ignore[arg-type]
    assert "window_bucket" in str(legacy.value)

    with pytest.raises(AlignmentPolicyValidationError):
        AlignmentPolicy(kind="dow_aligned")

    with pytest.raises(ValidationError):
        AlignmentPolicy(kind="dow_aligned", calendar={"id": "cn", "extra": 1})

    policy = AlignmentPolicy(kind="holiday_and_dow_aligned", calendar=CalendarRef("cn"))
    assert policy.kind == "holiday_and_dow_aligned"
    assert policy.calendar == CalendarRef("cn")
    assert policy.period == "month"
    assert policy.fallback == "drop"


def test_alignment_policy_helpers_match_explicit_constructors():
    calendar = CalendarRef("cn_holidays")

    cases = [
        (
            window_bucket(),
            AlignmentPolicy(kind="window_bucket"),
        ),
        (
            window_bucket(mode="calendar_bucket", strict_lengths=True),
            AlignmentPolicy(kind="window_bucket", mode="calendar_bucket", strict_lengths=True),
        ),
        (
            dow_aligned(calendar=calendar, period="week", fallback="nearest_prior_workday"),
            AlignmentPolicy(
                kind="dow_aligned",
                calendar=calendar,
                period="week",
                fallback="nearest_prior_workday",
            ),
        ),
        (
            holiday_aligned(calendar=calendar),
            AlignmentPolicy(kind="holiday_aligned", calendar=calendar),
        ),
        (
            holiday_and_dow_aligned(calendar=calendar, period="quarter"),
            AlignmentPolicy(kind="holiday_and_dow_aligned", calendar=calendar, period="quarter"),
        ),
    ]

    for helper_policy, explicit_policy in cases:
        assert helper_policy.model_dump(mode="json") == explicit_policy.model_dump(mode="json")


def test_calendar_alignment_helpers_reject_bare_string_calendar():
    with pytest.raises(ValidationError):
        dow_aligned(calendar="cn_holidays")  # type: ignore[arg-type]


def test_alignment_policy_validation_error_renders_fix_snippet():
    with pytest.raises(AlignmentPolicyValidationError) as missing_cal:
        AlignmentPolicy(kind="dow_aligned")
    rendered = str(missing_cal.value)
    assert "mv.dow_aligned(" in rendered
    assert 'mv.CalendarRef("cn_holidays")' in rendered

    with pytest.raises(AlignmentPolicyValidationError) as unexpected_cal:
        AlignmentPolicy(kind="window_bucket", calendar=CalendarRef("cn"))
    rendered_unexpected = str(unexpected_cal.value)
    assert "mv.window_bucket()" in rendered_unexpected


def test_lag_policy_is_not_public_policy():
    import marivo.analysis.policies as policies

    assert not hasattr(policies, "LagPolicy")


def test_sampling_policy_defaults_and_forbids_extra():
    from marivo.analysis import SamplingPolicy

    policy = SamplingPolicy()
    assert policy.unit == "bucket"
    assert policy.method == "paired_numeric_summary"
    assert policy.pairing == "window_bucket"
    assert policy.null_handling == "drop_pair"
    assert policy.min_n == 3

    with pytest.raises(ValidationError):
        SamplingPolicy(extra_field=True)  # type: ignore[call-arg]
