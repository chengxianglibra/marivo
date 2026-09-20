"""Single owner of decimal precision and scale derivation rules.

The rules are locked by the C4 plan's decimal derivation contract. Every
public function here is a pure, total rule over ``DecimalType`` operands and
returns ``None`` when the derivation is rejected (precision would exceed the
engines' 38-digit bound without a provably value-preserving cap). Callers turn
a ``None`` result into a structured semantic error; this module never renders
or raises errors and never silently truncates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

_DECIMAL_PRECISION_BOUND = 38


class DecimalPrecision(NamedTuple):
    """One resolved decimal ``(precision, scale)`` pair derived by the rules.

    Unlike :class:`DecimalType`, instances may exceed the 38-digit engine bound
    because the pair describes a declared fact, never an allocated engine
    type. Use :meth:`from_string` to parse ``"decimal(p, s)"`` strings.
    """

    precision: int
    scale: int

    @classmethod
    def from_string(cls, declared: str) -> DecimalPrecision:
        """Parse one ``decimal(p, s)`` type string into resolved precision facts.

        Example
        -------
        >>> DecimalPrecision.from_string("decimal(24, 4)")
        DecimalPrecision(precision=24, scale=4)

        Constraints
        -----------
        Rejects strings that are not fully resolved decimal type declarations;
        unresolved (precision-less) decimal strings never parse.
        """
        match = _DECLARED_DECIMAL.fullmatch(declared)
        if match is None:
            raise ValueError(
                f"expected a resolved decimal(p, s) type string; received {declared!r}"
            )
        return cls(int(match.group(1)), int(match.group(2)))


_DECLARED_DECIMAL = re.compile(r"decimal\((\d+), (\d+)\)")


@dataclass(frozen=True, order=True)
class DecimalType:
    """One resolved decimal precision and scale pair."""

    precision: int
    scale: int

    def __post_init__(self) -> None:
        if type(self.precision) is not int or type(self.scale) is not int:
            raise TypeError("decimal precision and scale must be integers")
        if self.precision < 1 or self.precision > _DECIMAL_PRECISION_BOUND:
            raise ValueError(
                f"decimal precision must be between 1 and {_DECIMAL_PRECISION_BOUND}; "
                f"received {self.precision}"
            )
        if self.scale > self.precision:
            raise ValueError(
                f"decimal scale must not exceed precision; received (p={self.precision}, "
                f"s={self.scale})"
            )

    def __str__(self) -> str:
        return f"decimal({self.precision}, {self.scale})"


def add_sub(left: DecimalType, right: DecimalType) -> DecimalType | None:
    """Derive the result type of decimal addition or subtraction.

    ``dec(max(p1-s1, p2-s2) + 1 + max(s1,s2), max(s1,s2))``; derivation above
    the 38-digit bound is rejected with ``None``.

    Example
    -------
    >>> add_sub(DecimalType(12, 2), DecimalType(4, 0))
    DecimalType(precision=14, scale=2)

    Constraints
    -----------
    ``None`` means the caller must raise a structured error, never truncate.
    """
    scale = max(left.scale, right.scale)
    precision = max(left.precision - left.scale, right.precision - right.scale) + 1 + scale
    return _resolve(precision, scale)


def multiply(left: DecimalType, right: DecimalType) -> DecimalType | None:
    """Derive the result type of decimal multiplication.

    ``dec(min(38, p1+p2), min(38, s1+s2))``; ``s1+s2 > 38`` rejects the
    derivation because capping the scale would change the produced values.

    Example
    -------
    >>> multiply(DecimalType(12, 2), DecimalType(4, 0))
    DecimalType(precision=16, scale=2)

    Constraints
    -----------
    ``None`` means the caller must raise a structured error, never truncate.
    """
    scale = left.scale + right.scale
    if scale > _DECIMAL_PRECISION_BOUND:
        return None
    precision = min(_DECIMAL_PRECISION_BOUND, left.precision + right.precision)
    return _resolve(precision, scale)


def sum_of(value: DecimalType) -> DecimalType:
    """Derive the aggregate-sum result type: ``dec(38, s)``."""
    return DecimalType(_DECIMAL_PRECISION_BOUND, value.scale)


def min_max(value: DecimalType) -> DecimalType:
    """Derive the min/max aggregate result type: the input type unchanged."""
    return value


def _resolve(precision: int, scale: int) -> DecimalType | None:
    if precision > _DECIMAL_PRECISION_BOUND:
        return None
    return DecimalType(precision, scale)
