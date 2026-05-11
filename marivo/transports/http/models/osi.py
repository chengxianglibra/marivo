"""OSI model compatibility shim.

The concrete OSI/AOI models now live under ``marivo.contracts.generated``.
This module preserves the legacy transport import path for API and test
consumers during the cutover.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, RootModel

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
from marivo.contracts.generated.osi import (
    MarivoDatasetCustomExtension,
    MarivoMetricCustomExtension,
)

Dialect = Literal["ANSI_SQL", "SNOWFLAKE", "MDX", "TABLEAU", "DATABRICKS"]
Vendor = Literal["MARIVO"]
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
    "MarivoMetricCustomExtension",
    "Metric",
    "OSIDocument",
    "Relationship",
    "SemanticModel",
    "Vendor",
]
