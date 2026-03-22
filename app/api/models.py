"""Pydantic request/response models for the Factum HTTP API.

Signal vs Decision design principle
-------------------------------------
Factum returns **signals** (evidence, claims, recommendations) and enforces
**decisions** (governance constraints, budget limits). Agents retain full
control over what to do with signals; governance decisions are hard stops
that Factum enforces on behalf of the operator.

Session constraints summary:
- constraints : Scalar key/value filters injected into step WHERE clauses.
                Signal-shaping input — narrows the analysis scope.
- budget      : Hard resource limits (scan bytes, latency).
                System-enforced decision — steps that exceed budget are blocked.
- policy      : Governance rules (aggregate_only, min_group_size).
                System-enforced decision — violations block step execution.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    goal: str
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Scalar key/value filters auto-injected into step WHERE clauses "
            "(e.g. {\"region\": \"us-east\"}). Narrows analysis scope — a signal-shaping input, "
            "not a governance constraint."
        ),
    )
    budget: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_scan_bytes": 500_000_000_000,
            "max_latency_sec": 120,
        },
        description=(
            "Hard resource limits enforced by Factum. Steps that would exceed "
            "max_scan_bytes or max_latency_sec are blocked before execution. "
            "This is a system decision constraint, not a suggestion."
        ),
    )
    policy: dict[str, Any] = Field(
        default_factory=lambda: {
            "aggregate_only": True,
            "min_group_size": 100,
        },
        description=(
            "Governance rules enforced by Factum (e.g. aggregate_only blocks raw row access, "
            "min_group_size enforces k-anonymity). System-enforced decision constraints — "
            "violations block step execution regardless of agent intent."
        ),
    )


class SourceRegisterRequest(BaseModel):
    source_type: str
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] | None = None


class SourceUpdateRequest(BaseModel):
    display_name: str | None = None
    connection: dict[str, Any] | None = None
    sync_mode: str | None = None


class EngineRegisterRequest(BaseModel):
    engine_type: str
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] | None = None


class EntityCreateRequest(BaseModel):
    name: str
    display_name: str
    description: str = ""
    keys: list[str]
    level: str | None = None
    join_constraints: dict[str, Any] | None = None
    upstream_dependencies: list[str] | None = None
    lineage: list[str] | None = None
    quality_expectations: dict[str, Any] | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class EntityUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    keys: list[str] | None = None
    level: str | None = None
    join_constraints: dict[str, Any] | None = None
    upstream_dependencies: list[str] | None = None
    lineage: list[str] | None = None
    quality_expectations: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None


class MetricCreateRequest(BaseModel):
    name: str
    display_name: str
    description: str = ""
    definition_sql: str
    dimensions: list[str]
    entity_id: str | None = None
    grain: str | None = None
    measure_type: str | None = None
    allowed_dimensions: list[str] | None = None
    lineage: list[str] | None = None
    quality_expectations: dict[str, Any] | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class MetricUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    definition_sql: str | None = None
    dimensions: list[str] | None = None
    entity_id: str | None = None
    grain: str | None = None
    measure_type: str | None = None
    allowed_dimensions: list[str] | None = None
    lineage: list[str] | None = None
    quality_expectations: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None


class MappingCreateRequest(BaseModel):
    semantic_type: str
    semantic_id: str
    object_id: str
    mapping_type: str
    mapping_json: dict[str, Any] = Field(default_factory=dict)


class BindingCreateRequest(BaseModel):
    source_id: str
    engine_id: str
    priority: int = 0
    namespace: dict[str, Any] = Field(default_factory=dict)


class RouteIntentRequest(BaseModel):
    step_type: str | None = None
    metric_names: list[str] = Field(default_factory=list)
    requested_dimensions: list[str] = Field(default_factory=list)
    compatible_dimensions: list[str] = Field(default_factory=list)
    legal_grains: list[str] = Field(default_factory=list)
    policy_hints: list[str] = Field(default_factory=list)


class RouteResolveRequest(BaseModel):
    table_names: list[str]
    routing_intent: RouteIntentRequest | None = None


class SyncSelectionItem(BaseModel):
    schema_name: str
    table_name: str


class SyncSelectionRequest(BaseModel):
    selections: list[SyncSelectionItem]


class PolicyCreateRequest(BaseModel):
    name: str
    policy_type: str
    definition: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)


class PolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    definition: dict[str, Any] | None = None


class QualityRuleCreateRequest(BaseModel):
    name: str
    rule_type: str
    table_name: str
    threshold: dict[str, Any]
    severity: str = "warn"


class GovernanceCheckRequest(BaseModel):
    session_id: str
    step_type: str
    params: dict[str, Any] = Field(default_factory=dict)


class JobSubmitRequest(BaseModel):
    session_id: str
    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ApprovalCreateRequest(BaseModel):
    session_id: str
    rec_id: str


class ApprovalDecisionRequest(BaseModel):
    reviewer: str
    reason: str = ""


class AutoFlagRequest(BaseModel):
    risk_threshold: str = "P0"


class ReadinessSignal(BaseModel):
    """Schema documentation for the readiness signal returned in step responses (M-04)."""

    goal_coverage: float = Field(ge=0.0, le=1.0)
    evidence_sufficiency: float = Field(ge=0.0, le=1.0)
    contradiction_resolution: float = Field(ge=0.0, le=1.0)
    budget_remaining: float = Field(ge=0.0, le=1.0)
    diminishing_returns: float = Field(ge=0.0, le=1.0)
    suggested_action: str  # continue_exploring | resolve_contradiction | synthesize | stop
