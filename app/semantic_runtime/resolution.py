"""Legacy semantic_runtime.resolution stubs — preserved for import compatibility."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ResolvedSemanticObject:
    object_kind: str
    object_id: str
    ref: str
    semantic_object: dict[str, Any]
    status: str
    revision: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ResolvedMetric:
    name: str
    metric_ref: str = ""
    display_name: str = ""
    description: str = ""
    metric_family: str = ""
    population_subject_ref: str | None = None
    observed_entity_ref: str = ""
    observation_grain_ref: str = ""
    sample_kind: str = ""
    value_semantics: str = ""
    aggregation_scope: str | None = None
    primary_time_ref: str | None = None
    additivity_constraints: dict[str, Any] | None = None
    metric_contract_version: str = ""
    family_payload: dict[str, Any] = field(default_factory=dict)
    definition_sql: str | None = None
    dimensions: list[str] = field(default_factory=list)
    grain: str | None = None
    measure_type: str | None = None
    allowed_dimensions: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    desired_direction: str | None = None


@dataclass(slots=True)
class ResolvedEntity:
    name: str
    entity_ref: str = ""
    display_name: str = ""
    description: str = ""
    entity_contract_version: str = ""
    key_refs: list[str] = field(default_factory=list)
    uniqueness_scope: str = ""
    id_stability: str = ""
    nullable_key_policy: str = ""
    parent_entity_ref: str | None = None
    cardinality_to_parent: str | None = None
    ownership_semantics: str | None = None
    primary_time_ref: str | None = None
    stable_descriptors: list[dict[str, Any]] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    level: str | None = None
    join_constraints: dict[str, Any] = field(default_factory=dict)
    upstream_dependencies: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeSemanticAvailability:
    resolved: ResolvedSemanticObject
    lifecycle_status: str
    readiness_status: str
    blocking_requirements: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    dependency_refs: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.lifecycle_status == "active"

    @property
    def is_ready(self) -> bool:
        return self.readiness_status == "ready"


class SemanticResolver:
    """Stub — removed during OSI v2 migration.  See Task 7."""

    def __init__(self, metadata: Any = None, **_kwargs: Any) -> None:
        self.metadata = metadata

    def resolve_ref(self, semantic_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_ref is removed")

    def inspect_ref(self, semantic_ref: str) -> RuntimeSemanticAvailability:
        raise NotImplementedError("SemanticResolver.inspect_ref is removed")

    def resolve_entity_ref(self, entity_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_entity_ref is removed")

    def resolve_metric_ref(self, metric_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_metric_ref is removed")

    def resolve_process_ref(self, process_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_process_ref is removed")

    def resolve_dimension_ref(self, dimension_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_dimension_ref is removed")

    def resolve_time_ref(self, time_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_time_ref is removed")

    def resolve_binding_ref(self, binding_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_binding_ref is removed")

    def resolve_relationship_ref(self, relationship_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_relationship_ref is removed")

    def resolve_predicate_ref(self, predicate_ref: str) -> ResolvedSemanticObject:
        raise NotImplementedError("SemanticResolver.resolve_predicate_ref is removed")

    def resolve_metric(self, metric_name: str) -> ResolvedMetric | None:
        raise NotImplementedError("SemanticResolver.resolve_metric is removed")

    def resolve_entity(self, entity_name: str) -> ResolvedEntity | None:
        raise NotImplementedError("SemanticResolver.resolve_entity is removed")
