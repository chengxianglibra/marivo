"""Frozen source-free observation facts shared by Query coordinate domains."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    ContributionClockBinding,
    MetricNode,
)
from marivo._definition.graph import definition_fingerprint
from marivo._definition.population_types import (
    MembershipBinding,
    PopulationDependency,
    PredicateExpression,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    EntityIdentityRead,
    FieldRead,
    IntervalCoordinate,
)
from marivo._definition.windows import (
    ObservationWindow,
)
from marivo._definition.windows import (
    SampleWindowContext as SampleWindowContext,
)
from marivo._kernel.refs import ExactRef
from marivo._kernel.temporal import InstantAnchor

CalendarGrouping: TypeAlias = CalendarCoordinate | IntervalCoordinate | BuiltinSampleCoordinate
SampleAxis: TypeAlias = AxisBinding | CalendarGrouping
SampleIntervalRanges: TypeAlias = tuple[tuple[int, InstantAnchor, InstantAnchor], ...]


@dataclass(frozen=True, slots=True)
class SampleAxisMatch:
    """One shared complete axis, with exact interval ordinal translation if needed."""

    outer: int
    inner: int
    interval_keys: tuple[tuple[int, int], ...] | None = None


SampleAxisMatches: TypeAlias = tuple[SampleAxisMatch, ...]


@dataclass(frozen=True, slots=True)
class SampleKeyMatch:
    """One physical key equality within a shared axis."""

    outer: str
    inner: str
    interval_keys: tuple[tuple[int, int], ...] | None


def sample_key_matches(
    groups: tuple[tuple[str, ...], ...], matches: SampleAxisMatches
) -> tuple[SampleKeyMatch, ...]:
    """Expand each shared axis to every ordered component of its complete key."""
    return tuple(
        SampleKeyMatch(outer, inner, match.interval_keys)
        for match in matches
        for outer, inner in zip(groups[match.outer], groups[match.inner], strict=True)
    )


@dataclass(frozen=True, slots=True)
class SampleLayer:
    """One occurrence's sample axes and membership allocation."""

    axes: tuple[SampleAxis, ...]
    memberships: tuple[MembershipBinding, ...]
    interval_ordinals: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class CoordinateBinding:
    """Ordered complete output axes and each root's single-valued classification."""

    axes: tuple[ComparisonAxis, ...]
    mode: Literal["calendar", "grouped", "singleton"]
    population: PopulationDependency | None = None
    memberships: tuple[MembershipBinding, ...] = ()
    parent_scope: PopulationDependency | None = None


@dataclass(frozen=True, slots=True)
class ObservationPeriod:
    start: InstantAnchor | None
    end: InstantAnchor
    timezone: str


@dataclass(frozen=True, slots=True)
class ContributionBinding:
    """One source reduction usage, before any physical fusion or source reading."""

    occurrence: tuple[int, ...]
    definition: MetricNode
    root: ExactRef
    intrinsic_conditions: tuple[PredicateExpression, ...]
    common_condition: PredicateExpression | None
    reads: tuple[FieldRead, ...]
    coordinates: tuple[FieldRead | EntityIdentityRead, ...]
    time: ContributionClockBinding | None
    period: ObservationPeriod | None
    physical_dtype: Literal["unresolved"] = "unresolved"
    logical_occurrence: tuple[int, ...] | None = None
    window_key: int | datetime | None = None
    sample_axes: tuple[SampleAxis, ...] = ()
    memberships: tuple[MembershipBinding, ...] = ()
    output_axis_count: int = 0
    calendars: tuple[tuple[str, CalendarGrouping], ...] = ()
    coordinate_slots: tuple[str, ...] = ()
    sample_calendar_reads: tuple[tuple[str, FieldRead], ...] = ()
    population_reads: tuple[tuple[AxisBinding, FieldRead | EntityIdentityRead], ...] = ()
    window_rule: ObservationWindow | None = None
    sample_window: SampleWindowContext | None = None
    window_coordinates: tuple[tuple[str, int | datetime], ...] = ()
    metric: str = ""
    sample_time_slots: tuple[str, ...] = ()
    sample_interval_ranges: tuple[tuple[str, SampleIntervalRanges], ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationBinding:
    """One observation's conditions, frozen period, output domain and occurrences."""

    period: ObservationPeriod | None
    condition: PredicateExpression | None
    coordinates: CoordinateBinding
    contributions: tuple[ContributionBinding, ...]
    count_populations: tuple[tuple[PopulationDependency, PopulationDependency], ...] = ()
    calendar_samples: tuple[tuple[tuple[int, ...], BuiltinSampleCoordinate], ...] = ()
    pending_interval: int | None = None
    empty_interval: bool = False
    sample_matches: tuple[tuple[tuple[int, ...], SampleAxisMatches], ...] = ()

    @property
    def fingerprint(self) -> str:
        return definition_fingerprint(self)
