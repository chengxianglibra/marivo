"""Base types and validators for semantic layer API models.

This module provides shared Literal types, validators, and base classes
used across all semantic object models. It follows the design contracts
defined in docs/semantic/*.md.
"""

from __future__ import annotations

import re
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

# =============================================================================
# Literal Type Definitions
# =============================================================================

# Entity types
EntityKind = Literal[
    "business_entity",
    "event_entity",
    "fact_entity",
    "snapshot_entity",
    "derived_entity",
]
UniquenessScope = Literal["global", "parent_scoped"]
IdStability = Literal["stable", "reassignable", "ephemeral"]
NullableKeyPolicy = Literal["reject", "allow_partial"]
CardinalityToParent = Literal["one_to_one", "many_to_one"]
OwnershipSemantics = Literal["belongs_to", "contains", "derives_from"]
DescriptorCardinality = Literal["one", "many"]

# Metric types
MetricFamily = Literal[
    "count_metric",
    "sum_metric",
    "rate_metric",
    "average_metric",
    "distribution_metric",
    "score_metric",
    "survival_metric",
]
ValueSemantics = Literal[
    "count",
    "sum",
    "ratio",
    "mean",
    "distribution_statistic",
    "score",
    "survival_probability",
]
AggregationScope = Literal["subject", "event", "session", "window"]
AggregationMethod = Literal[
    "count",
    "count_distinct",
    "sum",
    "mean",
    "boolean_any",
    "boolean_all",
]

# Process types
ProcessType = Literal[
    "experiment_context",
    "cohort_definition",
    "funnel_definition",
    "session_contract",
    "path_pattern",
    "lifecycle_state_machine",
]
ContractMode = Literal["context_provider", "entity_stream"]
ContextKind = Literal["cohort_membership", "experiment_split"]
MembershipCardinality = Literal["exclusive_one", "repeatable_many"]
SubjectCardinality = Literal["one", "many"]

# Dimension types
StructureKind = Literal["flat", "hierarchical", "ordinal", "time_derived"]
SemanticRole = Literal["category", "label", "state", "variant", "metric"]
DimensionDomainKind = Literal["open", "enumerated"]
DimensionValueType = Literal["string", "integer", "number", "boolean", "date", "datetime"]
HierarchyType = Literal["flat", "parent_child", "ordinal", "calendar_rollup"]

# Time types
TimeSemanticRole = Literal["business_anchor", "measurement", "operational_support"]
TimeGranularity = Literal["second", "minute", "hour", "day"]

# Enum set types
EnumValueType = Literal["string", "integer", "number", "boolean"]

# Binding types
BindingScope = Literal["entity", "process_object", "metric"]
BindingRole = Literal["primary", "auxiliary"]
CarrierKind = Literal["table", "view"]
TargetKind = Literal[
    "identity_key",
    "primary_time",
    "stable_descriptor",
    "population_subject",
    "analysis_window_anchor",
    "process_context",
]
NullabilityPolicy = Literal["reject", "allow", "impute"]
RepeatedValuePolicy = Literal["take_first", "take_last", "aggregate", "explode"]
JoinKind = Literal["inner", "left", "semi", "anti"]
Cardinality = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
ConsumptionPolicyType = Literal["late_arrival_policy", "incomplete_window_policy"]
ConsumptionBehavior = Literal["exclude_open_subjects", "clip_to_window", "keep_partial"]

# Entity relationship types
RelationshipCardinality = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
RelationshipTimeAlignmentKind = Literal[
    "same_time",
    "bounded_after",
    "bounded_before",
    "overlap",
    "snapshot_effective_window",
]
RelationshipGrainCompatibilityKind = Literal[
    "same_grain",
    "rollup_allowed",
    "many_to_one_rollup",
]

# Predicate types — see docs/semantic/predicate-schema-contract.zh.md for taxonomy
PredicateUsage = Literal[
    "metric_qualifier",  # Metric business predicates (qualifier_refs, default_predicate_refs)
    "carrier_row_filter",  # Carrier consumption invariants (row_filter_refs)
    "request_scope",  # Per-request non-time narrowing (scope.predicate)
]
PredicateTimePolicy = Literal["non_time_only"]
PredicateOperator = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_null",
    "is_not_null",
]

# Compatibility profile types
ProfileKind = Literal["requirement", "capability"]
ProfileSubjectKind = Literal["metric", "process", "binding"]
ProfileSchemaVersion = Literal["v1"]

# Lifecycle status
ObjectStatus = Literal["draft", "published", "deprecated"]
LifecycleStatus = Literal["draft", "validated", "active", "deprecated"]
ReadinessStatus = Literal["not_ready", "ready", "stale"]
DomainCatalogStatus = Literal["active", "deprecated"]


