"""_encode_window_bound: ISO string -> physical value per time field format."""

import pytest

from marivo.analysis_py.errors import WindowInvalidError
from marivo.analysis_py.executor.runner import _encode_window_bound


class FakeMeta:
    def __init__(self, data_type, format=None):
        self.data_type = data_type
        self.format = format


def test_date_passthrough():
    assert "2026" in str(_encode_window_bound("2026-07-01", FakeMeta("date")))


def test_timestamp_passthrough():
    assert "2026" in str(_encode_window_bound("2026-07-01T10:00:00", FakeMeta("timestamp")))


def test_string_yyyymmdd_encoding():
    assert _encode_window_bound("2026-07-01", FakeMeta("string", "yyyymmdd")) == "20260701"


def test_string_dashed_encoding_is_identity():
    assert _encode_window_bound("2026-07-01", FakeMeta("string", "yyyy-mm-dd")) == "2026-07-01"


def test_integer_yyyymmdd_encoding():
    assert _encode_window_bound("2026-07-01", FakeMeta("integer", "yyyymmdd")) == 20260701


def test_integer_epoch_seconds_encoding():
    out = _encode_window_bound("2026-07-01T00:00:00+00:00", FakeMeta("integer", "epoch_seconds"))
    assert out == 1782864000


def test_hh_format_raises():
    with pytest.raises(WindowInvalidError) as exc:
        _encode_window_bound("10", FakeMeta("integer", "hh"))
    assert "v1" in str(exc.value).lower() or "unsupported" in str(exc.value).lower()


def test_unknown_format_raises():
    with pytest.raises(WindowInvalidError):
        _encode_window_bound("2026-07-01", FakeMeta("string", "made_up"))
