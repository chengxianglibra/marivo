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


@pytest.mark.parametrize(
    "token,seconds",
    [
        ("second", 1),
        ("minute", 60),
        ("hour", 3600),
        ("day", 86400),
        ("week", 604800),
        ("5minute", 300),
        ("2hour", 7200),
    ],
)
def test_grain_width_seconds_fixed_size(token, seconds):
    assert parse_grain_token(token).width_seconds() == seconds


@pytest.mark.parametrize("token", ["month", "quarter", "year"])
def test_grain_width_seconds_rejects_calendar_variable(token):
    with pytest.raises(ValueError, match="calendar-variable"):
        parse_grain_token(token).width_seconds()


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


# -- Grain ordering -----------------------------------------------------------


class TestGrainOrdering:
    """Grain supports __lt__/__gt__/__le__/__ge__ for granularity comparison."""

    def test_subday_same_unit(self):
        assert Grain(count=5, unit="minute") < Grain(count=15, unit="minute")
        assert Grain(count=15, unit="minute") > Grain(count=5, unit="minute")

    def test_subday_different_unit(self):
        assert Grain(count=1, unit="minute") < Grain(count=1, unit="hour")
        assert Grain(count=1, unit="hour") > Grain(count=1, unit="minute")

    def test_subday_vs_calendar(self):
        assert Grain(count=1, unit="hour") < Grain(count=1, unit="day")
        assert Grain(count=1, unit="day") > Grain(count=1, unit="hour")

    def test_calendar_ordering(self):
        assert Grain(count=1, unit="day") < Grain(count=1, unit="week")
        assert Grain(count=1, unit="week") < Grain(count=1, unit="month")
        assert Grain(count=1, unit="month") < Grain(count=1, unit="year")

    def test_le_ge(self):
        g5 = Grain(count=5, unit="minute")
        g15 = Grain(count=15, unit="minute")
        assert g5 <= g15
        assert g15 >= g5
        assert g5 <= Grain(count=5, unit="minute")

    def test_same_grain_not_lt_not_gt(self):
        g = Grain(count=1, unit="hour")
        assert not (g < g)
        assert not (g > g)

    def test_comparison_with_non_grain_returns_not_implemented(self):
        g = Grain(count=1, unit="hour")
        assert g.__lt__("hour") is NotImplemented
        assert g.__gt__("hour") is NotImplemented
