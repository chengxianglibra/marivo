"""Exact fixed Duration facts from bound physical source fields."""

from collections.abc import Iterable

import ibis.expr.types as ir

from marivo.datasource._expression_fields import physical_fields
from marivo.datasource.runtime.read_types import DURATION_UNIT_METADATA_KEY, BoundSource


def source_duration_units(sources: Iterable[BoundSource], value: ir.Value) -> tuple[str, ...]:
    """Recover fixed Duration units from consumed, already-bound source fields."""
    origins = set(physical_fields(value))
    units: set[str] = set()
    for bound in sources:
        for field in bound.facts.schema:
            unit = (field.metadata or {}).get(DURATION_UNIT_METADATA_KEY)
            if unit is not None and origins.intersection(
                physical_fields(bound.relation[field.name])
            ):
                units.add(unit.decode("ascii"))
    return tuple(sorted(units))