# =============================================================================
# Ref Prefix Patterns
# =============================================================================

_REF_PREFIX_PATTERNS = {
    "entity": re.compile(r"^entity\."),
    "metric": re.compile(r"^metric\."),
    "process": re.compile(r"^process\."),
    "dimension": re.compile(r"^dimension\."),
    "time": re.compile(r"^time\."),
    "key": re.compile(r"^key\."),
    "grain": re.compile(r"^grain\."),
    "subject": re.compile(r"^subject\."),
    "binding": re.compile(r"^binding\."),
    "enum": re.compile(r"^enum\."),
    "compiler_profile": re.compile(r"^compiler_profile\."),
    "relationship": re.compile(r"^relationship\."),
    "domain": re.compile(r"^domain\."),
    "field": re.compile(r"^field\."),
    "time_surface": re.compile(r"^time_surface\."),
    "population": re.compile(r"^population\."),
    "event": re.compile(r"^event\."),
    "predicate": re.compile(r"^predicate\."),
    "measure": re.compile(r"^measure\."),
}


def validate_ref_prefix(value: str, prefix: str, field_name: str | None = None) -> str:
    """Validate that a ref starts with the expected prefix.

    Args:
        value: The ref value to validate.
        prefix: The expected prefix (e.g., "entity", "metric", "time").
        field_name: Optional field name for error messages.

    Returns:
        The validated value.

    Raises:
        ValueError: If the ref doesn't start with the expected prefix.
    """
    pattern = _REF_PREFIX_PATTERNS.get(prefix)
    if pattern is None:
        raise ValueError(f"Unknown ref prefix: {prefix}")

    if not pattern.match(value):
        field_desc = f"'{field_name}'" if field_name else "ref"
        raise ValueError(f"{field_desc} must start with '{prefix}.' prefix, got: {value}")

    return value


def validate_entity_field_ref(value: str, field_name: str | None = None) -> str:
    """Validate an entity-owned field ref.

    The canonical form is ``entity.<entity_ref>.field.<field_ref>``. The
    unqualified ``field.*`` form is still accepted for single-entity authoring
    contexts and is disambiguated by service/compiler validation.
    """
    normalized = value.strip()
    field_desc = f"'{field_name}'" if field_name else "field ref"
    if not normalized:
        raise ValueError(f"{field_desc} must not be empty")
    if normalized.startswith("field."):
        return normalized
    if normalized.startswith("entity.") and ".field." in normalized:
        entity_part, field_part = normalized.split(".field.", 1)
        if entity_part != "entity." and field_part:
            return normalized
    raise ValueError(
        f"{field_desc} must be an entity field ref such as "
        "'entity.order.field.status' or 'field.status', got: {value}"
    )


def validate_canonical_entity_field_ref(value: str, field_name: str | None = None) -> str:
    """Validate a fully qualified entity-owned field ref."""
    normalized = validate_entity_field_ref(value, field_name)
    if normalized.startswith("entity.") and ".field." in normalized:
        return normalized
    field_desc = f"'{field_name}'" if field_name else "field ref"
    raise ValueError(
        f"{field_desc} must use fully qualified entity field form "
        f"'entity.<entity>.field.<field>', got: {value}"
    )


def validate_process_semantic_ref(value: str, field_name: str | None = None) -> str:
    """Validate semantic refs accepted inside process contracts.

    Process objects can point at governed entity fields, time anchors,
    predicates, dimensions, events, or populations. They must not refer to
    unqualified carrier field surfaces such as ``field.user_id``.
    """
    normalized = value.strip()
    field_desc = f"'{field_name}'" if field_name else "process ref"
    if not normalized:
        raise ValueError(f"{field_desc} must not be empty")
    if normalized.startswith("entity.") and ".field." in normalized:
        return validate_canonical_entity_field_ref(normalized, field_name)
    for prefix in ("time", "predicate", "dimension", "event", "population"):
        if normalized.startswith(f"{prefix}."):
            return validate_ref_prefix(normalized, prefix, field_name)
    raise ValueError(
        f"{field_desc} must reference entity.<entity>.field.<field>, time.*, "
        f"predicate.*, dimension.*, event.*, or population.*, got: {value}"
    )


def validate_contract_version(value: str, domain: str) -> str:
    """Validate that a contract_version starts with the expected domain prefix.

    Args:
        value: The contract version value to validate.
        domain: The expected domain (e.g., "entity", "metric", "binding").

    Returns:
        The validated value.

    Raises:
        ValueError: If the version doesn't start with the expected domain.
    """
    expected_prefix = f"{domain}."
    if not value.startswith(expected_prefix):
        raise ValueError(f"contract_version must start with '{expected_prefix}', got: {value}")
    return value


