"""Bounded, deterministic unit algebra shared by catalog and runtime metrics.

The v2 algebra intentionally supports only dimensionless ``1``, products, and
one product division. Authoring-valid strings outside that grammar remain
opaque catalog metadata and cannot be combined automatically by a parent.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FactorizedUnitV2:
    """Canonical signed multiset representation of a factorable unit."""

    schema: Literal["metric-unit-algebra/v2"]
    numerator: tuple[str, ...]
    denominator: tuple[str, ...]

    def __post_init__(self) -> None:
        for atom in (*self.numerator, *self.denominator):
            if _parse_product(atom) != (atom,):
                raise ValueError(f"invalid MetricUnitAlgebraV2 atom: {atom!r}")
        canonical_numerator, canonical_denominator = _reduce_factors(
            self.numerator,
            self.denominator,
        )
        if self.numerator != canonical_numerator or self.denominator != canonical_denominator:
            raise ValueError("FactorizedUnitV2 factors must be reduced and bytewise sorted")

    def render(self) -> str:
        numerator = ".".join(self.numerator) or "1"
        if not self.denominator:
            return numerator
        return f"{numerator}/{'.'.join(self.denominator)}"


@dataclass(frozen=True)
class OpaqueUnitV2:
    """Authoring-valid unit that is outside the bounded factor grammar."""

    schema: Literal["metric-unit-opaque/v2"]
    value: str


@dataclass(frozen=True)
class UnknownUnitV2:
    """Explicit absence of a derivable unit."""

    schema: Literal["metric-unit-unknown/v2"]


type MetricUnitStateV2 = FactorizedUnitV2 | OpaqueUnitV2 | UnknownUnitV2

_UNKNOWN_UNIT = UnknownUnitV2(schema="metric-unit-unknown/v2")
_RESERVED_ATOM_CHARS = frozenset("./()")


def _bytewise_sorted(atoms: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(atoms, key=lambda atom: atom.encode("ascii")))


def _reduce_factors(
    numerator: Sequence[str],
    denominator: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    numerator_counts = Counter(numerator)
    denominator_counts = Counter(denominator)
    for atom in numerator_counts.keys() & denominator_counts.keys():
        cancelled = min(numerator_counts[atom], denominator_counts[atom])
        numerator_counts[atom] -= cancelled
        denominator_counts[atom] -= cancelled
    reduced_numerator = [atom for atom, count in numerator_counts.items() for _ in range(count)]
    reduced_denominator = [atom for atom, count in denominator_counts.items() for _ in range(count)]
    return _bytewise_sorted(reduced_numerator), _bytewise_sorted(reduced_denominator)


def _factorized(
    numerator: Sequence[str],
    denominator: Sequence[str] = (),
) -> FactorizedUnitV2:
    reduced_numerator, reduced_denominator = _reduce_factors(numerator, denominator)
    return FactorizedUnitV2(
        schema="metric-unit-algebra/v2",
        numerator=reduced_numerator,
        denominator=reduced_denominator,
    )


def _is_authoring_valid(unit: str) -> bool:
    return bool(unit) and all(0x21 <= ord(character) <= 0x7E for character in unit)


def _parse_product(product: str) -> tuple[str, ...] | None:
    if product == "1":
        return ()
    atoms = product.split(".")
    if not atoms or any(not atom for atom in atoms):
        return None
    for atom in atoms:
        if atom == "1" or any(character in _RESERVED_ATOM_CHARS for character in atom):
            return None
        if not all(0x21 <= ord(character) <= 0x7E for character in atom):
            return None
    return tuple(atoms)


def unit_state(unit: str | None) -> MetricUnitStateV2:
    """Classify a governed unit as factorized, opaque, or explicitly unknown."""
    if unit is None:
        return _UNKNOWN_UNIT
    if not _is_authoring_valid(unit):
        raise ValueError(
            f"unit must be a non-empty printable ASCII token without whitespace; got {unit!r}"
        )
    if unit == "1":
        return _factorized(())
    if unit.count("/") > 1:
        return OpaqueUnitV2(schema="metric-unit-opaque/v2", value=unit)
    numerator_text, separator, denominator_text = unit.partition("/")
    numerator = _parse_product(numerator_text)
    denominator = _parse_product(denominator_text) if separator else ()
    if numerator is None or denominator is None:
        return OpaqueUnitV2(schema="metric-unit-opaque/v2", value=unit)
    return _factorized(numerator, denominator)


def render_unit(state: MetricUnitStateV2) -> str | None:
    """Render a unit state without inventing text for unknown state."""
    match state:
        case FactorizedUnitV2():
            return state.render()
        case OpaqueUnitV2(value=value):
            return value
        case UnknownUnitV2():
            return None


def multiply_unit_states(
    left: MetricUnitStateV2,
    right: MetricUnitStateV2,
) -> MetricUnitStateV2:
    """Multiply two factorable units; opaque or unknown operands yield unknown."""
    if not isinstance(left, FactorizedUnitV2) or not isinstance(right, FactorizedUnitV2):
        return _UNKNOWN_UNIT
    return _factorized(
        (*left.numerator, *right.numerator),
        (*left.denominator, *right.denominator),
    )


def divide_unit_states(
    numerator: MetricUnitStateV2,
    denominator: MetricUnitStateV2,
) -> MetricUnitStateV2:
    """Divide two factorable units; opaque or unknown operands yield unknown."""
    if not isinstance(numerator, FactorizedUnitV2) or not isinstance(denominator, FactorizedUnitV2):
        return _UNKNOWN_UNIT
    return _factorized(
        (*numerator.numerator, *denominator.denominator),
        (*numerator.denominator, *denominator.numerator),
    )


def tier1_unit(agg_name: str, measure_unit: str | None) -> str | None:
    """Preserve the governed measure unit except for count aggregations."""
    if agg_name in ("count", "count_distinct"):
        return None
    return measure_unit


def ratio_unit(numerator_unit: str | None, denominator_unit: str | None) -> str | None:
    """Derive one canonical quotient using MetricUnitAlgebraV2."""
    return render_unit(divide_unit_states(unit_state(numerator_unit), unit_state(denominator_unit)))


def _comparable_unit_state(unit: str | None) -> MetricUnitStateV2:
    return unit_state(unit)


def linear_unit(term_units: Sequence[str | None]) -> str | None:
    """Return one commensurable unit or unknown for absent/conflicting terms."""
    units = tuple(term_units)
    if not units:
        return None
    states = tuple(_comparable_unit_state(unit) for unit in units)
    if any(isinstance(state, UnknownUnitV2) for state in states):
        return None
    first = states[0]
    if any(state != first for state in states[1:]):
        return None
    return render_unit(first)


def linear_units_conflict(term_units: Sequence[str | None]) -> bool:
    """Return whether at least two known terms have unequal unit states."""
    known = tuple(
        state
        for state in (_comparable_unit_state(unit) for unit in term_units)
        if not isinstance(state, UnknownUnitV2)
    )
    return bool(known) and any(state != known[0] for state in known[1:])


__all__ = [
    "FactorizedUnitV2",
    "MetricUnitStateV2",
    "OpaqueUnitV2",
    "UnknownUnitV2",
    "divide_unit_states",
    "linear_unit",
    "linear_units_conflict",
    "multiply_unit_states",
    "ratio_unit",
    "render_unit",
    "tier1_unit",
    "unit_state",
]
