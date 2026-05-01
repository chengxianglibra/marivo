"""MARIVO vendor extension models for OSI objects.

Layer 2: MARIVO extension schema. Defines the structure of
custom_extensions[].data when vendor_name == "MARIVO".
"""

from __future__ import annotations

from typing import Literal

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


MarivoFieldDataType = Literal["string", "integer", "number", "boolean", "date", "datetime"]


class MarivoFieldExtension(BaseModel):
    data_type: MarivoFieldDataType | None = None

    model_config = {"extra": "forbid"}


class MarivoRelationshipExtension(BaseModel):
    cardinality: Literal["many_to_one", "one_to_one"] | None = None

    model_config = {"extra": "forbid"}


class MarivoAdditivity(BaseModel):
    dimension_policy: Literal["all", "subset", "none"]
    additive_dimensions: list[str] | None = None
    time_axis_policy: Literal["additive", "non_additive"]

    @model_validator(mode="after")
    def _subset_requires_dimensions(self) -> MarivoAdditivity:
        if self.dimension_policy == "subset" and not self.additive_dimensions:
            raise ValueError("additive_dimensions is required when dimension_policy is subset")
        if self.dimension_policy != "subset" and self.additive_dimensions:
            raise ValueError("additive_dimensions must only be set when dimension_policy is subset")
        return self

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


class MarivoMetricExtension(BaseModel):
    observed_dataset: str | None = None
    observation_grain: list[str] | None = None
    primary_time_field: str | None = None
    additivity: MarivoAdditivity | None = None
    filters: list[MarivoMetricFilter] | None = None

    model_config = {"extra": "forbid"}