# =============================================================================
# =============================================================================
# Base Models
# =============================================================================


class SemanticRef(BaseModel):
    """Typed reference to a semantic object.

    Used throughout the semantic layer to reference entities, metrics,
    dimensions, time objects, and other semantic constructs.
    """

    ref: str = Field(description="Stable semantic reference (e.g., 'entity.user', 'metric.dau').")
    description: str | None = Field(
        default=None, description="Optional human-readable description of the reference."
    )

    @field_validator("ref")
    @classmethod
    def validate_ref_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("ref must not be empty")
        return v.strip()


class ObjectHeaderBase(BaseModel):
    """Common header fields shared by semantic object headers."""

    display_name: str | None = Field(default=None, description="Human-readable display name.")
    description: str | None = Field(default=None, description="Description of the semantic object.")


class CatalogMetadata(BaseModel):
    """Discovery-only catalog metadata shared by semantic objects.

    Domain metadata helps agents browse a large catalog. It is not a permission
    source, compiler compatibility truth, or part of any stable semantic ref.
    """

    domain_ref: str | None = Field(
        default=None,
        description="Optional discovery domain ref. Top-level semantic objects should provide it.",
    )
    related_domain_refs: list[str] = Field(
        default_factory=list,
        description="Optional related discovery domains.",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative catalog search names for the object.",
    )

    @field_validator("domain_ref")
    @classmethod
    def validate_domain_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_ref_prefix(value.strip(), "domain", "domain_ref")

    @field_validator("related_domain_refs")
    @classmethod
    def validate_related_domain_refs(cls, values: list[str]) -> list[str]:
        return [
            validate_ref_prefix(value.strip(), "domain", "related_domain_refs")
            for value in values
            if value.strip()
        ]

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class BlockingRequirement(BaseModel):
    """Structured blocker explaining why an object is not ready."""

    code: str = Field(description="Stable blocker code.")
    message: str = Field(description="Human-readable blocker message.")
    subject_ref: str | None = Field(
        default=None,
        description="Optional ref for the object or subject directly affected by the blocker.",
    )
    dependency_ref: str | None = Field(
        default=None,
        description="Optional dependency ref associated with the blocker.",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured remediation or coverage details associated with the blocker.",
    )


class ObjectListItemBase(BaseModel):
    """Lightweight model for list endpoints - excludes heavy detail fields."""

    status: ObjectStatus = Field(description="Lifecycle status: draft, published, or deprecated.")
    lifecycle_status: LifecycleStatus = Field(
        description="Derived lifecycle status: draft, validated, active, or deprecated."
    )
    readiness_status: ReadinessStatus = Field(
        description="Derived readiness status: not_ready, ready, or stale."
    )
    blocker_count: int = Field(
        default=0,
        ge=0,
        description="Count of blocking requirements for quick filtering (detail endpoint returns full list).",
    )
    capabilities_summary: dict[str, bool] = Field(
        default_factory=dict,
        description="Summary of key capability flags (detail endpoint returns full payload).",
    )
    revision: int = Field(ge=1, description="Revision number (>= 1).")
    created_at: str = Field(description="Creation timestamp (ISO-8601).")
    updated_at: str = Field(description="Last update timestamp (ISO-8601).")


class ObjectResponseBase(BaseModel):
    """Full detail model for single-object endpoints - includes all fields."""

    status: ObjectStatus = Field(description="Lifecycle status: draft, published, or deprecated.")
    lifecycle_status: LifecycleStatus = Field(
        description="Derived lifecycle status: draft, validated, active, or deprecated."
    )
    readiness_status: ReadinessStatus = Field(
        description="Derived readiness status: not_ready, ready, or stale."
    )
    blocking_requirements: list[BlockingRequirement] = Field(
        default_factory=list,
        description="Structured reasons why the object is not currently ready for default use.",
    )
    capabilities: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured capability flags or payloads exposed for this object.",
    )
    dependency_refs: list[str] = Field(
        default_factory=list,
        description="Direct refs or locators this object depends on for semantic use or grounding.",
    )
    dependent_refs: list[str] = Field(
        default_factory=list,
        description="Refs of objects that depend on this object (reverse dependencies).",
    )
    revision: int = Field(ge=1, description="Revision number (>= 1).")
    created_at: str = Field(description="Creation timestamp (ISO-8601).")
    updated_at: str = Field(description="Last update timestamp (ISO-8601).")


ListItemT = TypeVar("ListItemT")


class ListResponseBase(BaseModel, Generic[ListItemT]):  # noqa: UP046
    """Common list envelope for semantic object APIs."""

    items: list[ListItemT] = Field(default_factory=list, description="List of objects.")
    total: int = Field(ge=0, description="Total count of objects.")


