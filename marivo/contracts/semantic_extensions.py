"""MARIVO vendor extension models for OSI objects.

Layer 2: MARIVO extension schema. Defines the structure of
custom_extensions[].data when vendor_name == "MARIVO".
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class MarivoSemanticModelExtension(BaseModel):
    visibility: Literal["public", "private"]
    owner_user: str | None = None
    revision: int | None = None

    @model_validator(mode="after")
    def _private_requires_owner(self) -> MarivoSemanticModelExtension:
        if self.visibility == "private" and not self.owner_user:
            raise ValueError("owner_user is required when visibility is private")
        return self

    model_config = {"extra": "forbid"}


class MarivoDatasetExtension(BaseModel):
    datasource_id: str | None = None

    model_config = {"extra": "forbid"}


TimeGranularity = Literal["hour", "day", "week", "month", "quarter", "year"]
TimeFieldDataType = Literal["date", "timestamp", "string", "integer"]


class MarivoFieldExtension(BaseModel):
    support_min_granularity: TimeGranularity
    data_type: TimeFieldDataType
    format: str | None = None
    required_prefix: str | None = None

    model_config = {"extra": "forbid"}


class MarivoRelationshipExtension(BaseModel):
    cardinality: Literal["many_to_one", "one_to_one"] | None = None

    model_config = {"extra": "forbid"}


class MarivoMetricFilterExpressionDialect(BaseModel):
    dialect: Literal["ANSI_SQL", "SNOWFLAKE", "MDX", "TABLEAU", "DATABRICKS"]
    expression: str

    model_config = {"extra": "forbid"}


class MarivoMetricFilterExpression(BaseModel):
    dialects: list[MarivoMetricFilterExpressionDialect] = Field(..., min_length=1)

    model_config = {"extra": "forbid"}


class MarivoMetricFilter(BaseModel):
    name: str = Field(..., min_length=1)
    expression: MarivoMetricFilterExpression

    model_config = {"extra": "forbid"}


class MetricComponentRef(BaseModel):
    metric: str = Field(..., min_length=1, description="Reference to a published semantic metric")

    model_config = {"extra": "forbid"}


class ExpressionComponent(BaseModel):
    expression: str = Field(
        ..., min_length=1, description="SQL expression for computing the component value"
    )

    model_config = {"extra": "forbid"}


ComponentSpec = MetricComponentRef | ExpressionComponent


class SumDecomposition(BaseModel):
    type: Literal["sum"] = "sum"

    model_config = {"extra": "forbid"}


class RatioDecomposition(BaseModel):
    type: Literal["ratio"] = "ratio"
    numerator: ComponentSpec
    denominator: ComponentSpec

    model_config = {"extra": "forbid"}


class WeightedAverageDecomposition(BaseModel):
    type: Literal["weighted_average"] = "weighted_average"
    numerator: ComponentSpec
    weight: ComponentSpec

    model_config = {"extra": "forbid"}


DecompositionSemantics = Annotated[
    SumDecomposition | RatioDecomposition | WeightedAverageDecomposition,
    Field(discriminator="type"),
]


class MarivoMetricExtension(BaseModel):
    decomposition_semantics: DecompositionSemantics = SumDecomposition()

    @property
    def filters(self) -> list[MarivoMetricFilter] | None:
        return getattr(self, "__pydantic_extra__", {}).get("filters")

    model_config = {"extra": "allow"}


def decomposition_type(agg: DecompositionSemantics) -> str:
    """Extract the type discriminator string from a DecompositionSemantics variant."""
    return agg.type
