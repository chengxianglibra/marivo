"""Exact instants and built-in civil grains shared by scopes and grids."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic_core import CoreSchema, core_schema

_GRAIN_UNITS = frozenset({"second", "minute", "hour", "day", "week", "month", "quarter", "year"})


class InstantAnchor(datetime):
    """An immutable exact instant returned by Session.instant or scope boundaries.

    This datetime subtype preserves compatibility with existing temporal consumers.
    Construct through Session.instant; analysis constructors always return UTC.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"<InstantAnchor {self.isoformat()}; use .show()>"

    def show(self) -> None:
        """Display the exact instant.

        Args: None.
        Returns: None.
        Example: ``session.instant('2026-01-01').show()``.
        Constraints: Does not consult a clock, Session, or source.
        """
        print(self.isoformat())


def anchor_from_datetime(value: datetime) -> InstantAnchor:
    """Preserve an aware datetime as an exact UTC anchor without float conversion."""
    if value.utcoffset() is None:
        raise ValueError("an exact instant requires an aware datetime")
    utc = value.astimezone(timezone.utc)
    return InstantAnchor(
        utc.year,
        utc.month,
        utc.day,
        utc.hour,
        utc.minute,
        utc.second,
        utc.microsecond,
        tzinfo=timezone.utc,
    )


@dataclass(frozen=True, slots=True, init=False)
class Grain:
    """One immutable natural calendar grain, acquired through mv.grain(...)."""

    unit: str
    count: int

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("Grain values are returned by marivo.analysis.grain(...)")

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: object, _handler: object) -> CoreSchema:
        def validate(value: object, info: core_schema.ValidationInfo) -> Grain:
            if isinstance(value, Grain):
                return value
            if not isinstance(value, Mapping):
                raise ValueError("grain values must be returned Grain instances")
            if info.field_name is None:
                raise TypeError("grain values must be returned Grain instances")
            if (
                set(value) != {"schema", "kind", "unit", "count"}
                or value.get("schema") != "grain/v2"
                or value.get("kind") != "builtin"
            ):
                raise ValueError("unsupported Grain payload; obtain a natural Grain")
            return builtin_grain(value["unit"], count=value["count"])

        def serialize(value: Grain) -> dict[str, object]:
            return {
                "schema": "grain/v2",
                "kind": "builtin",
                "unit": value.unit,
                "count": value.count,
            }

        return core_schema.with_info_plain_validator_function(
            validate, serialization=core_schema.plain_serializer_function_ser_schema(serialize)
        )

    def _width_seconds(self) -> int:
        widths = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
        if self.unit not in widths:
            raise ValueError(
                f"Grain._width_seconds() is undefined for calendar-variable grain {self!r}"
            )
        return self.count * widths[self.unit]

    def __repr__(self) -> str:
        return f"Grain({grain_token(self)!r})"


def grain_token(grain: Grain) -> str:
    """Encode a natural Grain in the canonical calendar token."""
    return grain.unit if grain.count == 1 else f"{grain.count}{grain.unit}"


def builtin_grain(unit: str, *, count: int = 1) -> Grain:
    """Create the natural calendar value behind mv.grain."""
    if unit not in _GRAIN_UNITS or type(count) is not int or count < 1:
        raise ValueError("Grain requires a supported unit and count >= 1")
    if unit not in {"second", "minute", "hour"} and count != 1:
        raise ValueError(f"calendar grain {unit!r} only supports count == 1")
    if unit in _SUBDAY_UNIT_SECONDS:
        validate_subday_count(unit, count)
    value = object.__new__(Grain)
    object.__setattr__(value, "unit", unit)
    object.__setattr__(value, "count", count)
    return value


def grain_nests(inner: Grain, outer: Grain) -> bool:
    """Return the supported static natural calendar containment relation."""
    widths = {"second": 1, "minute": 60, "hour": 3600}
    if inner.unit in widths:
        return (
            outer.unit not in widths
            or (widths[outer.unit] * outer.count) % (widths[inner.unit] * inner.count) == 0
        )
    return inner == outer or (inner.unit, outer.unit) in {
        ("day", "week"),
        ("day", "month"),
        ("day", "quarter"),
        ("day", "year"),
        ("month", "quarter"),
        ("month", "year"),
        ("quarter", "year"),
    }


_CIVIL_DAY_SECONDS = 86_400
_SUBDAY_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3_600}


def validate_subday_count(unit: str, count: int) -> int:
    """Validate the unique supported spelling of a civil subday width."""
    if unit not in _SUBDAY_UNIT_SECONDS or type(count) is not int or count < 1:
        raise ValueError(
            "subday width requires second, minute or hour and a positive integer count"
        )
    width = _SUBDAY_UNIT_SECONDS[unit] * count
    if width >= _CIVIL_DAY_SECONDS:
        raise ValueError(
            "subday width must be less than one nominal day; choose civil day "
            "for calendar partitioning or timedelta for elapsed duration"
        )
    if unit == "second" and count % 60 == 0:
        replacement = f"{count // 3600}hour" if count % 3600 == 0 else f"{count // 60}minute"
        raise ValueError(f"subday width has a duplicate spelling; use {replacement}")
    if unit == "minute" and count % 60 == 0:
        raise ValueError(f"subday width has a duplicate spelling; use {count // 60}hour")
    if _CIVIL_DAY_SECONDS % width:
        raise ValueError("subday width must divide one civil day; choose a supported subdivision")
    return width


def civil_midnight_width_seconds(grain: Grain) -> int | None:
    """Return the validated width for a natural civil subday grain."""
    if grain.unit in _SUBDAY_UNIT_SECONDS:
        return validate_subday_count(grain.unit, grain.count)
    return None
