"""Private, source-free assembly entry for the Slice 2a observation contract."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

from marivo._temporal import PeriodCalendarSnapshotV1, TimeScope
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.domains.completeness import CompletenessDeclaration
from marivo.analysis.domains.event import LogicalEventDataset, make_match
from marivo.analysis.event import EventPattern, EveryStart, FirstPerSubject
from marivo.analysis.observation.contracts import (
    EntityInput,
    MetricInput,
    ObservationActionPort,
    ObservationOwner,
    TimeDimensionInput,
    construction_error,
    make_family_registry,
    make_ids,
)
from marivo.analysis.observation.metric import (
    LogicalMetricDataset,
    PopulationInput,
    make_observation,
)
from marivo.analysis.observation.population import LogicalPopulationDataset, make_population
from marivo.analysis.observation.source_bindings import SourceBindingMap, SourceBindingScopes
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.validator import Registry


@dataclass(frozen=True, slots=True, repr=False)
class LazyEvents:
    """Private Event source namespace with construction-only authority."""

    _owner: ObservationOwner
    _registry: DatasetFamilyRegistry

    def match(
        self,
        pattern: EventPattern,
        *,
        cohort_window: TimeScope,
        completion_through: datetime,
        matching: FirstPerSubject | EveryStart,
        population: PopulationInput | None = None,
        completeness: tuple[CompletenessDeclaration, ...] = (),
    ) -> LogicalEventDataset:
        """Describe dense journeys for pattern under explicit time and matching rules.

        Args:
            pattern: Ordered Event roles.
            cohort_window: Anchor interval.
            completion_through: Exclusive follow-up bound.
            matching: Assignment rule.
            population: Exact subject membership.
            completeness: Explicit completeness assumptions.

        Returns:
            A logical Event dataset.

        Example:
            ``sources.events.match(pattern, cohort_window=window,
            completion_through=end, matching=policy)``.

        Constraints: Same-domain complete identities; construction performs no I/O.
        """
        return make_match(
            self._owner,
            self._registry,
            pattern,
            cohort_window=cohort_window,
            completion_through=completion_through,
            matching=matching,
            population=population,
            completeness=completeness,
        )


@dataclass(frozen=True, slots=True, repr=False)
class LazySources:
    """Private source facade carrying already admitted in-memory authority."""

    _owner: ObservationOwner
    _registry: DatasetFamilyRegistry

    @property
    def events(self) -> LazyEvents:
        """Return the private Event source namespace without reading sources."""
        return LazyEvents(self._owner, self._registry)

    def population(
        self,
        entity: EntityInput,
        *,
        time_scope: TimeScope | None = None,
        time_dimension: TimeDimensionInput | None = None,
    ) -> LogicalPopulationDataset:
        """Construct governed membership for entity within optional selection scope.

        Returns: Logical Population. Example: ``sources.population(customer)``.
        Constraints: A versioned Entity requires its own finite membership scope.
        """
        return make_population(
            self._owner,
            self._registry,
            entity,
            time_scope=time_scope,
            time_dimension=time_dimension,
        )

    def observe(
        self,
        metrics: MetricInput | list[MetricInput] | tuple[MetricInput, ...],
        *,
        population: PopulationInput | None = None,
        time_scope: TimeScope | None = None,
        time_dimension: TimeDimensionInput | None = None,
    ) -> LogicalMetricDataset:
        """Construct ordered metrics over population with independent observation scope.

        Returns: Logical Metric. Example: ``sources.observe(revenue, time_scope=window)``.
        Constraints: Uses exact current semantic identities; performs no source work.
        """
        return make_observation(
            self._owner,
            self._registry,
            metrics,
            population=population,
            time_scope=time_scope,
            time_dimension=time_dimension,
        )

    def source_bindings(self, bindings: SourceBindingMap) -> AbstractContextManager[None]:
        """Bind non-secret source parameters during construction.

        Args: bindings: Exact Entity refs mapped to declared parameter values.
        Returns: Restoring context manager. Example: ``with sources.source_bindings(values): ...``.
        Constraints: Nested scopes completely replace the previous scope.
        """
        return self._owner.binding_scopes.scope(bindings)


def make_lazy_sources(
    *,
    semantic_registry: Registry,
    sidecar: CompiledExpressionSidecar,
    action_port: ObservationActionPort,
    session_id: str,
    store_id: str,
    catalog: SemanticCatalog | None = None,
    period_calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
) -> LazySources:
    """Assemble private source constructors from immutable, already loaded authority."""
    if not semantic_registry._frozen:
        raise construction_error("frozen in-memory semantic Registry", "mutable Registry")
    if catalog is not None and catalog._reg is not semantic_registry:
        raise construction_error(
            "catalog and Registry with exact shared authority", "mismatched catalog"
        )
    methods = (
        "execute_population",
        "execute_metric",
        "execute_event",
        "show",
        "to_pandas",
        "evidence_digest",
        "findings",
        "finding",
    )
    if any(not callable(getattr(action_port, method, None)) for method in methods):
        raise construction_error(
            "complete required Observation action/read port", "missing or incomplete runtime owner"
        )
    owner = ObservationOwner(
        session_id=session_id,
        store_id=store_id,
        catalog_identity=catalog,
        semantic_registry=semantic_registry,
        sidecar=sidecar,
        action_port=action_port,
        binding_scopes=SourceBindingScopes.from_registry(semantic_registry),
        period_calendar_snapshots=period_calendar_snapshots,
    )
    return LazySources(owner, make_family_registry(make_ids(())))
