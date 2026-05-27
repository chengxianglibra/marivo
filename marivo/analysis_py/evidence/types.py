"""Public immutable evidence models for the Python track."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from marivo.analysis_py.followups import BlockingIssue, ConfidenceScope, FollowupAction

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
    grain: Literal["hour", "day", "week", "month"] | None = None
    analysis_axis: Literal[
        "scalar", "time", "segment", "panel", "change", "decomposition",
        "correlation", "forecast", "anomaly",
    ]


class TimeWindow(_FrozenModel):
    field: str
    start: str
    end: str


class QualitySummary(_FrozenModel):
    coverage: float | None = None
    null_rate: float | None = None
    sample_size: int | None = None
    metric_definition_compatibility: Literal["exact", "compatible", "incompatible", "unknown"] | None = None


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
    "OpenAnomaly",
    "OpenItemKind",
    "OpenQuestion",
    "OpenQuestionReason",
    "Proposition",
    "PropositionType",
    "QualitySummary",
    "Subject",
    "TimeWindow",
    "TriggeredByFollowup",
]
