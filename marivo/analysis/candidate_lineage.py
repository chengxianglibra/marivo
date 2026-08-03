"""Closed ontology-candidate context, diagnostics, and lineage values."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis._semantic_persistence import JsonValue, SlicePredicateV1
from marivo.analysis.errors import AnalysisRepair
from marivo.ontology.types import SemanticEdgeRef
from marivo.refs import RefPayloadV1

SemanticHypothesisExclusionKind = Literal[
    "anchor_overlap",
    "source_metric_self_resolution",
    "no_observable_metric",
    "semantic_not_ready",
    "incompatible_inherited_scope",
]


class SemanticEdgeContext(BaseModel):
    """Historical author-time meaning required to judge one candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["semantic-edge-context/v1"] = "semantic-edge-context/v1"
    semantic_edge_ref: SemanticEdgeRef
    business_definition: str
    guardrails: tuple[str, ...] = ()
    anchor_role: Literal["outcome", "related_endpoint"]
    candidate_role: Literal["driver", "related_endpoint"]


class InheritedObservationScope(BaseModel):
    """Exact immutable observation scope inherited by ontology discovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["inherited-observation-scope/v1"] = "inherited-observation-scope/v1"
    window: dict[str, JsonValue] | None = None
    grain: str | None = None
    time_dimension_ref: RefPayloadV1 | None = None
    axis_refs: tuple[RefPayloadV1, ...] = ()
    slice_predicates: tuple[SlicePredicateV1, ...] = ()
    cohort: dict[str, JsonValue] | None = None
    source_catalog_fingerprint: str
    fingerprint: str


class SemanticHypothesisExclusion(BaseModel):
    """One bounded coordinate for an excluded ontology resolution attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["semantic-hypothesis-exclusion/v1"] = "semantic-hypothesis-exclusion/v1"
    reason: SemanticHypothesisExclusionKind
    semantic_edge_ref: SemanticEdgeRef
    candidate_semantic_ref: RefPayloadV1 | None = None
    metric_ref: RefPayloadV1 | None = None
    matched_anchor_refs: tuple[RefPayloadV1, ...] = ()

    @model_validator(mode="after")
    def _validate_coordinates(self) -> SemanticHypothesisExclusion:
        if self.reason == "anchor_overlap":
            if self.candidate_semantic_ref is not None or len(self.matched_anchor_refs) != 2:
                raise ValueError(
                    "anchor_overlap requires exactly two matched anchors and no candidate"
                )
        elif self.candidate_semantic_ref is None or self.matched_anchor_refs:
            raise ValueError("non-overlap exclusions require one candidate and no matched anchors")
        return self


class SemanticHypothesisExcludedCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor_overlap: int = Field(default=0, ge=0)
    source_metric_self_resolution: int = Field(default=0, ge=0)
    no_observable_metric: int = Field(default=0, ge=0)
    semantic_not_ready: int = Field(default=0, ge=0)
    incompatible_inherited_scope: int = Field(default=0, ge=0)


class SemanticHypothesisResolutionSummary(BaseModel):
    """Exact aggregate and bounded diagnostics for ontology resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    examined_edges: int = Field(ge=0)
    candidate_count_before_limit: int = Field(ge=0)
    emitted_candidates: int = Field(ge=0)
    candidate_limit: int = Field(ge=1, le=200)
    candidates_omitted: int = Field(ge=0)
    excluded_counts: SemanticHypothesisExcludedCounts
    exclusions: tuple[SemanticHypothesisExclusion, ...] = ()
    exclusions_omitted: int = Field(default=0, ge=0)


class CandidateOrigin(BaseModel):
    """Non-evidentiary lineage from one explicitly observed ontology candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["candidate-origin/v1"] = "candidate-origin/v1"
    ontology_catalog_fingerprint: str
    semantic_catalog_fingerprint: str
    candidate_set_ref: str
    item_id: str
    semantic_edge_ref: SemanticEdgeRef
    edge_relation: Literal["influences", "related_to"]
    source_metric_ref: RefPayloadV1
    candidate_semantic_ref: RefPayloadV1
    metric_ref: RefPayloadV1
    edge_context: SemanticEdgeContext
    inherited_scope_fingerprint: str
    readiness_fingerprint: str


class CandidateResolutionIssue(BaseModel):
    """Repairable analysis-artifact issue for one excluded ontology path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str
    kind: SemanticHypothesisExclusionKind
    severity: Literal["warning"] = "warning"
    source_refs: tuple[str, ...]
    semantic_edge_ref: SemanticEdgeRef
    candidate_semantic_ref: RefPayloadV1 | None = None
    metric_ref: RefPayloadV1 | None = None
    historical: bool = False
    repair: AnalysisRepair


def merge_candidate_origins(*groups: tuple[CandidateOrigin, ...]) -> tuple[CandidateOrigin, ...]:
    """Preserve public input order and first occurrence of each candidate coordinate."""
    result: list[CandidateOrigin] = []
    seen: dict[tuple[str, str], CandidateOrigin] = {}
    for group in groups:
        for origin in group:
            key = (origin.candidate_set_ref, origin.item_id)
            existing = seen.get(key)
            if existing is not None:
                if existing != origin:
                    raise ValueError(
                        "conflicting CandidateOrigin payload for one candidate coordinate"
                    )
                continue
            seen[key] = origin
            result.append(origin)
    return tuple(result)


__all__ = [
    "CandidateOrigin",
    "CandidateResolutionIssue",
    "InheritedObservationScope",
    "SemanticEdgeContext",
    "SemanticHypothesisExcludedCounts",
    "SemanticHypothesisExclusion",
    "SemanticHypothesisExclusionKind",
    "SemanticHypothesisResolutionSummary",
    "merge_candidate_origins",
]
