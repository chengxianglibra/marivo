import pytest

from marivo.semantic.time_format import normalize_strptime


def test_normalize_strips_whitespace():
    assert normalize_strptime("  %Y%m%d  ") == "%Y%m%d"


def test_normalize_returns_valid_strptime_unchanged():
    assert normalize_strptime("%Y%m%d") == "%Y%m%d"
    assert normalize_strptime("%Y-%m-%d") == "%Y-%m-%d"
    assert normalize_strptime("%Y/%m/%d") == "%Y/%m/%d"
    assert normalize_strptime("%Y%m%d%H") == "%Y%m%d%H"
    assert normalize_strptime("%Y-%m-%d-%H") == "%Y-%m-%d-%H"
    assert normalize_strptime("%Y%m%d-%H") == "%Y%m%d-%H"
    assert normalize_strptime("%Y%m%dT%H") == "%Y%m%dT%H"
    assert normalize_strptime("%Y%m%d%H%M") == "%Y%m%d%H%M"
    assert normalize_strptime("%Y-%m-%d %H:%M") == "%Y-%m-%d %H:%M"
    assert normalize_strptime("%Y-%m-%d %H:%M:%S") == "%Y-%m-%d %H:%M:%S"
    assert normalize_strptime("%Y%m") == "%Y%m"


def test_normalize_rejects_non_percent_prefixed_input():
    """Shorthand aliases like 'yyyymmdd' are no longer accepted."""
    with pytest.raises(ValueError, match="%"):
        normalize_strptime("yyyymmdd")
    with pytest.raises(ValueError, match="%"):
        normalize_strptime("hh")
    with pytest.raises(ValueError, match="%"):
        normalize_strptime("int")


def test_normalize_rejects_invalid_strptime_syntax():
    with pytest.raises(ValueError):
        normalize_strptime("%Q")  # Not a real strptime directive
