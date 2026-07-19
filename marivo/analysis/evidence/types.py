"""Closed, immutable evidence values for deterministic analysis digests."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from marivo.analysis._pages import _BoundedPage
from marivo.analysis.errors import AnalysisRepair
from marivo.semantic.metric_graph import TypedEvidenceSubject

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | tuple["JsonValue", ...] | dict[str, "JsonValue"]
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
ObservationShape = Literal["scalar", "time_series", "segmented", "panel"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Subject(_FrozenModel):
    typed_metric_subject: TypedEvidenceSubject | None = None
    metric: str | None = None
    entity: str | None = None
    slice: dict[str, JsonValue] = Field(default_factory=dict)
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


class TimeWindow(_FrozenModel):
    field: str
    start: str
    end: str


class AnalysisScope(_FrozenModel):
    """Metric-shaped scope for one artifact and its evidence projection."""

    metric_ids: tuple[str, ...] = ()
    segment_keys: dict[str, JsonValue] = Field(default_factory=dict)
    window: dict[str, JsonValue] | None = None
    assumptions: tuple[str, ...] = ()


class QualitySummary(_FrozenModel):
    coverage: float | None = None
    null_rate: float | None = None
    sample_size: int | None = None
    metric_definition_compatibility: (
        Literal["exact", "compatible", "incompatible", "unknown"] | None
    ) = None
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


ObservationValue = Annotated[
    ScalarObservationValue
    | TimeSeriesObservationValue
    | SegmentedObservationValue
    | PanelObservationValue,
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
    evaluated_scope: AnalysisScope
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
    subject: Subject
    canonical_item_key: str
    value: FindingValue
    derivation: DerivationRule
    source_refs: tuple[str, ...] = ()
    observed_window: TimeWindow | None = None
    quality_status: Literal["ready", "needs_attention", "not_ready"] | None = None
    committed_at: datetime
    extractor_version: str = "v3"
    artifact_schema_version: str = "v3"

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
    subject: Subject
    scope: AnalysisScope
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
    "null_rate_high",
    "sample_size_low",
    "time_coverage_incomplete",
    "outlier_sensitivity_detected",
    "duplicate_keys_detected",
    "unit_capability_unknown",
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
    evaluated_scope: AnalysisScope
    repair: AnalysisRepair | None = None


class ComparabilityIssue(_FrozenModel):
    issue_id: str
    kind: ComparabilityIssueKind
    severity: IssueSeverity
    source_refs: tuple[str, ...]
    left_scope: AnalysisScope
    right_scope: AnalysisScope
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
    DataQualityIssue | ComparabilityIssue | EvidenceAvailabilityIssue,
    Field(discriminator="kind"),
]
ArtifactIssueAdapter: TypeAdapter[ArtifactIssue] = TypeAdapter(ArtifactIssue)


class DigestReadContract(_FrozenModel):
    exact_reads: tuple[str, ...]


class ArtifactDigest(_FrozenModel):
    digest_version: str = "v1"
    artifact_ref: str
    operator: OperatorSemantics
    subject: Subject
    scope: AnalysisScope
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
        return (
            f"ArtifactDigest(ref={self.artifact_ref!r}, version={self.digest_version!r}, "
            f"items={len(self.items)}, omitted={self.omissions.omitted_items}; use .show())"
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
    "EvidenceAvailabilityIssue",
    "EvidenceCompleteness",
    "EvidenceDerivationTrace",
    "EvidenceStatus",
    "FallbackReason",
    "Finding",
    "FindingPage",
    "FindingType",
    "FindingValue",
    "ForecastOutput",
    "ForecastPointFindingValue",
    "InferenceBoundary",
    "InferenceBoundaryKind",
    "InferenceBoundaryReason",
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
