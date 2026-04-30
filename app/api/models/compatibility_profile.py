"""Compiler Compatibility Profile models.

This module defines the API models for compiler compatibility profiles,
following the contract defined in docs/semantic/compiler-compatibility-profile.zh.md.

Compatibility profiles capture constraints that cannot be derived from object
contracts but affect compile-time composition validity.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import (
    CatalogMetadata,
    ContextKind,
    ContractMode,
    DimensionValueType,
    InferentialSampleSummary,
    ListResponseBase,
    ObjectListItemBase,
    ObjectResponseBase,
    ProfileKind,
    ProfileSchemaVersion,
    ProfileSubjectKind,
    RelationshipCardinality,
    RelationshipGrainCompatibilityKind,
    RelationshipTimeAlignmentKind,
    validate_canonical_entity_field_ref,
    validate_ref_prefix,
)


class RelationshipKeyAlignment(BaseModel):
    """Entity field pair used as the semantic key alignment surface."""

    model_config = ConfigDict(extra="forbid")

    left_field_ref: str = Field(description="Fully qualified left entity field ref.")
    right_field_ref: str = Field(description="Fully qualified right entity field ref.")
    alignment_kind: str = Field(
        default="equality",
        description="Controlled key alignment kind. v1 only supports equality.",
    )

    @field_validator("left_field_ref", "right_field_ref")
    @classmethod
    def validate_field_ref(cls, v: str) -> str:
        return validate_canonical_entity_field_ref(v)

    @field_validator("alignment_kind")
    @classmethod
    def validate_alignment_kind(cls, v: str) -> str:
        if v != "equality":
            raise ValueError(
                "relationship key_alignment v1 only supports alignment_kind='equality'"
            )
        return v


class RelationshipTimeAlignment(BaseModel):
    """Controlled time alignment declaration between two entity surfaces."""

    model_config = ConfigDict(extra="forbid")

    left_time_ref: str = Field(
        description="Left time semantic or fully qualified entity field ref."
    )
    right_time_ref: str = Field(
        description="Right time semantic or fully qualified entity field ref."
    )
    alignment_kind: RelationshipTimeAlignmentKind = Field(
        description="Controlled time alignment kind."
    )
    window: str | None = Field(
        default=None,
        description="Optional ISO-8601 duration for bounded or snapshot-window alignment.",
    )

    @field_validator("left_time_ref", "right_time_ref")
    @classmethod
    def validate_time_ref(cls, v: str) -> str:
        if v.startswith("time."):
            return validate_ref_prefix(v, "time")
        return validate_canonical_entity_field_ref(v)


class RelationshipGrainCompatibility(BaseModel):
    """Minimal grain compatibility declaration for a relationship."""

    model_config = ConfigDict(extra="forbid")

    left_grain_ref: str | None = Field(default=None, description="Optional left grain ref.")
    right_grain_ref: str | None = Field(default=None, description="Optional right grain ref.")
    compatibility: RelationshipGrainCompatibilityKind = Field(
        default="same_grain",
        description="Controlled grain compatibility kind.",
    )

    @field_validator("left_grain_ref", "right_grain_ref")
    @classmethod
    def validate_grain_ref(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "grain")
        return v


class SnapshotEffectiveWindowAlignment(BaseModel):
    """Snapshot effective-window alignment for event-to-snapshot relationships."""

    model_config = ConfigDict(extra="forbid")

    event_time_ref: str = Field(description="Event time semantic or entity field ref.")
    effective_from_ref: str = Field(description="Snapshot effective-from time or entity field ref.")
    effective_to_ref: str | None = Field(
        default=None,
        description="Optional snapshot effective-to time or entity field ref.",
    )
    inclusivity: str = Field(
        default="from_inclusive_to_exclusive",
        description="Controlled interval inclusivity. v1 supports from_inclusive_to_exclusive.",
    )

    @field_validator("event_time_ref", "effective_from_ref", "effective_to_ref")
    @classmethod
    def validate_time_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v.startswith("time."):
            return validate_ref_prefix(v, "time")
        return validate_canonical_entity_field_ref(v)

    @field_validator("inclusivity")
    @classmethod
    def validate_inclusivity(cls, v: str) -> str:
        if v != "from_inclusive_to_exclusive":
            raise ValueError(
                "snapshot effective-window alignment v1 only supports "
                "inclusivity='from_inclusive_to_exclusive'"
            )
        return v


class EntityRelationshipCreateRequest(BaseModel):
    """Create an entity relationship for cross-entity semantic composition."""

    model_config = ConfigDict(extra="forbid")

    relationship_ref: str = Field(
        description="Stable relationship reference. Must start with 'relationship.'."
    )
    display_name: str | None = Field(default=None, description="Optional display name.")
    description: str | None = Field(default=None, description="Optional description.")
    left_entity_ref: str = Field(description="Left entity ref.")
    right_entity_ref: str = Field(description="Right entity ref.")
    key_alignment: RelationshipKeyAlignment = Field(
        description="Required key alignment between entity fields."
    )
    time_alignment: RelationshipTimeAlignment | None = Field(
        default=None, description="Optional controlled time alignment."
    )
    cardinality: RelationshipCardinality = Field(description="Relationship cardinality.")
    grain_compatibility: RelationshipGrainCompatibility | None = Field(
        default=None, description="Optional controlled grain compatibility declaration."
    )
    snapshot_effective_window_alignment: SnapshotEffectiveWindowAlignment | None = Field(
        default=None,
        description="Optional snapshot effective-window alignment declaration.",
    )
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )

    @field_validator("relationship_ref")
    @classmethod
    def validate_relationship_ref(cls, v: str) -> str:
        return validate_ref_prefix(v, "relationship", "relationship_ref")

    @field_validator("left_entity_ref", "right_entity_ref")
    @classmethod
    def validate_entity_ref(cls, v: str) -> str:
        return validate_ref_prefix(v, "entity")


class EntityRelationshipUpdateRequest(BaseModel):
    """Update mutable relationship fields while preserving identity and endpoints."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    description: str | None = None
    key_alignment: RelationshipKeyAlignment | None = None
    time_alignment: RelationshipTimeAlignment | None = None
    cardinality: RelationshipCardinality | None = None
    grain_compatibility: RelationshipGrainCompatibility | None = None
    snapshot_effective_window_alignment: SnapshotEffectiveWindowAlignment | None = None
    catalog_metadata: CatalogMetadata | None = None


