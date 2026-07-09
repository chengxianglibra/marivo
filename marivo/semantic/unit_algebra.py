"""Pure unit-combination algebra for derived and tier-1 metric units.

Single source of truth shared by the loader unit resolver, the validator
linear-commensurability check, and the authoring brief unit preview. These
functions operate on unit strings only — no registry or IR dependencies —
so they are trivially testable and reusable.

Conventions (see docs/specs/semantic/semantic-object-model.md):
- ``None`` means "no unit declared/derivable" (honest absence, never a guess).
- ``"1"`` is the UCUM dimensionless unit (a ratio of equal units cancels to it).
"""

from __future__ import annotations

from collections.abc import Sequence


def tier1_unit(agg_name: str, measure_unit: str | None) -> str | None:
    """Unit of a tier-1 metric: preserve the measure unit, except counts.

    ``sum``/``min``/``max``/``mean``/``median``/``percentile`` of values in unit
    U are still in unit U. ``count``/``count_distinct`` change dimension to a
    counted noun (e.g. ``{order}``), which is content-specific and not inferred
    here, so they yield ``None``.
    """
    if agg_name in ("count", "count_distinct"):
        return None
    return measure_unit


def ratio_unit(numerator_unit: str | None, denominator_unit: str | None) -> str | None:
    """Same known unit cancels to dimensionless ``"1"``; otherwise ``None``.

    Differing known units would form a compound (e.g. ``CNY/{user}``);
    constructing compound units is a non-goal, so they resolve to ``None`` and
    the author may declare one explicitly.
    """
    if numerator_unit is not None and numerator_unit == denominator_unit:
        return "1"
    return None


def weighted_average_unit(value_unit: str | None) -> str | None:
    """Weighting preserves the value's unit; the weight's unit is irrelevant."""
    return value_unit


def linear_unit(term_units: Sequence[str | None]) -> str | None:
    """Resolved unit for a linear composition, or ``None`` when unknown/conflicting.

    Any ``None`` term yields ``None`` (honest absence, not a conflict). All terms
    sharing one known unit yield that unit. ≥2 distinct known units yield ``None``
    here — the conflict is reported by the validator, not resolved.
    """
    units = list(term_units)
    if any(u is None for u in units):
        return None
    distinct = set(units)
    return next(iter(distinct)) if len(distinct) == 1 else None


def linear_units_conflict(term_units: Sequence[str | None]) -> bool:
    """True iff ≥2 distinct *known* units (incommensurable addition)."""
    known = {u for u in term_units if u is not None}
    return len(known) >= 2
