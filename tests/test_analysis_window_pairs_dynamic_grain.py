"""Tests for sub-day (dynamic) grain support in compare window_bucket alignment."""

from datetime import datetime

import pandas as pd
import pytest

from marivo.analysis.errors import AlignmentFailedError
from marivo.analysis.intents._window_pairs import (
    _advance_bucket_datetime,
    _bucket_key,
    _window_bucket_values,
)
from marivo.analysis.windows.grain import Grain


class _FakeMeta:
    def __init__(self, start, end, grain, report_tz=None):
        self.window = {"start": start, "end": end}
        self.axes = {"time": {"role": "time", "grain": grain}}
        self.report_tz = report_tz


class _FakeFrame:
    def __init__(self, start, end, grain, report_tz=None):
        self.meta = _FakeMeta(start, end, grain, report_tz)
        self.ref = "frame_test"


def _window_bucket_values_for(*, start, end, grain, report_tz=None):
    return _window_bucket_values(_FakeFrame(start, end, grain, report_tz))


def test_bucket_key_subday_floors_to_width():
    key = _bucket_key(pd.Timestamp("2026-06-03 00:07:30"), grain="5minute")
    assert key == "2026-06-03T00:05:00"


def test_advance_bucket_datetime_steps_width():
    nxt = _advance_bucket_datetime(
        datetime(2026, 6, 3, 0, 5, 0), grain=Grain(count=5, unit="minute")
    )
    assert nxt == datetime(2026, 6, 3, 0, 10, 0)


def test_window_bucket_values_subday_sequence():
    values = _window_bucket_values_for(
        start="2026-06-03 00:00:00", end="2026-06-03 00:25:00", grain="5minute"
    )
    assert [str(v) for v in values] == [
        "2026-06-03 00:00:00",
        "2026-06-03 00:05:00",
        "2026-06-03 00:10:00",
        "2026-06-03 00:15:00",
        "2026-06-03 00:20:00",
    ]


@pytest.mark.parametrize(
    ("grain", "start", "end", "expected"),
    [
        ("day", "2026-09-01T00:00:00", "2026-09-01T22:00:00", ["2026-09-01"]),
        ("week", "2026-09-02", "2026-09-04", ["2026-08-31"]),
        ("month", "2026-09-01", "2026-09-15", ["2026-09-01"]),
        ("quarter", "2026-08-15", "2026-09-15", ["2026-07-01"]),
        ("year", "2026-03-01", "2026-06-01", ["2026-01-01"]),
    ],
)
def test_window_bucket_values_calendar_grain_keeps_partial_period(grain, start, end, expected):
    values = _window_bucket_values_for(start=start, end=end, grain=grain)

    assert [str(value) for value in values] == expected


@pytest.mark.parametrize(
    ("end", "expected"),
    [
        ("2026-09-02T00:00:00", ["2026-09-01"]),
        ("2026-09-02T12:00:00", ["2026-09-01", "2026-09-02"]),
    ],
)
def test_window_bucket_values_calendar_grain_respects_precise_end(end, expected):
    values = _window_bucket_values_for(
        start="2026-09-01T12:00:00",
        end=end,
        grain="day",
    )

    assert [str(value) for value in values] == expected


def test_window_bucket_values_normalizes_aware_bounds_to_report_timezone():
    local = _window_bucket_values_for(
        start="2026-09-01T00:00:00+08:00",
        end="2026-09-01T22:00:00+08:00",
        grain="day",
        report_tz="Asia/Shanghai",
    )
    utc = _window_bucket_values_for(
        start="2026-08-31T16:00:00Z",
        end="2026-09-01T14:00:00Z",
        grain="day",
        report_tz="Asia/Shanghai",
    )

    assert local == utc == [datetime(2026, 9, 1).date()]


def test_window_bucket_values_safety_cap():
    with pytest.raises(AlignmentFailedError):
        _window_bucket_values_for(
            start="2026-06-03 00:00:00", end="2030-06-03 00:00:00", grain="1second"
        )
