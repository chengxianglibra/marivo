"""Compiler Compatibility Profile models.

This module defines the API models for compiler compatibility profiles,
following the contract defined in docs/semantic/compiler-compatibility-profile.zh.md.

Compatibility profiles capture constraints that cannot be derived from object
contracts but affect compile-time composition validity.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import (
    ContextKind,
    ContractMode,
    InferentialSampleSummary,
    ListResponseBase,
    ObjectListItemBase,
    ObjectResponseBase,
    ProfileKind,
    ProfileSchemaVersion,
    ProfileSubjectKind,
    validate_ref_prefix,
)

# =============================================================================
# Process Requirement
# =============================================================================


class ProcessRequirement(BaseModel):
    """Metric requirements for a process.

    Captures what a metric requires from a process at compile time,
    beyond what can be derived from the object contracts.
    """

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


# =============================================================================
# Process Capability
# =============================================================================


class ProcessCapability(BaseModel):
    """Process capabilities for compile-time validation.

    Captures what a process can reliably provide for inferential workflows,
    beyond what can be derived from the object contract.
    """

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

    requirement: ProcessRequirement | None = Field(
        default=None, description="New requirement payload. Only valid for requirement profiles."
    )
    capability: ProcessCapability | None = Field(
        default=None, description="New capability payload. Only valid for capability profiles."
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
