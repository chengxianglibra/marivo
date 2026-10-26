"""Frozen source-free observation facts shared by Query coordinate domains."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import MetricNode, PredicateExpression, Reducer, TimeBinding
from marivo._definition.relations import (
    MembershipBinding,
    PopulationDependency,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    IdentityRead,
    IntervalCoordinate,
)
from marivo._definition.windows import ObservationWindow
from marivo._temporal import InstantAnchor


@dataclass(frozen=True, slots=True)
class SampleLayer:
    """One occurrence's complete sample axes, allocation and coverage policy."""

    axes: tuple[AxisBinding | CalendarCoordinate | IntervalCoordinate, ...]
    memberships: tuple[MembershipBinding, ...]
    occurrence: tuple[int, ...]
    reducer: Reducer | Literal["weighted_mean"]

    @property
    def requires_coverage(self) -> bool:
        return self.reducer not in ("mean", "weighted_mean")


@dataclass(frozen=True, slots=True)
class SampleCoverage:
    """Expected samples and optional raw-wall coverage on one internal key."""

    key: str
    points: tuple[datetime, ...]
    output_key: int | None = None
    output_slot: str = "key.0"
    wall_timezone: str | None = None
    metric: str = ""
    field: str = ""
    reducer: str = ""
    occurrence: tuple[int, ...] = ()
    period: ObservationPeriod | None = None
    coordinate_names: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CoordinateBinding:
    """Ordered complete output axes and each root's single-valued classification."""

    axes: tuple[ComparisonAxis, ...]
    mode: Literal["calendar", "grouped", "singleton"]
    population: PopulationDependency | None = None
    memberships: tuple[MembershipBinding, ...] = ()
    complete_calendar: bool = True
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
    coordinates: tuple[FieldRead | IdentityRead, ...]
    time: TimeBinding | None
    period: ObservationPeriod | None
    physical_dtype: Literal["unresolved"] = "unresolved"
    logical_occurrence: tuple[int, ...] | None = None
    window_key: int | None = None
    window_calendar_digest: str | None = None
    sample_axes: tuple[AxisBinding | CalendarCoordinate | IntervalCoordinate, ...] = ()
    memberships: tuple[MembershipBinding, ...] = ()
    output_axis_count: int = 0
    calendars: tuple[tuple[str, CalendarCoordinate | IntervalCoordinate], ...] = ()
    coverage: tuple[SampleCoverage, ...] = ()
    population_reads: tuple[tuple[AxisBinding, FieldRead | IdentityRead], ...] = ()
    window_rule: ObservationWindow | None = None
    sample_layers: tuple[SampleLayer, ...] = ()
    metric: str = ""


@dataclass(frozen=True, slots=True)
class ObservationBinding:
    """One observation's conditions, frozen period, output domain and occurrences."""

    period: ObservationPeriod | None
    condition: PredicateExpression | None
    coordinates: CoordinateBinding
    contributions: tuple[ContributionBinding, ...]
    count_populations: tuple[tuple[PopulationDependency, PopulationDependency], ...] = ()

    @property
    def fingerprint(self) -> str:
        from marivo._definition.graph import _encode, fingerprint

        return fingerprint(_encode(self, {}))
