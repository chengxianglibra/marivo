"""Enum Set semantic object models.

This module defines the API models for enum value sets,
following the contract defined in docs/semantic/enum-set-schema-contract.zh.md.

Enum sets define governed value domains that can be referenced by dimensions
with domain_kind="enumerated".
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import (
    EnumValueType,
    ListResponseBase,
    ObjectResponseBase,
    validate_ref_prefix,
)


def _raw_value_matches_enum_value_type(
    raw_value: str | int | float | bool, value_type: EnumValueType
) -> bool:
    """Return whether a raw enum value matches the declared enum value_type."""
    if value_type == "string":
        return isinstance(raw_value, str)
    if value_type == "integer":
        return isinstance(raw_value, int) and not isinstance(raw_value, bool)
    if value_type == "number":
        return (isinstance(raw_value, int) and not isinstance(raw_value, bool)) or isinstance(
            raw_value, float
        )
    return isinstance(raw_value, bool)


# =============================================================================
# Enum Set Header
# =============================================================================


class EnumSetHeader(BaseModel):
    """Header for an enum set object.

    Defines the stable identity and value type of an enum set.
    The actual values are defined in versions.
    """

    enum_set_ref: str = Field(
        description="Stable enum set reference (e.g., 'enum.iso_country_code'). "
        "Must start with 'enum.'."
    )
    value_type: EnumValueType = Field(
        description="Type of values in this enum set: string, integer, number, or boolean."
    )

    @field_validator("enum_set_ref")
    @classmethod
    def validate_enum_set_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "enum", "enum_set_ref")


# =============================================================================
# Enum Value Specification
# =============================================================================


class EnumValueSpec(BaseModel):
    """Specification for a single enum value.

    Each value has a stable semantic key (value_key), a raw value
    that appears in data, and a human-readable label.
    """

    value_key: str = Field(description="Stable semantic key for this value (e.g., 'CN' for China).")
    raw_value: str | int | float | bool = Field(description="Actual value as it appears in data.")
    label: str = Field(description="Human-readable label for display.")
    aliases: list[str] | None = Field(
        default=None, description="Optional aliases for this value (for search/matching)."
    )

    @field_validator("value_key")
    @classmethod
    def validate_value_key_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("value_key must not be empty")
        return v.strip()

    @field_validator("label")
    @classmethod
    def validate_label_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("label must not be empty")
        return v.strip()


# =============================================================================
# Enum Set Version Specification
# =============================================================================


class EnumSetVersionSpec(BaseModel):
    """Specification for a versioned enum set.

    Each version contains a snapshot of the allowed values.
    Versions allow dimensions to pin to a specific value set snapshot.
    """

    enum_version: str = Field(description="Version identifier (e.g., '2026-01', 'v1').")
    values: Annotated[
        list[EnumValueSpec],
        Field(min_length=1, description="List of enum values in this version. Must be non-empty."),
    ]

    @field_validator("enum_version")
    @classmethod
    def validate_version_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("enum_version must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_unique_value_keys(self) -> EnumSetVersionSpec:
        """Ensure value_keys are unique within the version."""
        keys = [v.value_key for v in self.values]
        if len(keys) != len(set(keys)):
            duplicates = [k for k in keys if keys.count(k) > 1]
            raise ValueError(f"Duplicate value_key(s) found: {set(duplicates)}")
        return self

    @model_validator(mode="after")
    def validate_unique_raw_values(self) -> EnumSetVersionSpec:
        """Ensure raw_values are unique within the version."""
        raw_values = [v.raw_value for v in self.values]
        if len(raw_values) != len(set(raw_values)):
            # For display purposes, convert to strings
            duplicates = [str(rv) for rv in raw_values if raw_values.count(rv) > 1]
            raise ValueError(f"Duplicate raw_value(s) found: {set(duplicates)}")
        return self


# =============================================================================
# Request Models
# =============================================================================


class EnumSetCreateRequest(BaseModel):
    """Request to create a new enum set."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "header": {
                        "enum_set_ref": "enum.country_code",
                        "value_type": "string",
                    },
                    "display_name": "Country Code",
                    "versions": [
                        {
                            "enum_version": "v1",
                            "values": [
                                {"value_key": "CN", "raw_value": "CN", "label": "China"},
                                {
                                    "value_key": "US",
                                    "raw_value": "US",
                                    "label": "United States",
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )

    header: EnumSetHeader = Field(description="Enum set header with ref and value type.")
    display_name: str = Field(description="Human-readable display name.")
    description: str = Field(default="", description="Description of the enum set.")
    versions: Annotated[
        list[EnumSetVersionSpec],
        Field(min_length=1, description="List of versions. At least one version is required."),
    ]

    @model_validator(mode="after")
    def validate_unique_versions(self) -> EnumSetCreateRequest:
        """Ensure enum_version values are unique across versions."""
        version_ids = [v.enum_version for v in self.versions]
        if len(version_ids) != len(set(version_ids)):
            duplicates = [vid for vid in version_ids if version_ids.count(vid) > 1]
            raise ValueError(f"Duplicate enum_version(s) found: {set(duplicates)}")
        return self

    @model_validator(mode="after")
    def validate_value_types_match_header(self) -> EnumSetCreateRequest:
        """Ensure raw_value types align with header.value_type across versions."""
        for version in self.versions:
            for value in version.values:
                if not _raw_value_matches_enum_value_type(value.raw_value, self.header.value_type):
                    raise ValueError(
                        f"raw_value for value_key '{value.value_key}' in enum_version "
                        f"'{version.enum_version}' must match header.value_type "
                        f"'{self.header.value_type}'"
                    )
        return self


class EnumSetUpdateRequest(BaseModel):
    """Request to update an existing enum set.

    All fields are optional; only provided fields will be updated.
    """

    display_name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")
    versions: list[EnumSetVersionSpec] | None = Field(
        default=None,
        description="New list of versions. If provided, replaces all existing versions.",
    )

    @model_validator(mode="after")
    def validate_unique_versions(self) -> EnumSetUpdateRequest:
        """Ensure enum_version values are unique across versions if provided."""
        if self.versions is not None:
            version_ids = [v.enum_version for v in self.versions]
            if len(version_ids) != len(set(version_ids)):
                duplicates = [vid for vid in version_ids if version_ids.count(vid) > 1]
                raise ValueError(f"Duplicate enum_version(s) found: {set(duplicates)}")
        return self


# =============================================================================
# Response Models
# =============================================================================


class EnumSetResponse(ObjectResponseBase):
    """Response model for an enum set object.

    Includes all fields from storage plus catalog metadata.
    """

    enum_set_contract_id: str = Field(description="Internal ID of the enum set contract.")
    header: EnumSetHeader = Field(description="Enum set header.")
    display_name: str = Field(description="Human-readable display name.")
    description: str = Field(description="Description of the enum set.")
    versions: list[EnumSetVersionSpec] = Field(description="List of versions with their values.")


class EnumSetListResponse(ListResponseBase[EnumSetResponse]):
    """Response model for listing enum set objects."""


class EnumSetVersionResponse(BaseModel):
    """Response model for a single enum set version."""

    enum_set_version_id: str = Field(description="Internal ID of the version.")
    enum_set_contract_id: str = Field(description="Parent enum set ID.")
    enum_version: str = Field(description="Version identifier.")
    values: list[EnumValueSpec] = Field(description="List of values in this version.")
    created_at: str = Field(description="Creation timestamp (ISO-8601).")
    updated_at: str = Field(description="Last update timestamp (ISO-8601).")
