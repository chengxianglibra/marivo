"""OSI Core Metadata Specification v0.1.1 — Pydantic models.

Layer 1: OSI external contract models. These models represent the wire format
for API input/output. All MARIVO-specific data lives in custom_extensions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, RootModel
from pydantic import Field as PydanticField

Dialect = Literal["ANSI_SQL", "SNOWFLAKE", "MDX", "TABLEAU", "DATABRICKS"]
Vendor = Literal["COMMON", "SNOWFLAKE", "SALESFORCE", "DBT", "DATABRICKS", "MARIVO"]


class DialectExpression(BaseModel):
    """Expression in a specific dialect."""

    dialect: Dialect
    expression: str

    model_config = {"extra": "forbid"}


class Expression(BaseModel):
    """Multi-dialect expression definition."""

    dialects: list[DialectExpression] = PydanticField(..., min_length=1)

    model_config = {"extra": "forbid"}


class AIContextObject(BaseModel):
    """Structured AI context for instructions, synonyms, and examples."""

    instructions: str | None = None
    synonyms: list[str] | None = None
    examples: list[str] | None = None

    model_config = {"extra": "forbid"}


class AIContext(RootModel[str | AIContextObject]):
    """AI context — either a string or a typed OSI AI context object."""

    root: str | AIContextObject


class CustomExtension(BaseModel):
    """Vendor-specific extension container."""

    vendor_name: Vendor
    data: str  # JSON string containing vendor-specific data

    model_config = {"extra": "forbid"}


class MarivoSemanticModelCustomExtension(BaseModel):
    """MARIVO custom extension container for SemanticModel."""

    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {
                "$ref": "https://marivo.dev/schemas/osi-marivo-schema.json#/$defs/MarivoSemanticModelExtension"
            },
        },
    )

    model_config = {"extra": "forbid"}


class MarivoDatasetCustomExtension(BaseModel):
    """MARIVO custom extension container for Dataset."""

    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {
                "$ref": "https://marivo.dev/schemas/osi-marivo-schema.json#/$defs/MarivoDatasetExtension"
            },
        },
    )

    model_config = {"extra": "forbid"}


class MarivoFieldCustomExtension(BaseModel):
    """MARIVO custom extension container for Field."""

    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {
                "$ref": "https://marivo.dev/schemas/osi-marivo-schema.json#/$defs/MarivoFieldExtension"
            },
        },
    )

    model_config = {"extra": "forbid"}


class MarivoRelationshipCustomExtension(BaseModel):
    """MARIVO custom extension container for Relationship."""

    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {
                "$ref": "https://marivo.dev/schemas/osi-marivo-schema.json#/$defs/MarivoRelationshipExtension"
            },
        },
    )

    model_config = {"extra": "forbid"}


class MarivoMetricCustomExtension(BaseModel):
    """MARIVO custom extension container for Metric."""

    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {
                "$ref": "https://marivo.dev/schemas/osi-marivo-schema.json#/$defs/MarivoMetricExtension"
            },
        },
    )

    model_config = {"extra": "forbid"}


class Dimension(BaseModel):
    """Dimension metadata for a Field."""

    is_time: bool = False

    model_config = {"extra": "forbid"}


class Field(BaseModel):
    """Row-level attribute for grouping, filtering, and metric expressions."""

    name: str
    expression: Expression
    dimension: Dimension | None = None
    label: str | None = None
    description: str | None = None
    ai_context: AIContext | None = None
    custom_extensions: list[MarivoFieldCustomExtension | CustomExtension] | None = None

    model_config = {"extra": "forbid"}


class Dataset(BaseModel):
    """Logical dataset representing a business entity."""

    name: str
    source: str
    primary_key: list[str] | None = None
    unique_keys: list[list[str]] | None = None
    description: str | None = None
    ai_context: AIContext | None = None
    fields: list[Field] | None = None
    custom_extensions: list[MarivoDatasetCustomExtension | CustomExtension] | None = None

    model_config = {"extra": "forbid"}


class Relationship(BaseModel):
    """Foreign key relationship between datasets."""

    model_config = {"extra": "forbid"}

    name: str
    from_: str = PydanticField(alias="from")
    to: str
    from_columns: list[str] = PydanticField(..., min_length=1)
    to_columns: list[str] = PydanticField(..., min_length=1)
    ai_context: AIContext | None = None
    custom_extensions: list[MarivoRelationshipCustomExtension | CustomExtension] | None = None


class Metric(BaseModel):
    """Quantitative measure defined on business data."""

    name: str
    expression: Expression
    description: str | None = None
    ai_context: AIContext | None = None
    custom_extensions: list[MarivoMetricCustomExtension | CustomExtension] | None = None

    model_config = {"extra": "forbid"}


class SemanticModel(BaseModel):
    """Top-level container representing a complete semantic model."""

    name: str
    datasets: list[Dataset] = PydanticField(..., min_length=1)
    description: str | None = None
    ai_context: AIContext | None = None
    relationships: list[Relationship] | None = None
    metrics: list[Metric] | None = None
    custom_extensions: list[MarivoSemanticModelCustomExtension | CustomExtension] | None = None

    model_config = {"extra": "forbid"}


class OSIDocument(BaseModel):
    """Top-level OSI document structure."""

    version: Literal["0.1.1"]
    semantic_model: list[SemanticModel]
    dialects: list[Dialect] | None = None
    vendors: list[Vendor] | None = None

    model_config = {"extra": "forbid"}


OSI_SPEC_VERSION = "0.1.1"
