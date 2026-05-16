"""Typed request models for the Marivo intent-based write surface.

Compatibility DTOs for derived diagnose inputs. AOI-backed atomic requests
and attribute use generated contract models directly.

Path (/intents/<intent_type>) acts as the discriminator; no step_type field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from marivo.time_contracts import normalize_hour_boundary
from marivo.transports.http.models.base import validate_ref_prefix
from marivo.transports.http.models.json_contract import JsonObject, JsonScalar, ScalarMap


class ObservationRef(BaseModel):
    """Typed reference to an upstream `observe` step artifact."""

    session_id: str | None = Field(
        default=None,
        description="Session containing the upstream observe step. Defaults to the path session when omitted.",
    )
    step_id: str
    step_type: Literal["observe"]


class CorrelateObservationRef(ObservationRef):
    """Full typed artifact reference for `correlate` — adds artifact identity fields
    required by the correlate.md Reference Contract."""

    artifact_id: str | None = Field(
        default=None,
        description="Artifact ID of the upstream observe artifact (optional; resolved from step_id if omitted).",
    )
    observation_type: Literal["time_series"] = Field(
        default="time_series",
        description="Must be 'time_series'.  v1 does not support scalar or segmented correlations.",
    )


class ArtifactRef(BaseModel):
    """Typed reference to any upstream intent step artifact."""

    session_id: str | None = Field(
        default=None,
        description="Session containing the upstream step. Defaults to the path session when omitted.",
    )
    step_id: str
    step_type: str


# ObserveTimeScope — explicit range only


class ObserveTimeScopeRange(BaseModel):
    kind: Literal["range"]
    start: str = Field(description="Inclusive start of the range (ISO-8601 date or datetime).")
    end: str = Field(description="Exclusive end of the range (ISO-8601 date or datetime).")


ObserveTimeScope = ObserveTimeScopeRange


class PredicateComparison(BaseModel):
    """Deprecated inline predicate comparison shape."""

    field: str
    operator: Literal["eq", "neq", "gt", "gte", "lt", "lte", "in"]
    value: JsonScalar = None
    values: list[JsonScalar] | None = None


class ObserveScope(BaseModel):
    """Non-time population scope for an observe intent.

    `constraints` holds scalar equality filters; `predicate` holds a
    structured predicate AST (dict) or raw SQL WHERE clause string.
    Time conditions must not appear here.
    """

    constraints: ScalarMap | None = Field(
        default=None,
        description="Scalar equality constraints on semantic dimensions.",
    )
    predicate: PredicateComparison | JsonObject | str | None = Field(
        default=None,
        description="Structured non-time predicate AST or raw SQL WHERE clause string. "
        "Must not contain time conditions.",
    )


# Atomic HTTP intent routes use generated AOI request models directly.
# The shared time/scope DTOs above remain for derived compatibility requests.


class DetectTimeScope(BaseModel):
    """Range-only time_scope for auto-detect diagnose."""

    kind: Literal["range"]
    start: str = Field(description="Inclusive start of the range (ISO-8601 date or datetime).")
    end: str = Field(description="Exclusive end of the range (ISO-8601 date or datetime).")

    @field_validator("start", "end")
    @classmethod
    def _validate_non_blank_boundary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("time_scope.start and time_scope.end must be non-empty")
        return normalized


class AttributeObservationInput(BaseModel):
    """One side of an attribute request — canonical observe scalar profile."""

    time_scope: ObserveTimeScope
    scope: ObserveScope | None = Field(default=None)


class DiagnoseRequest(BaseModel):
    """Derived intent: diagnose anomalies or known current-vs-baseline degradation."""

    mode: Literal["auto_detect", "explicit_compare"] = Field(
        default="auto_detect",
        description="auto_detect expands detect+follow-up; explicit_compare expands observe+compare+decompose directly.",
    )
    metric: str = Field(
        description="Canonical semantic metric ref to diagnose (e.g., 'metric.watch_time')."
    )
    time_scope: DetectTimeScope | None = Field(
        default=None,
        description="Required when mode='auto_detect'.",
    )
    granularity: Literal["hour", "day", "week", "month"] | None = Field(
        default=None,
        description="Required when mode='auto_detect'.",
    )
    current: AttributeObservationInput | None = Field(
        default=None,
        description="Required current side when mode='explicit_compare'.",
    )
    baseline: AttributeObservationInput | None = Field(
        default=None,
        description="Required baseline side when mode='explicit_compare'.",
    )
    scope: ObserveScope | None = Field(default=None)
    detect_dimension: str | None = Field(
        default=None,
        description="Optional semantic dimension to split detect into independent series.",
    )
    candidate_dimensions: list[str] = Field(
        min_length=1,
        description="Attribution dimensions to decompose each followed candidate over.",
    )
    strategy: Literal["point_anomaly", "period_shift"] = Field(
        description="Detection strategy.",
    )
    sensitivity: Literal["conservative", "balanced", "aggressive"] = Field(
        default="aggressive",
        description="Detection sensitivity preset.",
    )
    candidate_limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of candidates returned by the internal detect step.",
    )
    followup_limit: int | None = Field(
        default=3,
        ge=1,
        description="Number of top-ranked candidates to follow up with compare+decompose.",
    )
    decomposition_limit: int | None = Field(
        default=5,
        ge=1,
        description="Maximum driver rows per dimension per candidate.",
    )
    baseline_policy: Literal["previous_adjacent_equal_length"] = Field(
        default="previous_adjacent_equal_length",
        description="Baseline policy for auto-detect follow-up candidates.",
    )

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")

    @model_validator(mode="after")
    def _validate_mode_inputs(self) -> DiagnoseRequest:
        if self.mode == "auto_detect":
            if self.time_scope is None:
                raise ValueError("time_scope is required when mode='auto_detect'")
            if self.granularity is None:
                raise ValueError("granularity is required when mode='auto_detect'")
            if self.granularity == "hour":
                normalize_hour_boundary(self.time_scope.start, label="time_scope.start")
                normalize_hour_boundary(self.time_scope.end, label="time_scope.end")
            if self.current is not None or self.baseline is not None:
                raise ValueError("current/baseline are only valid when mode='explicit_compare'")
        else:
            if self.current is None or self.baseline is None:
                raise ValueError("current and baseline are required when mode='explicit_compare'")
            if self.time_scope is not None or self.granularity is not None:
                raise ValueError("time_scope/granularity are only valid when mode='auto_detect'")
        return self