class ApiErrorDetail(BaseModel):
    """Structured API error detail."""

    message: str = Field(description="Human-readable error message.")
    code: str | None = Field(default=None, description="Optional stable error code.")
    field: str | None = Field(default=None, description="Optional field associated with the error.")


class SemanticValidationSummary(BaseModel):
    """Structured validate-action summary for semantic lifecycle checks."""

    blocking_requirements: list[BlockingRequirement] = Field(
        default_factory=list,
        description="Current blocking requirements after validate checks complete.",
    )
    capabilities: dict[str, Any] = Field(
        default_factory=dict,
        description="Current capability payload exposed by the semantic object.",
    )


class SemanticValidateActionResponse(BaseModel):
    """Response envelope for check-only semantic validate actions."""

    action: Literal["validate"] = Field(description="Lifecycle action that was executed.")
    ok: bool = Field(description="Whether the validate action completed successfully.")
    semantic_object: dict[str, Any] = Field(
        description="Current semantic object detail payload after validation."
    )
    validation: SemanticValidationSummary = Field(
        description="Structured validation summary derived from the current object detail."
    )


# =============================================================================
# Shared Sub-Models
# =============================================================================


class WindowOffset(BaseModel):
    """Offset specification for time windows."""

    model_config = ConfigDict(extra="forbid")

    value: int | float = Field(description="Offset value.")
    unit: Literal["minute", "hour", "day", "week"] = Field(description="Time unit.")


class WindowSpec(BaseModel):
    """Time window specification.

    Used in process objects and other contexts where time windows
    need to be defined relative to an anchor time.
    """

    model_config = ConfigDict(extra="forbid")

    anchor_ref: str | None = Field(
        default=None, description="Reference to the anchor time (time.*)."
    )
    start_offset: WindowOffset | None = Field(
        default=None, description="Window start offset from anchor."
    )
    end_offset: WindowOffset | None = Field(
        default=None, description="Window end offset from anchor."
    )

    @field_validator("anchor_ref")
    @classmethod
    def validate_anchor_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            validate_ref_prefix(v, "time", "anchor_ref")
        return v


class PopulationSpec(BaseModel):
    """Population specification for cohort definitions."""

    model_config = ConfigDict(extra="forbid")

    base_population_ref: str = Field(description="Reference to the base population.")
    include_refs: list[str] | None = Field(
        default=None, description="Optional populations to include."
    )
    exclude_refs: list[str] | None = Field(
        default=None, description="Optional populations to exclude."
    )
    membership_mode: Literal["once", "repeatable", "rolling"] | None = Field(
        default=None, description="Population membership mode."
    )

    @field_validator("base_population_ref")
    @classmethod
    def validate_base_population_ref(cls, v: str) -> str:
        return validate_process_semantic_ref(v, "base_population_ref")

    @field_validator("include_refs", "exclude_refs")
    @classmethod
    def validate_population_filter_refs(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [validate_process_semantic_ref(ref, "population_ref") for ref in v]


class StepSpec(BaseModel):
    """Step specification for funnel and path definitions."""

    model_config = ConfigDict(extra="forbid")

    step_key: str = Field(description="Unique key for this step.")
    event_ref: str = Field(description="Reference to the event that defines this step.")
    qualifier_refs: list[str] | None = Field(
        default=None, description="Optional qualifier references."
    )

    @field_validator("event_ref")
    @classmethod
    def validate_event_ref(cls, v: str) -> str:
        return validate_process_semantic_ref(v, "event_ref")

    @field_validator("qualifier_refs")
    @classmethod
    def validate_qualifier_refs(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [validate_ref_prefix(ref, "predicate", "qualifier_refs") for ref in v]


class StateSpec(BaseModel):
    """State specification for lifecycle state machines."""

    model_config = ConfigDict(extra="forbid")

    state_key: str = Field(description="Unique key for this state.")
    entry_ref: str = Field(description="Reference to the predicate for state entry.")
    exit_ref: str | None = Field(
        default=None, description="Optional reference to the predicate for state exit."
    )
    priority: int | float | None = Field(
        default=None, description="Optional priority for conflict resolution."
    )

    @field_validator("entry_ref")
    @classmethod
    def validate_entry_ref(cls, v: str) -> str:
        return validate_process_semantic_ref(v, "entry_ref")

    @field_validator("exit_ref")
    @classmethod
    def validate_exit_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_process_semantic_ref(v, "exit_ref")


class HorizonSpec(BaseModel):
    """Horizon specification for survival metrics."""

    value: int | float = Field(description="Horizon value.")
    unit: Literal["day", "week", "month"] = Field(description="Time unit.")
