"""Base types and validators for semantic layer API models.

This module provides shared Literal types, validators, and base classes
used across all semantic object models. It follows the design contracts
defined in docs/semantic/*.md.
"""

from __future__ import annotations

import re
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

# =============================================================================
# Literal Type Definitions
# =============================================================================

# Entity types
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
SampleKind = Literal["numeric", "rate", "binary", "survival"]
ValueSemantics = Literal[
    "count",
    "sum",
    "ratio",
    "mean",
    "distribution_statistic",
    "score",
    "survival_probability",
]
Additivity = Literal["additive", "semi_additive", "non_additive"]
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
    "metric_input",
]
NullabilityPolicy = Literal["reject", "allow", "impute"]
RepeatedValuePolicy = Literal["take_first", "take_last", "aggregate", "explode"]
JoinKind = Literal["inner", "left", "semi", "anti"]
Cardinality = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
ConsumptionPolicyType = Literal["late_arrival_policy", "incomplete_window_policy"]
ConsumptionBehavior = Literal["exclude_open_subjects", "clip_to_window", "keep_partial"]

# Compatibility profile types
ProfileKind = Literal["requirement", "capability"]
ProfileSubjectKind = Literal["metric", "process", "binding"]
ProfileSchemaVersion = Literal["v1"]
InferentialSampleSummary = Literal["numeric_sample_summary", "rate_sample_summary"]

# Lifecycle status
ObjectStatus = Literal["draft", "published", "deprecated"]


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


class ObjectResponseBase(BaseModel):
    """Common lifecycle metadata returned by semantic object APIs."""

    status: ObjectStatus = Field(description="Lifecycle status: draft, published, or deprecated.")
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


# =============================================================================
# Shared Sub-Models
# =============================================================================


class WindowOffset(BaseModel):
    """Offset specification for time windows."""

    value: int | float = Field(description="Offset value.")
    unit: Literal["minute", "hour", "day", "week"] = Field(description="Time unit.")


class WindowSpec(BaseModel):
    """Time window specification.

    Used in process objects and other contexts where time windows
    need to be defined relative to an anchor time.
    """

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


class StepSpec(BaseModel):
    """Step specification for funnel and path definitions."""

    step_key: str = Field(description="Unique key for this step.")
    event_ref: str = Field(description="Reference to the event that defines this step.")
    qualifier_refs: list[str] | None = Field(
        default=None, description="Optional qualifier references."
    )


class StateSpec(BaseModel):
    """State specification for lifecycle state machines."""

    state_key: str = Field(description="Unique key for this state.")
    entry_ref: str = Field(description="Reference to the predicate for state entry.")
    exit_ref: str | None = Field(
        default=None, description="Optional reference to the predicate for state exit."
    )
    priority: int | float | None = Field(
        default=None, description="Optional priority for conflict resolution."
    )


class HorizonSpec(BaseModel):
    """Horizon specification for survival metrics."""

    value: int | float = Field(description="Horizon value.")
    unit: Literal["day", "week", "month"] = Field(description="Time unit.")
