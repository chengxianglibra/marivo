"""Process Object semantic models.

This module defines the API models for process objects,
following the contract defined in docs/semantic/process-object-schema.zh.md.

Process objects define processes like experiments, cohorts, funnels, sessions,
paths, and lifecycle state machines.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import (
    CatalogMetadata,
    ContextKind,
    ContractMode,
    ListResponseBase,
    MembershipCardinality,
    ObjectHeaderBase,
    ObjectListItemBase,
    ObjectResponseBase,
    PopulationSpec,
    ProcessType,
    StateSpec,
    StepSpec,
    SubjectCardinality,
    WindowSpec,
    validate_contract_version,
    validate_process_semantic_ref,
    validate_ref_prefix,
)

# =============================================================================
# Process Object Header
# =============================================================================


class ProcessObjectHeader(ObjectHeaderBase):
    """Header for a process object.

    Defines the stable identity and type of a process.
    """

    model_config = ConfigDict(extra="forbid")

    process_ref: str = Field(
        description="Stable process reference (e.g., 'process.exp_123'). "
        "Must start with 'process.'."
    )
    process_type: ProcessType = Field(
        description="Type of process: experiment_context, cohort_definition, "
        "funnel_definition, session_contract, path_pattern, or lifecycle_state_machine."
    )
    process_contract_version: str = Field(
        description="Contract version (e.g., 'process.v2'). Must start with 'process.'."
    )

    @field_validator("process_ref")
    @classmethod
    def validate_process_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "process", "process_ref")

    @field_validator("process_contract_version")
    @classmethod
    def validate_version_prefix(cls, v: str) -> str:
        return validate_contract_version(v, "process")


# =============================================================================
# Interface Contract (Discriminated Union)
# =============================================================================


class ContextProcessContract(BaseModel):
    """Interface contract for context-provider processes.

    Context providers supply context (cohort membership, experiment split)
    to downstream metrics and analysis.
    """

    model_config = ConfigDict(extra="forbid")

    contract_mode: Literal["context_provider"] = Field(
        default="context_provider", description="Must be 'context_provider'."
    )
    context_kind: ContextKind = Field(
        description="Kind of context: cohort_membership or experiment_split."
    )
    population_subject_ref: str = Field(
        description="Reference to the population subject (subject.*)."
    )
    membership_cardinality: MembershipCardinality = Field(
        description="Membership cardinality: exclusive_one (subject in one variant) "
        "or repeatable_many (subject can be in multiple variants)."
    )
    anchor_time_ref: str | None = Field(
        default=None, description="Reference to the anchor time semantic (time.*)."
    )
    exported_dimension_refs: list[str] | None = Field(
        default=None, description="List of dimensions exported by this process (dimension.*)."
    )

    @field_validator("population_subject_ref")
    @classmethod
    def validate_population_subject_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "subject", "population_subject_ref")

    @field_validator("anchor_time_ref")
    @classmethod
    def validate_anchor_time_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "time", "anchor_time_ref")
        return v

    @field_validator("exported_dimension_refs")
    @classmethod
    def validate_exported_dimension_refs_prefix(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for dim_ref in v:
                validate_ref_prefix(dim_ref, "dimension", "exported_dimension_refs")
        return v


class EntityProcessContract(BaseModel):
    """Interface contract for entity-stream processes.

    Entity streams emit entity instances (sessions, path matches, state assignments)
    to downstream metrics and analysis.
    """

    model_config = ConfigDict(extra="forbid")

    contract_mode: Literal["entity_stream"] = Field(
        default="entity_stream", description="Must be 'entity_stream'."
    )
    entity_ref: str = Field(description="Reference to the emitted entity (entity.*).")
    emitted_grain_ref: str = Field(description="Reference to the emitted grain (grain.*).")
    population_subject_ref: str = Field(
        description="Reference to the population subject (subject.*)."
    )
    subject_cardinality: SubjectCardinality = Field(
        description="Cardinality per subject: 'one' or 'many'."
    )
    anchor_time_ref: str | None = Field(
        default=None, description="Reference to the anchor time semantic (time.*)."
    )
    exported_dimension_refs: list[str] | None = Field(
        default=None, description="List of dimensions exported by this process (dimension.*)."
    )

    @field_validator("entity_ref")
    @classmethod
    def validate_entity_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "entity", "entity_ref")

    @field_validator("emitted_grain_ref")
    @classmethod
    def validate_emitted_grain_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "grain", "emitted_grain_ref")

    @field_validator("population_subject_ref")
    @classmethod
    def validate_population_subject_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "subject", "population_subject_ref")

    @field_validator("anchor_time_ref")
    @classmethod
    def validate_anchor_time_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "time", "anchor_time_ref")
        return v

    @field_validator("exported_dimension_refs")
    @classmethod
    def validate_exported_dimension_refs_prefix(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for dim_ref in v:
                validate_ref_prefix(dim_ref, "dimension", "exported_dimension_refs")
        return v


ProcessInterfaceContract = Annotated[
    ContextProcessContract | EntityProcessContract,
    Field(discriminator="contract_mode"),
]


# =============================================================================
# Subtype Payloads
# =============================================================================

# --- Experiment Context ---


class ExperimentVariant(BaseModel):
    """Variant definition for an experiment."""

    model_config = ConfigDict(extra="forbid")

    variant_key: str = Field(description="Unique key for this variant.")
    population_ref: str = Field(description="Reference to the variant population.")

    @field_validator("population_ref")
    @classmethod
    def validate_population_ref(cls, v: str) -> str:
        return validate_process_semantic_ref(v, "population_ref")


class ExperimentSplitBasis(BaseModel):
    """Split basis definition for an experiment."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="Split basis kind: assignment or exposure.")
    basis_ref: str = Field(description="Reference to the split basis event/predicate.")
    resolution: str = Field(description="Resolution: first or last.")

    @field_validator("basis_ref")
    @classmethod
    def validate_basis_ref(cls, v: str) -> str:
        return validate_process_semantic_ref(v, "basis_ref")