class EntityRelationshipListItem(ObjectListItemBase):
    """Lightweight relationship list item."""

    relationship_id: str
    relationship_ref: str
    left_entity_ref: str
    right_entity_ref: str
    cardinality: RelationshipCardinality
    catalog_metadata: CatalogMetadata = Field(default_factory=CatalogMetadata)


class EntityRelationshipResponse(ObjectResponseBase):
    """Detailed entity relationship response."""

    relationship_id: str
    relationship_ref: str
    display_name: str
    description: str
    left_entity_ref: str
    right_entity_ref: str
    key_alignment: RelationshipKeyAlignment
    time_alignment: RelationshipTimeAlignment | None = None
    cardinality: RelationshipCardinality
    grain_compatibility: RelationshipGrainCompatibility | None = None
    snapshot_effective_window_alignment: SnapshotEffectiveWindowAlignment | None = None
    catalog_metadata: CatalogMetadata = Field(default_factory=CatalogMetadata)


class EntityRelationshipListResponse(ListResponseBase[EntityRelationshipListItem]):
    """Response model for listing entity relationships."""


class ProfileGrainCompatibility(BaseModel):
    """Profile-level grain compatibility requirement."""

    model_config = ConfigDict(extra="forbid")

    required_grain_refs: list[str] | None = None
    compatibility: RelationshipGrainCompatibilityKind | None = None

    @field_validator("required_grain_refs")
    @classmethod
    def validate_required_grain_refs(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for ref in v:
                validate_ref_prefix(ref, "grain", "required_grain_refs")
        return v


class ProfileTimeCompatibility(BaseModel):
    """Profile-level controlled time compatibility requirement."""

    model_config = ConfigDict(extra="forbid")

    alignment_basis: str | None = None
    required_time_refs: list[str] | None = None

    @field_validator("required_time_refs")
    @classmethod
    def validate_required_time_refs(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for ref in v:
                if ref.startswith("time."):
                    validate_ref_prefix(ref, "time", "required_time_refs")
                else:
                    validate_canonical_entity_field_ref(ref, "required_time_refs")
        return v


class ProfileAggregationCompatibility(BaseModel):
    """Profile-level additivity/aggregation compatibility requirement."""

    model_config = ConfigDict(extra="forbid")

    allowed_methods: list[str] | None = None
    requires_additive_inputs: bool | None = None


class FieldProfileRequirement(BaseModel):
    """Profile-level requirement on an entity field profile."""

    model_config = ConfigDict(extra="forbid")

    field_ref: str
    required_value_type: DimensionValueType | None = None
    required_sensitivity_tags: list[str] | None = None
    nullable_allowed: bool | None = None

    @field_validator("field_ref")
    @classmethod
    def validate_field_ref(cls, v: str) -> str:
        return validate_canonical_entity_field_ref(v, "field_ref")


class GovernancePreflightRequirement(BaseModel):
    """Profile-level governance preflight declaration."""

    model_config = ConfigDict(extra="forbid")

    required_checks: list[str] = Field(default_factory=list)


# =============================================================================
# Process Requirement
# =============================================================================


class ProcessRequirement(BaseModel):
    """Metric requirements for a process.

    Captures what a metric requires from a process at compile time,
    beyond what can be derived from the object contracts.
    """

    model_config = ConfigDict(extra="forbid")

    contract_modes: list[ContractMode] | None = Field(
        default=None, description="Required contract modes: context_provider and/or entity_stream."
    )
    context_kinds: list[ContextKind] | None = Field(
        default=None,
        description="Required context kinds: cohort_membership and/or experiment_split.",
    )
    entity_refs: list[str] | None = Field(
        default=None, description="Required entity stream types (entity.*)."
    )
    population_subject_refs: list[str] | None = Field(
        default=None, description="Required population subject types (subject.*)."
    )
    required_relationship_refs: list[str] | None = Field(
        default=None,
        description="Required entity relationship refs for cross-entity composition.",
    )
    grain_compatibility: ProfileGrainCompatibility | None = Field(
        default=None,
        description="Optional grain compatibility requirement.",
    )
    time_compatibility: ProfileTimeCompatibility | None = Field(
        default=None,
        description="Optional time compatibility requirement.",
    )
    aggregation_compatibility: ProfileAggregationCompatibility | None = Field(
        default=None,
        description="Optional additivity/aggregation compatibility requirement.",
    )
    field_profile_requirements: list[FieldProfileRequirement] | None = Field(
        default=None,
        description="Optional field profile requirements.",
    )
    governance_preflight: GovernancePreflightRequirement | None = Field(
        default=None,
        description="Optional governance preflight requirements.",
    )

    @field_validator("entity_refs")
    @classmethod
    def validate_entity_refs_prefix(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for ref in v:
                validate_ref_prefix(ref, "entity", "entity_refs")
        return v

    @field_validator("population_subject_refs")
    @classmethod
    def validate_population_subject_refs_prefix(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for ref in v:
                validate_ref_prefix(ref, "subject", "population_subject_refs")
        return v

    @field_validator("required_relationship_refs")
    @classmethod
    def validate_required_relationship_refs_prefix(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for ref in v:
                validate_ref_prefix(ref, "relationship", "required_relationship_refs")
        return v


# =============================================================================
# Process Capability
# =============================================================================


class ProcessCapability(BaseModel):
    """Process capabilities for compile-time validation.

    Captures what a process can reliably provide for inferential workflows,
    beyond what can be derived from the object contract.
    """

    model_config = ConfigDict(extra="forbid")

    inferential_ready: bool | None = Field(
        default=None, description="Whether the process can enter validate/test workflows."
    )
    supported_sample_summaries: list[InferentialSampleSummary] | None = Field(
        default=None,
        description="Supported inferential summary types: "
        "numeric_sample_summary and/or rate_sample_summary.",
    )


# =============================================================================
# Profile Request/Response Models
# =============================================================================


class CompatibilityProfileCreateRequest(BaseModel):
    """Request to create a new compiler compatibility profile."""

    model_config = ConfigDict(extra="forbid")

    profile_ref: str = Field(
        description="Stable profile reference (e.g., 'compiler_profile.conversion_rate_requirement'). "
        "Must start with 'compiler_profile.'."
    )
    profile_kind: ProfileKind = Field(
        description="Kind of profile: requirement (for metrics) or capability (for processes/bindings)."
    )
    schema_version: ProfileSchemaVersion = Field(
        default="v1", description="Schema version. Currently only 'v1' is supported."
    )
    subject_kind: ProfileSubjectKind = Field(
        description="Kind of subject: metric, process, or binding."
    )
    subject_ref: str = Field(
        description="Reference to the subject object. "
        "Prefix must match subject_kind (metric.*, process.*, or binding.*)."
    )
    requirement: ProcessRequirement | None = Field(
        default=None, description="Requirement payload. Required when profile_kind='requirement'."
    )
    capability: ProcessCapability | None = Field(
        default=None, description="Capability payload. Required when profile_kind='capability'."
    )
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )

    @field_validator("profile_ref")
    @classmethod
    def validate_profile_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "compiler_profile", "profile_ref")

    @model_validator(mode="after")
    def validate_subject_ref_matches_kind(self) -> CompatibilityProfileCreateRequest:
        """Ensure subject_ref prefix matches subject_kind."""
        expected_prefixes = {
            "metric": "metric.",
            "process": "process.",
            "binding": "binding.",
        }
        expected = expected_prefixes.get(self.subject_kind)
        if expected and not self.subject_ref.startswith(expected):
            raise ValueError(
                f"subject_ref must start with '{expected}' for subject_kind '{self.subject_kind}'"
            )
        return self

    @model_validator(mode="after")
    def validate_profile_kind_matches_subject(self) -> CompatibilityProfileCreateRequest:
        """Validate legal combinations of subject_kind and profile_kind.

        Rules:
        - metric -> requirement
        - process -> capability
        - binding -> capability
        """
        valid_combinations = {
            ("metric", "requirement"),
            ("process", "capability"),
            ("binding", "capability"),
        }
        if (self.subject_kind, self.profile_kind) not in valid_combinations:
            raise ValueError(
                f"Invalid combination: subject_kind='{self.subject_kind}' cannot have "
                f"profile_kind='{self.profile_kind}'. "
                f"Valid combinations: metric->requirement, process->capability, binding->capability"
            )
        return self

    @model_validator(mode="after")
    def validate_payload_matches_kind(self) -> CompatibilityProfileCreateRequest:
        """Ensure correct payload is provided based on profile_kind."""
        if self.profile_kind == "requirement":
            if self.requirement is None:
                raise ValueError("requirement is required when profile_kind='requirement'")
            if self.capability is not None:
                raise ValueError("capability must be None when profile_kind='requirement'")
        elif self.profile_kind == "capability":
            if self.capability is None:
                raise ValueError("capability is required when profile_kind='capability'")
            if self.requirement is not None:
                raise ValueError("requirement must be None when profile_kind='capability'")
        return self


class CompatibilityProfileUpdateRequest(BaseModel):
    """Request to update an existing compatibility profile.

    Only requirement or capability can be updated; the profile structure
    (ref, kind, subject) cannot be changed.
    """

    model_config = ConfigDict(extra="forbid")

    requirement: ProcessRequirement | None = Field(
        default=None, description="New requirement payload. Only valid for requirement profiles."
    )
    capability: ProcessCapability | None = Field(
        default=None, description="New capability payload. Only valid for capability profiles."
    )
    catalog_metadata: CatalogMetadata | None = Field(
        default=None,
        description="Updated discovery-only catalog metadata.",
    )


class CompatibilityProfileRevalidateRequest(BaseModel):
    """Request to revalidate a compatibility profile against a subject revision."""

    subject_revision: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Subject revision to pin after revalidation. Defaults to the current active subject "
            "revision."
        ),
    )


class CompatibilityProfileListItem(ObjectListItemBase):
    """Lightweight list item for compatibility profile endpoints.

    Includes core identity fields only.
    """

    profile_id: str = Field(description="Internal ID of the profile.")
    profile_ref: str = Field(description="Stable profile reference.")
    subject_kind: ProfileSubjectKind = Field(
        description="Kind of subject: metric, process, or binding."
    )
    subject_ref: str = Field(description="Reference to the subject object.")
    system_managed: bool = Field(
        default=False, description="Whether the item is a builtin read-only compatibility surface."
    )
    catalog_source: str | None = Field(
        default=None,
        description="Optional discovery source identifier for builtin compatibility surfaces.",
    )
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )


