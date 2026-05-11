"""OSI model compatibility shim.

The concrete OSI/AOI models now live under ``marivo.contracts.generated``.
This module preserves the legacy transport import path for API and test
consumers during the cutover.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, RootModel
from pydantic import Field as PydanticField

from marivo.contracts.generated import (
    CustomExtension,
    Dataset,
    DialectExpression,
    Dimension,
    Expression,
    Field,
    Metric,
    OSIDocument,
    Relationship,
    SemanticModel,
)

Dialect = Literal["ANSI_SQL", "SNOWFLAKE", "MDX", "TABLEAU", "DATABRICKS"]
Vendor = Literal["COMMON", "SNOWFLAKE", "SALESFORCE", "DBT", "DATABRICKS", "MARIVO"]
OSI_SPEC_VERSION = "0.1.1"


class AIContextObject(BaseModel):
    """Structured AI context for instructions, synonyms, and examples."""

    instructions: str | None = None
    synonyms: list[str] | None = None
    examples: list[str] | None = None

    model_config = {"extra": "forbid"}


class AIContext(RootModel[str | AIContextObject]):
    """Legacy AI context root model used by transport tests."""

    root: str | AIContextObject


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


__all__ = [
    "OSI_SPEC_VERSION",
    "AIContext",
    "AIContextObject",
    "CustomExtension",
    "Dataset",
    "Dialect",
    "DialectExpression",
    "Dimension",
    "Expression",
    "Field",
    "MarivoDatasetCustomExtension",
    "MarivoFieldCustomExtension",
    "MarivoMetricCustomExtension",
    "MarivoRelationshipCustomExtension",
    "MarivoSemanticModelCustomExtension",
    "Metric",
    "OSIDocument",
    "Relationship",
    "SemanticModel",
    "Vendor",
]
