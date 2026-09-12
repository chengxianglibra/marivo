import pytest

from marivo._fixed_duration import fixed_duration_seconds
from marivo._temporal import Grain
from marivo.analysis import grain


def test_keyword_construction_and_token():
    g = grain("minute", count=5)
    assert g.is_subday is True
    assert g.is_day is False
    assert g.width_seconds() == 300
    assert g.to_token() == "5minute"
    assert grain("day", count=1).to_token() == "day"


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
    count, unit = (
        (5, "minute") if token == "5minute" else (2, "hour") if token == "2hour" else (1, token)
    )
    assert grain(unit, count=count).width_seconds() == seconds


@pytest.mark.parametrize("token", ["month", "quarter", "year"])
def test_grain_width_seconds_rejects_calendar_variable(token):
    with pytest.raises(ValueError, match="calendar-variable"):
        grain(token).width_seconds()


def test_fixed_duration_seconds_rejects_unsupported_unit():
    with pytest.raises(ValueError, match="unsupported fixed duration unit"):
        fixed_duration_seconds(1, "month")


def test_positional_construction_rejected():
    with pytest.raises(TypeError):
        Grain(5, "minute")  # type: ignore[misc, call-arg]


def test_calendar_multiples_rejected():
    with pytest.raises(ValueError):
        grain("week", count=2)


def test_count_must_be_positive():
    with pytest.raises(ValueError):
        grain("minute", count=0)


def test_subday_value_does_not_claim_calendar_containment():
    for count, unit, width in [
        (30, "minute", 1800),
        (12, "hour", 43200),
        (7, "minute", 420),
        (25, "minute", 1500),
        (5, "hour", 18000),
    ]:
        value = grain(unit, count=count)
        assert value.width_seconds() == width
        assert value.to_token() == f"{count}{unit}"


# -- Grain ordering -----------------------------------------------------------


class TestGrainOrdering:
    """Grain supports __lt__/__gt__/__le__/__ge__ for granularity comparison."""

    def test_subday_same_unit(self):
        assert grain("minute", count=5) < grain("minute", count=15)
        assert grain("minute", count=15) > grain("minute", count=5)

    def test_subday_different_unit(self):
        assert grain("minute", count=1) < grain("hour", count=1)
        assert grain("hour", count=1) > grain("minute", count=1)

    def test_subday_vs_calendar(self):
        assert grain("hour", count=1) < grain("day", count=1)
        assert grain("day", count=1) > grain("hour", count=1)

    def test_calendar_ordering(self):
        assert grain("day", count=1) < grain("week", count=1)
        assert grain("week", count=1) < grain("month", count=1)
        assert grain("month", count=1) < grain("year", count=1)

    def test_le_ge(self):
        g5 = grain("minute", count=5)
        g15 = grain("minute", count=15)
        assert g5 <= g15
        assert g15 >= g5
        assert g5 <= grain("minute", count=5)

    def test_same_grain_not_lt_not_gt(self):
        g = grain("hour", count=1)
        assert not (g < g)
        assert not (g > g)

    def test_comparison_with_non_grain_returns_not_implemented(self):
        g = grain("hour", count=1)
        assert g.__lt__("hour") is NotImplemented
        assert g.__gt__("hour") is NotImplemented
