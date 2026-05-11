"""Typed request models for the Marivo intent-based write surface.

Intent API models for observe, compare, correlate, decompose, detect,
test, forecast, attribute, diagnose, and validate intents.

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


class TestObservationRef(ObservationRef):
    """Full typed artifact reference for `test`.

    Matches the typed reference contract from docs/analysis/intents/atomic/test.md.
    """

    artifact_id: str = Field(
        description="Artifact ID of the upstream committed observe artifact.",
    )
    observation_type: Literal["numeric_sample_summary", "rate_sample_summary"] = Field(
        description=(
            "Inferential-ready observation artifact type. "
            "Must be 'numeric_sample_summary' or 'rate_sample_summary'."
        ),
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
    structured predicate AST (dict).  Time conditions must not appear here.
    Prefer `predicate_ref` over `predicate` for governed predicate references.
    """

    constraints: ScalarMap | None = Field(
        default=None,
        description="Scalar equality constraints on semantic dimensions.",
    )
    predicate: PredicateComparison | JsonObject | None = Field(
        default=None,
        description="DEPRECATED: Use predicate_ref instead. "
        "Structured non-time predicate AST.  Must not contain time conditions.",
    )
    predicate_ref: str | None = Field(
        default=None,
        description="Reference to a governed predicate (predicate.*) declaring 'request_scope' usage. "
        "Mutually exclusive with predicate.",
    )

    @field_validator("predicate_ref")
    @classmethod
    def _validate_predicate_ref_prefix(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_ref_prefix(v, "predicate", "predicate_ref")
        return v

    @model_validator(mode="after")
    def _validate_mutual_exclusion(self) -> ObserveScope:
        if self.predicate is not None and self.predicate_ref is not None:
            raise ValueError("predicate and predicate_ref are mutually exclusive")
        return self


class ObserveRequest(BaseModel):
    """Atomic intent: read a typed observation for a semantic metric."""

    metric: str = Field(description="Canonical semantic metric ref (e.g., 'metric.watch_time').")
    result_mode: Literal["standard", "numeric_sample_summary", "rate_sample_summary"] = Field(
        default="standard",
        description=(
            "Observation contract type.  'standard' returns scalar/time-series/segmented output "
            "depending on granularity and dimensions.  'numeric_sample_summary' and "
            "'rate_sample_summary' return inferential-ready summaries for downstream `test`."
        ),
    )
    time_scope: ObserveTimeScope
    calendar_policy_ref: str | None = Field(
        default=None,
        description=(
            "Optional fixed calendar alignment policy ref. v1 only accepts compiler-owned "
            "catalog refs such as 'calendar_policy.natural_yoy', "
            "'calendar_policy.weekday_yoy', 'calendar_policy.holiday_yoy', "
            "'calendar_policy.event_yoy', 'calendar_policy.natural_mom', "
            "'calendar_policy.weekday_mom', 'calendar_policy.event_mom', or "
            "'calendar_policy.weekday_wow'."
        ),
    )
    scope: ObserveScope | None = Field(default=None)
    granularity: Literal["hour", "day", "week", "month"] | None = Field(
        default=None,
        description="Time-series bucket size.  Only valid when result_mode='standard'.",
    )
    dimensions: list[str] | None = Field(
        default=None,
        description="Semantic dimensions for segmented output.  Only valid when result_mode='standard'.",
    )

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")

    @field_validator("calendar_policy_ref")
    @classmethod
    def _validate_calendar_policy_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return validate_calendar_policy_ref(value)
        except CalendarPolicyResolutionError as error:
            raise ValueError(str(error)) from error

    @model_validator(mode="after")
    def _validate_mode_combinations(self) -> ObserveRequest:
        if self.granularity is not None and self.dimensions is not None:
            raise ValueError("granularity and dimensions are mutually exclusive")
        if self.result_mode != "standard":
            if self.granularity is not None:
                raise ValueError("granularity is only valid when result_mode='standard'")
            if self.dimensions is not None:
                raise ValueError("dimensions is only valid when result_mode='standard'")
        kind = self.time_scope.kind if hasattr(self.time_scope, "kind") else None
        if kind in {"snapshot_now", "latest_available", "as_of"} and self.granularity is not None:
            raise ValueError(f"granularity is not valid when time_scope.kind='{kind}'")
        if self.granularity == "hour" and isinstance(self.time_scope, ObserveTimeScopeRange):
            normalize_hour_boundary(self.time_scope.start, label="time_scope.start")
            normalize_hour_boundary(self.time_scope.end, label="time_scope.end")
        if self.dimensions == []:
            self.dimensions = None
        return self


class CompareRequest(BaseModel):
    """Atomic intent: compute a typed delta between two observations."""

    left_ref: ObservationRef = Field(description="Reference to the 'current' observe artifact.")
    right_ref: ObservationRef = Field(description="Reference to the 'baseline' observe artifact.")
    mode: Literal["auto", "scalar", "segmented", "time_series"] = Field(
        default="auto",
        description=(
            "'auto' selects scalar, segmented, or time_series based on the input observation "
            "types. Explicit modes enforce a specific delta type."
        ),
    )


class DecomposeRequest(BaseModel):
    """Atomic intent: attribute a scalar delta across a single semantic dimension."""

    compare_ref: ArtifactRef = Field(
        description="Reference to an upstream `compare` step artifact (step_type='compare')."
    )
    dimension: str = Field(
        min_length=1,
        description="Single semantic dimension to decompose the delta across.",
    )
    method: str = Field(
        default="delta_share",
        description="Attribution method. Only 'delta_share' is supported in v1.",
    )

    @field_validator("compare_ref")
    @classmethod
    def _validate_compare_ref_type(cls, ref: ArtifactRef) -> ArtifactRef:
        if ref.step_type != "compare":
            raise ValueError(f"compare_ref.step_type must be 'compare', got '{ref.step_type}'")
        return ref


class CorrelateRequest(BaseModel):
    """Atomic intent: estimate statistical association between two time-series."""

    left_ref: CorrelateObservationRef = Field(
        description="Reference to a time-series observe artifact (left series)."
    )
    right_ref: CorrelateObservationRef = Field(
        description="Reference to a time-series observe artifact (right series)."
    )
    method: Literal["spearman", "pearson"] = Field(
        default="spearman",
        description="Correlation method ('spearman' or 'pearson').  v1 supports one method per request.",
    )
    min_pairs: int = Field(
        default=5,
        ge=1,
        description="Minimum number of aligned time-bucket pairs required.  Requests with fewer aligned pairs are rejected.",
    )


class DetectTimeScope(BaseModel):
    """Range-only time_scope for detect and auto-detect diagnose."""

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


class DetectRequest(BaseModel):
    """Atomic intent: scan a metric time range for anomaly candidates."""

    metric: str = Field(
        description="Canonical semantic metric ref to scan (e.g., 'metric.watch_time')."
    )
    time_scope: DetectTimeScope
    granularity: Literal["hour", "day", "week", "month"] = Field(
        description="Scan bucket size. Uses the same naming as observe.granularity."
    )
    scope: ObserveScope | None = Field(default=None)
    split_by: str | None = Field(
        default=None,
        description="Optional semantic dimension to split the metric into independent series.",
    )
    profile: Literal["auto", "spike_dip", "level_shift", "seasonal_residual"] = Field(
        default="auto",
        description="Detection profile preset.",
    )
    sensitivity: Literal["conservative", "balanced", "aggressive"] = Field(
        default="balanced",
        description="Detection sensitivity preset.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of candidates to return.",
    )
    max_series: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of series to scan when split_by is set.",
    )
    patterns: list[Literal["point_anomaly", "period_shift"]] | None = Field(
        default=None,
        description="Candidate patterns to scan. Omitted uses profile-derived defaults.",
    )

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")

    @model_validator(mode="after")
    def _validate_hour_window(self) -> DetectRequest:
        if self.granularity == "hour":
            normalize_hour_boundary(self.time_scope.start, label="time_scope.start")
            normalize_hour_boundary(self.time_scope.end, label="time_scope.end")
        return self


