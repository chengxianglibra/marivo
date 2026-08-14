"""Runtime compatibility behavior shared by Python 3.10 and newer."""

from __future__ import annotations

from datetime import timezone
from enum import Enum

from marivo._compat import UTC, StrEnum, tomllib


class _Example(StrEnum):
    VALUE = "value"


def test_compat_str_enum_matches_string_behavior() -> None:
    assert isinstance(_Example.VALUE, str)
    assert str(_Example.VALUE) == "value"
    assert f"{_Example.VALUE}" == "value"


def test_compat_utc_is_timezone_utc() -> None:
    assert UTC is timezone.utc


def test_compat_tomllib_loads_toml() -> None:
    assert tomllib.loads('[project]\nname = "marivo"\n') == {"project": {"name": "marivo"}}


def test_compat_str_enum_is_an_enum() -> None:
    assert issubclass(_Example, Enum)
