"""Typed response models for session lifecycle, state, and runtime APIs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from marivo.transports.http.models.json_contract import JsonObject, ScalarMap


class SessionGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str


class SessionScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: ScalarMap | None = None


class SessionLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    terminal_reason: str | None = None
    ended_at: str | None = None
    rollover_from_session_id: str | None = None


class SessionStateViewRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    view_type: str


class SessionStateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_view_ref: SessionStateViewRef


class AnalysisSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    goal: SessionGoal
    scope: SessionScope
    owner_user: str | None = None
    lifecycle: SessionLifecycle
    state_summary: SessionStateSummary
    created_at: str
    updated_at: str
    schema_version: str


SessionCreateResponse = AnalysisSession
SessionDetailResponse = AnalysisSession
SessionTerminateResponse = AnalysisSession


class SessionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AnalysisSession]
    next_page_token: str | None = None


class RuntimeBacklogSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queued_artifacts: int
    queued_propositions: int
    backpressured_propositions: int
    failed_items: int


class SessionRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    overall_status: str
    last_successful_stage: str | None = None
    blocked_reason: str
    backlog_summary: RuntimeBacklogSummary
    updated_at: str
    schema_version: str


class SessionTraceWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    field: str | None = None


class SessionTraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    step_type: str
    created_at: str
    summary: str | None = None
    artifact_id: str | None = None
    output_summary: JsonObject | None = None
    provenance: JsonObject | None = None
    semantic_metadata: JsonObject | None = None
    warnings: list[SessionTraceWarning]


class SessionTraceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    goal: str | None = None
    lifecycle_status: str
    created_at: str
    updated_at: str
    steps: list[SessionTraceStep]
    artifact_ids: list[str]
    schema_version: str


class ArtifactExtractorKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: str
    artifact_schema_version: str | None = None
    extractor_version: str | None = None


class ArtifactRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    artifact_id: str
    artifact_stage: str
    extractor_key: ArtifactExtractorKey
    correlation_id: str
    attempt_id: str | None = None
    last_failure_reason: str | None = None
    last_failure_at: str | None = None
    schema_version: str


class ArtifactPayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    artifact_id: str
    result: JsonObject


class PropositionRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    proposition_id: str
    current_stage: str
    last_successful_stage: str | None = None
    current_assessment_id: str | None = None
    current_attempt: int | None = None
    backlog_state: str
    last_failure_reason: str
    last_failure_at: str | None = None
    schema_version: str


class SessionStateTruncation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_truncated: bool
    returned_count: int
    total_count: int
    sort_key: str
    applies_to: str


class SessionStateView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    active_propositions: list[JsonObject]
    backing_findings: list[JsonObject]
    blocking_gaps: list[JsonObject]
    artifact_refs: list[JsonObject]
    focus_subjects: list[JsonObject]
    truncation: SessionStateTruncation
    schema_version: str
    next_page_token: str | None = None


class PropositionContextView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposition: JsonObject
    seed_entries: list[JsonObject]
    relevant_findings: list[JsonObject]
    latest_assessment: JsonObject | None = None
    blocking_gaps: list[JsonObject]
    non_blocking_gaps: list[JsonObject]
    applied_inference_records: list[JsonObject]
    assessment_dependencies: list[JsonObject]
    artifact_refs: list[JsonObject]
    schema_version: str
