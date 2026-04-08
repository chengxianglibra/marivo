"""Time semantic object models.

This module defines the API models for time semantic objects,
following the contract defined in docs/semantic/time-schema-contract.zh.md.

Time objects represent stable time semantics that can be referenced
by entities, metrics, process objects, dimensions, and bindings.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from .base import (
    ListResponseBase,
    ObjectHeaderBase,
    ObjectResponseBase,
    TimeGranularity,
    TimeSemanticRole,
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


# =============================================================================
# Request Models
# =============================================================================


class TimeCreateRequest(BaseModel):
    """Request to create a new time semantic object."""

    header: TimeSemanticHeader = Field(
        description="Time semantic header containing identity and roles."
    )


class TimeUpdateRequest(BaseModel):
    """Request to update an existing time semantic object.

    All fields are optional; only provided fields will be updated.
    """

    display_name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")
    semantic_roles: list[TimeSemanticRole] | None = Field(
        default=None, description="New semantic roles. Must be non-empty if provided."
    )

    @field_validator("semantic_roles")
    @classmethod
    def validate_roles_not_empty(
        cls, v: list[TimeSemanticRole] | None
    ) -> list[TimeSemanticRole] | None:
        if v is not None and not v:
            raise ValueError("semantic_roles must be non-empty if provided")
        return v


# =============================================================================
# Response Models
# =============================================================================


class TimeResponse(ObjectResponseBase):
    """Response model for a time semantic object.

    Includes all fields from storage plus catalog metadata.
    """

    time_contract_id: str = Field(description="Internal ID of the time contract.")
    header: TimeSemanticHeader = Field(description="Time semantic header.")


class TimeListResponse(ListResponseBase[TimeResponse]):
    """Response model for listing time semantic objects."""


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
