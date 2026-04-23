"""Metric v2 semantic object models.

This module defines the API models for metric objects,
following the contract defined in docs/semantic/metric-v2-schema.zh.md.

Metrics define measurement contracts with 7 family types, each with
specific payload structures.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import (
    AdditivityConstraints,
    AggregationMethod,
    AggregationScope,
    HorizonSpec,
    ListResponseBase,
    MetricFamily,
    ObjectHeaderBase,
    ObjectListItemBase,
    ObjectResponseBase,
    SampleKind,
    ValueSemantics,
    validate_contract_version,
    validate_ref_prefix,
)

# =============================================================================
# Metric Header
# =============================================================================


class MetricHeader(ObjectHeaderBase):
    """Header for a metric object.

    Defines the stable identity and core measurement semantics of a metric.
    """

    metric_ref: str = Field(
        description="Stable metric reference (e.g., 'metric.dau'). Must start with 'metric.'."
    )
    metric_family: MetricFamily = Field(
        description="Metric family: count_metric, sum_metric, rate_metric, "
        "average_metric, distribution_metric, score_metric, or survival_metric."
    )
    population_subject_ref: str | None = Field(
        default=None, description="Reference to the population subject (subject.*), if applicable."
    )
    observed_entity_ref: str = Field(description="Reference to the observed entity (entity.*).")
    observation_grain_ref: str = Field(description="Reference to the observation grain (grain.*).")
    sample_kind: SampleKind = Field(description="Sample kind: numeric, rate, binary, or survival.")
    value_semantics: ValueSemantics = Field(
        description="Value semantics: count, sum, ratio, mean, distribution_statistic, "
        "score, or survival_probability."
    )
    aggregation_scope: AggregationScope | None = Field(
        default=None, description="Aggregation scope: subject, event, session, or window."
    )
    primary_time_ref: str | None = Field(
        default=None, description="Reference to the primary time semantic (time.*)."
    )
    additivity_constraints: AdditivityConstraints = Field(
        description="Structured additivity constraints: dimension policy and time-axis policy."
    )
    default_predicate_refs: list[str] | None = Field(
        default=None,
        description="Shared predicate defaults for all measurement components. "
        "Must reference predicate.* declaring 'metric_qualifier' usage. "
        "Does not replace component qualifier_refs lineage.",
    )
    metric_contract_version: str = Field(
        description="Contract version (e.g., 'metric.v1'). Must start with 'metric.'."
    )

    @field_validator("metric_ref")
    @classmethod
    def validate_metric_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "metric", "metric_ref")

    @field_validator("population_subject_ref")
    @classmethod
    def validate_population_subject_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "subject", "population_subject_ref")
        return v

    @field_validator("observed_entity_ref")
    @classmethod
    def validate_observed_entity_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "entity", "observed_entity_ref")

    @field_validator("observation_grain_ref")
    @classmethod
    def validate_observation_grain_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "grain", "observation_grain_ref")

    @field_validator("primary_time_ref")
    @classmethod
    def validate_primary_time_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "time", "primary_time_ref")
        return v

    @field_validator("metric_contract_version")
    @classmethod
    def validate_version_prefix(cls, v: str) -> str:
        return validate_contract_version(v, "metric")

    @field_validator("default_predicate_refs")
    @classmethod
    def validate_default_predicate_refs_prefix(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            return [validate_ref_prefix(ref, "predicate", "default_predicate_refs") for ref in v]
        return v


# =============================================================================
# Measurement Component
# =============================================================================


class MeasurementComponent(BaseModel):
    """Component of a measurement (numerator, denominator, etc.).

    Expresses what is being measured without exposing physical field names.
    """

    name: str = Field(description="Name of this measurement component.")
    semantics: str = Field(description="Semantic description of what this measures.")
    aggregation: AggregationMethod = Field(
        description="Aggregation method: count, count_distinct, sum, mean, "
        "boolean_any, or boolean_all."
    )
    measure_ref: str | None = Field(
        default=None, description="Optional reference to a measure definition (measure.*)."
    )
    qualifier_refs: list[str] | None = Field(
        default=None,
        description="Component-specific business predicate references (predicate.*). "
        "Must reference predicates declaring 'metric_qualifier' usage.",
    )

    @field_validator("measure_ref")
    @classmethod
    def validate_measure_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "measure", "measure_ref")
        return v

    @field_validator("qualifier_refs")
    @classmethod
    def validate_qualifier_refs_prefix(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            return [validate_ref_prefix(ref, "predicate", "qualifier_refs") for ref in v]
        return v


# =============================================================================
# Distribution Specification
# =============================================================================


class DistributionSpec(BaseModel):
    """Specification for distribution metrics (percentiles, etc.)."""

    kind: str = Field(description="Distribution kind: percentile, quantile, or histogram_ready.")
    percentile: int | float | None = Field(
        default=None, description="Percentile value (0 < p < 1) for percentile kind."
    )
    sketch_policy_ref: str | None = Field(
        default=None, description="Optional reference to a sketch policy."
    )

    @model_validator(mode="after")
    def validate_percentile_range(self) -> DistributionSpec:
        """Ensure percentile metrics use normalized percentile values."""
        if self.kind == "percentile":
            if self.percentile is None:
                raise ValueError("percentile must be provided when kind is 'percentile'")
            if not 0 < self.percentile < 1:
                raise ValueError("percentile must satisfy 0 < p < 1")
        return self


# =============================================================================
# Survival Specification
# =============================================================================


class SurvivalSpec(BaseModel):
    """Specification for survival metrics."""

    origin_time_ref: str = Field(description="Reference to the origin time (time.*).")
    event_time_ref: str = Field(description="Reference to the event time (time.*).")
    censor_time_ref: str | None = Field(
        default=None, description="Optional reference to the censor time (time.*)."
    )
    entry_time_ref: str | None = Field(
        default=None, description="Optional reference to the entry time (time.*)."
    )
    event_definition_ref: str = Field(description="Reference to the event definition.")
    censoring_policy: str | None = Field(
        default=None, description="Censoring policy: right_censor_only or custom."
    )
    horizon: HorizonSpec | None = Field(default=None, description="Optional horizon specification.")


# =============================================================================
# Family-Specific Payloads
# =============================================================================


class CountMetricPayload(BaseModel):
    """Payload for count_metric family."""

    metric_family: Literal["count_metric"] = Field(
        default="count_metric", description="Discriminator."
    )
    count_target: MeasurementComponent = Field(description="The target being counted.")


class SumMetricPayload(BaseModel):
    """Payload for sum_metric family."""

    metric_family: Literal["sum_metric"] = Field(default="sum_metric", description="Discriminator.")
    measure: MeasurementComponent = Field(description="The measure being summed.")


class RateMetricPayload(BaseModel):
    """Payload for rate_metric family."""

    metric_family: Literal["rate_metric"] = Field(
        default="rate_metric", description="Discriminator."
    )
    numerator: MeasurementComponent = Field(description="The numerator of the rate.")
    denominator: MeasurementComponent = Field(description="The denominator of the rate.")
    default_test_method: str | None = Field(
        default=None, description="Default test method: two_proportion_z or auto."
    )


class AverageMetricPayload(BaseModel):
    """Payload for average_metric family."""

    metric_family: Literal["average_metric"] = Field(
        default="average_metric", description="Discriminator."
    )
    numerator: MeasurementComponent = Field(description="The numerator (total).")
    denominator: MeasurementComponent = Field(description="The denominator (count).")


class DistributionMetricPayload(BaseModel):
    """Payload for distribution_metric family."""

    metric_family: Literal["distribution_metric"] = Field(
        default="distribution_metric", description="Discriminator."
    )
    value_component: MeasurementComponent = Field(
        description="The value component for the distribution."
    )
    distribution_spec: DistributionSpec = Field(
        description="Distribution specification (percentile, etc.)."
    )


class ScoreMetricPayload(BaseModel):
    """Payload for score_metric family."""

    metric_family: Literal["score_metric"] = Field(
        default="score_metric", description="Discriminator."
    )
    score_source: MeasurementComponent = Field(description="The source of the score.")
    score_kind: str | None = Field(
        default=None, description="Score kind: precomputed, model_output, or rule_score."
    )


class SurvivalMetricPayload(BaseModel):
    """Payload for survival_metric family."""

    metric_family: Literal["survival_metric"] = Field(
        default="survival_metric", description="Discriminator."
    )
    survival_spec: SurvivalSpec = Field(description="Survival specification.")


# =============================================================================
# Payload Union
# =============================================================================

MetricPayload = Annotated[
    CountMetricPayload
    | SumMetricPayload
    | RateMetricPayload
    | AverageMetricPayload
    | DistributionMetricPayload
    | ScoreMetricPayload
    | SurvivalMetricPayload,
    Field(discriminator="metric_family"),
]


# =============================================================================
# Family <-> Value Semantics Mapping
# =============================================================================

_FAMILY_VALUE_SEMANTICS_MAP: dict[MetricFamily, ValueSemantics] = {
    "count_metric": "count",
    "sum_metric": "sum",
    "rate_metric": "ratio",
    "average_metric": "mean",
    "distribution_metric": "distribution_statistic",
    "score_metric": "score",
    "survival_metric": "survival_probability",
}


def _collect_aggregation_methods(payload: MetricPayload) -> set[str]:
    """Extract all aggregation method strings from a metric payload."""
    methods: set[str] = set()
    if hasattr(payload, "count_target"):
        methods.add(payload.count_target.aggregation)
    if hasattr(payload, "measure"):
        methods.add(payload.measure.aggregation)
    if hasattr(payload, "numerator"):
        methods.add(payload.numerator.aggregation)
    if hasattr(payload, "denominator"):
        methods.add(payload.denominator.aggregation)
    if hasattr(payload, "value_component"):
        methods.add(payload.value_component.aggregation)
    if hasattr(payload, "score_source"):
        methods.add(payload.score_source.aggregation)
    return methods


# =============================================================================
# Request Models
# =============================================================================


class TypedMetricCreateRequest(BaseModel):
    """Request to create a new typed metric."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "header": {
                        "metric_ref": "metric.dau",
                        "display_name": "DAU",
                        "metric_family": "count_metric",
                        "observed_entity_ref": "entity.user",
                        "observation_grain_ref": "grain.user",
                        "sample_kind": "numeric",
                        "value_semantics": "count",
                        "additivity_constraints": {
                            "dimension_policy": "none",
                            "time_axis_policy": "non_additive",
                        },
                        "metric_contract_version": "metric.v1",
                    },
                    "payload": {
                        "metric_family": "count_metric",
                        "count_target": {
                            "name": "active_users",
                            "semantics": "distinct active users",
                            "aggregation": "count_distinct",
                        },
                    },
                }
            ]
        }
    )

    header: MetricHeader = Field(description="Metric header.")
    payload: MetricPayload = Field(description="Family-specific payload.")

    @model_validator(mode="after")
    def validate_family_matches_semantics(self) -> TypedMetricCreateRequest:
        """Ensure metric_family matches value_semantics."""
        expected_semantics = _FAMILY_VALUE_SEMANTICS_MAP.get(self.header.metric_family)
        if expected_semantics and self.header.value_semantics != expected_semantics:
            raise ValueError(
                f"value_semantics must be '{expected_semantics}' for "
                f"metric_family '{self.header.metric_family}', "
                f"got '{self.header.value_semantics}'"
            )
        return self

    @model_validator(mode="after")
    def validate_family_matches_payload(self) -> TypedMetricCreateRequest:
        """Ensure header.metric_family matches payload.metric_family."""
        if self.header.metric_family != self.payload.metric_family:
            raise ValueError(
                f"header.metric_family ({self.header.metric_family}) must match "
                f"payload.metric_family ({self.payload.metric_family})"
            )
        return self

    @model_validator(mode="after")
    def validate_count_distinct_not_all_additive(self) -> TypedMetricCreateRequest:
        """Metrics with count_distinct aggregation must not use dimension_policy='all'."""
        if self.header.additivity_constraints.dimension_policy == "all":
            components = _collect_aggregation_methods(self.payload)
            if "count_distinct" in components:
                raise ValueError(
                    "Metrics with count_distinct aggregation must not use "
                    "dimension_policy='all'; use 'subset' or 'none' instead."
                )
        return self


