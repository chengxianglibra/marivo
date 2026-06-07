from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from marivo.analysis.errors import GrainUnsupportedError

GrainUnit = Literal["second", "minute", "hour", "day", "week", "month", "quarter", "year"]

_UNIT_RANK: dict[str, int] = {
    "second": 0,
    "minute": 1,
    "hour": 2,
    "day": 3,
    "week": 4,
    "month": 5,
    "quarter": 6,
    "year": 7,
}
_SUPPORTED_GRANULARITIES = (
    "year",
    "quarter",
    "month",
    "week",
    "day",
    "hour",
    "minute",
    "second",
)
_SUBDAY_UNITS: frozenset[str] = frozenset({"second", "minute", "hour"})
_UNIT_SECONDS: dict[str, int] = {"second": 1, "minute": 60, "hour": 3600}
_TRUNCATE_CODE: dict[str, str] = {
    "second": "s",
    "minute": "m",
    "hour": "h",
    "day": "D",
    "week": "W",
    "month": "M",
    "quarter": "Q",
    "year": "Y",
}
_DAY_SECONDS = 86_400

_ALIASES: dict[str, str] = {
    "s": "second",
    "sec": "second",
    "secs": "second",
    "second": "second",
    "seconds": "second",
    "min": "minute",
    "mins": "minute",
    "minute": "minute",
    "minutes": "minute",
    "h": "hour",
    "hr": "hour",
    "hrs": "hour",
    "hour": "hour",
    "hours": "hour",
    "day": "day",
    "days": "day",
    "week": "week",
    "weeks": "week",
    "month": "month",
    "months": "month",
    "quarter": "quarter",
    "quarters": "quarter",
    "year": "year",
    "years": "year",
}

_TOKEN_RE = re.compile(r"^\s*(\d+)?\s*([A-Za-z]+)\s*$")


class Grain(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int = 1
    unit: GrainUnit

    @model_validator(mode="after")
    def _check_invariants(self) -> Grain:
        if self.count < 1:
            raise ValueError(f"Grain.count must be >= 1; got {self.count}")
        if self.unit not in _SUBDAY_UNITS and self.count != 1:
            raise ValueError(
                f"calendar grain {self.unit!r} only supports count == 1; got {self.count}"
            )
        if self.unit in _SUBDAY_UNITS:
            width = self.count * _UNIT_SECONDS[self.unit]
            if _DAY_SECONDS % width != 0:
                raise ValueError(
                    f"sub-day grain {self.count}{self.unit} ({width}s) must divide a day evenly"
                )
        return self

    @property
    def is_subday(self) -> bool:
        return self.unit in _SUBDAY_UNITS

    @property
    def is_day(self) -> bool:
        return self.count == 1 and self.unit == "day"

    def width_seconds(self) -> int:
        if not self.is_subday:
            raise ValueError(f"width_seconds is undefined for calendar grain {self.unit!r}")
        return self.count * _UNIT_SECONDS[self.unit]

    def to_token(self) -> str:
        return self.unit if self.count == 1 else f"{self.count}{self.unit}"

    def __lt__(self, other: object) -> bool:
        """True if this grain represents a finer (shorter) duration than *other*."""
        if not isinstance(other, Grain):
            return NotImplemented
        if self.is_subday and other.is_subday:
            return self.width_seconds() < other.width_seconds()
        return _UNIT_RANK[self.unit] < _UNIT_RANK[other.unit]

    def __gt__(self, other: object) -> bool:
        """True if this grain represents a coarser (longer) duration than *other*."""
        if not isinstance(other, Grain):
            return NotImplemented
        if self.is_subday and other.is_subday:
            return self.width_seconds() > other.width_seconds()
        return _UNIT_RANK[self.unit] > _UNIT_RANK[other.unit]

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Grain):
            return NotImplemented
        return not self.__gt__(other)

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Grain):
            return NotImplemented
        return not self.__lt__(other)


GrainInput = Grain | tuple[int, str] | str | None


def parse_grain_token(text: str) -> Grain:
    match = _TOKEN_RE.match(text)
    if match is None:
        raise ValueError(f"invalid grain token {text!r}")
    count_str, unit_raw = match.groups()
    unit = _ALIASES.get(unit_raw.lower())
    if unit is None:
        raise ValueError(f"unknown grain unit in token {text!r}")
    count = int(count_str) if count_str is not None else 1
    return Grain(count=count, unit=unit)  # type: ignore[arg-type]


def normalize_grain(value: GrainInput) -> Grain | None:
    if value is None:
        return None
    if isinstance(value, Grain):
        return value
    if isinstance(value, str):
        return parse_grain_token(value)
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ValueError(f"grain tuple must be (count, unit); got {value!r}")
        count, unit = value
        return Grain(count=int(count), unit=unit)  # type: ignore[arg-type]
    raise TypeError(f"unsupported grain input type {type(value).__name__}")


def ensure_grain_supported(grain: Grain, base_granularity: str) -> None:
    base = base_granularity
    if base not in _UNIT_RANK:
        supported = ", ".join(_SUPPORTED_GRANULARITIES)
        raise ValueError(f"unknown base granularity {base!r}; supported granularity: {supported}")
    if grain.is_subday:
        if base not in _SUBDAY_UNITS:
            raise GrainUnsupportedError(
                message=(
                    f"requested grain {grain.to_token()!r} is finer than the time field "
                    f"base granularity {base!r}"
                ),
                hint=f"Use a grain of {base!r} or coarser.",
                details={"kind": "GrainFinerThanBase", "requested": grain.to_token(), "base": base},
            )
        base_w = _UNIT_SECONDS[base]
        if grain.width_seconds() % base_w != 0:
            raise GrainUnsupportedError(
                message=(
                    f"requested grain {grain.to_token()!r} is not an integer multiple of the "
                    f"base granularity {base!r}"
                ),
                hint=f"Choose a width that is a whole multiple of {base_w} seconds.",
                details={
                    "kind": "GrainNotMultipleOfBase",
                    "requested": grain.to_token(),
                    "base": base,
                },
            )
    elif _UNIT_RANK[grain.unit] < _UNIT_RANK[base]:
        raise GrainUnsupportedError(
            message=(
                f"requested grain {grain.to_token()!r} is finer than the time field "
                f"base granularity {base!r}"
            ),
            hint=f"Use a grain of {base!r} or coarser.",
            details={"kind": "GrainFinerThanBase", "requested": grain.to_token(), "base": base},
        )
