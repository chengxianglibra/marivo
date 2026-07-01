"""Tests for Subject.grain accepting both calendar and dynamic grain tokens."""

from marivo.analysis.evidence.types import Subject


def test_subject_accepts_dynamic_grain_token():
    s = Subject(metric="m", grain="5minute", analysis_axis="time")
    assert s.grain == "5minute"


def test_subject_accepts_calendar_grain_token():
    s = Subject(metric="m", grain="day", analysis_axis="time")
    assert s.grain == "day"
