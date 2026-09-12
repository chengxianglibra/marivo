"""Typed source recipe handed to the private materialization runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import ibis.expr.types as ir

from marivo.analysis.datasets.handles import CanonicalValue
from marivo.analysis.observation.sampling import EntitySamplingPolicy
from marivo.analysis.observation.temporal import TemporalExecution
from marivo.analysis.operators.candidate_contracts import CandidateDefinition
from marivo.analysis.operators.driver_contracts import DriverCandidateDefinition
from marivo.semantic.ir import TargetEntityContract

if TYPE_CHECKING:
    from marivo.analysis.domains.completeness import EventCoverageResolution
    from marivo.analysis.domains.contracts import EventSelectionPayload
    from marivo.analysis.domains.lifecycle_reducers import LifecycleSelectionPayload


@dataclass(frozen=True, slots=True, repr=False)
class CompiledArtifactScan:
    expression: ir.Table
    entity: TargetEntityContract
    parts: tuple[tuple[str, ir.Table], ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class CompiledValidation:
    """A named scalar relation whose violations column must equal zero."""

    name: str
    expression: ir.Table
    expected: str | None = None
    repair: str | None = None


@dataclass(frozen=True, slots=True)
class RetainedPartSpec:
    """A keyed projection of the sole transfer carrying sufficient Metric state."""

    role: str
    contract_id: str
    contract_version: int
    column_names: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class RetainedRelationSpec:
    """An independent source-private relation with its own schema and cardinality."""

    role: str
    contract_id: str
    contract_version: int
    expression: ir.Table


@dataclass(frozen=True, slots=True, repr=False)
class CompiledSampleFence:
    """One predeclared, action-owned physical realization of eligible Entity rows."""

    relation_name: str
    expression: ir.Table
    policy: EntitySamplingPolicy
    population_definition_fingerprint: str
    target_population_definition_fingerprint: str
    identity_columns: tuple[str, ...]
    root_identity: int


@dataclass(frozen=True, slots=True, repr=False)
class CompiledRelationFence:
    """One source-private realization shared by scoring and scalar validation."""

    relation_name: str
    expression: ir.Table
    root_identity: int


@dataclass(frozen=True, slots=True, repr=False)
class CompiledDataset:
    """One final source expression with separate, named assertion preflights."""

    expression: ir.Table
    validations: tuple[CompiledValidation, ...]
    primary_columns: tuple[str, ...]
    retained_parts: tuple[RetainedPartSpec | RetainedRelationSpec, ...]
    preparations: tuple[CompiledValidation | CompiledSampleFence | CompiledRelationFence, ...] = ()
    attribution_proof: ir.Table | None = None
    numerical_input: Literal["distribution_coalitions"] | None = None
    association_proof: ir.Table | None = None
    candidate_proof: ir.Table | None = None
    candidate_definition: CandidateDefinition | DriverCandidateDefinition | None = None
    lifecycle_coverage: EventCoverageResolution | None = None
    lifecycle_reducer_coverage: EventCoverageResolution | None = None
    lifecycle_selection_payload: LifecycleSelectionPayload | None = None
    lifecycle_selection_proof: ir.Table | None = None
    event_proof: ir.Table | None = None
    event_coverage: EventCoverageResolution | None = None
    event_reducer_proof: ir.Table | None = None
    event_reducer_coverage: EventCoverageResolution | None = None
    selection_proof: ir.Table | None = None
    selection_coverage: EventCoverageResolution | None = None
    selection_payload: EventSelectionPayload | None = None
    selection_input_definition: str | None = None
    temporal_execution: TemporalExecution | None = None
    version_selections: tuple[tuple[str, CanonicalValue], ...] = ()
