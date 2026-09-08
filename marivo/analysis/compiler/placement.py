"""Deterministic source prefixes and exact local continuations, before data work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias

import duckdb
import ibis

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.normalize import required_entities
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import (
    ObservationOwner,
    RetainedRowsPayload,
    source_owner_of,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.operators import registry
from marivo.analysis.operators.registry import ImplementationRegistration


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class SourceBinding:
    owner: ObservationOwner
    datasource_id: str
    adapter: str
    adapter_versions: tuple[str, str]

    def same_domain(self, other: ExecutionBinding) -> bool:
        return (
            isinstance(other, SourceBinding)
            and self.owner is other.owner
            and self.datasource_id == other.datasource_id
            and self.adapter == other.adapter
            and self.adapter_versions == other.adapter_versions
        )


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class EngineBinding:
    """Runtime-admitted immutable scan domain without semantic origin authority."""

    owner: object
    datasource_id: str
    domain_digest: str
    adapter_versions: tuple[str, str]
    adapter: str = "duckdb"

    def same_domain(self, other: ExecutionBinding) -> bool:
        return (
            isinstance(other, EngineBinding)
            and self.owner is other.owner
            and self.datasource_id == other.datasource_id
            and self.domain_digest == other.domain_digest
            and self.adapter_versions == other.adapter_versions
        )


ExecutionBinding: TypeAlias = SourceBinding | EngineBinding


@dataclass(frozen=True, slots=True, repr=False)
class SourceStep:
    output: int
    dataset: LogicalDataset
    binding: ExecutionBinding


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
        (duckdb.__version__, ibis.__version__),
    )


def source_eligible(
    registration: ImplementationRegistration,
    inputs: tuple[ExecutionBinding | None, ...],
    binding: ExecutionBinding,
) -> bool:
    return (
        registration.source_adapter == binding.adapter
        and registration.source_versions == binding.adapter_versions
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
        if all(item is not None for item in child_domains):
            candidate = (
                child_domains[0]
                if child_domains
                and (
                    isinstance(child_domains[0], EngineBinding)
                    or isinstance(value._root.payload, (RetainedRowsPayload, RetainedFoldPayload))
                )
                else source_binding(value)
            )
            if candidate is not None and source_eligible(registration, child_domains, candidate):
                binding = candidate
        if binding is None:
            registry.admit_local(value, registration)
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
            if binding is not None:
                index = len(steps)
                steps.append(SourceStep(index, value, binding))
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
