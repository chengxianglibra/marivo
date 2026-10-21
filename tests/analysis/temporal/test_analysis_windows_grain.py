import pytest

from marivo._temporal import Grain
from marivo.analysis import grain


def test_keyword_construction_and_token():
    g = grain("minute", count=5)
    assert g._is_subday is True
    assert g._width_seconds() == 300
    assert g._to_token() == "5minute"
    assert grain("day", count=1)._to_token() == "day"


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
    assert grain(unit, count=count)._width_seconds() == seconds


@pytest.mark.parametrize("token", ["month", "quarter", "year"])
def test_grain_width_seconds_rejects_calendar_variable(token):
    with pytest.raises(ValueError, match="calendar-variable"):
        grain(token)._width_seconds()


def test_positional_construction_rejected():
    with pytest.raises(TypeError):
        Grain(5, "minute")  # type: ignore[misc, call-arg]


def test_calendar_multiples_rejected():
    with pytest.raises(ValueError):
        grain("week", count=2)


def test_count_must_be_positive():
    with pytest.raises(ValueError):
        grain("minute", count=0)


@pytest.mark.parametrize("operator", ["lt", "le", "gt", "ge"])
@pytest.mark.parametrize("units", [("minute", "hour"), ("week", "month"), ("day", "day")])
def test_grains_have_no_ordering(operator, units):
    import operator as comparisons

    with pytest.raises(TypeError):
        getattr(comparisons, operator)(grain(units[0]), grain(units[1]))


@pytest.mark.parametrize(
    "unit,count,repair",
    [
        ("minute", 7, "divide"),
        ("second", 60, "1minute"),
        ("second", 120, "2minute"),
        ("minute", 60, "1hour"),
        ("hour", 24, "timedelta"),
        ("minute", 1440, "timedelta"),
        ("second", 86400, "timedelta"),
    ],
)
def test_subday_grain_rejects_invalid_spelling_at_construction(unit, count, repair):
    with pytest.raises(ValueError, match=repair):
        grain(unit, count=count)


@pytest.mark.parametrize("unit,count,width", [("second", 90, 90), ("minute", 90, 5400)])
def test_subday_grain_admits_canonical_non_integral_larger_units(unit, count, width):
    value = grain(unit, count=count)
    assert value._width_seconds() == width
    assert value == grain(unit, count=count)
    assert hash(value) == hash(grain(unit, count=count))