class TypedMetricUpdateRequest(BaseModel):
    """Request to update an existing typed metric.

    All fields are optional; only provided fields will be updated.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "display_name": "Daily Active Users",
                    "payload": {
                        "metric_family": "count_metric",
                        "count_target": {
                            "name": "active_users",
                            "semantics": "distinct active users",
                            "aggregation": "count_distinct",
                        },
                    },
                }
            ]
        }
    )

    display_name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(default=None, description="New description.")
    additivity_constraints: AdditivityConstraints | None = Field(
        default=None,
        description="Updated additivity constraints. Only updatable in draft status.",
    )
    payload: MetricPayload | None = Field(
        default=None, description="New payload. Note: cannot change metric_family."
    )
    default_predicate_refs: list[str] | None = Field(
        default=None,
        description="Updated shared predicate defaults for all measurement components. "
        "Must reference predicate.*.",
    )

    @field_validator("default_predicate_refs")
    @classmethod
    def validate_default_predicate_refs_prefix(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            return [validate_ref_prefix(ref, "predicate", "default_predicate_refs") for ref in v]
        return v


# =============================================================================
# Response Models
# =============================================================================


class TypedMetricListItem(ObjectListItemBase):
    """Lightweight list item for metric endpoints.

    Includes header only, not full payload.
    """

    metric_contract_id: str = Field(description="Internal ID of the metric contract.")
    header: MetricHeader = Field(description="Metric header (contains metric_ref).")


class TypedMetricResponse(ObjectResponseBase):
    """Response model for a typed metric object.

    Includes all fields from storage plus catalog metadata.
    """

    metric_contract_id: str = Field(description="Internal ID of the metric contract.")
    header: MetricHeader = Field(description="Metric header.")
    payload: MetricPayload = Field(description="Family-specific payload.")


class TypedMetricListResponse(ListResponseBase[TypedMetricListItem]):
    """Response model for listing typed metric objects."""
