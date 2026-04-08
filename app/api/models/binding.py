"""Typed Binding semantic object models.

This module defines the API models for typed binding objects,
following the contract defined in docs/semantic/typed-binding-contract.zh.md.

Typed bindings connect semantic objects (entities, metrics, processes)
to their physical carriers (tables, views) with typed field mappings.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import (
    BindingRole,
    BindingScope,
    Cardinality,
    CarrierKind,
    ConsumptionBehavior,
    ConsumptionPolicyType,
    JoinKind,
    ListResponseBase,
    NullabilityPolicy,
    ObjectHeaderBase,
    ObjectResponseBase,
    RepeatedValuePolicy,
    TargetKind,
    validate_contract_version,
    validate_ref_prefix,
)
from .time import TimeSurfaceSpec

# =============================================================================
# Binding Header
# =============================================================================


class BindingHeader(ObjectHeaderBase):
    """Header for a typed binding object.

    Defines the stable identity and scope of a binding.
    """

    binding_ref: str = Field(
        description="Stable binding reference (e.g., 'binding.user_identity'). "
        "Must start with 'binding.'."
    )
    binding_scope: BindingScope = Field(
        description="Scope of the binding: entity, process_object, or metric."
    )
    bound_object_ref: str = Field(
        description="Reference to the bound semantic object. "
        "Prefix must match binding_scope (entity.*, process.*, or metric.*)."
    )
    binding_contract_version: str = Field(
        description="Contract version (e.g., 'binding.v2'). Must start with 'binding.'."
    )

    @field_validator("binding_ref")
    @classmethod
    def validate_binding_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "binding", "binding_ref")

    @field_validator("binding_contract_version")
    @classmethod
    def validate_version_prefix(cls, v: str) -> str:
        return validate_contract_version(v, "binding")

    @model_validator(mode="after")
    def validate_bound_object_ref_matches_scope(self) -> BindingHeader:
        """Ensure bound_object_ref prefix matches binding_scope."""
        expected_prefixes = {
            "entity": "entity.",
            "process_object": "process.",
            "metric": "metric.",
        }
        expected = expected_prefixes.get(self.binding_scope)
        if expected and not self.bound_object_ref.startswith(expected):
            raise ValueError(
                f"bound_object_ref must start with '{expected}' for "
                f"binding_scope '{self.binding_scope}'"
            )
        return self


# =============================================================================
# Binding Import
# =============================================================================


class BindingImport(BaseModel):
    """Import declaration for a binding.

    Declares dependencies on other bindings that provide required refs.
    """

    import_key: str = Field(description="Local key used to reference the imported binding.")
    binding_ref: str = Field(description="Reference to the imported binding (binding.*).")
    required_ref_prefixes: list[str] | None = Field(
        default=None, description="List of ref prefixes required from the imported binding."
    )

    @field_validator("binding_ref")
    @classmethod
    def validate_binding_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "binding", "binding_ref")


# =============================================================================
# Field Surface
# =============================================================================


class FieldSurfaceSpec(BaseModel):
    """Specification for a field surface exposed by a carrier.

    Field surfaces map semantic field refs to physical column names.
    """

    surface_ref: str = Field(
        description="Surface reference (e.g., 'field.user_id'). Must start with 'field.'."
    )
    physical_name: str = Field(description="Physical column name in the carrier.")
    field_type: str | None = Field(default=None, description="Optional field type information.")

    @field_validator("surface_ref")
    @classmethod
    def validate_surface_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "field", "surface_ref")


# =============================================================================
# Carrier Binding
# =============================================================================


class CarrierBinding(BaseModel):
    """Binding to a carrier (table or view).

    Carriers are the physical tables/views that back semantic objects.
    """

    binding_key: str = Field(description="Local key for this carrier binding within the binding.")
    source_object_ref: str | None = Field(
        default=None, description="Optional reference to a source object catalog entry."
    )
    carrier_kind: CarrierKind = Field(description="Kind of carrier: table or view.")
    carrier_locator: str = Field(description="Internal locator for the carrier (e.g., FQN).")
    binding_role: BindingRole = Field(description="Role of this carrier: primary or auxiliary.")
    semantic_role_ref: str | None = Field(
        default=None, description="Optional semantic role reference (e.g., assignment, exposure)."
    )
    grain_ref: str | None = Field(default=None, description="Optional grain reference (grain.*).")
    primary_entity_ref: str | None = Field(
        default=None, description="Optional primary entity reference (entity.*)."
    )
    row_filter_refs: list[str] | None = Field(
        default=None, description="Optional row filter references."
    )
    freshness_policy_ref: str | None = Field(
        default=None, description="Optional freshness policy reference."
    )
    field_surfaces: list[FieldSurfaceSpec] | None = Field(
        default=None, description="List of field surfaces exposed by this carrier."
    )
    time_surfaces: list[TimeSurfaceSpec] | None = Field(
        default=None, description="List of time surfaces exposed by this carrier."
    )

    @field_validator("grain_ref")
    @classmethod
    def validate_grain_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "grain", "grain_ref")
        return v

    @field_validator("primary_entity_ref")
    @classmethod
    def validate_primary_entity_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "entity", "primary_entity_ref")
        return v


# =============================================================================
# Binding Target
# =============================================================================


class BindingTarget(BaseModel):
    """Typed target for a field binding.

    Replaces the legacy target_path string with a structured target.
    """

    target_kind: TargetKind = Field(
        description="Kind of target: identity_key, primary_time, stable_descriptor, "
        "population_subject, analysis_window_anchor, process_context, or metric_input."
    )
    target_key: str = Field(
        description="Key identifying the specific target (e.g., 'key.user_id', 'time.exposure_time')."
    )
    context_ref: str | None = Field(
        default=None, description="Optional context reference for multi-dimensional targets."
    )


# =============================================================================
# Field Binding
# =============================================================================


class FieldBinding(BaseModel):
    """Binding of a carrier field to a semantic target.

    Maps a physical field to a semantic contract target.
    """

    carrier_binding_key: str = Field(
        description="Key of the carrier binding that provides this field."
    )
    target: BindingTarget = Field(description="Typed target for this binding.")
    semantic_ref: str = Field(
        description="Semantic reference for this field (e.g., 'key.user_id')."
    )
    surface_ref: str = Field(description="Reference to the field surface (field.*).")
    field_type_ref: str | None = Field(default=None, description="Optional field type reference.")
    nullability_policy: NullabilityPolicy | None = Field(
        default=None, description="Policy for null values: reject, allow, or impute."
    )
    repeated_value_policy: RepeatedValuePolicy | None = Field(
        default=None,
        description="Policy for repeated values: take_first, take_last, aggregate, or explode.",
    )

    @field_validator("surface_ref")
    @classmethod
    def validate_surface_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "field", "surface_ref")


# =============================================================================
# Join Relation
# =============================================================================


class JoinRelation(BaseModel):
    """Join relation between two carrier bindings.

    Defines how carriers are connected via semantic keys.
    """

    relation_key: str = Field(description="Local key for this join relation.")
    left_binding_key: str = Field(description="Key of the left carrier binding.")
    right_binding_key: str = Field(description="Key of the right carrier binding.")
    join_kind: JoinKind | None = Field(
        default=None, description="Kind of join: inner, left, semi, or anti."
    )
    key_ref_pairs: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Pairs of semantic key refs to join on [(left_key, right_key), ...].",
    )
    cardinality: Cardinality | None = Field(
        default=None,
        description="Cardinality of the join: one_to_one, many_to_one, one_to_many, or many_to_many.",
    )
    temporal_constraint_refs: list[str] | None = Field(
        default=None, description="Optional temporal constraint references."
    )
    compatibility_rule_refs: list[str] | None = Field(
        default=None, description="Optional compatibility rule references."
    )


# =============================================================================
# Consumption Policy
# =============================================================================


class ConsumptionPolicySpec(BaseModel):
    """Policy for consuming data from carriers.

    Defines late arrival, incomplete window, and other consumption behaviors.
    """

    policy_key: str = Field(description="Local key for this policy.")
    policy_type: ConsumptionPolicyType = Field(
        description="Type of policy: late_arrival_policy or incomplete_window_policy."
    )
    policy_target_path: str = Field(description="Path to the target this policy applies to.")
    anchor_ref: str | None = Field(
        default=None, description="Optional anchor time reference for the policy."
    )
    grace_period_ref: str | None = Field(
        default=None, description="Optional grace period reference."
    )
    behavior: ConsumptionBehavior | None = Field(
        default=None,
        description="Behavior: exclude_open_subjects, clip_to_window, or keep_partial.",
    )


# =============================================================================
# Binding Interface Contract
# =============================================================================


class BindingInterfaceContract(BaseModel):
    """Interface contract for a typed binding.

    Combines imports, carriers, field bindings, join relations, and policies.
    """

    imports: list[BindingImport] = Field(
        default_factory=list, description="List of binding imports."
    )
    carrier_bindings: list[CarrierBinding] = Field(
        default_factory=list, description="List of carrier bindings."
    )
    field_bindings: list[FieldBinding] = Field(
        default_factory=list, description="List of field bindings."
    )
    join_relations: list[JoinRelation] = Field(
        default_factory=list, description="List of join relations between carriers."
    )
    consumption_policies: list[ConsumptionPolicySpec] = Field(
        default_factory=list, description="List of consumption policies."
    )


# =============================================================================
# Request Models
# =============================================================================


class TypedBindingCreateRequest(BaseModel):
    """Request to create a new typed binding."""

    header: BindingHeader = Field(description="Binding header.")
    interface_contract: BindingInterfaceContract = Field(description="Binding interface contract.")


class TypedBindingUpdateRequest(BaseModel):
    """Request to update an existing typed binding.

    All fields are optional; only provided fields will be updated.
    """

    display_name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")
    interface_contract: BindingInterfaceContract | None = Field(
        default=None, description="New interface contract."
    )


# =============================================================================
# Response Models
# =============================================================================


class TypedBindingResponse(ObjectResponseBase):
    """Response model for a typed binding object.

    Includes all fields from storage plus catalog metadata.
    """

    binding_id: str = Field(description="Internal ID of the binding.")
    header: BindingHeader = Field(description="Binding header.")
    interface_contract: BindingInterfaceContract = Field(description="Binding interface contract.")


class TypedBindingListResponse(ListResponseBase[TypedBindingResponse]):
    """Response model for listing typed binding objects."""
