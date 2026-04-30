"""Dimension semantic object models.

This module defines the API models for dimension objects,
following the contract defined in docs/semantic/dimension-schema-contract.zh.md.

Dimensions define analysis axes that can be used for grouping in metrics
and exported by process objects.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import (
    CatalogMetadata,
    DimensionDomainKind,
    DimensionValueType,
    HierarchyType,
    ListResponseBase,
    ObjectHeaderBase,
    ObjectListItemBase,
    ObjectResponseBase,
    SemanticRole,
    StructureKind,
    validate_canonical_entity_field_ref,
    validate_contract_version,
    validate_ref_prefix,
)

# =============================================================================
# Dimension Header
# =============================================================================


class DimensionHeader(ObjectHeaderBase):
    """Header for a dimension object.

    Defines the stable identity of a dimension.
    """

    model_config = ConfigDict(extra="forbid")

    dimension_ref: str = Field(
        description="Stable dimension reference (e.g., 'dimension.country'). "
        "Must start with 'dimension.'."
    )
    dimension_contract_version: str = Field(
        description="Contract version (e.g., 'dimension.v1'). Must start with 'dimension.'."
    )

    @field_validator("dimension_ref")
    @classmethod
    def validate_dimension_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "dimension", "dimension_ref")

    @field_validator("dimension_contract_version")
    @classmethod
    def validate_version_prefix(cls, v: str) -> str:
        return validate_contract_version(v, "dimension")


# =============================================================================
# Dimension Value Domain Specification
# =============================================================================


class DimensionValueDomainSpec(BaseModel):
    """Specification for the value domain of a dimension.

    Defines the structure, type, and governance of dimension values.
    """

    structure_kind: StructureKind = Field(
        description="Structural organization of values: flat, hierarchical, ordinal, or time_derived."
    )
    semantic_role: SemanticRole | None = Field(
        default=None,
        description="Behavioral role of the dimension: category, label, state, variant, or metric.",
    )
    value_type: DimensionValueType = Field(
        description="Type of dimension values: string, integer, number, boolean, date, or datetime."
    )
    domain_kind: DimensionDomainKind = Field(
        description="Whether values come from an open or enumerated (governed) domain."
    )
    enum_set_ref: str | None = Field(
        default=None,
        description="Reference to the enum set (enum.*) for enumerated domains. "
        "Required when domain_kind='enumerated'.",
    )
    enum_version: str | None = Field(
        default=None,
        description="Version of the enum set to use. Required when domain_kind='enumerated'.",
    )

    @field_validator("enum_set_ref")
    @classmethod
    def validate_enum_set_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "enum", "enum_set_ref")
        return v

    @model_validator(mode="after")
    def validate_enumerated_domain(self) -> DimensionValueDomainSpec:
        """If domain_kind is enumerated, require enum_set_ref and enum_version."""
        if self.domain_kind == "enumerated":
            if not self.enum_set_ref:
                raise ValueError("enum_set_ref is required when domain_kind='enumerated'")
            if not self.enum_version:
                raise ValueError("enum_version is required when domain_kind='enumerated'")
        return self


# =============================================================================
# Dimension Hierarchy Specification
# =============================================================================


class DimensionHierarchySpec(BaseModel):
    """Specification for dimension hierarchy relationships.

    Defines how this dimension rolls up to parent dimensions.
    """

    hierarchy_type: HierarchyType = Field(
        description="Type of hierarchy: flat, parent_child, ordinal, or calendar_rollup."
    )
    parent_dimension_ref: str | None = Field(
        default=None,
        description="Reference to the parent dimension for roll-up. "
        "Required when hierarchy_type is 'parent_child', 'ordinal', or 'calendar_rollup'.",
    )

    @field_validator("parent_dimension_ref")
    @classmethod
    def validate_parent_dimension_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "dimension", "parent_dimension_ref")
        return v

    @model_validator(mode="after")
    def validate_hierarchy_parent(self) -> DimensionHierarchySpec:
        """If hierarchy_type implies a parent, require parent_dimension_ref."""
        if (
            self.hierarchy_type in ("parent_child", "ordinal", "calendar_rollup")
            and not self.parent_dimension_ref
        ):
            raise ValueError(
                f"parent_dimension_ref is required when hierarchy_type='{self.hierarchy_type}'"
            )
        return self


# =============================================================================
# Dimension Grouping Contract
# =============================================================================


class DimensionGroupingContract(BaseModel):
    """Specification for whether a dimension supports grouping."""

    supports_grouping: bool = Field(
        description="Whether this dimension can be used as a grouping axis."
    )


# =============================================================================
# Time-Derived Requirement Specification
# =============================================================================


class TimeDerivedRequirementSpec(BaseModel):
    """Specification for time-derived dimensions.

    Time-derived dimensions require a time anchor from the consuming object
    (entity, metric, or process) to derive their values.
    """

    required_time_anchor_ref: str = Field(
        description="Reference to the required time anchor (time.*). "
        "The consuming object must provide this time anchor."
    )

    @field_validator("required_time_anchor_ref")
    @classmethod
    def validate_time_anchor_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "time", "required_time_anchor_ref")


# =============================================================================
# Dimension Interface Contract
# =============================================================================


class DimensionInterfaceContract(BaseModel):
    """Interface contract for a dimension.

    Combines value domain, hierarchy, grouping, and time-derived requirements.
    """

    model_config = ConfigDict(extra="forbid")

    value_domain: DimensionValueDomainSpec = Field(description="Specification of the value domain.")
    source_field_ref: str | None = Field(
        default=None,
        description=(
            "Optional entity field that grounds this analysis axis. Must use "
            "entity.<entity>.field.<field>. Dimensions never bind physical columns directly."
        ),
    )
    hierarchy: DimensionHierarchySpec | None = Field(
        default=None, description="Optional hierarchy specification."
    )
    grouping: DimensionGroupingContract | None = Field(
        default=None, description="Optional grouping contract."
    )
    time_derived_requirement: TimeDerivedRequirementSpec | None = Field(
        default=None,
        description="Required for time_derived dimensions. Specifies the required time anchor.",
    )

    @field_validator("source_field_ref")
    @classmethod
    def validate_source_field_ref(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_canonical_entity_field_ref(v, "source_field_ref")
        return v

    @model_validator(mode="after")
    def validate_time_derived(self) -> DimensionInterfaceContract:
        """If structure_kind is time_derived, require time_derived_requirement."""
        if self.value_domain.structure_kind == "time_derived" and not self.time_derived_requirement:
            raise ValueError(
                "time_derived_requirement is required when structure_kind='time_derived'"
            )
        return self


# =============================================================================
# Request Models
# =============================================================================


class DimensionCreateRequest(BaseModel):
    """Request to create a new dimension."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "header": {
                        "dimension_ref": "dimension.country",
                        "display_name": "Country",
                        "dimension_contract_version": "dimension.v1",
                    },
                    "interface_contract": {
                        "source_field_ref": "entity.user.field.country",
                        "value_domain": {
                            "structure_kind": "flat",
                            "semantic_role": "category",
                            "value_type": "string",
                            "domain_kind": "open",
                        },
                        "grouping": {"supports_grouping": True},
                    },
                }
            ]
        },
    )

    header: DimensionHeader = Field(description="Dimension header.")
    interface_contract: DimensionInterfaceContract = Field(
        description="Dimension interface contract."
    )
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )


