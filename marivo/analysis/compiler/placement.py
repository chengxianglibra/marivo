"""Deterministic source prefixes and exact local continuations, before data work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.analysis.compiler.errors import DatasetCompilationError, compilation_error
from marivo.analysis.compiler.normalize import required_entities
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import (
    _row_contract_fingerprint,
    _row_set_contract_fingerprint,
)
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
from marivo.analysis.operators import registry
from marivo.analysis.operators.association_contracts import CorrelatePayload
from marivo.analysis.operators.attribution_contracts import AttributePayload
from marivo.analysis.operators.candidate_contracts import CandidatePayload
from marivo.analysis.operators.contracts import ComparePayload
from marivo.analysis.operators.driver_contracts import DriverCandidatePayload
from marivo.analysis.operators.forecast_contracts import ForecastPayload
from marivo.analysis.operators.registry import BackendRegistration, ImplementationRegistration


def _j1_placement_error(expected: str, received: str) -> DatasetCompilationError:
    return DatasetCompilationError(
        expected=expected,
        received=received,
        repair="Use the exact constructed J1 root with its qualified DuckDB source or fixed pandas input.",
        location="dataset.compiler.j1_placement",
    )


def place_j1_source(context: object, root: LogicalRootHandle, backend: str) -> None:
    """Admit the private J1 source definition before any business query."""
    from marivo.analysis.observation.dsl_j1 import J1Context, j1_row_contracts

    if not isinstance(context, J1Context):
        raise _j1_placement_error("one declared J1 context", type(context).__name__)
    if backend != "duckdb":
        raise _j1_placement_error("qualified DuckDB J1 source route", backend)
    pending = [root]
    seen: set[LogicalRootHandle] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if current.session_id != context.session_id or current.store_id != context.store_id:
            raise _j1_placement_error("J1 root from this Session and Store", "foreign root")
        row, rows = j1_row_contracts(context, current)
        if (
            current.shape_id.family_id != "dsl_j1"
            or _row_contract_fingerprint(row) != current.row_contract_fingerprint
            or _row_set_contract_fingerprint(rows) != current.row_set_contract_fingerprint
        ):
            raise _j1_placement_error("exact J1 row definition", "definition binding differs")
        if not current.inputs:
            continue
        expected_roles = (
            ("current", "baseline") if current.operator_id == "dsl.j1.compare" else ("input",)
        )
        if tuple(item.role for item in current.inputs) != expected_roles or any(
            not isinstance(item.root, LogicalRootHandle) for item in current.inputs
        ):
            raise _j1_placement_error("exact J1 logical predecessors", current.operator_id)
        for item in reversed(current.inputs):
            assert isinstance(item.root, LogicalRootHandle)
            pending.append(item.root)


def place_j1_local(
    root: LogicalRootHandle,
    input_root: LogicalRootHandle,
    *,
    baseline_root: LogicalRootHandle | None = None,
) -> None:
    """Admit only an exact retained J1 successor on the pandas route."""
    if baseline_root is not None:
        if (
            root.shape_id.family_id == "dsl_j1"
            and root.operator_id == "dsl.j1.compare"
            and len(root.inputs) == 2
            and root.inputs[0].role == "current"
            and root.inputs[1].role == "baseline"
            and root.inputs[0].root is input_root
            and root.inputs[1].root is baseline_root
            and root.session_id == input_root.session_id == baseline_root.session_id
            and root.store_id == input_root.store_id == baseline_root.store_id
        ):
            return
        raise _j1_placement_error("exact ordered retained J1 comparison", root.operator_id)
    if (
        root.shape_id.family_id != "dsl_j1"
        or root.operator_id
        not in ("dsl.j1.where", "dsl.j1.group", "dsl.j1.rollup", "dsl.j1.summarize")
        or len(root.inputs) != 1
        or root.inputs[0].root is not input_root
        or root.session_id != input_root.session_id
        or root.store_id != input_root.store_id
    ):
        raise _j1_placement_error("exact retained J1 pandas successor", root.operator_id)


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
                reason = (
                    registry.source_unsupported_reason(value, candidate.adapter)
                    if candidate is not None
                    else None
                )
                raise DatasetCompilationError(
                    expected=error.expected or "an exact registered implementation",
                    received=(
                        f"{value._root.operator_id}; backend={candidate.adapter if candidate else 'mixed/local'}; "
                        f"shape={value.row_contract.shape_id}; {reason or error.received}"
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