class ExperimentContextPayload(BaseModel):
    """Payload for experiment_context process type."""

    model_config = ConfigDict(extra="forbid")

    process_type: Literal["experiment_context"] = Field(
        default="experiment_context", description="Discriminator."
    )
    experiment_key: str = Field(description="Unique key for this experiment.")
    variants: list[ExperimentVariant] = Field(
        min_length=2, description="List of variants. At least 2 variants required."
    )
    split_basis: ExperimentSplitBasis = Field(description="Split basis definition.")
    analysis_window: WindowSpec | None = Field(
        default=None, description="Optional analysis window."
    )
    contamination_policy: str | None = Field(
        default=None, description="Contamination policy: allow, exclude_mixed_subjects, or strict."
    )
    expected_split: dict[str, int | float] | None = Field(
        default=None, description="Expected split ratios by variant key."
    )


# --- Cohort Definition ---


class CohortDefinitionPayload(BaseModel):
    """Payload for cohort_definition process type."""

    model_config = ConfigDict(extra="forbid")

    process_type: Literal["cohort_definition"] = Field(
        default="cohort_definition", description="Discriminator."
    )
    cohort_key: str = Field(description="Unique key for this cohort.")
    entry_population: PopulationSpec = Field(description="Entry population specification.")
    cohort_anchor_ref: str = Field(description="Reference to the cohort anchor time.")
    observation_window: WindowSpec | None = Field(
        default=None, description="Optional observation window."
    )
    return_population_ref: str | None = Field(
        default=None, description="Optional return population reference."
    )
    return_anchor_ref: str | None = Field(
        default=None, description="Optional return anchor time reference."
    )

    @field_validator("cohort_anchor_ref", "return_anchor_ref")
    @classmethod
    def validate_anchor_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_ref_prefix(v, "time", "anchor_ref")


# --- Funnel Definition ---


class StepGapSpec(BaseModel):
    """Maximum gap between steps in a funnel."""

    model_config = ConfigDict(extra="forbid")

    value: int | float = Field(description="Gap value.")
    unit: str = Field(description="Time unit: minute, hour, or day.")


