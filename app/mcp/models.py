from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResponseFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"


class MCPBaseModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class HealthInput(MCPBaseModel):
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class CatalogInput(MCPBaseModel):
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class CreateSessionInput(MCPBaseModel):
    goal: str = Field(..., min_length=5, description="Analysis goal, for example 'Investigate the recent watch time decline'.")
    constraints: dict[str, Any] = Field(default_factory=dict, description="Optional analysis constraints forwarded to the FastAPI service.")
    budget: dict[str, Any] | None = Field(default=None, description="Optional execution budget forwarded to the FastAPI service.")
    policy: dict[str, Any] | None = Field(default=None, description="Optional policy overrides forwarded to the FastAPI service.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class RunStepInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    step_type: str = Field(
        ...,
        description=(
            "Step to run. Supported values: compare_watch_time, analyze_qoe, analyze_ads, "
            "analyze_recommendation, synthesize_findings, compare_metric, profile_table, sample_rows."
        ),
    )
    params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional step parameters forwarded to the FastAPI service as the request body. "
            "For example, compare_metric requires {metric_name, table_name}; "
            "profile_table requires {table_name}."
        ),
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class WorkflowInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class EvidenceInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ListSourcesInput(MCPBaseModel):
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class SearchCatalogInput(MCPBaseModel):
    query: str = Field(..., min_length=1, description="Search term to match against entity/metric names, display names, and descriptions.")
    type: str | None = Field(default=None, description="Optional filter: 'entity', 'metric', or 'asset'.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ResolveTermInput(MCPBaseModel):
    name: str = Field(..., min_length=1, description="Business term to resolve, e.g. 'watch_time'.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class PlannerContextInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class DraftPlanInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier returned by omnidb_create_session.")
    steps: list[dict[str, Any]] = Field(
        ...,
        description="List of plan steps. Each step: {step_type, params?, dependencies?}.",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ValidatePlanInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier.")
    plan_id: str = Field(..., min_length=5, description="Plan identifier returned by omnidb_draft_plan.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ExecutePlanInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier.")
    plan_id: str = Field(..., min_length=5, description="Plan identifier. Must be in 'approved' status.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class SubmitJobInput(MCPBaseModel):
    session_id: str = Field(..., min_length=5, description="Session identifier.")
    job_type: str = Field(..., description="Job type: 'step', 'workflow', or 'plan'.")
    payload: dict[str, Any] = Field(default_factory=dict, description="Job payload, e.g. {step_type, params} or {plan_id}.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class GetJobInput(MCPBaseModel):
    job_id: str = Field(..., min_length=5, description="Job identifier returned by omnidb_submit_job.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )


class ListApprovalsInput(MCPBaseModel):
    session_id: str | None = Field(default=None, description="Optional session ID filter.")
    status: str | None = Field(default=None, description="Optional status filter: 'pending', 'approved', 'rejected'.")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: 'json' for structured data or 'markdown' for a concise human-readable summary.",
    )
