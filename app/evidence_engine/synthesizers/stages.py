from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.evidence_engine.schemas import Claim, Observation


@dataclass
class ScopeCluster:
    """Stage 1 output: a group of observations sharing the same metric+slice scope."""

    scope_key: str  # "<metric>/<k=v,...>" canonical key
    metric: str
    slice_dict: dict[str, Any]
    metric_observation_obs: list[Observation]
    funnel_drop_obs: list[Observation]
    contribution_shift_obs: list[Observation]
    anomaly_detection_obs: list[Observation]
    other_obs: list[Observation]
    # Audit fields
    total_observation_count: int = 0
    cluster_reason: str = ""  # "exact_scope_match"


@dataclass
class AlignedSignal:
    """Stage 2 output: observations within a ScopeCluster with direction+strength alignment."""

    scope_cluster: ScopeCluster
    primary_obs: Observation
    primary_selection_reason: str  # "max |delta_pct| * log1p(sample_size)"
    supporting_obs_ids: list[str]
    contradicting_obs_ids: list[str]
    # Inputs to score_confidence()
    effect_strength: float
    consistency: float
    sample_score: float
    data_quality_score: float
    contradiction_penalty: float
    # Audit fields
    consistency_factors: list[float] = field(default_factory=list)
    support_reasons: list[str] = field(default_factory=list)
    alignment_notes: list[str] = field(default_factory=list)


@dataclass
class ClaimFormulation:
    """Stage 3 output: a fully formed claim dict plus formulation audit data."""

    claim: Claim  # complete Claim dict, ready for DB insertion
    # Audit fields
    claim_type_decision: str
    claim_type_reason: str
    text_template: str
    confidence_inputs: dict[str, float]
    final_confidence: float
    is_non_metric: bool


@dataclass
class PipelineAuditLog:
    """Structured audit log produced by one ThreeStagePipeline.run() call.

    Persisted as artifact_type="synthesis_audit" in the artifacts table.
    Fully JSON-serialisable via dataclasses.asdict().
    """

    stage: str = "three_stage_pipeline"
    observation_count: int = 0
    scope_clusters: list[dict[str, Any]] = field(default_factory=list)
    alignment_scores: list[dict[str, Any]] = field(default_factory=list)
    formulation_decisions: list[dict[str, Any]] = field(default_factory=list)
    claims_produced: int = 0
    overall_trend_generated: bool = False
    dropped_observation_count: int = 0
    error: str | None = None
