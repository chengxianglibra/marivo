"""Frozen source-free observation facts shared by Query coordinate domains."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, TypeAlias

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import BuiltinSampleCoordinate, MetricNode
from marivo._definition.graph import definition_fingerprint
from marivo._definition.observation_time import ContributionClockBinding, ObservationPeriod
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
    # Endpoint expansion can retain a schema-only read with no physical rule.
    # The authored rule already participates in the observation graph's identity.
    window_intent: ObservationWindow | None = field(
        default=None, compare=False, repr=False, metadata={"definition_identity": False}
    )

    @property
    def logical_window_rule(self) -> ObservationWindow | None:
        return self.window_rule or self.window_intent

    @property
    def window_group_id(self) -> tuple[int, ...] | None:
        """Identify the reduction before expansion into independent window reads."""
        return (
            (self.logical_occurrence or self.occurrence)
            if self.logical_window_rule is not None
            else None
        )

    @property
    def report_calendar(self) -> CalendarCoordinate | None:
        """Return this contribution's own report calendar, if it has one."""
        return next(
            (
                calendar
                for slot, calendar in self.calendars
                if isinstance(calendar, CalendarCoordinate) and slot not in self.sample_time_slots
            ),
            None,
        )

    @property
    def report_window_endpoint(self) -> WindowEndpoint | None:
        """Expose only endpoints whose contribution time range has been bound."""
        if (
            self.window_rule is None
            or self.period is None
            or self.logical_occurrence is None
            or self.sample_window is not None
            or self.window_coordinates
        ):
            return None
        return WindowEndpoint(self.window_key, self.period)


@dataclass(frozen=True, slots=True)
class WindowEndpoint:
    key: int | datetime | None
    period: ObservationPeriod


@dataclass(frozen=True, slots=True)
class WindowGroup:
    """The report rule and resolved endpoint grid for one logical reduction."""

    window_group_id: tuple[int, ...]
    window_rule: ObservationWindow
    report_calendar: CalendarCoordinate | None
    endpoint_grid: tuple[WindowEndpoint, ...]


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

    @property
    def window_groups(self) -> tuple[WindowGroup, ...]:
        """View window bindings without adding derived data to the saved identity."""
        groups: dict[tuple[int, ...], list[ContributionBinding]] = {}
        for contribution in self.contributions:
            group_id = contribution.window_group_id
            if group_id is not None:
                groups.setdefault(group_id, []).append(contribution)
        result: list[WindowGroup] = []
        report_calendar = next(
            (axis for axis in self.coordinates.axes if isinstance(axis, CalendarCoordinate)),
            None,
        )
        for group_id, members in groups.items():
            rule = members[0].logical_window_rule
            assert rule is not None
            endpoints = tuple(
                endpoint
                for member in members
                if (endpoint := member.report_window_endpoint) is not None
            )
            result.append(
                WindowGroup(
                    group_id, rule, members[0].report_calendar or report_calendar, endpoints
                )
            )
        return tuple(result)