class HypothesisContract(BaseModel):
    """Hypothesis definition for the `test` atomic intent."""

    family: Literal["difference"] = Field(
        default="difference",
        description="Hypothesis family.  v1 only supports 'difference'.",
    )
    alternative: Literal["two_sided", "greater", "less"] = Field(
        default="two_sided",
        description="Direction of the alternative hypothesis.",
    )
    alpha: float = Field(
        default=0.05,
        gt=0.0,
        lt=1.0,
        description="Significance level α ∈ (0, 1).",
    )
    label: str | None = Field(
        default=None,
        description="Human-readable label.  Does not affect artifact identity.",
    )


class IntentTestRequest(BaseModel):
    """Atomic intent: evaluate a typed statistical hypothesis.

    Named IntentTestRequest to avoid collision with Python's built-in `test` usage.
    Exposed via the /intents/test endpoint.
    """

    left_ref: TestObservationRef = Field(
        description=(
            "Reference to an inferential-ready observe artifact "
            "(numeric_sample_summary or rate_sample_summary)."
        )
    )
    right_ref: TestObservationRef = Field(
        description="Reference to a second inferential-ready observe artifact."
    )
    hypothesis: HypothesisContract = Field(
        description="Hypothesis contract defining the statistical test to run.",
    )
    method: Literal["auto", "welch_t", "two_proportion_z"] = Field(
        default="auto",
        description=(
            "'auto' selects welch_t for numeric_sample_summary and "
            "two_proportion_z for rate_sample_summary."
        ),
    )


