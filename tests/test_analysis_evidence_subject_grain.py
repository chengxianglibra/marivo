"""Tests for Subject.grain accepting both calendar and dynamic grain tokens."""

from tests.shared_fixtures import make_test_subject


def test_subject_accepts_dynamic_grain_token():
    s = make_test_subject(metric_id="m", grain="5minute", analysis_axis="time")
    assert s.grain == "5minute"


def test_subject_accepts_calendar_grain_token():
    s = make_test_subject(metric_id="m", grain="day", analysis_axis="time")
    assert s.grain == "day"
