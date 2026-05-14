"""Typed request models for the Marivo intent-based write surface.

Intent API models for observe, compare, correlate, decompose, detect,
forecast, attribute, and diagnose intents.

Path (/intents/<intent_type>) acts as the discriminator; no step_type field.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from marivo.core.semantic.calendar import (
    CalendarPolicyResolutionError,
    validate_calendar_policy_ref,
)
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


# ObserveTimeScope — discriminated union keyed on `kind`


class ObserveTimeScopeRange(BaseModel):
    kind: Literal["range"]
    start: str = Field(description="Inclusive start of the range (ISO-8601 date or datetime).")
    end: str = Field(description="Exclusive end of the range (ISO-8601 date or datetime).")


class ObserveTimeScopeSnapshotNow(BaseModel):
    kind: Literal["snapshot_now"]


class ObserveTimeScopeLatestAvailable(BaseModel):
    kind: Literal["latest_available"]


class ObserveTimeScopeAsOf(BaseModel):
    kind: Literal["as_of"]
    at: str = Field(description="Point-in-time snapshot (ISO-8601 datetime).")


ObserveTimeScope = Annotated[
    ObserveTimeScopeRange
    | ObserveTimeScopeSnapshotNow
    | ObserveTimeScopeLatestAvailable
    | ObserveTimeScopeAsOf,
    Field(discriminator="kind"),
]


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
    calendar_policy_ref: str | None = Field(
        default=None,
        description=(
            "Optional fixed calendar alignment policy ref for this side's internal observe step. "
            "Uses the same validation and builtin ref whitelist as observe-derived inputs."
        ),
    )
    scope: ObserveScope | None = Field(default=None)

    @field_validator("calendar_policy_ref")
    @classmethod
    def _validate_calendar_policy_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_calendar_policy_ref(value)
        except CalendarPolicyResolutionError as error:
            raise ValueError(str(error)) from error


class AttributeRequest(BaseModel):
    """Derived intent: attribute a metric change (expands to observe+observe+compare+decompose)."""

    metric: str = Field(
        description="Canonical semantic metric ref to attribute (e.g., 'metric.watch_time')."
    )
    left: AttributeObservationInput = Field(
        description="Current / treatment side observation scope."
    )
    right: AttributeObservationInput = Field(
        description="Baseline / control side observation scope."
    )
    dimensions: list[str] = Field(
        min_length=1,
        description="Attribution dimensions (deduped in order).",
    )
    decomposition_method: Literal["delta_share"] = Field(default="delta_share")
    decomposition_limit: int = Field(default=5, ge=1)

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")


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
    detect_split_by: str | None = Field(
        default=None,
        description="Optional semantic dimension to split detect into independent series.",
    )
    candidate_dimensions: list[str] = Field(
        min_length=1,
        description="Attribution dimensions to decompose each followed candidate over.",
    )
    profile: Literal["auto", "spike_dip", "level_shift", "seasonal_residual"] = Field(
        default="auto",
        description="Detection profile preset.",
    )
    sensitivity: Literal["conservative", "balanced", "aggressive"] = Field(
        default="balanced",
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
    patterns: list[Literal["point_anomaly", "period_shift"]] | None = Field(
        default=None,
        description="Candidate patterns passed to detect when mode='auto_detect'.",
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
            if self.patterns is not None:
                raise ValueError("patterns are only valid when mode='auto_detect'")
        return self
