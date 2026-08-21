"""Closed, immutable evidence values for deterministic analysis digests."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from typing_extensions import TypeAliasType

from marivo.analysis._cumulative import AllHistoryLevelChangeSchema
from marivo.analysis._pages import _BoundedPage
from marivo.analysis._semantic_persistence import SlicePredicateV1
from marivo.analysis.candidate_lineage import CandidateOrigin, CandidateResolutionIssue
from marivo.analysis.errors import AnalysisRepair
from marivo.refs import RefPayloadV1
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, Card, result_repr
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    CatalogMetricSubjectV1,
    DeltaComparisonIdentity,
    DeltaMetricSubjectV1,
    MetricIdentity,
    RuntimeExpressionIdentity,
    RuntimeExpressionSubjectV1,
    TypedEvidenceSubject,
)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue = TypeAliasType(  # type: ignore[misc]
    "JsonValue",
    JsonScalar | tuple["JsonValue", ...] | list["JsonValue"] | dict[str, "JsonValue"],  # type: ignore[misc]
)
EvidenceStatus = Literal["complete", "partial", "unavailable"]
EvidenceCompleteness = EvidenceStatus
EpistemicKind = Literal[
    "observed",
    "algebraic",
    "estimated",
    "tested",
    "predicted",
    "candidate",
]
FindingType = Literal[
    "observation",
    "delta",
    "metric_value",
    "decomposition_item",
    "anomaly_candidate",
    "correlation_result",
    "test_result",
    "forecast_point",
    "quality_check",
]
DigestItemKind = Literal[
    "observation",
    "change",
    "contribution",
    "association",
    "test_decision",
    "forecast_output",
    "anomaly_candidate",
    "quality_check",
]
Direction = Literal["increase", "decrease", "flat", "undefined"]
ObservationShape = Literal[
    "scalar",
    "time_series",
    "segmented",
    "panel",
    "event_journey",
    "event_funnel",
    "event_time_to_event",
    "lifecycle_history",
    "lifecycle_distribution",
    "lifecycle_transitions",
    "lifecycle_dwell",
    "lifecycle_violations",
    "subject_set",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Subject(_FrozenModel):
    kind: Literal["metric"] = "metric"
    typed_metric_subject: TypedEvidenceSubject | None = None
    entity_ref: RefPayloadV1 | None = None
    slice_predicates: tuple[SlicePredicateV1, ...] = ()
    grain: str | None = None
    analysis_axis: Literal[
        "scalar",
        "time",
        "segment",
        "panel",
        "change",
        "decomposition",
        "correlation",
        "forecast",
        "anomaly",
        "quality",
    ]

    @property
    def metric(self) -> str | None:
        if isinstance(self.typed_metric_subject, CatalogMetricSubjectV1):
            return self.typed_metric_subject.metric_ref.path
        if isinstance(self.typed_metric_subject, RuntimeExpressionSubjectV1):
            return f"runtime:{self.typed_metric_subject.expression_fingerprint}"
        if isinstance(self.typed_metric_subject, DeltaMetricSubjectV1):
            current = self.typed_metric_subject.comparison.current
            baseline = self.typed_metric_subject.comparison.baseline
            if current == baseline:
                return (
                    current.metric_ref.path
                    if isinstance(current, CatalogMetricIdentity)
                    else f"runtime:{current.expression_fingerprint}"
                )
        return None

    @property
    def entity(self) -> str | None:
        return self.entity_ref.path if self.entity_ref is not None else None

    @property
    def slice(self) -> dict[str, JsonValue]:
        return {
            item.dimension_ref.path: cast("JsonValue", item.value) for item in self.slice_predicates
        }


class TimeWindow(_FrozenModel):
    field: str
    start: str
    end: str


class AnalysisScope(_FrozenModel):
    """Metric-shaped scope for one artifact and its evidence projection."""

    kind: Literal["metric"] = "metric"
    metric_identities: tuple[MetricIdentity, ...] = ()
    comparison: DeltaComparisonIdentity | None = None
    axis_refs: tuple[RefPayloadV1, ...] = ()
    segment_predicates: tuple[SlicePredicateV1, ...] = ()
    window: dict[str, JsonValue] | None = None
    assumptions: tuple[str, ...] = ()

    @property
    def metric_ids(self) -> tuple[str, ...]:
        identities = self.metric_identities
        if not identities and self.comparison is not None:
            identities = (self.comparison.current, self.comparison.baseline)
        metric_ids = tuple(
            identity.metric_ref.path
            if isinstance(identity, CatalogMetricIdentity)
            else f"runtime:{identity.expression_fingerprint}"
            for identity in identities
            if isinstance(identity, (CatalogMetricIdentity, RuntimeExpressionIdentity))
        )
        metric_ids = tuple(dict.fromkeys(metric_ids))
        return metric_ids

    @property
    def segment_keys(self) -> dict[str, JsonValue]:
        return {
            item.dimension_ref.path: cast("JsonValue", item.value)
            for item in self.segment_predicates
        }


class EventSubject(_FrozenModel):
    """Identity-safe subject descriptor for an Event artifact."""

    kind: Literal["event"] = "event"
    subject_entity_ref: RefPayloadV1
    subject_identity_signature: tuple[str, ...]
    analysis_axis: Literal[
        "journey",
        "funnel",
        "time_to_event",
        "funnel_delta",
        "funnel_loss_rate",
    ] = "journey"


class LifecycleSubject(_FrozenModel):
    """Identity-safe subject descriptor for a Lifecycle artifact."""

    kind: Literal["lifecycle"] = "lifecycle"
    subject_entity_ref: RefPayloadV1
    subject_identity_signature: tuple[str, ...]
    analysis_axis: Literal[
        "history",
        "distribution",
        "transitions",
        "dwell",
        "violations",
    ]


class EventAnalysisScope(_FrozenModel):
    """Typed Event Journey scope without raw subject or event identities."""

    kind: Literal["event"] = "event"
    pattern: dict[str, JsonValue]
    roles: tuple[dict[str, JsonValue], ...]
    matching: dict[str, JsonValue]
    cohort_window: dict[str, JsonValue]
    completion_through: str
    coverage: dict[str, JsonValue]
    assumptions: tuple[str, ...] = ()
    cohort_binding: dict[str, JsonValue] | None = None


class EventFunnelAnalysisScope(_FrozenModel):
    """Typed Event funnel scope derived from one committed journey."""

    kind: Literal["event_funnel"] = "event_funnel"
    source_artifact_ref: str
    source_scope: EventAnalysisScope
    axes: tuple[dict[str, JsonValue], ...] = ()
    grouped_reconciliation: dict[str, JsonValue]


class EventTimeToEventAnalysisScope(_FrozenModel):
    """Typed time-to-event scope derived from one committed journey."""

    kind: Literal["event_time_to_event"] = "event_time_to_event"
    source_artifact_ref: str
    source_scope: EventAnalysisScope
    start_step: dict[str, JsonValue]
    end_step: dict[str, JsonValue]
    axes: tuple[dict[str, JsonValue], ...] = ()


class LifecycleAnalysisScope(_FrozenModel):
    """Typed replay/reducer scope without raw subject or Event identities."""

    kind: Literal["lifecycle"] = "lifecycle"
    state_model_ref: RefPayloadV1
    state_model_fingerprint: str
    analysis_axis: Literal[
        "history",
        "distribution",
        "transitions",
        "dwell",
        "violations",
    ]
    source_history_ref: str | None = None
    window: dict[str, JsonValue] | None = None
    coverage: dict[str, JsonValue] | None = None
    cohort_binding: dict[str, JsonValue] | None = None
    replay_semantics: dict[str, JsonValue] | None = None
    reducer: dict[str, JsonValue] | None = None
    assumptions: tuple[str, ...] = ()


class SubjectSetSubject(_FrozenModel):
    """Identity-safe subject descriptor for a persisted SubjectSet."""

    kind: Literal["subject_set"] = "subject_set"
    subject_entity_ref: RefPayloadV1
    subject_identity_signature: tuple[str, ...]
    analysis_axis: Literal["subject_set"] = "subject_set"


class SubjectSetAnalysisScope(_FrozenModel):
    """Typed SubjectSet scope without selected identity rows."""

    kind: Literal["subject_set"] = "subject_set"
    source_artifact_ref: str
    source_artifact_fingerprint: str
    selection: dict[str, JsonValue]
    selection_fingerprint: str
    coverage_status: Literal["ready", "coverage_censored"]


EvidenceSubject = Annotated[
    Subject | EventSubject | LifecycleSubject | SubjectSetSubject,
    Field(discriminator="kind"),
]
EvidenceSubjectAdapter: TypeAdapter[EvidenceSubject] = TypeAdapter(EvidenceSubject)
EvidenceScope = Annotated[
    AnalysisScope
    | EventAnalysisScope
    | EventFunnelAnalysisScope
    | EventTimeToEventAnalysisScope
    | LifecycleAnalysisScope
    | SubjectSetAnalysisScope,
    Field(discriminator="kind"),
]
EvidenceScopeAdapter: TypeAdapter[EvidenceScope] = TypeAdapter(EvidenceScope)


class QualitySummary(_FrozenModel):
    coverage: float | None = None
    null_rate: float | None = None
    sample_size: int | None = None
    sample_coverage_min: float | None = None
    sample_coverage_avg: float | None = None
    sample_coverage_partial_buckets: int | None = None
    zero_denominator_rows: int | None = None
    evaluated_check_count: int | None = Field(default=None, ge=0)
    failed_check_count: int | None = Field(default=None, ge=0)
    warning_check_count: int | None = Field(default=None, ge=0)


class DerivationRule(_FrozenModel):
    rule_id: str
    rule_version: str
    operator: str
    source_fields: tuple[str, ...]
    source_finding_refs: tuple[str, ...]
    candidate_origins: tuple[CandidateOrigin, ...] = ()


class ObservationSegmentValue(_FrozenModel):
    keys: dict[str, JsonScalar] = Field(default_factory=dict)
    value: float | None = None
    share: float | None = None


class ScalarObservationValue(_FrozenModel):
    shape: Literal["scalar"] = "scalar"
    value: float | None = None
    unit: str | None = None


class TimeSeriesObservationValue(_FrozenModel):
    shape: Literal["time_series"] = "time_series"
    bucket_count: int = Field(ge=0)
    first_bucket: str | None = None
    last_bucket: str | None = None
    first_value: float | None = None
    last_value: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    mean_value: float | None = None
    partial_tail_bucket: bool = False
    endpoint_change_direction: Direction = "undefined"
    unit: str | None = None


class SegmentedObservationValue(_FrozenModel):
    shape: Literal["segmented"] = "segmented"
    segment_count: int = Field(ge=0)
    total_value: float | None = None
    top_segments: tuple[ObservationSegmentValue, ...] = ()
    unit: str | None = None


class PanelObservationValue(_FrozenModel):
    shape: Literal["panel"] = "panel"
    bucket_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    first_bucket: str | None = None
    last_bucket: str | None = None
    total_value: float | None = None
    top_segments: tuple[ObservationSegmentValue, ...] = ()
    unit: str | None = None


class EventJourneyObservationValue(_FrozenModel):
    shape: Literal["event_journey"] = "event_journey"
    attempt_count: int = Field(ge=0)
    complete_count: int = Field(ge=0)
    incomplete_count: int = Field(ge=0)
    coverage_censored_count: int = Field(ge=0)
    unused_event_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_attempt_partition(self) -> EventJourneyObservationValue:
        represented = self.complete_count + self.incomplete_count + self.coverage_censored_count
        if represented != self.attempt_count:
            raise ValueError(
                "attempt_count must equal complete_count + incomplete_count + "
                "coverage_censored_count"
            )
        return self


class EventFunnelStepObservation(_FrozenModel):
    """One bounded identity-safe funnel step summary."""

    step_key: str
    reached_count: int = Field(ge=0)
    lost_count: int = Field(ge=0)
    coverage_censored_count: int = Field(ge=0)
    conversion_from_first: float | None = Field(default=None, ge=0.0, le=1.0)
    conversion_from_previous: float | None = Field(default=None, ge=0.0, le=1.0)


class EventFunnelObservationValue(_FrozenModel):
    shape: Literal["event_funnel"] = "event_funnel"
    cohort_count: int = Field(ge=0)
    step_count: int = Field(ge=0)
    axis_tuple_count: int = Field(ge=0)
    source_unused_event_count: int = Field(ge=0)
    grouped: bool
    reconciliation_passed: bool
    steps: tuple[EventFunnelStepObservation, ...] = ()

    @model_validator(mode="after")
    def _validate_step_bound(self) -> EventFunnelObservationValue:
        if len(self.steps) > 5:
            raise ValueError("event funnel evidence retains at most five step summaries")
        if len(self.steps) > self.step_count:
            raise ValueError("step summaries cannot exceed step_count")
        return self


class EventTimeToEventObservationValue(_FrozenModel):
    shape: Literal["event_time_to_event"] = "event_time_to_event"
    qualifying_count: int = Field(ge=0)
    complete_count: int = Field(ge=0)
    incomplete_count: int = Field(ge=0)
    coverage_censored_count: int = Field(ge=0)
    source_unused_end_count: int = Field(ge=0)
    duration_count: int = Field(ge=0)
    min_duration_seconds: float | None = Field(default=None, ge=0.0)
    median_duration_seconds: float | None = Field(default=None, ge=0.0)
    max_duration_seconds: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _validate_partition(self) -> EventTimeToEventObservationValue:
        represented = self.complete_count + self.incomplete_count + self.coverage_censored_count
        if represented != self.qualifying_count:
            raise ValueError(
                "qualifying_count must equal complete_count + incomplete_count + "
                "coverage_censored_count"
            )
        if self.duration_count != self.complete_count:
            raise ValueError("duration_count must equal complete_count")
        durations = (
            self.min_duration_seconds,
            self.median_duration_seconds,
            self.max_duration_seconds,
        )
        if self.duration_count == 0 and any(value is not None for value in durations):
            raise ValueError("empty duration summaries must be null")
        if self.duration_count > 0 and any(value is None for value in durations):
            raise ValueError("non-empty duration summaries must be complete")
        return self


class LifecycleHistoryObservationValue(_FrozenModel):
    shape: Literal["lifecycle_history"] = "lifecycle_history"
    population_count: int = Field(ge=0)
    seeded_subject_count: int = Field(ge=0)
    coverage_censored_subject_count: int = Field(ge=0)
    interval_count: int = Field(ge=0)
    completed_interval_count: int = Field(ge=0)
    right_censored_interval_count: int = Field(ge=0)
    coverage_censored_interval_count: int = Field(ge=0)
    violation_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_intervals(self) -> LifecycleHistoryObservationValue:
        represented = (
            self.completed_interval_count
            + self.right_censored_interval_count
            + self.coverage_censored_interval_count
        )
        if represented != self.interval_count:
            raise ValueError("Lifecycle interval statuses must partition interval_count")
        if self.seeded_subject_count > self.population_count:
            raise ValueError("seeded_subject_count cannot exceed population_count")
        return self


class LifecycleDistributionObservationValue(_FrozenModel):
    shape: Literal["lifecycle_distribution"] = "lifecycle_distribution"
    instant_count: int = Field(ge=0)
    state_count: int = Field(ge=0)
    row_count: int = Field(ge=0)
    grouped: bool
    reconciliation_passed: bool


class LifecycleTransitionsObservationValue(_FrozenModel):
    shape: Literal["lifecycle_transitions"] = "lifecycle_transitions"
    modeled_pair_count: int = Field(ge=0)
    transition_count: int = Field(ge=0)
    nonzero_pair_count: int = Field(ge=0)


class LifecycleDwellObservationValue(_FrozenModel):
    shape: Literal["lifecycle_dwell"] = "lifecycle_dwell"
    state_count: int = Field(ge=0)
    interval_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    right_censored_count: int = Field(ge=0)
    coverage_censored_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_partition(self) -> LifecycleDwellObservationValue:
        represented = (
            self.completed_count + self.right_censored_count + self.coverage_censored_count
        )
        if represented != self.interval_count:
            raise ValueError("Lifecycle dwell statuses must partition interval_count")
        return self


class LifecycleViolationsObservationValue(_FrozenModel):
    shape: Literal["lifecycle_violations"] = "lifecycle_violations"
    violation_count: int = Field(ge=0)
    illegal_transition_count: int = Field(ge=0)
    transition_from_terminal_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_partition(self) -> LifecycleViolationsObservationValue:
        represented = self.illegal_transition_count + self.transition_from_terminal_count
        if represented != self.violation_count:
            raise ValueError("Lifecycle violation kinds must partition violation_count")
        return self


class SubjectSetObservationValue(_FrozenModel):
    shape: Literal["subject_set"] = "subject_set"
    selected_count: int = Field(ge=0)
    excluded_coverage_censored_count: int = Field(ge=0)
    coverage_status: Literal["ready", "coverage_censored"]


class FunnelDeltaObservationValue(_FrozenModel):
    shape: Literal["funnel_delta"] = "funnel_delta"
    step_count: int = Field(ge=0)
    axis_count: int = Field(ge=0)
    zero_filled_tuple_count: int = Field(ge=0)
    current_coverage_basis: str
    baseline_coverage_basis: str


class FunnelAttributionObservationValue(_FrozenModel):
    shape: Literal["funnel_attribution"] = "funnel_attribution"
    target_step_key: str
    contribution_count: int = Field(ge=0)
    positive_pool: float
    negative_pool: float
    residual: float
    reconciliation_status: Literal["reconciled"]


ObservationValue = Annotated[
    ScalarObservationValue
    | TimeSeriesObservationValue
    | SegmentedObservationValue
    | PanelObservationValue
    | EventJourneyObservationValue
    | EventFunnelObservationValue
    | EventTimeToEventObservationValue
    | LifecycleHistoryObservationValue
    | LifecycleDistributionObservationValue
    | LifecycleTransitionsObservationValue
    | LifecycleDwellObservationValue
    | LifecycleViolationsObservationValue
    | SubjectSetObservationValue
    | FunnelDeltaObservationValue
    | FunnelAttributionObservationValue,
    Field(discriminator="shape"),
]


class ObservationFindingValue(_FrozenModel):
    kind: Literal["observation"] = "observation"
    row_count: int = Field(ge=0)
    value: ObservationValue


class MetricValueFindingValue(_FrozenModel):
    kind: Literal["metric_value"] = "metric_value"
    value: float | None = None
    unit: str | None = None
    dimension_keys: dict[str, JsonScalar] = Field(default_factory=dict)
    bucket: str | None = None


class DeltaFindingValue(_FrozenModel):
    kind: Literal["delta"] = "delta"
    delta_kind: Literal["scalar_delta", "segmented_delta", "time_series_delta", "panel_delta"]
    current: float | None = None
    baseline: float | None = None
    magnitude: float | None = None
    relative_delta: float | None = None
    relative_delta_undefined_reason: str | None = None
    direction: Direction
    presence: Literal["current_only", "baseline_only"] | None = None
    unit: str | None = None
    dimension_keys: dict[str, JsonScalar] = Field(default_factory=dict)
    bucket: str | None = None
    current_evaluation_end: str | None = None
    baseline_evaluation_end: str | None = None
    matched_rows: int | None = Field(default=None, ge=0)
    matched_null_rows: int | None = Field(default=None, ge=0)
    current_unpaired_rows: int | None = Field(default=None, ge=0)
    baseline_unpaired_rows: int | None = Field(default=None, ge=0)
    fallback_rows: int | None = Field(default=None, ge=0)
    unpaired_action: Literal["dropped"] | None = None
    cumulative_change: AllHistoryLevelChangeSchema | None = None
    source_revision: Literal["unverified"] | None = None
    interval_flow_equivalence: Literal["not_asserted"] | None = None


class ContributionFindingValue(_FrozenModel):
    kind: Literal["decomposition_item"] = "decomposition_item"
    dimension: str
    dimension_keys: dict[str, JsonScalar] = Field(default_factory=dict)
    contribution_value: float | None = None
    contribution_share: float | None = None
    contribution_rank: int | None = Field(default=None, ge=1)
    direction: Direction = "undefined"
    decomposition_method: str
    reconciliation_residual: float | None = None
    scope_delta_ref: str
    resolution_axis_refs: tuple[RefPayloadV1, ...] = ()
    rollup_safe: bool | None = None
    causal_claim: Literal["none"] = "none"
    contribution_std_error: float | None = None
    source_error_bound: float | None = None


class AnomalyCandidateFindingValue(_FrozenModel):
    kind: Literal["anomaly_candidate"] = "anomaly_candidate"
    candidate_ref: str
    score: float | None = None
    detector: str
    threshold: float | None = None
    rank: int = Field(ge=1)
    reason_codes: tuple[str, ...] = ()
    flag_level: str | None = None
    current_value: float | None = None
    baseline_value: float | None = None
    deviation_absolute: float | None = None
    deviation_relative: float | None = None


class AssociationFindingValue(_FrozenModel):
    kind: Literal["correlation_result"] = "correlation_result"
    left_ref: str
    right_ref: str
    method: str
    coefficient: float | None = None
    p_value: float | None = None
    confidence_interval: tuple[float, float] | None = None
    sample_size: int | None = Field(default=None, ge=0)
    join_basis: str
    lag: float | None = None


class TestFindingValue(_FrozenModel):
    kind: Literal["test_result"] = "test_result"
    null_predicate: str
    alternative: Literal["two_sided", "greater", "less"]
    method: str
    alpha: float = Field(gt=0.0, lt=1.0)
    statistic: float | None = None
    p_value: float | None = None
    effect_estimate: float | None = None
    confidence_interval: tuple[float, float] | None = None
    reject_null: bool | None = None
    sample_size: int | None = Field(default=None, ge=0)


class ForecastPointFindingValue(_FrozenModel):
    kind: Literal["forecast_point"] = "forecast_point"
    bucket_start: str
    bucket_end: str
    predicted_value: float | None = None
    prediction_interval: tuple[float, float] | None = None
    horizon_index: int = Field(ge=1)
    model: str
    training_scope: AnalysisScope
    evaluation_scope: AnalysisScope | None = None
    observed_actual: float | None = None
    accuracy_metric: float | None = None


class QualityCheckFindingValue(_FrozenModel):
    kind: Literal["quality_check"] = "quality_check"
    check_id: str
    measured_value: JsonScalar
    expectation_predicate: str
    expectation_parameters: dict[str, JsonScalar] = Field(default_factory=dict)
    expectation_condition_passed: bool
    evaluated_scope: EvidenceScope
    source_refs: tuple[str, ...] = ()


FindingValue = Annotated[
    ObservationFindingValue
    | MetricValueFindingValue
    | DeltaFindingValue
    | ContributionFindingValue
    | AnomalyCandidateFindingValue
    | AssociationFindingValue
    | TestFindingValue
    | ForecastPointFindingValue
    | QualityCheckFindingValue,
    Field(discriminator="kind"),
]
FindingValueAdapter: TypeAdapter[FindingValue] = TypeAdapter(FindingValue)

_FINDING_EPISTEMIC_KIND: dict[FindingType, EpistemicKind] = {
    "observation": "observed",
    "metric_value": "observed",
    "delta": "algebraic",
    "decomposition_item": "algebraic",
    "anomaly_candidate": "candidate",
    "correlation_result": "estimated",
    "test_result": "tested",
    "forecast_point": "predicted",
    "quality_check": "tested",
}


class Finding(_FrozenModel):
    finding_id: str
    finding_type: FindingType
    epistemic_kind: EpistemicKind
    artifact_id: str
    session_id: str
    subject: EvidenceSubject
    canonical_item_key: str
    value: FindingValue
    derivation: DerivationRule
    source_refs: tuple[str, ...] = ()
    observed_window: TimeWindow | None = None
    quality_status: Literal["ready", "needs_attention", "not_ready"] | None = None
    committed_at: datetime
    extractor_version: str = "v4"
    artifact_schema_version: str = "v4"

    @model_validator(mode="after")
    def _validate_kind_mapping(self) -> Finding:
        if self.value.kind != self.finding_type:
            raise ValueError("finding_type must match value.kind")
        expected = _FINDING_EPISTEMIC_KIND[self.finding_type]
        if self.epistemic_kind != expected:
            raise ValueError(f"{self.finding_type} findings require epistemic_kind={expected!r}")
        return self


class OperatorSemantics(_FrozenModel):
    operator: str
    operator_version: str
    artifact_family: str
    semantic_shape: str | None = None


class _DigestItemBase(_FrozenModel):
    item_id: str
    kind: DigestItemKind
    epistemic_kind: EpistemicKind
    artifact_ref: str
    subject: EvidenceSubject
    scope: EvidenceScope
    derivation: DerivationRule


class ObservationFact(_DigestItemBase):
    kind: Literal["observation"] = "observation"
    epistemic_kind: Literal["observed"] = "observed"
    row_count: int = Field(ge=0)
    value: ObservationValue


class ChangeFact(_DigestItemBase):
    kind: Literal["change"] = "change"
    epistemic_kind: Literal["algebraic"] = "algebraic"
    current: float | None = None
    baseline: float | None = None
    delta: float | None = None
    relative_delta: float | None = None
    relative_delta_undefined_reason: str | None = None
    direction: Direction
    presence: Literal["current_only", "baseline_only"] | None = None
    unit: str | None = None
    dimension_keys: dict[str, JsonScalar] = Field(default_factory=dict)


class ContributionFact(_DigestItemBase):
    kind: Literal["contribution"] = "contribution"
    epistemic_kind: Literal["algebraic"] = "algebraic"
    dimension: str
    dimension_keys: dict[str, JsonScalar] = Field(default_factory=dict)
    contribution_value: float | None = None
    contribution_share: float | None = None
    contribution_rank: int | None = Field(default=None, ge=1)
    decomposition_method: str
    reconciliation_residual: float | None = None
    resolution_axis_refs: tuple[RefPayloadV1, ...] = ()
    rollup_safe: bool | None = None
    causal_claim: Literal["none"] = "none"
    contribution_std_error: float | None = None
    source_error_bound: float | None = None


class AssociationFact(_DigestItemBase):
    kind: Literal["association"] = "association"
    epistemic_kind: Literal["estimated"] = "estimated"
    left_ref: str
    right_ref: str
    method: str
    coefficient: float | None = None
    p_value: float | None = None
    confidence_interval: tuple[float, float] | None = None
    sample_size: int | None = Field(default=None, ge=0)
    join_basis: str
    lag: float | None = None


class TestDecision(_DigestItemBase):
    kind: Literal["test_decision"] = "test_decision"
    epistemic_kind: Literal["tested"] = "tested"
    null_predicate: str
    alternative: Literal["two_sided", "greater", "less"]
    method: str
    alpha: float = Field(gt=0.0, lt=1.0)
    statistic: float | None = None
    p_value: float | None = None
    effect_estimate: float | None = None
    confidence_interval: tuple[float, float] | None = None
    reject_null: bool | None = None
    sample_size: int | None = Field(default=None, ge=0)


class ForecastOutput(_DigestItemBase):
    kind: Literal["forecast_output"] = "forecast_output"
    epistemic_kind: Literal["predicted"] = "predicted"
    bucket_start: str
    bucket_end: str
    predicted_value: float | None = None
    prediction_interval: tuple[float, float] | None = None
    horizon_index: int = Field(ge=1)
    model: str
    training_scope: AnalysisScope
    evaluation_scope: AnalysisScope | None = None


class AnomalyCandidate(_DigestItemBase):
    kind: Literal["anomaly_candidate"] = "anomaly_candidate"
    epistemic_kind: Literal["candidate"] = "candidate"
    candidate_ref: str
    score: float | None = None
    detector: str
    threshold: float | None = None
    rank: int = Field(ge=1)
    reason_codes: tuple[str, ...] = ()
    flag_level: str | None = None
    current_value: float | None = None
    baseline_value: float | None = None
    deviation_absolute: float | None = None
    deviation_relative: float | None = None


class QualityCheckResult(_DigestItemBase):
    kind: Literal["quality_check"] = "quality_check"
    epistemic_kind: Literal["tested"] = "tested"
    check_id: str
    measured_value: JsonScalar
    expectation_predicate: str
    expectation_parameters: dict[str, JsonScalar] = Field(default_factory=dict)
    expectation_condition_passed: bool


DigestItem = Annotated[
    ObservationFact
    | ChangeFact
    | ContributionFact
    | AssociationFact
    | TestDecision
    | ForecastOutput
    | AnomalyCandidate
    | QualityCheckResult,
    Field(discriminator="kind"),
]
DigestItemAdapter: TypeAdapter[DigestItem] = TypeAdapter(DigestItem)

InferenceBoundaryKind = Literal[
    "significance_not_computed",
    "interval_not_computed",
    "causal_effect_not_estimated",
    "business_impact_not_provided",
    "forecast_actual_not_observed",
    "forecast_accuracy_not_evaluated",
    "candidate_not_reviewed",
    "full_distribution_not_in_digest",
    "raw_rows_omitted",
    "quality_dimensions_not_tested",
]
InferenceBoundaryReason = Literal[
    "operator_did_not_compute",
    "artifact_does_not_contain",
    "digest_bound_exceeded",
    "outside_library_contract",
    "requires_independent_evidence",
]
RequiredEvidenceKind = Literal[
    "significance_statistic",
    "uncertainty_interval",
    "causal_design",
    "business_policy",
    "observed_forecast_actual",
    "forecast_error_metric",
    "independent_review",
    "full_distribution",
    "raw_rows",
    "additional_quality_check",
]


class InferenceBoundary(_FrozenModel):
    kind: InferenceBoundaryKind
    reason: InferenceBoundaryReason
    required_evidence: tuple[RequiredEvidenceKind, ...]


class OmissionSummary(_FrozenModel):
    retained_items: int = Field(ge=0)
    omitted_items: int = Field(ge=0)
    omitted_kinds: tuple[DigestItemKind, ...] = ()
    bounded: bool


FallbackReason = Literal[
    "omitted_item_detail",
    "row_level_validation",
    "unregistered_question",
    "recompute_with_additional_statistic",
    "partial_evidence",
]


class RawFallback(_FrozenModel):
    artifact_ref: str
    findings_available: bool
    rows_available: bool
    recommended_when: tuple[FallbackReason, ...] = ()


IssueSeverity = Literal["warning", "blocking"]
DataQualityIssueKind = Literal[
    "metric_row_contract_invalid",
    "null_rate_high",
    "sample_size_low",
    "time_coverage_incomplete",
    "value_density_low",
    "outlier_sensitivity_detected",
    "duplicate_keys_detected",
    "delta_row_contract_invalid",
    "delta_math_invalid",
    "attribution_row_contract_invalid",
    "attribution_contribution_invalid",
    "attribution_reconciliation_invalid",
    "cumulative_alignment_caveat_present",
    "unit_capability_unknown",
    "event_identity_invalid",
    "event_participant_invalid",
    "event_order_invalid",
    "event_coverage_unknown",
    "event_row_contract_invalid",
    "event_censoring_present",
    "declared_completeness_used",
    "lifecycle_row_contract_invalid",
    "lifecycle_source_invalid",
    "lifecycle_trace_invalid",
    "lifecycle_coverage_unknown",
    "lifecycle_censoring_present",
]
ComparabilityIssueKind = Literal[
    "comparability_incompatible",
    "comparability_approximate",
    "definition_drift_detected",
    "cross_session_scope_mismatch",
]
EvidenceAvailabilityIssueKind = Literal[
    "evidence_partial",
    "evidence_store_unavailable",
    "evidence_digest_unavailable",
]


class DataQualityIssue(_FrozenModel):
    issue_id: str
    kind: DataQualityIssueKind
    severity: IssueSeverity
    source_refs: tuple[str, ...]
    check_id: str
    observed_value: JsonScalar
    expectation: str
    evaluated_scope: EvidenceScope
    repair: AnalysisRepair | None = None


class ComparabilityIssue(_FrozenModel):
    issue_id: str
    kind: ComparabilityIssueKind
    severity: IssueSeverity
    source_refs: tuple[str, ...]
    left_scope: EvidenceScope
    right_scope: EvidenceScope
    incompatible_fields: tuple[str, ...] = ()
    definition_refs: tuple[str, ...] = ()
    approximation_details: tuple[str, ...] = ()
    repair: AnalysisRepair | None = None


class EvidenceAvailabilityIssue(_FrozenModel):
    issue_id: str
    kind: EvidenceAvailabilityIssueKind
    severity: IssueSeverity
    source_refs: tuple[str, ...]
    failed_stage: Literal["extract", "digest", "store"]
    findings_available: bool
    fallback: RawFallback
    stable_error_category: str
    repair: AnalysisRepair | None = None


ArtifactIssue = Annotated[
    DataQualityIssue | ComparabilityIssue | EvidenceAvailabilityIssue | CandidateResolutionIssue,
    Field(discriminator="kind"),
]
ArtifactIssueAdapter: TypeAdapter[ArtifactIssue] = TypeAdapter(ArtifactIssue)


class DigestReadContract(_FrozenModel):
    exact_reads: tuple[str, ...]

    def _repr_identity(self) -> str:
        return f"DigestReadContract exact_reads={len(self.exact_reads)}"

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str:
        """Render exact persisted reads without reading SQLite or raw rows."""
        return (
            Card(
                identity=self._repr_identity(),
                available=(".exact_reads", ".model_dump()", ".show()"),
            )
            .listing("exact persisted reads", self.exact_reads)
            .render(max_output_bytes=max_output_bytes)
        )

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None:
        """Print exact persisted reads without executing them."""
        print(self.render(max_output_bytes=max_output_bytes))

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def __str__(self) -> str:
        return self.render()


class ArtifactDigest(_FrozenModel):
    digest_version: Literal["v1", "v2"] = "v2"
    artifact_ref: str
    operator: OperatorSemantics
    subject: EvidenceSubject
    scope: EvidenceScope
    items: tuple[DigestItem, ...] = ()
    boundaries: tuple[InferenceBoundary, ...] = ()
    omissions: OmissionSummary
    quality: QualitySummary | None = None
    fallback: RawFallback
    fingerprint: str

    @model_validator(mode="after")
    def _validate_bounds(self) -> ArtifactDigest:
        if len(self.items) > 5:
            raise ValueError("ArtifactDigest retains at most five items")
        if len(self.boundaries) > 3:
            raise ValueError("ArtifactDigest retains at most three boundaries")
        if self.omissions.retained_items != len(self.items):
            raise ValueError("omissions.retained_items must match items")
        return self

    def __repr__(self) -> str:
        return result_repr(
            f"ArtifactDigest ref={self.artifact_ref} operator={self.operator.operator} "
            f"items={len(self.items)} omitted={self.omissions.omitted_items}"
        )

    def render(self, *, max_output_bytes: int | None = 8_000) -> str:
        """Render this persisted digest without reading raw rows or SQLite."""
        from marivo.analysis.evidence.summary import render_artifact_digest

        return render_artifact_digest(self, max_output_bytes=max_output_bytes)

    def show(self, *, max_output_bytes: int | None = 8_000) -> None:
        """Print this persisted digest without reading raw rows or SQLite."""
        print(self.render(max_output_bytes=max_output_bytes))

    def contract(self) -> DigestReadContract:
        return DigestReadContract(
            exact_reads=(
                f"session.evidence.digest({self.artifact_ref!r})",
                f"session.evidence.findings(artifact_ref={self.artifact_ref!r})",
                f"session.get_frame({self.artifact_ref!r})",
            )
        )


class EvidenceDerivationTrace(_FrozenModel):
    finding: Finding
    derivation: DerivationRule
    source_artifact_ref: str
    source_fields: tuple[str, ...]
    source_refs: tuple[str, ...]
    retained_digest_item_refs: tuple[str, ...] = ()


class ArtifactDigestPage(_BoundedPage[ArtifactDigest]):
    """Bounded newest-first page of persisted artifact digests."""


class FindingPage(_BoundedPage[Finding]):
    """Bounded newest-first page of canonical typed findings."""


__all__ = [
    "AnalysisScope",
    "AnomalyCandidate",
    "AnomalyCandidateFindingValue",
    "ArtifactDigest",
    "ArtifactDigestPage",
    "ArtifactIssue",
    "ArtifactIssueAdapter",
    "AssociationFact",
    "AssociationFindingValue",
    "ChangeFact",
    "ComparabilityIssue",
    "ContributionFact",
    "ContributionFindingValue",
    "DataQualityIssue",
    "DeltaFindingValue",
    "DerivationRule",
    "DigestItem",
    "DigestItemKind",
    "Direction",
    "EpistemicKind",
    "EventAnalysisScope",
    "EventJourneyObservationValue",
    "EventSubject",
    "EvidenceAvailabilityIssue",
    "EvidenceCompleteness",
    "EvidenceDerivationTrace",
    "EvidenceScope",
    "EvidenceScopeAdapter",
    "EvidenceStatus",
    "EvidenceSubject",
    "EvidenceSubjectAdapter",
    "FallbackReason",
    "Finding",
    "FindingPage",
    "FindingType",
    "FindingValue",
    "ForecastOutput",
    "ForecastPointFindingValue",
    "FunnelAttributionObservationValue",
    "FunnelDeltaObservationValue",
    "InferenceBoundary",
    "InferenceBoundaryKind",
    "InferenceBoundaryReason",
    "LifecycleAnalysisScope",
    "LifecycleDistributionObservationValue",
    "LifecycleDwellObservationValue",
    "LifecycleHistoryObservationValue",
    "LifecycleSubject",
    "LifecycleTransitionsObservationValue",
    "LifecycleViolationsObservationValue",
    "MetricValueFindingValue",
    "ObservationFact",
    "ObservationFindingValue",
    "ObservationSegmentValue",
    "ObservationShape",
    "ObservationValue",
    "OmissionSummary",
    "OperatorSemantics",
    "PanelObservationValue",
    "QualityCheckFindingValue",
    "QualityCheckResult",
    "QualitySummary",
    "RawFallback",
    "RequiredEvidenceKind",
    "ScalarObservationValue",
    "SegmentedObservationValue",
    "Subject",
    "TestDecision",
    "TestFindingValue",
    "TimeSeriesObservationValue",
    "TimeWindow",
]