class FunnelDefinitionPayload(BaseModel):
    """Payload for funnel_definition process type."""

    model_config = ConfigDict(extra="forbid")

    process_type: Literal["funnel_definition"] = Field(
        default="funnel_definition", description="Discriminator."
    )
    funnel_key: str = Field(description="Unique key for this funnel.")
    steps: list[StepSpec] = Field(
        min_length=2, description="List of funnel steps. At least 2 steps required."
    )
    ordering_rule: str | None = Field(default=None, description="Ordering rule: strict or weak.")
    counting_rule: str | None = Field(
        default=None, description="Counting rule: first_pass, all_passes, or first_success."
    )
    max_step_gap: StepGapSpec | None = Field(
        default=None, description="Optional maximum gap between steps."
    )
    conversion_step_key: str = Field(description="Key of the conversion step.")
    partition_scope: str | None = Field(
        default=None,
        description="Partition scope: same_process_instance, same_session, or cross_session.",
    )


# --- Session Contract ---


class IdleGapSpec(BaseModel):
    """Idle gap specification for session detection."""

    model_config = ConfigDict(extra="forbid")

    value: int | float = Field(description="Gap value.")
    unit: str = Field(description="Time unit: minute or hour.")


class SessionContractPayload(BaseModel):
    """Payload for session_contract process type."""

    model_config = ConfigDict(extra="forbid")

    process_type: Literal["session_contract"] = Field(
        default="session_contract", description="Discriminator."
    )
    session_key: str = Field(description="Unique key for this session type.")
    event_stream_ref: str = Field(description="Reference to the event stream.")
    included_event_refs: list[str] | None = Field(
        default=None, description="Optional list of included events."
    )
    excluded_event_refs: list[str] | None = Field(
        default=None, description="Optional list of excluded events."
    )
    start_ref: str | None = Field(default=None, description="Optional session start predicate.")
    continuation_ref: str | None = Field(
        default=None, description="Optional session continuation predicate."
    )
    close_ref: str | None = Field(default=None, description="Optional session close predicate.")
    idle_gap: IdleGapSpec | None = Field(
        default=None, description="Optional idle gap for session timeout."
    )
    canonical_session_ref: str | None = Field(
        default=None, description="Optional reference to canonical session definition."
    )

    @field_validator("event_stream_ref")
    @classmethod
    def validate_event_stream_ref(cls, v: str) -> str:
        return validate_process_semantic_ref(v, "event_stream_ref")

    @field_validator("included_event_refs", "excluded_event_refs")
    @classmethod
    def validate_event_refs(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [validate_process_semantic_ref(ref, "event_ref") for ref in v]

    @field_validator("start_ref", "continuation_ref", "close_ref", "canonical_session_ref")
    @classmethod
    def validate_session_refs(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_process_semantic_ref(v, "session_ref")


# --- Path Pattern ---


class PathLagSpec(BaseModel):
    """Maximum lag between path nodes."""

    model_config = ConfigDict(extra="forbid")

    value: int | float = Field(description="Lag value.")
    unit: str = Field(description="Time unit: minute, hour, or day.")


class PathPatternPayload(BaseModel):
    """Payload for path_pattern process type."""

    model_config = ConfigDict(extra="forbid")

    process_type: Literal["path_pattern"] = Field(
        default="path_pattern", description="Discriminator."
    )
    path_key: str = Field(description="Unique key for this path pattern.")
    nodes: list[StepSpec] = Field(
        min_length=2, description="List of path nodes. At least 2 nodes required."
    )
    match_mode: str | None = Field(
        default=None, description="Match mode: ordered, unordered, or contains_subsequence."
    )
    revisit_policy: str | None = Field(
        default=None, description="Revisit policy: allow, forbid, or compress."
    )
    max_path_length: int | float | None = Field(
        default=None, description="Optional maximum path length."
    )
    max_lag: PathLagSpec | None = Field(
        default=None, description="Optional maximum lag between nodes."
    )
    partition_scope: str | None = Field(
        default=None, description="Partition scope: same_session or cross_session."
    )


# --- Lifecycle State Machine ---


class LifecycleStateMachinePayload(BaseModel):
    """Payload for lifecycle_state_machine process type."""

    model_config = ConfigDict(extra="forbid")

    process_type: Literal["lifecycle_state_machine"] = Field(
        default="lifecycle_state_machine", description="Discriminator."
    )
    machine_key: str = Field(description="Unique key for this state machine.")
    states: list[StateSpec] = Field(
        min_length=1, description="List of states. At least 1 state required."
    )
    conflict_resolution: str | None = Field(
        default=None, description="Conflict resolution: highest_priority or first_match."
    )
    evaluation_anchor_ref: str | None = Field(
        default=None, description="Optional evaluation anchor time reference."
    )
    transition_anchor_ref: str | None = Field(
        default=None, description="Optional transition anchor time reference."
    )

    @field_validator("evaluation_anchor_ref", "transition_anchor_ref")
    @classmethod
    def validate_anchor_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_ref_prefix(v, "time", "anchor_ref")


# =============================================================================
# Payload Union
# =============================================================================

ProcessPayload = Annotated[
    ExperimentContextPayload
    | CohortDefinitionPayload
    | FunnelDefinitionPayload
    | SessionContractPayload
    | PathPatternPayload
    | LifecycleStateMachinePayload,
    Field(discriminator="process_type"),
]

_CONTEXT_PROVIDER_PROCESS_TYPES: set[ProcessType] = {
    "experiment_context",
    "cohort_definition",
}


# =============================================================================
# Request Models
# =============================================================================


class ProcessObjectCreateRequest(BaseModel):
    """Request to create a new process object."""

    model_config = ConfigDict(extra="forbid")

    header: ProcessObjectHeader = Field(description="Process object header.")
    interface_contract: ProcessInterfaceContract = Field(description="Process interface contract.")
    payload: ProcessPayload = Field(description="Process-type-specific payload.")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )

    @model_validator(mode="after")
    def validate_process_type_matches_payload(self) -> ProcessObjectCreateRequest:
        """Ensure header.process_type matches payload.process_type."""
        if self.header.process_type != self.payload.process_type:
            raise ValueError(
                f"header.process_type ({self.header.process_type}) must match "
                f"payload.process_type ({self.payload.process_type})"
            )
        return self

    @model_validator(mode="after")
    def validate_contract_mode_matches_process_type(self) -> ProcessObjectCreateRequest:
        """Ensure interface contract family matches process subtype."""
        expected_contract_mode: ContractMode = (
            "context_provider"
            if self.header.process_type in _CONTEXT_PROVIDER_PROCESS_TYPES
            else "entity_stream"
        )
        if self.interface_contract.contract_mode != expected_contract_mode:
            raise ValueError(
                f"interface_contract.contract_mode ({self.interface_contract.contract_mode}) "
                f"must be '{expected_contract_mode}' for process_type "
                f"'{self.header.process_type}'"
            )
        return self


class ProcessObjectUpdateRequest(BaseModel):
    """Request to update an existing process object.

    All fields are optional; only provided fields will be updated.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")
    catalog_metadata: CatalogMetadata | None = Field(
        default=None,
        description="Updated discovery-only catalog metadata.",
    )
    interface_contract: ProcessInterfaceContract | None = Field(
        default=None, description="New interface contract."
    )
    payload: ProcessPayload | None = Field(default=None, description="New payload.")


# =============================================================================
# Response Models
# =============================================================================


class ProcessObjectListItem(ObjectListItemBase):
    """Lightweight list item for process object endpoints.

    Includes header only, not full interface_contract or payload.
    """

    process_contract_id: str = Field(description="Internal ID of the process contract.")
    header: ProcessObjectHeader = Field(description="Process header (contains process_ref).")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )


class ProcessObjectResponse(ObjectResponseBase):
    """Response model for a process object.

    Includes all fields from storage plus catalog metadata.
    """

    process_contract_id: str = Field(description="Internal ID of the process contract.")
    header: ProcessObjectHeader = Field(description="Process object header.")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )
    interface_contract: ProcessInterfaceContract = Field(description="Process interface contract.")
    payload: ProcessPayload = Field(description="Process-type-specific payload.")


class ProcessObjectListResponse(ListResponseBase[ProcessObjectListItem]):
    """Response model for listing process objects."""


ProcessObjectListItemOrFull = ProcessObjectListItem | ProcessObjectResponse


class ProcessObjectListResponseFull(ListResponseBase[ProcessObjectListItemOrFull]):
    """Response model for listing process objects with detail=true."""
