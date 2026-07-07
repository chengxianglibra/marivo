"""Public immutable evidence models for the Python track."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from marivo.analysis.followups import BlockingIssue, ConfidenceScope, FollowupAction

FindingType = Literal[
    "observation",
    "delta",
    "metric_value",
    "decomposition_item",
    "anomaly_candidate",
    "correlation_result",
    "test_result",
    "forecast_point",
]

PropositionType = Literal[
    "change",
    "anomaly",
    "driver",
    "tested_hypothesis",
    "forecast",
    "association",
]

AssessmentStatus = Literal["validated", "refuted", "inconclusive", "pending"]
FactKind = Literal["change", "driver", "tested_hypothesis", "forecast", "association"]
OpenItemKind = Literal["anomaly", "question"]
OpenQuestionReason = Literal["reopened_gap", "persistent_blocking_issue"]

EvidenceCompleteness = Literal["complete", "partial", "unavailable"]
EvidenceStatus = Literal["complete", "partial", "unavailable"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Subject(_FrozenModel):
    metric: str | None = None
    entity: str | None = None
    slice: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
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
    ]

    @field_validator("slice", mode="before")
    @classmethod
    def _normalize_slice(cls, value: Any) -> dict[str, str | int | float | bool | None]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str | int | float | bool | None] = {}
        for key, raw in value.items():
            if raw is None or isinstance(raw, (str, int, float, bool)):
                normalized[str(key)] = raw
            else:
                normalized[str(key)] = json.dumps(
                    raw,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
        return normalized


class TimeWindow(_FrozenModel):
    field: str
    start: str
    end: str


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


class Finding(_FrozenModel):
    finding_id: str
    finding_type: FindingType
    artifact_id: str
    session_id: str
    subject: Subject
    canonical_item_key: str
    observed_window: TimeWindow | None = None
    quality_status: Literal["ready", "needs_attention", "not_ready"] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    committed_at: datetime
    extractor_version: str = "v1"
    artifact_schema_version: str = "v1"


class Proposition(_FrozenModel):
    proposition_id: str
    session_id: str
    proposition_type: PropositionType
    origin_kind: Literal["system_seeded"] = "system_seeded"
    derivation_version: str
    subject_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    seed_finding_refs: list[str] = Field(default_factory=list)
    created_at: datetime


class Assessment(_FrozenModel):
    snapshot_id: str
    proposition_id: str
    session_id: str
    supersedes_id: str | None = None
    status: AssessmentStatus
    confidence: float | None = None
    confidence_basis: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    is_latest: bool = True


class _FactBase(_FrozenModel):
    id: str
    kind: FactKind
    subject: Subject
    window: TimeWindow | None = None
    status: AssessmentStatus
    confidence: float | None = None
    confidence_basis: str
    source_refs: list[str] = Field(default_factory=list)
    latest_assessment_id: str


class ChangeFact(_FactBase):
    kind: Literal["change"] = "change"
    direction: Literal["increase", "decrease", "flat", "undefined"]
    magnitude: float | None = None
    comparison_window: TimeWindow | None = None
    comparison_basis: str
    dimension_keys: dict[str, str] | None = None


class LagSweepSummary(_FrozenModel):
    grid_min: float
    grid_max: float
    step: float
    selected_lag: float | None = None


class AttributedDriver(_FactBase):
    kind: Literal["driver"] = "driver"
    dimension: str
    dimension_keys: dict[str, str | int | float | bool | None]
    contribution_value: float | None = None
    contribution_share: float | None = None
    contribution_role: Literal[
        "offsetting_factor",
        "primary_driver",
        "secondary_driver",
        "material_component",
    ]
    scope_change_id: str | None = None


class TestedHypothesis(_FactBase):
    kind: Literal["tested_hypothesis"] = "tested_hypothesis"
    hypothesis_family: Literal["difference", "association"]
    alternative: Literal["two_sided", "greater", "less"]
    method_family: str
    alpha: float
    p_value: float | None = None
    reject_null: bool | None = None


class ForecastSummary(_FactBase):
    kind: Literal["forecast"] = "forecast"
    forecast_window: TimeWindow
    horizon_index: int
    forecast_kind: Literal["interval", "point"]
    prediction_interval: list[float] | None = None


class AssociationSummary(_FactBase):
    kind: Literal["association"] = "association"
    left_subject: dict[str, Any]
    right_subject: dict[str, Any]
    method_family: str
    coefficient: float | None = None
    lag_mode: Literal["single", "sweep"] = "single"
    lag: float | None = None
    lag_sweep: LagSweepSummary | None = None
    join_basis: str


ObservationShape = Literal["scalar", "time_series", "segmented", "panel"]


class ObservationSegmentShare(_FrozenModel):
    keys: dict[str, str] = Field(default_factory=dict)
    value: float | None = None
    share: float | None = None


class ScalarObservationDigest(_FrozenModel):
    shape: Literal["scalar"] = "scalar"
    value: float | None = None


class TimeSeriesObservationDigest(_FrozenModel):
    shape: Literal["time_series"] = "time_series"
    bucket_count: int
    first_bucket: str | None = None
    last_bucket: str | None = None
    first_value: float | None = None
    last_value: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    mean_value: float | None = None
    direction: Literal["increase", "decrease", "flat", "undefined"] = "undefined"


class SegmentedObservationDigest(_FrozenModel):
    shape: Literal["segmented"] = "segmented"
    segment_count: int
    total_value: float | None = None
    top_segments: list[ObservationSegmentShare] = Field(default_factory=list)


class PanelObservationDigest(_FrozenModel):
    shape: Literal["panel"] = "panel"
    bucket_count: int
    segment_count: int
    first_bucket: str | None = None
    last_bucket: str | None = None
    top_segments: list[ObservationSegmentShare] = Field(default_factory=list)


ObservationDigest = Annotated[
    ScalarObservationDigest
    | TimeSeriesObservationDigest
    | SegmentedObservationDigest
    | PanelObservationDigest,
    Field(discriminator="shape"),
]


class ObservationSummary(_FrozenModel):
    """Bounded Surface 2 record of one observe / derive_metric_frame commit.

    Observations are ground truth, not assessed claims: unlike facts they
    carry no status, confidence, or assessment linkage.
    """

    id: str
    subject: Subject
    window: TimeWindow | None = None
    semantic_kind: ObservationShape
    analysis_purpose: str | None = None
    row_count: int = 0
    digest: ObservationDigest
    source_refs: list[str] = Field(default_factory=list)


class _OpenItemBase(_FrozenModel):
    id: str
    kind: OpenItemKind
    subject: Subject
    window: TimeWindow | None = None
    status: AssessmentStatus
    confidence: float | None = None
    confidence_basis: str
    source_refs: list[str] = Field(default_factory=list)
    latest_assessment_id: str


class OpenAnomaly(_OpenItemBase):
    kind: Literal["anomaly"] = "anomaly"


class OpenQuestion(_OpenItemBase):
    kind: Literal["question"] = "question"
    reason: OpenQuestionReason


class TriggeredByFollowup(_FrozenModel):
    action_id: str
    source_artifact_id: str
    via: Literal["run_followup", "manual"]


class BlockedFollowup(_FrozenModel):
    action_id: str
    operator: str | None
    source_artifact_id: str
    reason: Literal[
        "missing_input_artifact",
        "blocking_issue_unresolved",
        "downstream_of_unavailable_evidence",
    ]
    blocking_issue_kind: str | None = None


class EvidenceTrace(_FrozenModel):
    proposition: Proposition
    latest_assessment: Assessment | None = None
    seed_findings: list[Finding] = Field(default_factory=list)
    support_findings: list[Finding] = Field(default_factory=list)
    oppose_findings: list[Finding] = Field(default_factory=list)
    source_artifacts: list[str] = Field(default_factory=list)
    source_steps: list[str] = Field(default_factory=list)


__all__ = [
    "Assessment",
    "AssessmentStatus",
    "AssociationSummary",
    "AttributedDriver",
    "BlockedFollowup",
    "BlockingIssue",
    "ChangeFact",
    "ConfidenceScope",
    "EvidenceCompleteness",
    "EvidenceStatus",
    "EvidenceTrace",
    "FactKind",
    "Finding",
    "FindingType",
    "FollowupAction",
    "ForecastSummary",
    "LagSweepSummary",
    "ObservationDigest",
    "ObservationSegmentShare",
    "ObservationShape",
    "ObservationSummary",
    "OpenAnomaly",
    "OpenItemKind",
    "OpenQuestion",
    "OpenQuestionReason",
    "PanelObservationDigest",
    "Proposition",
    "PropositionType",
    "QualitySummary",
    "ScalarObservationDigest",
    "SegmentedObservationDigest",
    "Subject",
    "TestedHypothesis",
    "TimeSeriesObservationDigest",
    "TimeWindow",
    "TriggeredByFollowup",
]
