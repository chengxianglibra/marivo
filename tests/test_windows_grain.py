import pytest

from marivo.analysis.errors import GrainUnsupportedError
from marivo.analysis.windows.grain import (
    Grain,
    ensure_grain_supported,
    normalize_grain,
    parse_grain_token,
)


def test_keyword_construction_and_token():
    g = Grain(count=5, unit="minute")
    assert g.is_subday is True
    assert g.is_day is False
    assert g.width_seconds() == 300
    assert g.to_token() == "5minute"
    assert Grain(count=1, unit="day").to_token() == "day"


def test_positional_construction_rejected():
    with pytest.raises(TypeError):
        Grain(5, "minute")  # type: ignore[misc, call-arg]


def test_calendar_multiples_rejected():
    with pytest.raises(ValueError):
        Grain(count=2, unit="week")


def test_count_must_be_positive():
    with pytest.raises(ValueError):
        Grain(count=0, unit="minute")


def test_subday_width_must_divide_a_day():
    Grain(count=30, unit="minute")  # 1800s divides 86400 -> ok
    Grain(count=12, unit="hour")  # 43200s divides 86400 -> ok
    for bad in [(7, "minute"), (25, "minute"), (5, "hour")]:
        with pytest.raises(ValueError):
            Grain(count=bad[0], unit=bad[1])


def test_normalize_grain_forms():
    assert normalize_grain(None) is None
    assert normalize_grain("day") == Grain(count=1, unit="day")
    assert normalize_grain("5minute") == Grain(count=5, unit="minute")
    assert normalize_grain((10, "minute")) == Grain(count=10, unit="minute")
    assert normalize_grain(Grain(count=1, unit="hour")) == Grain(count=1, unit="hour")


def test_parse_grain_token_aliases():
    assert parse_grain_token("30s") == Grain(count=30, unit="second")
    assert parse_grain_token("5 min") == Grain(count=5, unit="minute")
    assert parse_grain_token("2hr") == Grain(count=2, unit="hour")
    with pytest.raises(ValueError):
        parse_grain_token("5 bananas")


def test_ensure_grain_supported_rules():
    # finer-than-base: sub-day request, calendar base
    with pytest.raises(GrainUnsupportedError):
        ensure_grain_supported(Grain(count=5, unit="minute"), "day")
    # not an integer multiple of base
    with pytest.raises(GrainUnsupportedError):
        ensure_grain_supported(Grain(count=90, unit="second"), "minute")
    with pytest.raises(GrainUnsupportedError):
        ensure_grain_supported(Grain(count=5, unit="minute"), "hour")
    # accepted
    ensure_grain_supported(Grain(count=5, unit="minute"), "minute")
    ensure_grain_supported(Grain(count=90, unit="second"), "second")
    ensure_grain_supported(Grain(count=1, unit="day"), "hour")  # coarser calendar request
    # calendar request finer than calendar base
    with pytest.raises(GrainUnsupportedError):
        ensure_grain_supported(Grain(count=1, unit="day"), "month")


def test_ensure_grain_supported_unknown_base_granularity_lists_supported_values():
    with pytest.raises(ValueError) as exc_info:
        ensure_grain_supported(Grain(count=5, unit="minute"), "5min")

    assert "unknown base granularity '5min'" in str(exc_info.value)
    assert "supported granularity: year, quarter, month, week, day, hour, minute, second" in str(
        exc_info.value
    )
