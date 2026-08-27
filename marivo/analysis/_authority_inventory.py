"""Private Slice 0 inventory for persisted Artifact authority inputs.

This module records extraction coverage only.  It deliberately does not build
``ArtifactAuthorityContext`` values or perform current-catalog validation; those
behaviors belong to later implementation slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.analysis._capabilities.model import ArtifactFamily
from marivo.analysis.frames.association import AssociationResultMeta
from marivo.analysis.frames.attribution import (
    AttributionFrameMeta,
    FunnelAttributionFrameMeta,
)
from marivo.analysis.frames.base import BaseFrameMeta
from marivo.analysis.frames.candidate import (
    ScoredCandidateSetMeta,
    SemanticHypothesisCandidateSetMeta,
)
from marivo.analysis.frames.component import ComponentFrameMeta
from marivo.analysis.frames.coverage import CoverageFrameMeta
from marivo.analysis.frames.delta import (
    CumulativeDeltaFrameMetaV1,
    DeltaFrameMeta,
    FunnelDeltaFrameMeta,
)
from marivo.analysis.frames.event import (
    EventFrameMeta,
    EventFunnelFrameMeta,
    EventTimeToEventFrameMeta,
)
from marivo.analysis.frames.forecast import ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResultMeta
from marivo.analysis.frames.lifecycle import (
    LifecycleDistributionFrameMeta,
    LifecycleDwellFrameMeta,
    LifecycleHistoryFrameMeta,
    LifecycleTransitionsFrameMeta,
    LifecycleViolationsFrameMeta,
)
from marivo.analysis.frames.metric import MetricFrameMeta
from marivo.analysis.frames.subject import SubjectSetMeta

AuthorityExtractionMode = Literal[
    "direct_scoped",
    "source_lineage",
    "direct_scoped_and_source_lineage",
]
AuthoritySchemaAction = Literal["normalization_only", "clean_schema_cutover"]


@dataclass(frozen=True, slots=True)
class ArtifactAuthorityInventoryEntry:
    """One closed FrameMeta variant and its existing authority-bearing fields."""

    artifact_family: ArtifactFamily
    frame_kind: str
    variant: str
    meta_type: type[BaseFrameMeta]
    extraction_mode: AuthorityExtractionMode
    catalog_identity_fields: tuple[str, ...] = ()
    scoped_identity_fields: tuple[str, ...] = ()
    semantic_ref_fields: tuple[str, ...] = ()
    source_identity_fields: tuple[str, ...] = ()
    schema_action: AuthoritySchemaAction = "normalization_only"


COMMON_AUTHORITY_FIELDS = (
    "artifact_id",
    "ref",
    "session_id",
    "content_hash",
    "lineage",
    "evidence_digest",
)


def artifact_authority_ref(meta: BaseFrameMeta) -> str:
    """Normalize one FrameMeta to its canonical persisted-or-local Artifact ref."""

    return meta.artifact_id or meta.ref


ARTIFACT_AUTHORITY_INVENTORY: tuple[ArtifactAuthorityInventoryEntry, ...] = (
    ArtifactAuthorityInventoryEntry(
        artifact_family="MetricFrame",
        frame_kind="metric_frame",
        variant="metric",
        meta_type=MetricFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("semantic_dependency_digest",),
        semantic_ref_fields=(
            "metric_identities",
            "axis_bindings",
            "slice_predicates",
        ),
        source_identity_fields=("cohort",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="EventFrame",
        frame_kind="event_frame",
        variant="journey",
        meta_type=EventFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("event_fingerprints",),
        semantic_ref_fields=(
            "subject_entity_ref",
            "pattern",
            "event_identity_components",
            "role_endpoints",
        ),
        source_identity_fields=("cohort",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="EventFrame",
        frame_kind="event_frame",
        variant="funnel",
        meta_type=EventFunnelFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("event_fingerprints",),
        semantic_ref_fields=(
            "subject_entity_ref",
            "pattern",
            "event_identity_components",
            "role_endpoints",
        ),
        source_identity_fields=(
            "source_journey_ref",
            "source_journey_fingerprint",
            "cohort",
        ),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="EventFrame",
        frame_kind="event_frame",
        variant="time_to_event",
        meta_type=EventTimeToEventFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("event_fingerprints",),
        semantic_ref_fields=(
            "subject_entity_ref",
            "pattern",
            "event_identity_components",
            "role_endpoints",
        ),
        source_identity_fields=(
            "source_journey_ref",
            "source_journey_fingerprint",
            "cohort",
        ),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="LifecycleFrame",
        frame_kind="lifecycle_frame",
        variant="history",
        meta_type=LifecycleHistoryFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("state_model_fingerprint", "event_fingerprints"),
        semantic_ref_fields=(
            "state_model_ref",
            "subject_entity_ref",
            "event_identity_components",
        ),
        source_identity_fields=("cohort",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="LifecycleFrame",
        frame_kind="lifecycle_frame",
        variant="distribution",
        meta_type=LifecycleDistributionFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("state_model_fingerprint",),
        semantic_ref_fields=("state_model_ref", "subject_entity_ref"),
        source_identity_fields=("source_history_ref", "source_history_fingerprint"),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="LifecycleFrame",
        frame_kind="lifecycle_frame",
        variant="transitions",
        meta_type=LifecycleTransitionsFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("state_model_fingerprint",),
        semantic_ref_fields=("state_model_ref", "subject_entity_ref"),
        source_identity_fields=("source_history_ref", "source_history_fingerprint"),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="LifecycleFrame",
        frame_kind="lifecycle_frame",
        variant="dwell",
        meta_type=LifecycleDwellFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("state_model_fingerprint",),
        semantic_ref_fields=("state_model_ref", "subject_entity_ref"),
        source_identity_fields=("source_history_ref", "source_history_fingerprint"),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="LifecycleFrame",
        frame_kind="lifecycle_frame",
        variant="violations",
        meta_type=LifecycleViolationsFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("state_model_fingerprint",),
        semantic_ref_fields=("state_model_ref", "subject_entity_ref"),
        source_identity_fields=("source_history_ref", "source_history_fingerprint"),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="SubjectSet",
        frame_kind="subject_set",
        variant="subjects",
        meta_type=SubjectSetMeta,
        extraction_mode="source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        semantic_ref_fields=("subject_entity_ref",),
        source_identity_fields=("source",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="DeltaFrame",
        frame_kind="delta_frame",
        variant="metric",
        meta_type=DeltaFrameMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("source_dependency_digests",),
        semantic_ref_fields=(
            "comparison_identity",
            "axis_bindings",
            "slice_predicates",
        ),
        source_identity_fields=("source_current_ref", "source_baseline_ref"),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="DeltaFrame",
        frame_kind="delta_frame",
        variant="cumulative_metric",
        meta_type=CumulativeDeltaFrameMetaV1,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        scoped_identity_fields=("source_dependency_digests",),
        semantic_ref_fields=(
            "comparison_identity",
            "axis_bindings",
            "slice_predicates",
        ),
        source_identity_fields=("source_current_ref", "source_baseline_ref"),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="DeltaFrame",
        frame_kind="delta_frame",
        variant="funnel",
        meta_type=FunnelDeltaFrameMeta,
        extraction_mode="source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        semantic_ref_fields=("subject_entity_ref", "pattern", "axes"),
        source_identity_fields=(
            "source_current_ref",
            "source_current_fingerprint",
            "source_baseline_ref",
            "source_baseline_fingerprint",
            "source_current_journey_ref",
            "source_baseline_journey_ref",
        ),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="AttributionFrame",
        frame_kind="attribution_frame",
        variant="metric",
        meta_type=AttributionFrameMeta,
        extraction_mode="source_lineage",
        semantic_ref_fields=("axis_bindings",),
        source_identity_fields=(
            "source_refs",
            "scope_delta_ref",
            "source_attribution_ref",
        ),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="AttributionFrame",
        frame_kind="attribution_frame",
        variant="funnel_loss_rate",
        meta_type=FunnelAttributionFrameMeta,
        extraction_mode="source_lineage",
        catalog_identity_fields=("catalog_definition_fingerprint",),
        semantic_ref_fields=("subject_entity_ref", "target", "axes"),
        source_identity_fields=(
            "source_delta_ref",
            "source_delta_fingerprint",
            "source_current_journey_ref",
            "source_baseline_journey_ref",
        ),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="ForecastFrame",
        frame_kind="forecast_frame",
        variant="forecast",
        meta_type=ForecastFrameMeta,
        extraction_mode="source_lineage",
        source_identity_fields=("source_refs",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="CandidateSet",
        frame_kind="candidate_set",
        variant="scored",
        meta_type=ScoredCandidateSetMeta,
        extraction_mode="source_lineage",
        source_identity_fields=("source_ref", "source_refs"),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="CandidateSet",
        frame_kind="candidate_set",
        variant="semantic_hypothesis",
        meta_type=SemanticHypothesisCandidateSetMeta,
        extraction_mode="direct_scoped_and_source_lineage",
        catalog_identity_fields=(
            "semantic_catalog_fingerprint",
            "ontology_catalog_fingerprint",
        ),
        scoped_identity_fields=("readiness_bindings",),
        semantic_ref_fields=(
            "source_metric_ref",
            "source_entity_ref",
            "readiness_bindings",
        ),
        source_identity_fields=("source_ref",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="AssociationResult",
        frame_kind="association_result",
        variant="association",
        meta_type=AssociationResultMeta,
        extraction_mode="source_lineage",
        source_identity_fields=("source_refs",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="ComponentFrame",
        frame_kind="component_frame",
        variant="component",
        meta_type=ComponentFrameMeta,
        extraction_mode="source_lineage",
        semantic_ref_fields=("metric_identity", "component_bindings", "axis_bindings"),
        source_identity_fields=("parent_ref",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="CoverageFrame",
        frame_kind="coverage_frame",
        variant="coverage",
        meta_type=CoverageFrameMeta,
        extraction_mode="source_lineage",
        source_identity_fields=("parent_ref",),
    ),
    ArtifactAuthorityInventoryEntry(
        artifact_family="HypothesisTestResult",
        frame_kind="hypothesis_test_result",
        variant="hypothesis_test",
        meta_type=HypothesisTestResultMeta,
        extraction_mode="source_lineage",
        source_identity_fields=("source_refs",),
    ),
)
