"""Time semantic object models.

This module defines the API models for time semantic objects,
following the contract defined in docs/semantic/time-schema-contract.zh.md.

Time objects represent stable time semantics that can be referenced
by entities, metrics, process objects, dimensions, and bindings.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import (
    CatalogMetadata,
    ListResponseBase,
    ObjectHeaderBase,
    ObjectListItemBase,
    ObjectResponseBase,
    TimeGranularity,
    TimeSemanticRole,
    validate_canonical_entity_field_ref,
    validate_contract_version,
    validate_ref_prefix,
)

# =============================================================================
# Time Semantic Header
# =============================================================================


class TimeSemanticHeader(ObjectHeaderBase):
    """Header for a time semantic object.

    This defines the stable identity and roles of a time semantic.
    Time semantics can serve multiple roles: business_anchor, measurement,
    and/or operational_support.
    """

    model_config = ConfigDict(extra="forbid")

    time_ref: str = Field(
        description="Stable time semantic reference (e.g., 'time.exposure_time'). "
        "Must start with 'time.'."
    )
    semantic_roles: Annotated[
        list[TimeSemanticRole],
        Field(
            min_length=1,
            description="Roles this time semantic can serve. Must be non-empty. "
            "Roles are: business_anchor, measurement, operational_support.",
        ),
    ]
    time_contract_version: str = Field(
        description="Contract version (e.g., 'time.v1'). Must start with 'time.'."
    )
    source_field_ref: str | None = Field(
        default=None,
        description=(
            "Optional entity field that grounds this time semantic. Must use "
            "entity.<entity>.field.<field>. Time objects never bind physical columns directly."
        ),
    )

    @field_validator("time_ref")
    @classmethod
    def validate_time_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "time", "time_ref")

    @field_validator("time_contract_version")
    @classmethod
    def validate_version_prefix(cls, v: str) -> str:
        return validate_contract_version(v, "time")

    @field_validator("semantic_roles")
    @classmethod
    def validate_roles_not_empty(cls, v: list[TimeSemanticRole]) -> list[TimeSemanticRole]:
        if not v:
            raise ValueError("semantic_roles must be non-empty")
        return v

    @field_validator("source_field_ref")
    @classmethod
    def validate_source_field_ref(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_canonical_entity_field_ref(v, "source_field_ref")
        return v


# =============================================================================
# Request Models
# =============================================================================


class TimeCreateRequest(BaseModel):
    """Request to create a new time semantic object."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "header": {
                        "time_ref": "time.signup_time",
                        "display_name": "Signup Time",
                        "semantic_roles": ["business_anchor"],
                        "time_contract_version": "time.v1",
                        "source_field_ref": "entity.user.field.signup_time",
                    }
                }
            ]
        },
    )

    header: TimeSemanticHeader = Field(
        description="Time semantic header containing identity and roles."
    )
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )


class TimeUpdateRequest(BaseModel):
    """Request to update an existing time semantic object.

    All fields are optional; only provided fields will be updated.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")
    catalog_metadata: CatalogMetadata | None = Field(
        default=None,
        description="Updated discovery-only catalog metadata.",
    )
    semantic_roles: list[TimeSemanticRole] | None = Field(
        default=None, description="New semantic roles. Must be non-empty if provided."
    )
    source_field_ref: str | None = Field(
        default=None,
        description="Updated entity field ref that grounds this time semantic.",
    )

    @field_validator("semantic_roles")
    @classmethod
    def validate_roles_not_empty(
        cls, v: list[TimeSemanticRole] | None
    ) -> list[TimeSemanticRole] | None:
        if v is not None and not v:
            raise ValueError("semantic_roles must be non-empty if provided")
        return v

    @field_validator("source_field_ref")
    @classmethod
    def validate_source_field_ref(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_canonical_entity_field_ref(v, "source_field_ref")
        return v


# =============================================================================
# Response Models
# =============================================================================


class TimeListItem(ObjectListItemBase):
    """Lightweight list item for time semantic endpoints."""

    time_contract_id: str = Field(description="Internal ID of the time contract.")
    header: TimeSemanticHeader = Field(description="Time header (contains time_ref).")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )


class TimeResponse(ObjectResponseBase):
    """Response model for a time semantic object.

    Includes all fields from storage plus catalog metadata.
    """

    time_contract_id: str = Field(description="Internal ID of the time contract.")
    header: TimeSemanticHeader = Field(description="Time semantic header.")
    catalog_metadata: CatalogMetadata = Field(
        default_factory=CatalogMetadata,
        description="Discovery-only catalog metadata.",
    )


class TimeListResponse(ListResponseBase[TimeListItem]):
    """Response model for listing time semantic objects."""


TimeListItemOrFull = TimeListItem | TimeResponse


class TimeListResponseFull(ListResponseBase[TimeListItemOrFull]):
    """Response model for listing time semantic objects with detail=true."""


# =============================================================================
# Time Surface (for bindings)
# =============================================================================


class TimeSurfaceSpec(BaseModel):
    """Specification for a time surface in a carrier binding.

    Time surfaces expose time fields from a carrier (table/view)
    that can be mapped to semantic time refs.
    """

    surface_ref: str = Field(
        description="Surface reference (e.g., 'time_surface.partition'). "
        "Must start with 'time_surface.'."
    )
    physical_name: str = Field(description="Physical column name in the carrier.")
    time_granularity: TimeGranularity | None = Field(
        default=None, description="Time granularity of this surface."
    )

    @field_validator("surface_ref")
    @classmethod
    def validate_surface_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "time_surface", "surface_ref")