class ForecastRequest(BaseModel):
    """Atomic intent: project a time-series into future buckets."""

    source_ref: ObservationRef = Field(
        description="Reference to a completed time-series observe artifact."
    )
    horizon: int = Field(ge=1, le=90, description="Number of future buckets to forecast.")
    profile: Literal["auto", "level", "trend", "seasonal", "seasonal_trend"] = Field(
        default="auto",
        description=(
            "Forecast profile.  'auto' selects the best available v1 algorithm. "
            "'level' uses last-value carry-forward; 'trend' uses OLS linear extrapolation. "
            "'seasonal' and 'seasonal_trend' are accepted but unsupported in v1."
        ),
    )
    interval_level: float | None = Field(
        default=None,
        gt=0.0,
        lt=1.0,
        description="Prediction interval confidence level in (0, 1).  Defaults to 0.95.",
    )


class AttributeObservationInput(BaseModel):
    """One side of an attribute request — canonical observe scalar profile."""

    time_scope: ObserveTimeScope
    calendar_policy_ref: str | None = Field(
        default=None,
        description=(
            "Optional fixed calendar alignment policy ref for this side's internal observe step. "
            "Uses the same validation and builtin ref whitelist as ObserveRequest."
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


class ValidateObservationInput(BaseModel):
    """One side (left/right) of a validate intent: time scope + optional non-time scope."""

    time_scope: ObserveTimeScope
    scope: ObserveScope | None = None


class ValidateHypothesis(BaseModel):
    """Hypothesis specification for a validate intent (difference family only in v1)."""

    family: Literal["difference"] = "difference"
    alternative: Literal["two_sided", "greater", "less"] = "two_sided"
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    label: str | None = None


class ValidateRequest(BaseModel):
    """Derived intent: validate a hypothesis (expands to observe×2 + test)."""

    metric: str = Field(
        description="Canonical semantic metric ref to validate (e.g., 'metric.watch_time')."
    )
    left: ValidateObservationInput = Field(description="Primary / treatment population.")
    right: ValidateObservationInput = Field(description="Comparison / control population.")
    sample_kind: Literal["auto", "numeric", "rate"] | None = Field(
        default=None,
        description=(
            "Inferential summary mode. 'auto' fails in v1; use 'numeric' or 'rate' explicitly."
        ),
    )
    hypothesis: ValidateHypothesis | None = Field(default=None)
    method: Literal["auto", "welch_t", "two_proportion_z"] | None = Field(default=None)

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")
