"""Pure declaration authority for source wall clocks and business dates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from marivo.semantic.ir import (
    DateParse,
    HourPrefixParse,
    PeriodCalendarIR,
    StrptimeParse,
    TargetDimensionContract,
    TimestampParse,
)

if TYPE_CHECKING:
    from marivo.semantic.validator import Registry


@dataclass(frozen=True, slots=True)
class DeclaredSourceTimezone:
    """The declaration supplying a source timezone, without physical inference."""

    timezone: str
    origin: Literal["field", "datasource"]
    inherited_from: str | None = None


def source_timezone_axis(registry: Registry, dimension_id: str) -> TargetDimensionContract:
    """Capture declaration facts without requiring a direct physical-column body."""
    from marivo.semantic.validator import normalize_target_dimension

    dimension = registry.dimensions[dimension_id]
    return normalize_target_dimension(
        registry, dimension_id, prepared_column=dimension.source_column or dimension.name
    )


def effective_source_timezone(
    registry: Registry, axis: TargetDimensionContract
) -> DeclaredSourceTimezone | None:
    """Resolve parser → Datasource authority, recursively through hour prefixes."""
    from marivo._timezones import normalize_timezone

    visited: set[str] = set()

    def resolve(current: TargetDimensionContract) -> DeclaredSourceTimezone | None:
        if current.ref.path in visited:
            return None
        visited.add(current.ref.path)
        parse = current.parse
        if isinstance(parse, HourPrefixParse):
            if parse.prefix not in registry.dimensions:
                return None
            inherited = resolve(source_timezone_axis(registry, parse.prefix))
            if inherited is None:
                return None
            return DeclaredSourceTimezone(inherited.timezone, inherited.origin, parse.prefix)
        if (
            isinstance(parse, DateParse | TimestampParse | StrptimeParse)
            and parse.timezone is not None
        ):
            return DeclaredSourceTimezone(normalize_timezone(parse.timezone).name, "field")
        entity = registry.entities.get(current.entity_ref.path)
        datasource = registry.datasources.get(entity.datasource) if entity is not None else None
        timezone = datasource.wall_clock_timezone if datasource is not None else None
        if timezone is None:
            return None
        return DeclaredSourceTimezone(normalize_timezone(timezone).name, "datasource")

    return resolve(axis)


def require_source_timezone(
    registry: Registry, axis: TargetDimensionContract, *, physical_type: str
) -> DeclaredSourceTimezone:
    """Require source authority before a business-data read."""
    from marivo.semantic.errors import SourceTimezoneMissing

    declaration = effective_source_timezone(registry, axis)
    if declaration is None:
        entity = registry.entities[axis.entity_ref.path]
        datasource = registry.datasources[entity.datasource]
        raise SourceTimezoneMissing(
            axis=axis,
            physical_type=physical_type,
            datasource=datasource.semantic_id,
            backend=datasource.backend_type,
        )
    return declaration


def period_calendar_timezone(registry: Registry, calendar: PeriodCalendarIR) -> str:
    """Derive a governed calendar boundary from its business-date field."""
    return require_source_timezone(
        registry, source_timezone_axis(registry, calendar.date), physical_type="date"
    ).timezone
