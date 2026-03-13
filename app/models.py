from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_scan_bytes": 500_000_000_000,
            "max_latency_sec": 120,
        }
    )
    policy: dict[str, Any] = Field(
        default_factory=lambda: {
            "aggregate_only": True,
            "min_group_size": 100,
        }
    )


# ── Source registry ──────────────────────────────────────────────────

class SourceRegisterRequest(BaseModel):
    source_type: str
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] | None = None


# ── Engine registry ─────────────────────────────────────────────────

class EngineRegisterRequest(BaseModel):
    engine_type: str
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] | None = None


# ── Semantic entities ────────────────────────────────────────────────

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


# ── Semantic metrics ─────────────────────────────────────────────────

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


# ── Semantic mappings ────────────────────────────────────────────────

class MappingCreateRequest(BaseModel):
    semantic_type: str  # 'entity', 'metric', 'dimension'
    semantic_id: str
    object_id: str
    mapping_type: str  # 'primary_source', 'join_target', 'derived'
    mapping_json: dict[str, Any] = Field(default_factory=dict)


# ── Source-engine bindings ──────────────────────────────────────────

class BindingCreateRequest(BaseModel):
    source_id: str
    engine_id: str
    priority: int = 0
    namespace: dict[str, Any] = Field(default_factory=dict)


# ── Query routing ──────────────────────────────────────────────────

class RouteResolveRequest(BaseModel):
    table_names: list[str]
    routing_intent: "RouteIntentRequest | None" = None


class RouteIntentRequest(BaseModel):
    step_type: str | None = None
    metric_names: list[str] = Field(default_factory=list)
    requested_dimensions: list[str] = Field(default_factory=list)
    compatible_dimensions: list[str] = Field(default_factory=list)
    legal_grains: list[str] = Field(default_factory=list)
    policy_hints: list[str] = Field(default_factory=list)


# ── Sync selections ──────────────────────────────────────────────────

class SyncSelectionItem(BaseModel):
    schema_name: str
    table_name: str


class SyncSelectionRequest(BaseModel):
    selections: list[SyncSelectionItem]


# ── Governance ──────────────────────────────────────────────────────

class PolicyCreateRequest(BaseModel):
    name: str
    policy_type: str  # 'aggregate_only', 'field_mask', 'row_filter', 'max_rows'
    definition: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)


class PolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    definition: dict[str, Any] | None = None


class QualityRuleCreateRequest(BaseModel):
    name: str
    rule_type: str  # 'freshness', 'null_rate', 'row_count_min'
    table_name: str
    threshold: dict[str, Any]
    severity: str = "warn"  # 'warn' | 'block'


class GovernanceCheckRequest(BaseModel):
    session_id: str
    step_type: str
    params: dict[str, Any] = Field(default_factory=dict)


# ── Async jobs ──────────────────────────────────────────────────────

class JobSubmitRequest(BaseModel):
    session_id: str
    job_type: str  # 'step' | 'workflow' | 'plan'
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Approval workflow ───────────────────────────────────────────────

class ApprovalCreateRequest(BaseModel):
    session_id: str
    rec_id: str


class ApprovalDecisionRequest(BaseModel):
    reviewer: str
    reason: str = ""


class AutoFlagRequest(BaseModel):
    risk_threshold: str = "P0"
