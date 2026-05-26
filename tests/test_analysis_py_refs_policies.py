import pytest
from pydantic import ValidationError

import marivo.analysis_py as mv
from marivo.analysis_py.policies import AlignmentKind, AlignmentPolicy, LagPolicy
from marivo.analysis_py.refs import CalendarRef, DimensionRef, MetricRef


def test_refs_are_exported_and_preserve_ids():
    assert mv.AlignmentKind is AlignmentKind
    assert mv.MetricRef("sales.revenue").id == "sales.revenue"
    assert mv.DimensionRef("region").id == "region"
    assert mv.CalendarRef("cn_holidays").id == "cn_holidays"
    assert MetricRef("sales.revenue").id == "sales.revenue"
    assert DimensionRef("region").id == "region"
    assert CalendarRef("cn_holidays").id == "cn_holidays"


def test_refs_reject_empty_ids():
    for ref_cls in (MetricRef, DimensionRef, CalendarRef):
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

    with pytest.raises(ValidationError):
        AlignmentPolicy(kind="calendar_bucket", calendar=CalendarRef("cn"))

    with pytest.raises(ValidationError):
        AlignmentPolicy(kind="dow_aligned")

    with pytest.raises(ValidationError):
        AlignmentPolicy(kind="dow_aligned", calendar={"id": "cn", "extra": 1})

    policy = AlignmentPolicy(kind="holiday_and_dow_aligned", calendar=CalendarRef("cn"))
    assert policy.kind == "holiday_and_dow_aligned"
    assert policy.calendar == CalendarRef("cn")
    assert policy.period == "month"
    assert policy.fallback == "drop"


def test_lag_policy_supports_only_single_zero_offset_for_now():
    assert LagPolicy(mode="single", offset=0).offset == 0

    with pytest.raises(ValidationError):
        LagPolicy(mode="single", offset=1)

    with pytest.raises(ValidationError):
        LagPolicy(mode="sweep", offset=0)


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
