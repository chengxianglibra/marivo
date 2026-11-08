from typing import Literal

import pytest

from marivo.analysis import grain


def test_keyword_construction_and_token() -> None:
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
def test_grain_width_seconds_fixed_size(
    token: Literal["second", "minute", "hour", "day", "week", "5minute", "2hour"], seconds: int
) -> None:
    count = 5 if token == "5minute" else 2 if token == "2hour" else 1
    units: dict[str, Literal["second", "minute", "hour", "day", "week"]] = {
        "second": "second",
        "minute": "minute",
        "hour": "hour",
        "day": "day",
        "week": "week",
        "5minute": "minute",
        "2hour": "hour",
    }
    unit = units[token]
    assert grain(unit, count=count)._width_seconds() == seconds


@pytest.mark.parametrize("unit,count,width", [("second", 90, 90), ("minute", 90, 5400)])
def test_subday_grain_admits_canonical_non_integral_larger_units(
    unit: Literal["second", "minute"], count: int, width: int
) -> None:
    value = grain(unit, count=count)
    assert value._width_seconds() == width
    assert value == grain(unit, count=count)
    assert hash(value) == hash(grain(unit, count=count))
