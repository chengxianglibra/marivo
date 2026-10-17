"""Frozen source-free observation facts shared by Query coordinate domains."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import FieldPredicate, MetricNode, TimeBinding
from marivo._definition.relations import (
    MembershipBinding,
    PopulationCondition,
    PopulationDependency,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    IdentityRead,
)
from marivo._temporal import InstantAnchor


@dataclass(frozen=True, slots=True)
class SampleCoverage:
    """Expected instants on one physical internal key and optional calendar output."""

    key: str
    points: tuple[datetime, ...]
    output_key: int | None = None
    output_slot: str = "key.0"


@dataclass(frozen=True, slots=True)
class CoordinateBinding:
    """Ordered complete output axes and each root's single-valued classification."""

    axes: tuple[ComparisonAxis, ...]
    mode: Literal["calendar", "grouped", "singleton"]
    population: PopulationDependency | None = None
    memberships: tuple[MembershipBinding, ...] = ()


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
    intrinsic_conditions: tuple[FieldPredicate, ...]
    common_condition: FieldPredicate | None
    reads: tuple[FieldRead, ...]
    coordinates: tuple[FieldRead | IdentityRead, ...]
    time: TimeBinding | None
    period: ObservationPeriod | None
    physical_dtype: Literal["unresolved"] = "unresolved"
    logical_occurrence: tuple[int, ...] | None = None
    window_key: int | None = None
    sample_axes: tuple[AxisBinding | CalendarCoordinate, ...] = ()
    memberships: tuple[MembershipBinding, ...] = ()
    output_axis_count: int = 0
    calendars: tuple[tuple[str, CalendarCoordinate], ...] = ()
    coverage: tuple[SampleCoverage, ...] = ()
    population_conditions: tuple[PopulationCondition, ...] = ()
    population_reads: tuple[tuple[AxisBinding, FieldRead | IdentityRead], ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationBinding:
    """One observation's conditions, frozen period, output domain and occurrences."""

    period: ObservationPeriod | None
    condition: FieldPredicate | None
    coordinates: CoordinateBinding
    contributions: tuple[ContributionBinding, ...]
    members: PopulationDependency | None = None
    member_population: PopulationDependency | None = None

    @property
    def fingerprint(self) -> str:
        from marivo._definition.graph import _encode, fingerprint

        return fingerprint(_encode(self, {}))
