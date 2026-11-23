"""Bound formula and sample-domain records shared with the compiler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo._definition.correspondence import ComparisonAxis
    from marivo._definition.expressions import BuiltinSampleCoordinate, MetricNode
    from marivo._definition.observations import SampleAxis, SampleAxisMatches
    from marivo._definition.relations import PopulationDependency
    from marivo._definition.types import CalendarCoordinate, ExactRef, IntervalCoordinate
    from marivo.analysis.model.programs import FormulaInput


@dataclass(frozen=True, slots=True)
class PopulationInput:
    """One independently selected source-backed internal sample domain."""

    definition: PopulationDependency
    stage_id: str
    keys: tuple[str, ...]
    key_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class MemberInput:
    definition: PopulationDependency
    population: PopulationDependency
    keys: tuple[str, ...]
    axis_mapping: tuple[tuple[ComparisonAxis, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class FormulaProgram:
    outputs: tuple[tuple[str, MetricNode], ...]
    declared: tuple[tuple[ExactRef, MetricNode], ...]
    values: tuple[FormulaInput, ...]
    calendar: CalendarCoordinate | IntervalCoordinate | None = None
    populations: tuple[PopulationInput, ...] = ()
    output_population: PopulationDependency | None = None
    calendar_key: str = "key.0"
    counts: tuple[MemberInput, ...] = ()
    axes: tuple[SampleAxis, ...] = ()
    parent_scope: PopulationDependency | None = None
    parent_slots: tuple[str, ...] = ()
    population_checked: bool = False
    calendar_samples: tuple[tuple[tuple[int, ...], BuiltinSampleCoordinate], ...] = ()
    window_endpoints: tuple[tuple[tuple[int, ...], tuple[datetime, ...]], ...] = ()
    sample_matches: tuple[tuple[tuple[int, ...], SampleAxisMatches], ...] = ()
    key_groups: tuple[tuple[str, ...], ...] = ()
