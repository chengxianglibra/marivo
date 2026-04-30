"""Entity semantic object models.

This module defines the API models for entity objects,
following the contract defined in docs/semantic/entity-schema-contract.zh.md.

Entities define stable business objects that can be referenced by metrics,
process objects, and bindings.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import (
    CardinalityToParent,
    CarrierKind,
    CatalogMetadata,
    DescriptorCardinality,
    DimensionValueType,
    EntityKind,
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
# Entity Field and Binding Specification
# =============================================================================

_PHYSICAL_LOCATOR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_FORBIDDEN_EXPRESSION_PARAMETER_KEYS = {
    "expression",
    "lowering_template",
    "raw_sql",
    "sql",
    "sql_expression",
    "template",
}


def _validate_physical_locator_name(v: str, field_name: str) -> str:
    value = v.strip()
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    if value != v or not _PHYSICAL_LOCATOR_NAME_PATTERN.fullmatch(v):
        raise ValueError(
            f"{field_name} must be a simple column or alias name without whitespace or SQL syntax"
        )
    return v


def _validate_expression_parameter_value(value: Any, path: str = "parameters") -> None:
    if value is None or isinstance(value, str | int | bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain non-finite float values")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_expression_parameter_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("parameters keys must be strings")
            if key.strip().casefold() in _FORBIDDEN_EXPRESSION_PARAMETER_KEYS:
                forbidden = ", ".join(sorted(_FORBIDDEN_EXPRESSION_PARAMETER_KEYS))
                raise ValueError(f"parameters must not contain raw expression keys: {forbidden}")
            _validate_expression_parameter_value(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} must contain only JSON scalar, list, or object values")


class PhysicalExpressionLocatorSpec(BaseModel):
    """Controlled locator for an expression-backed entity field.

    This is not a SQL expression DSL. It only describes how source columns produce
    one execution-side column or alias.
    """

    model_config = ConfigDict(extra="forbid")

    expression_kind: Literal["cast", "date_trunc", "coalesce", "concat", "bucket"] = Field(
        description="Controlled expression operation used to derive the execution-side column."
    )
    input_columns: Annotated[
        list[str],
        Field(min_length=1, description="Source columns consumed by the controlled expression."),
    ]
    output_name: str | None = Field(
        default=None, description="Optional execution-side output column name or alias."
    )
    parameters: dict[str, Any] | None = Field(
        default=None, description="Controlled JSON parameters for the expression kind."
    )

    @field_validator("input_columns")
    @classmethod
    def validate_input_columns(cls, v: list[str]) -> list[str]:
        return [_validate_physical_locator_name(column, "input_columns") for column in v]

    @field_validator("output_name")
    @classmethod
    def validate_output_name(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_physical_locator_name(v, "output_name")
        return v

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            _validate_expression_parameter_value(v)
        return v


class EntityFieldSpec(BaseModel):
    """Thin field exposed by an entity contract.

    Fields are grounding surfaces only. Role semantics remain outside this contract.
    """

    model_config = ConfigDict(extra="forbid")

    field_ref: str = Field(description="Stable field reference. Must start with 'field.'.")
    display_name: str | None = Field(default=None, description="Optional display name.")
    description: str | None = Field(default=None, description="Optional description.")
    value_type: DimensionValueType | None = Field(
        default=None, description="Optional value type for the field."
    )
    nullable: bool | None = Field(default=None, description="Whether the field may be null.")
    unit: str | None = Field(default=None, description="Optional unit hint.")
    enum_hint: str | None = Field(default=None, description="Optional enum reference hint.")
    sample_values: list[str | int | float | bool] | None = Field(
        default=None, description="Optional sample values for discovery and review."
    )
    profile_summary: dict[str, Any] | None = Field(
        default=None, description="Optional lightweight profiling summary."
    )
    sensitivity_tags: list[str] | None = Field(
        default=None, description="Optional governance sensitivity tags."
    )
    physical_column: str | None = Field(
        default=None, description="Physical column name used to ground this field."
    )
    physical_expression_locator: PhysicalExpressionLocatorSpec | None = Field(
        default=None, description="Controlled locator for expression-backed fields."
    )

    @field_validator("field_ref")
    @classmethod
    def validate_field_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "field", "field_ref")

    @field_validator("enum_hint")
    @classmethod
    def validate_enum_hint_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "enum", "enum_hint")
        return v

    @field_validator("physical_column")
    @classmethod
    def validate_physical_column(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_physical_locator_name(v, "physical_column")
        return v

    @model_validator(mode="after")
    def validate_physical_locator(self) -> EntityFieldSpec:
        has_column = self.physical_column is not None
        has_expression = self.physical_expression_locator is not None
        if has_column and has_expression:
            raise ValueError(
                "Entity field must not define both physical_column and physical_expression_locator"
            )
        if not has_column and not has_expression:
            raise ValueError("Entity field requires one physical locator")
        return self


class EntityBindingSpec(BaseModel):
    """Minimal binding from an entity contract to one table or view."""

    model_config = ConfigDict(extra="forbid")

    source_object_ref: str | None = Field(
        default=None, description="Optional source object catalog reference."
    )
    source_object_fqn: str | None = Field(
        default=None, description="Optional fully-qualified source object name."
    )
    carrier_kind: CarrierKind = Field(description="Physical carrier kind: table or view.")

    @model_validator(mode="after")
    def validate_source_locator(self) -> EntityBindingSpec:
        if self.source_object_ref is None and self.source_object_fqn is None:
            raise ValueError("Either source_object_ref or source_object_fqn is required")
        return self


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
    fields: list[EntityFieldSpec] | None = Field(
        default=None, description="Thin fields exposed by this entity."
    )
    binding: EntityBindingSpec | None = Field(
        default=None, description="Optional single table/view binding for entity grounding."
    )

    @field_validator("primary_time_ref")
    @classmethod
    def validate_primary_time_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "time", "primary_time_ref")
        return v

    @model_validator(mode="after")
    def validate_unique_field_refs(self) -> EntityInterfaceContract:
        field_refs = [field.field_ref for field in self.fields or []]
        if len(field_refs) != len(set(field_refs)):
            raise ValueError("field_ref values must be unique within one entity")
        return self


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
    entity_kind: EntityKind = Field(
        default="business_entity",
        description=(
            "Discovery/readiness hint only. Does not drive SQL lowering, permissions, "
            "or field usage semantics."
        ),
    )
    interface_contract: EntityInterfaceContract = Field(description="Entity interface contract.")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata. Entity fields inherit the entity domain by default.",
    )


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
    entity_kind: EntityKind | None = Field(
        default=None,
        description=(
            "Updated discovery/readiness hint only. Does not drive SQL lowering, permissions, "
            "or field usage semantics."
        ),
    )
    catalog_metadata: CatalogMetadata | None = Field(
        default=None,
        description="Updated discovery-only catalog metadata.",
    )
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
    entity_kind: EntityKind = Field(
        default="business_entity",
        description="Discovery/readiness hint only.",
    )
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )


class TypedEntityResponse(ObjectResponseBase):
    """Response model for a typed entity object.

    Includes all fields from storage plus catalog metadata.
    """

    entity_contract_id: str = Field(description="Internal ID of the entity contract.")
    header: EntityHeader = Field(description="Entity header.")
    entity_kind: EntityKind = Field(
        default="business_entity",
        description="Discovery/readiness hint only.",
    )
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )
    interface_contract: EntityInterfaceContract = Field(description="Entity interface contract.")
    field_dependency_graph: dict[str, list[dict[str, Any]]] = Field(
        default_factory=dict,
        description="Reverse dependency entries keyed by entity field_ref.",
    )


class TypedEntityListResponse(ListResponseBase[TypedEntityListItem]):
    """Response model for listing typed entity objects (lightweight, default)."""


# Union for backward compatibility - accepts both lightweight and full items
TypedEntityListItemOrFull = TypedEntityListItem | TypedEntityResponse


class TypedEntityListResponseFull(ListResponseBase[TypedEntityListItemOrFull]):
    """Response model for listing typed entity objects with detail=true (accepts both formats)."""
