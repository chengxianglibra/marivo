"""Deterministic source prefixes and exact local continuations, before data work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.analysis.compiler.errors import DatasetCompilationError, compilation_error
from marivo.analysis.compiler.normalize import required_entities
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventSelectionPayload,
    EventTimeToEventPayload,
)
from marivo.analysis.domains.event_attribution import FunnelAttributePayload
from marivo.analysis.domains.event_comparison import FunnelComparePayload
from marivo.analysis.domains.lifecycle_reducers import (
    LifecycleReducerPayload,
    LifecycleSelectionPayload,
)
from marivo.analysis.observation.contracts import (
    ObservationOwner,
    RetainedRowsPayload,
    source_owner_of,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.observation.population_sample import PopulationSamplePayload
from marivo.analysis.operators import registry
from marivo.analysis.operators.association_contracts import CorrelatePayload
from marivo.analysis.operators.attribution_contracts import AttributePayload
from marivo.analysis.operators.candidate_contracts import CandidatePayload
from marivo.analysis.operators.contracts import ComparePayload
from marivo.analysis.operators.driver_contracts import DriverCandidatePayload
from marivo.analysis.operators.forecast_contracts import ForecastPayload
from marivo.analysis.operators.registry import BackendRegistration, ImplementationRegistration


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class SourceBinding:
    owner: ObservationOwner
    datasource_id: str
    adapter: str

    def same_domain(self, other: ExecutionBinding) -> bool:
        return (
            isinstance(other, SourceBinding)
            and self.owner.session_id == other.owner.session_id
            and self.owner.store_id == other.owner.store_id
            and self.owner.catalog_identity is other.owner.catalog_identity
            and self.owner.semantic_registry is other.owner.semantic_registry
            and self.owner.sidecar is other.owner.sidecar
            and self.owner.binding_scopes is other.owner.binding_scopes
            and self.owner.action_port is other.owner.action_port
            and self.datasource_id == other.datasource_id
            and self.adapter == other.adapter
        )


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ParquetBinding:
    """Runtime-admitted immutable scan domain without semantic origin authority."""

    owner: object
    datasource_id: str
    domain_digest: str
    adapter: str = "duckdb"

    def same_domain(self, other: ExecutionBinding) -> bool:
        return (
            isinstance(other, ParquetBinding)
            and self.owner is other.owner
            and self.datasource_id == other.datasource_id
            and self.domain_digest == other.domain_digest
        )


ExecutionBinding: TypeAlias = SourceBinding | ParquetBinding


@dataclass(frozen=True, slots=True, repr=False)
class SourceStep:
    output: int
    dataset: LogicalDataset
    binding: ExecutionBinding
    implementation: BackendRegistration
    operation: Literal["source", "correlation", "distribution"]

    def __post_init__(self) -> None:
        supports_operation = (
            self.implementation.source
            if self.operation == "source"
            else self.implementation.preparation == self.operation
        )
        if self.implementation.backend != self.binding.adapter or not supports_operation:
            raise compilation_error(
                "the selected source operation and binding", "inconsistent source step"
            )


@dataclass(frozen=True, slots=True, repr=False)
class ArtifactReadStep:
    output: int
    dataset: MaterializedDataset


@dataclass(frozen=True, slots=True, repr=False)
class PandasStep:
    output: int
    inputs: tuple[int, ...]
    dataset: LogicalDataset
    implementation: ImplementationRegistration


@dataclass(frozen=True, slots=True, repr=False)
class PhysicalStageGraph:
    steps: tuple[SourceStep | ArtifactReadStep | PandasStep, ...]
    primary_output: int

    @property
    def local_steps(self) -> tuple[PandasStep, ...]:
        return tuple(step for step in self.steps if isinstance(step, PandasStep))


def source_binding(dataset: LogicalDataset) -> SourceBinding:
    entities = required_entities(dataset)
    domains = {entity.datasource_ref.path for entity in entities}
    if len(domains) != 1:
        raise compilation_error("one exact source domain for semantic evaluation", "mixed sources")
    owner = source_owner_of(dataset)
    domain = next(iter(domains))
    return SourceBinding(
        owner,
        domain,
        owner.semantic_registry.datasources[domain].backend_type,
    )


def source_eligible(
    registration: ImplementationRegistration,
    inputs: tuple[ExecutionBinding | None, ...],
    binding: ExecutionBinding,
    *,
    preparation: bool = False,
) -> bool:
    selected = registration.for_backend(binding.adapter)
    return (
        selected is not None
        and (selected.preparation is not None if preparation else selected.source)
        and all(item is not None and binding.same_domain(item) for item in inputs)
    )


def place(
    dataset: LogicalDataset,
    *,
    artifact_binding: Callable[[MaterializedDataset], ExecutionBinding | None] | None = None,
) -> PhysicalStageGraph:
    """Retain maximal admitted prefixes; unsupported source-required successors fail."""
    placements: dict[int, ExecutionBinding | None] = {}
    registrations: dict[int, ImplementationRegistration] = {}
    preparations: dict[int, ExecutionBinding] = {}
    selected: dict[int, BackendRegistration] = {}

    def classify(value: Dataset) -> ExecutionBinding | None:
        if id(value) in placements:
            return placements[id(value)]
        if isinstance(value, MaterializedDataset):
            binding = artifact_binding(value) if artifact_binding is not None else None
            placements[id(value)] = binding
            return binding
        if not isinstance(value, LogicalDataset) or not isinstance(value._root, LogicalRootHandle):
            raise compilation_error("typed Dataset graph", "invalid node")
        child_domains = tuple(classify(child) for child in value._inputs)
        registration = registry.implementation(value)
        registrations[id(value)] = registration
        binding = None
        candidate = None
        if all(item is not None for item in child_domains):
            # Pure retained/comparison operations inherit operand domains. New
            # semantic evaluation must also prove its own source owner matches.
            candidate = (
                child_domains[0]
                if child_domains
                and not (
                    isinstance(value._root.payload, (EventFunnelPayload, LifecycleReducerPayload))
                    and value._root.payload.axes
                )
                and isinstance(
                    value._root.payload,
                    (
                        EventFunnelPayload,
                        EventTimeToEventPayload,
                        EventSelectionPayload,
                        LifecycleSelectionPayload,
                        LifecycleReducerPayload,
                        PopulationSamplePayload,
                        RetainedRowsPayload,
                        RetainedFoldPayload,
                        ComparePayload,
                        FunnelComparePayload,
                        FunnelAttributePayload,
                        AttributePayload,
                        CorrelatePayload,
                        ForecastPayload,
                        CandidatePayload,
                        DriverCandidatePayload,
                    ),
                )
                else source_binding(value)
            )
            if candidate is not None:
                backend = registration.for_backend(candidate.adapter)
                if backend is not None:
                    if source_eligible(registration, child_domains, candidate):
                        binding = candidate
                        selected[id(value)] = backend
                    elif source_eligible(registration, child_domains, candidate, preparation=True):
                        preparations[id(value)] = candidate
                        selected[id(value)] = backend
        if binding is None and id(value) not in preparations:
            try:
                registry.admit_local(value, registration)
            except DatasetCompilationError as error:
                source_backends = ", ".join(
                    item.backend for item in registration.backends if item.source
                )
                preparation_backends = ", ".join(
                    f"{item.backend} {item.preparation}"
                    for item in registration.backends
                    if item.preparation is not None
                )
                if source_backends:
                    repair = f"Use the declared {source_backends} source with matching input authority for this method."
                elif preparation_backends:
                    repair = (
                        f"Keep the complete inputs in one matching domain for {preparation_backends} "
                        "preparation before local evaluation."
                    )
                else:
                    repair = "Use retained inputs satisfying this method's local input contract."
                raise DatasetCompilationError(
                    expected=error.expected or "an exact registered implementation",
                    received=(
                        f"{value._root.operator_id}; backend={candidate.adapter if candidate else 'mixed/local'}; "
                        f"shape={value.row_contract.shape_id}; {error.received}"
                    ),
                    repair=repair,
                    location="dataset.compiler",
                ) from error
        placements[id(value)] = binding
        return binding

    classify(dataset)
    steps: list[SourceStep | ArtifactReadStep | PandasStep] = []
    emitted: dict[int, int] = {}

    def emit(value: Dataset) -> int:
        if id(value) in emitted:
            return emitted[id(value)]
        binding = placements[id(value)]
        if isinstance(value, MaterializedDataset):
            index = len(steps)
            steps.append(ArtifactReadStep(index, value))
        elif isinstance(value, LogicalDataset):
            if id(value) in preparations:
                boundary = len(steps)
                backend = selected[id(value)]
                operation = backend.preparation
                if operation is None:
                    raise compilation_error(
                        "a selected preparation", "missing preparation operation"
                    )
                steps.append(
                    SourceStep(boundary, value, preparations[id(value)], backend, operation)
                )
                index = len(steps)
                steps.append(PandasStep(index, (boundary,), value, registrations[id(value)]))
            elif binding is not None:
                index = len(steps)
                steps.append(SourceStep(index, value, binding, selected[id(value)], "source"))
            else:
                inputs = tuple(emit(child) for child in value._inputs)
                index = len(steps)
                steps.append(PandasStep(index, inputs, value, registrations[id(value)]))
        else:
            raise compilation_error("a paired Dataset", "invalid node")
        emitted[id(value)] = index
        return index

    output = emit(dataset)
    return PhysicalStageGraph(tuple(steps), output)
