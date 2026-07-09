"""Plan result types and planned-detail adapters for the observe planner.

Internal to ``marivo.analysis.intents`` — extracted from ``observe_planner``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, Literal

from marivo.refs import SemanticRef
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    DimensionDetails,
    MetricDetails,
    RelationshipDetails,
    SimpleMetricDetails,
    TimeDimensionDetails,
)


class JoinSafety(StrEnum):
    MANY_TO_ONE = "many_to_one"
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlannedDimension:
    field: Any
    column: str


@dataclass(frozen=True)
class PlannedWhere:
    original_key: str
    field: Any
    value: Any
    phase: Literal["root", "joined"]


@dataclass(frozen=True)
class BaseObservePlan:
    root_entity: str
    additivity: str
    table: Any
    dataset_tables: dict[str, Any]
    dimensions: list[PlannedDimension]
    where: list[PlannedWhere]
    axes_metadata: dict[str, Any]
    lineage_metadata: dict[str, Any]
    warnings: list[dict[str, Any]]
    datasource_name: str
    status_time_dimension: str | None = None
    time_fold: Any | None = None


@dataclass(frozen=True)
class ComponentPlan:
    component_metric_ir: Any
    role: str
    base_plan: BaseObservePlan | CumulativeObservePlan


@dataclass(frozen=True)
class CumulativeObservePlan:
    metric_ir: Any
    base_metric_ir: Any
    base_plan: BaseObservePlan
    over: str | None
    window: Any | None
    # Resolved CumulativeComposition (carries the real anchor) from the
    # metric IR. Present when the plan is built from a real MetricIR; absent
    # (None) for adapter-only construction paths.
    composition: Any = None

    @property
    def dimensions(self) -> list[PlannedDimension]:
        return self.base_plan.dimensions

    @property
    def where(self) -> list[PlannedWhere]:
        return self.base_plan.where

    @property
    def axes_metadata(self) -> dict[str, Any]:
        return self.base_plan.axes_metadata

    @property
    def lineage_metadata(self) -> dict[str, Any]:
        return self.base_plan.lineage_metadata

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return self.base_plan.warnings

    @property
    def datasource_name(self) -> str:
        return self.base_plan.datasource_name

    @property
    def root_entity(self) -> str:
        return self.base_plan.root_entity

    @property
    def table(self) -> Any:
        return self.base_plan.table


@dataclass(frozen=True)
class DerivedObservePlan:
    metric_ir: Any
    component_plans: list[ComponentPlan]
    parent_axes: dict[str, Any]
    lineage_metadata: dict[str, Any]
    warnings: list[dict[str, Any]] = field(default_factory=list)


ObservePlan = BaseObservePlan | CumulativeObservePlan | DerivedObservePlan


@dataclass(frozen=True)
class ResolvedObserveFields:
    dimensions: list[Any] = field(default_factory=list)
    where_fields: dict[str, Any] = field(default_factory=dict)
    raw_root_where_keys: tuple[str, ...] = ()
    time_dimension: Any | None = None


FieldDetails = DimensionDetails | TimeDimensionDetails


@dataclass(frozen=True)
class _PlannedFieldDetails:
    details: FieldDetails

    @property
    def ref(self) -> Any:
        return self.details.ref

    @property
    def semantic_id(self) -> str:
        return self.details.ref.id

    @property
    def name(self) -> str:
        return self.details.name

    @property
    def entity(self) -> str:
        return self.details.entity.id

    def __getattr__(self, name: str) -> Any:
        return getattr(self.details, name)


def _planned_field(field: Any) -> _PlannedFieldDetails:
    if isinstance(field, _PlannedFieldDetails):
        return field
    return _PlannedFieldDetails(field)


@dataclass(frozen=True)
class _PlannedRelationshipDetails:
    details: RelationshipDetails

    @property
    def ref(self) -> Any:
        return self.details.ref

    @property
    def semantic_id(self) -> str:
        return self.details.ref.id

    @property
    def from_entity(self) -> str:
        return self.details.from_entity.id

    @property
    def to_entity(self) -> str:
        return self.details.to_entity.id

    @property
    def from_keys(self) -> tuple[str, ...]:
        return self.details.from_keys

    @property
    def to_keys(self) -> tuple[str, ...]:
        return self.details.to_keys

    def __getattr__(self, name: str) -> Any:
        return getattr(self.details, name)


def _planned_relationship(relationship: RelationshipDetails) -> _PlannedRelationshipDetails:
    return _PlannedRelationshipDetails(relationship)


RelationshipInfo = RelationshipDetails | _PlannedRelationshipDetails
PlannerField = FieldDetails | _PlannedFieldDetails


@dataclass(frozen=True)
class _MetricDetailsAdapter:
    details: MetricDetails

    @property
    def semantic_id(self) -> str:
        return self.details.ref.id

    @property
    def name(self) -> str:
        return self.details.name

    @property
    def root_entity(self) -> str | None:
        return self.details.root_entity.id if self.details.root_entity is not None else None

    @property
    def entities(self) -> tuple[str, ...]:
        return tuple(entity.id for entity in self.details.entities)

    @property
    def additivity(self) -> str | None:
        return self.details.additivity

    @property
    def fanout_policy(self) -> str:
        return self.details.fanout_policy

    @property
    def metric_type(self) -> str:
        return self.details.metric_type

    @property
    def composition(self) -> Any:
        if not isinstance(self.details, DerivedMetricDetails):
            return None
        components = {
            role: (ref.id if isinstance(ref, SemanticRef) else str(ref))
            for role, ref in self.details.components
        }
        return SimpleNamespace(
            kind=self.details.composition,
            components=components,
            signs=(
                dict(self.details.linear_terms)
                if self.details.composition == "linear" and self.details.linear_terms
                else None
            ),
            # Cumulative-specific fields.  The adapter wraps
            # DerivedMetricDetails, which carries composition as a string
            # and components as role-ref pairs; the real CumulativeComposition
            # IR (with resolved over) lives on MetricIR.  When over is not
            # available from the details, default to None — the real
            # MetricIR path provides the resolved value.
            base=components.get("base") if self.details.composition == "cumulative" else None,
            over=None,
            anchor="all_history" if self.details.composition == "cumulative" else None,
        )

    @property
    def linear_terms(self) -> tuple[tuple[str, str], ...]:
        if isinstance(self.details, DerivedMetricDetails):
            return self.details.linear_terms
        return ()

    @property
    def aggregation(self) -> Any:
        if isinstance(self.details, SimpleMetricDetails):
            return self.details.aggregation
        return None

    @property
    def measure(self) -> str | None:
        if isinstance(self.details, SimpleMetricDetails):
            return self.details.measure.id if self.details.measure else None
        return None

    @property
    def time_fold(self) -> Any | None:
        if self.details.fold is None:
            return None
        return _TimeFoldDetailsAdapter(self.details.fold)

    @property
    def status_time_dimension(self) -> str | None:
        return self.details.status_time_dimension

    @property
    def unit(self) -> str | None:
        return self.details.unit


@dataclass(frozen=True)
class _TimeFoldDetailsAdapter:
    value: str

    @property
    def kind(self) -> str:
        if self.value.startswith("percentile("):
            return "percentile"
        return self.value

    @property
    def q(self) -> float | None:
        if not self.value.startswith("percentile("):
            return None
        return float(self.value.removeprefix("percentile(").removesuffix(")"))

    def label(self) -> str:
        return self.value


def _planned_metric(details: MetricDetails) -> _MetricDetailsAdapter:
    return _MetricDetailsAdapter(details)


def _composition_kind(metric_ir: Any) -> str | None:
    """Return the composition kind string (e.g. 'ratio', 'cumulative') or None."""
    composition = getattr(metric_ir, "composition", None)
    if composition is None:
        return None
    kind = getattr(composition, "kind", None)
    return str(kind) if kind is not None else None


def _is_cumulative_metric(metric_ir: Any) -> bool:
    """True when the metric's composition kind is 'cumulative'."""
    return _composition_kind(metric_ir) == "cumulative"
