"""Entity semantic object models.

This module defines the API models for entity objects,
following the contract defined in docs/semantic/entity-schema-contract.zh.md.

Entities define stable business objects that can be referenced by metrics,
process objects, and bindings.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import (
    CardinalityToParent,
    DescriptorCardinality,
    IdStability,
    ListResponseBase,
    NullableKeyPolicy,
    ObjectHeaderBase,
    ObjectListItemBase,
    ObjectResponseBase,
    OwnershipSemantics,
    UniquenessScope,
    validate_contract_version,
    validate_ref_prefix,
)

# =============================================================================
# Entity Header
# =============================================================================


class EntityHeader(ObjectHeaderBase):
    """Header for an entity object.

    Defines the stable identity of an entity.
    """

    entity_ref: str = Field(
        description="Stable entity reference (e.g., 'entity.user'). Must start with 'entity.'."
    )
    entity_contract_version: str = Field(
        description="Contract version (e.g., 'entity.v4'). Must start with 'entity.'."
    )

    @field_validator("entity_ref")
    @classmethod
    def validate_entity_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "entity", "entity_ref")

    @field_validator("entity_contract_version")
    @classmethod
    def validate_version_prefix(cls, v: str) -> str:
        return validate_contract_version(v, "entity")


# =============================================================================
# Entity Identity Specification
# =============================================================================


class EntityIdentitySpec(BaseModel):
    """Specification for entity identity.

    Defines what constitutes the unique identity of an entity.
    """

    key_refs: Annotated[
        list[str],
        Field(
            min_length=1,
            description="List of semantic key references (key.*) that define entity identity. "
            "At least one key is required.",
        ),
    ]
    uniqueness_scope: UniquenessScope = Field(
        description="Scope of uniqueness: 'global' (unique across all instances) "
        "or 'parent_scoped' (unique within parent entity scope)."
    )
    id_stability: IdStability = Field(
        description="Stability of entity IDs: 'stable' (never reassigned), "
        "'reassignable' (may be reassigned), or 'ephemeral' (short-lived)."
    )
    nullable_key_policy: NullableKeyPolicy | None = Field(
        default=None,
        description="Policy for nullable keys: 'reject' (keys must not be null) "
        "or 'allow_partial' (some keys may be null).",
    )

    @field_validator("key_refs")
    @classmethod
    def validate_key_refs_prefix(cls, v: list[str]) -> list[str]:
        for key_ref in v:
            validate_ref_prefix(key_ref, "key", "key_refs")
        return v


# =============================================================================
# Entity Hierarchy Specification
# =============================================================================


class EntityHierarchySpec(BaseModel):
    """Specification for entity hierarchy relationships.

    Defines parent-child relationships between entities.
    """

    parent_entity_ref: str | None = Field(
        default=None,
        description="Reference to the parent entity (entity.*). "
        "If set, cardinality_to_parent and ownership_semantics are required.",
    )
    cardinality_to_parent: CardinalityToParent | None = Field(
        default=None, description="Cardinality to parent: 'one_to_one' or 'many_to_one'."
    )
    ownership_semantics: OwnershipSemantics | None = Field(
        default=None,
        description="Ownership semantics: 'belongs_to', 'contains', or 'derives_from'.",
    )

    @field_validator("parent_entity_ref")
    @classmethod
    def validate_parent_entity_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "entity", "parent_entity_ref")
        return v

    @model_validator(mode="after")
    def validate_hierarchy_consistency(self) -> EntityHierarchySpec:
        """If parent_entity_ref is set, require cardinality_to_parent and ownership_semantics."""
        if self.parent_entity_ref is not None:
            if self.cardinality_to_parent is None:
                raise ValueError("cardinality_to_parent is required when parent_entity_ref is set")
            if self.ownership_semantics is None:
                raise ValueError("ownership_semantics is required when parent_entity_ref is set")
        return self


# =============================================================================
# Stable Descriptor Specification
# =============================================================================


class StableDescriptorSpec(BaseModel):
    """Specification for a stable descriptor of an entity.

    Stable descriptors are dimensions that stably describe an entity
    and can be used without as-of semantics.
    """

    dimension_ref: str = Field(
        description="Reference to the dimension (dimension.*) that describes this entity."
    )
    cardinality: DescriptorCardinality | None = Field(
        default=None,
        description="Cardinality of this descriptor: 'one' (single-valued) or 'many' (multi-valued).",
    )

    @field_validator("dimension_ref")
    @classmethod
    def validate_dimension_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "dimension", "dimension_ref")


# =============================================================================
# Entity Interface Contract
# =============================================================================


class EntityInterfaceContract(BaseModel):
    """Interface contract for an entity.

    Combines identity, hierarchy, time reference, and stable descriptors.
    """

    identity: EntityIdentitySpec = Field(description="Entity identity specification.")
    hierarchy: EntityHierarchySpec | None = Field(
        default=None, description="Optional hierarchy specification."
    )
    primary_time_ref: str | None = Field(
        default=None, description="Reference to the primary time semantic (time.*) for this entity."
    )
    stable_descriptors: list[StableDescriptorSpec] | None = Field(
        default=None, description="List of stable descriptors for this entity."
    )

    @field_validator("primary_time_ref")
    @classmethod
    def validate_primary_time_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "time", "primary_time_ref")
        return v


# =============================================================================
# Request Models
# =============================================================================


class TypedEntityCreateRequest(BaseModel):
    """Request to create a new typed entity."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "header": {
                        "entity_ref": "entity.user",
                        "display_name": "User",
                        "entity_contract_version": "entity.v4",
                    },
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.user_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        }
                    },
                }
            ]
        }
    )

    header: EntityHeader = Field(description="Entity header.")
    interface_contract: EntityInterfaceContract = Field(description="Entity interface contract.")


class TypedEntityUpdateRequest(BaseModel):
    """Request to update an existing typed entity.

    All fields are optional; only provided fields will be updated.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "display_name": "User",
                    "interface_contract": {
                        "identity": {
                            "key_refs": ["key.user_id"],
                            "uniqueness_scope": "global",
                            "id_stability": "stable",
                        }
                    },
                }
            ]
        }
    )

    display_name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")
    interface_contract: EntityInterfaceContract | None = Field(
        default=None,
        description="New interface contract. Replaces the entire contract if provided.",
    )


# =============================================================================
# Response Models
# =============================================================================


class TypedEntityListItem(ObjectListItemBase):
    """Lightweight list item for entity endpoints.

    Includes header only, not full interface_contract.
    """

    entity_contract_id: str = Field(description="Internal ID of the entity contract.")
    header: EntityHeader = Field(description="Entity header (contains entity_ref).")


class TypedEntityResponse(ObjectResponseBase):
    """Response model for a typed entity object.

    Includes all fields from storage plus catalog metadata.
    """

    entity_contract_id: str = Field(description="Internal ID of the entity contract.")
    header: EntityHeader = Field(description="Entity header.")
    interface_contract: EntityInterfaceContract = Field(description="Entity interface contract.")


class TypedEntityListResponse(ListResponseBase[TypedEntityListItem]):
    """Response model for listing typed entity objects (lightweight, default)."""


# Union for backward compatibility - accepts both lightweight and full items
TypedEntityListItemOrFull = TypedEntityListItem | TypedEntityResponse


class TypedEntityListResponseFull(ListResponseBase[TypedEntityListItemOrFull]):
    """Response model for listing typed entity objects with detail=true (accepts both formats)."""
