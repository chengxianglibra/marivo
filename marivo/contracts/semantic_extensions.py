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


TimeGranularity = Literal["hour", "day", "week", "month", "quarter", "year"]


class MarivoFieldExtension(BaseModel):
    support_min_granularity: TimeGranularity

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


_ADDITIVE_DIMENSIONS_ALL = "__all"


class MarivoMetricExtension(BaseModel):
    additive_dimensions: list[str] = []
    aggregation_semantics: Literal["sum", "ratio", "weighted_average"] = "sum"
    observed_dataset: str | None = None
    observation_grain: list[str] | None = None
    primary_time_field: str | None = None

    @model_validator(mode="after")
    def _validate_additive_dimensions(self) -> MarivoMetricExtension:
        if any(dimension == "" for dimension in self.additive_dimensions):
            raise ValueError("additive_dimensions entries must be non-empty strings")
        if _ADDITIVE_DIMENSIONS_ALL in self.additive_dimensions and self.additive_dimensions != [
            _ADDITIVE_DIMENSIONS_ALL
        ]:
            raise ValueError(
                f"additive_dimensions uses {_ADDITIVE_DIMENSIONS_ALL!r} and must not "
                "mix it with explicit fields"
            )
        return self

    @property
    def filters(self) -> list[MarivoMetricFilter] | None:
        return getattr(self, "__pydantic_extra__", {}).get("filters")

    model_config = {"extra": "allow"}