class CompatibilityProfileResponse(ObjectResponseBase):
    """Response model for a compiler compatibility profile.

    Includes all fields from storage plus catalog metadata.
    """

    profile_id: str = Field(description="Internal ID of the profile.")
    profile_ref: str = Field(description="Stable profile reference.")
    profile_kind: ProfileKind = Field(description="Kind of profile: requirement or capability.")
    schema_version: ProfileSchemaVersion = Field(description="Schema version.")
    subject_kind: ProfileSubjectKind = Field(
        description="Kind of subject: metric, process, or binding."
    )
    subject_ref: str = Field(description="Reference to the subject object.")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )
    subject_revision: int | None = Field(
        default=None,
        description="Published revision of the bound subject when this profile was last published.",
    )
    requirement: ProcessRequirement | None = Field(
        default=None, description="Requirement payload, if profile_kind='requirement'."
    )
    capability: ProcessCapability | None = Field(
        default=None, description="Capability payload, if profile_kind='capability'."
    )
    system_managed: bool = Field(
        default=False,
        description="Whether the profile is a builtin read-only compatibility surface.",
    )
    catalog_source: str | None = Field(
        default=None,
        description="Optional discovery source identifier for builtin compatibility surfaces.",
    )


class CompatibilityProfileListResponse(ListResponseBase[CompatibilityProfileListItem]):
    """Response model for listing compatibility profiles."""


# =============================================================================
# Profile Trace (for compile reports)
# =============================================================================


class ProfileTrace(BaseModel):
    """Trace record for compile reports.

    Records which profiles were consulted during compilation
    and whether they were satisfied.
    """

    profile_ref: str = Field(description="Reference to the profile that was consulted.")
    applied: bool = Field(description="Whether the profile was applied during compilation.")
    reason: str = Field(
        description=(
            "Reason for the result: satisfied, missing, revision_mismatch, "
            "not_satisfied, or not_required."
        )
    )
    subject_ref: str = Field(description="Semantic object ref that the compiler checked.")
    subject_revision: int | None = Field(
        default=None,
        description="Published subject revision recorded on the consulted profile, if any.",
    )
    resolved_subject_revision: int | None = Field(
        default=None,
        description="Published subject revision resolved by the compiler for the current compile.",
    )
