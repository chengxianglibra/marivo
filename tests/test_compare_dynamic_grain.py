"""Tests for sub-day (dynamic) grain support in compare window_bucket alignment."""

from datetime import datetime

import pandas as pd
import pytest

from marivo.analysis.errors import AlignmentFailedError
from marivo.analysis.intents.compare import (
    _advance_bucket_datetime,
    _bucket_key,
    _window_bucket_values,
)
from marivo.analysis.windows.grain import Grain


class _FakeMeta:
    def __init__(self, start, end, grain):
        self.window = {"start": start, "end": end}
        self.axes = {"time": {"role": "time", "grain": grain}}


class _FakeFrame:
    def __init__(self, start, end, grain):
        self.meta = _FakeMeta(start, end, grain)
        self.ref = "frame_test"


def _window_bucket_values_for(*, start, end, grain):
    return _window_bucket_values(_FakeFrame(start, end, grain))


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


def test_window_bucket_values_safety_cap():
    with pytest.raises(AlignmentFailedError):
        _window_bucket_values_for(
            start="2026-06-03 00:00:00", end="2030-06-03 00:00:00", grain="1second"
        )