class DimensionUpdateRequest(BaseModel):
    """Request to update an existing dimension.

    All fields are optional; only provided fields will be updated.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")
    catalog_metadata: CatalogMetadata | None = Field(
        default=None,
        description="Updated discovery-only catalog metadata.",
    )
    interface_contract: DimensionInterfaceContract | None = Field(
        default=None,
        description="New interface contract. Replaces the entire contract if provided.",
    )


# =============================================================================
# Response Models
# =============================================================================


class DimensionListItem(ObjectListItemBase):
    """Lightweight list item for dimension endpoints.

    Includes header only, not full interface_contract.
    """

    dimension_contract_id: str = Field(description="Internal ID of the dimension contract.")
    header: DimensionHeader = Field(description="Dimension header (contains dimension_ref).")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )


class DimensionResponse(ObjectResponseBase):
    """Response model for a dimension object.

    Includes all fields from storage plus catalog metadata.
    """

    dimension_contract_id: str = Field(description="Internal ID of the dimension contract.")
    header: DimensionHeader = Field(description="Dimension header.")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )
    interface_contract: DimensionInterfaceContract = Field(
        description="Dimension interface contract."
    )


class DimensionListResponse(ListResponseBase[DimensionListItem]):
    """Response model for listing dimension objects."""


DimensionListItemOrFull = DimensionListItem | DimensionResponse


class DimensionListResponseFull(ListResponseBase[DimensionListItemOrFull]):
    """Response model for listing dimension objects with detail=true."""
