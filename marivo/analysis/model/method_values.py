"""Immutable scalar method facts with exact native validation and bounded display."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from decimal import Decimal
from itertools import islice
from math import isfinite
from typing import NoReturn, TypeAlias

from marivo.analysis.model.data_types import (
    DataColumn,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.model.errors import reject

_MethodScalar: TypeAlias = bool | int | float | str | date | Decimal | None


def _fail(
    expected: str,
    received: str,
    location: str = "frame.method_facts",
    *,
    canonical_id: str = "analysis_frame.method_facts",
) -> NoReturn:
    reject(
        expected,
        received,
        "Retain the exact closed facts from the completed Attribute result.",
        location,
        canonical_id=canonical_id,
    )


def _text(value: str) -> None:
    if type(value) is not str or not value or any(c in value for c in "\n\r\t"):
        _fail("a nonempty single-line fact identity", type(value).__name__)


def _count(value: int) -> None:
    if type(value) is not int or value < 0:
        _fail("an exact nonnegative integer count or byte size", repr(value))


def _digest_identity(value: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        _fail("a nonexecutable lowercase SHA256 formula identity", repr(value))


def _short(value: str, limit: int = 100) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _cell(value: _MethodScalar) -> str:
    if isinstance(value, str):
        return repr(_short(value, 70))
    if isinstance(value, Decimal):
        sign, digits, exponent = value.as_tuple()
        if len(digits) > 80:
            return f"Decimal(sign={sign}, digits={len(digits)}, exponent={exponent})"
    return repr(value)


def _coefficient_text(value: int | float | Decimal) -> str:
    if isinstance(value, int) and value.bit_length() > 256:
        return f"int(bits={value.bit_length()})"
    return _cell(value)


def _scalar(column: DataColumn, value: _MethodScalar) -> None:
    if type(column) is not DataColumn:
        _fail("a concrete DataColumn", type(column).__name__)
    if value is None:
        if not column.nullable:
            _fail("a nonnull value for a nonnullable column", column.name)
        return
    dtype = column.dtype
    valid = False
    if isinstance(dtype, ScalarDataType):
        if dtype.name == "int64":
            valid = type(value) is int and -(2**63) <= value < 2**63
        elif dtype.name == "float64":
            valid = type(value) is float and isfinite(value)
        elif dtype.name == "boolean":
            valid = type(value) is bool
        elif dtype.name == "string":
            valid = type(value) is str
        else:
            valid = type(value) is date
    elif isinstance(dtype, (TimestampDataType, DurationDataType)):
        valid = type(value) is int and -(2**63) <= value < 2**63
    elif type(value) is Decimal and value.is_finite():
        _, digits, exponent = value.as_tuple()
        assert isinstance(exponent, int)
        # Check representability without rounding or changing the original Decimal.
        trailing = max(0, -exponent - dtype.scale)
        valid = (not trailing or all(d == 0 for d in digits[-trailing:])) and (
            value.is_zero() or len(digits) + exponent <= dtype.precision - dtype.scale
        )
    if not valid:
        _fail("an exact finite scalar matching column type, precision and time unit", column.name)


class _FactRecord:
    __slots__ = ()

    def _summary(self) -> str:
        return f"kind={type(self).__name__}"

    def _lines(self) -> Iterator[str]:
        assert is_dataclass(self)
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, _FactRecord):
                yield f"{item.name}: {value!r}"
                yield from value._lines()
            elif isinstance(value, tuple):
                for index, child in enumerate(value):
                    yield f"{item.name}[{index}]={_short(repr(child), 160)}"
                    if isinstance(child, _FactRecord):
                        yield from child._lines()
            else:
                shown = (
                    _coefficient_text(value)
                    if type(value) in (int, float, Decimal)
                    else repr(value)
                )
                yield f"{item.name}={_short(shown, 200)}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({_short(self._summary(), 120)}; .show())"

    def show(self, *, n: int = 20) -> None:
        """Print a bounded view of already-owned facts.

        Args:
            n: Exact nonnegative int excluding bool; zero shows fields and counts.
        Returns:
            None; prints at most n detail lines, with each line bounded.
        Example:
            ``facts.show(n=0)``.
        Constraints: Reads only these immutable records; performs no source or file I/O.
        """
        if type(n) is not int or n < 0:
            _fail(
                "a nonnegative integer preview count",
                repr(n),
                "frame.method_facts.show",
                canonical_id="frame_method_facts.show",
            )
        print(f"{type(self).__name__}: {_short(self._summary(), 180)}")
        assert is_dataclass(self)
        print("Fields: " + ", ".join(field.name for field in fields(self)))
        for line in islice(self._lines(), n):
            print(_short(line, 240))


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodValue(_FactRecord):
    """One exact physical value. Args: column and value; e.g. ``FrameMethodValue(c, 1)``.

    The frozen result preserves nullable values, Decimal precision and integer time ticks.
    """

    column: DataColumn = field(
        metadata={
            "meaning": "Read method_value.column for this saved value's exact physical dtype, unit and nullability. This is read-only saved metadata; nested dtype implementations are not public constructors."
        }
    )
    value: bool | int | float | str | date | Decimal | None

    def __post_init__(self) -> None:
        _scalar(self.column, self.value)

    def _summary(self) -> str:
        return f"kind=value, column={_short(self.column.name)!r}"

    def _lines(self) -> Iterator[str]:
        yield f"{_short(self.column.name)} = {_cell(self.value)}; dtype={self.column.dtype!r}; unit={self.column.unit!r}"
