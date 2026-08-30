"""Slice 0 coverage for evidence and Artifact authority contracts."""

from __future__ import annotations

from typing import get_args

import marivo.analysis as mv
from marivo.analysis._artifact_authority import (
    ArtifactAuthorityContext,
    CatalogOnlyDependencyAuthority,
    ScopedDependencyAuthority,
    UnresolvedDependencyAuthority,
)
from marivo.analysis._authority_inventory import (
    ARTIFACT_AUTHORITY_INVENTORY,
    COMMON_AUTHORITY_FIELDS,
    artifact_authority_ref,
)
from marivo.analysis._capabilities.model import ARTIFACT_FAMILIES
from marivo.analysis.evidence.types import EpistemicKind, EvidenceScope, EvidenceSubject
from marivo.analysis.frames.base import BaseFrameMeta
from marivo.analysis.frames.event import EventFrameMetaBase
from marivo.analysis.frames.lifecycle import (
    LifecycleFrameMetaBase,
    LifecycleReducerFrameMetaBase,
)
from marivo.analysis.session._load import _FRAME_CLASSES


def _annotated_union_members(value: object) -> tuple[type[object], ...]:
    union = get_args(value)[0]
    return get_args(union)


def _recursive_subclasses(cls: type[object]) -> set[type[object]]:
    children = set(cls.__subclasses__())
    return children | {
        descendant for child in children for descendant in _recursive_subclasses(child)
    }


def test_evidence_closed_vocabulary_inventory() -> None:
    subject_types = _annotated_union_members(EvidenceSubject)
    scope_types = _annotated_union_members(EvidenceScope)

    assert tuple(item.__name__ for item in subject_types) == (
        "Subject",
        "EventSubject",
        "LifecycleSubject",
        "SubjectSetSubject",
    )
    assert tuple(item.model_fields["kind"].default for item in subject_types) == (
        "metric",
        "event",
        "lifecycle",
        "subject_set",
    )
    assert tuple(item.__name__ for item in scope_types) == (
        "AnalysisScope",
        "EventAnalysisScope",
        "EventFunnelAnalysisScope",
        "EventTimeToEventAnalysisScope",
        "LifecycleAnalysisScope",
        "SubjectSetAnalysisScope",
    )
    assert tuple(item.model_fields["kind"].default for item in scope_types) == (
        "metric",
        "event",
        "event_funnel",
        "event_time_to_event",
        "lifecycle",
        "subject_set",
    )
    assert get_args(EpistemicKind) == (
        "observed",
        "algebraic",
        "estimated",
        "tested",
        "predicted",
        "candidate",
    )


def test_authority_context_inventory_covers_every_artifact_family_and_loader_kind() -> None:
    inventory_families = {item.artifact_family for item in ARTIFACT_AUTHORITY_INVENTORY}
    inventory_kinds = {item.frame_kind for item in ARTIFACT_AUTHORITY_INVENTORY}
    loader_families = {frame_type.__name__ for frame_type, _ in _FRAME_CLASSES.values()}

    assert inventory_families == set(ARTIFACT_FAMILIES) == loader_families
    assert inventory_kinds == set(_FRAME_CLASSES)
    for item in ARTIFACT_AUTHORITY_INVENTORY:
        frame_type, _ = _FRAME_CLASSES[item.frame_kind]
        assert frame_type.__name__ == item.artifact_family


def test_authority_context_inventory_covers_every_concrete_frame_meta_variant() -> None:
    abstract_meta_types = {
        EventFrameMetaBase,
        LifecycleFrameMetaBase,
        LifecycleReducerFrameMetaBase,
    }
    concrete_meta_types = _recursive_subclasses(BaseFrameMeta) - abstract_meta_types
    inventoried_meta_types = {item.meta_type for item in ARTIFACT_AUTHORITY_INVENTORY}

    assert len(ARTIFACT_AUTHORITY_INVENTORY) == 22
    assert len(inventoried_meta_types) == len(ARTIFACT_AUTHORITY_INVENTORY)
    assert inventoried_meta_types == concrete_meta_types

    assert tuple(
        (item.artifact_family, item.variant, item.meta_type.__name__)
        for item in ARTIFACT_AUTHORITY_INVENTORY
    ) == (
        ("MetricFrame", "metric", "MetricFrameMeta"),
        ("EventFrame", "journey", "EventFrameMeta"),
        ("EventFrame", "funnel", "EventFunnelFrameMeta"),
        ("EventFrame", "time_to_event", "EventTimeToEventFrameMeta"),
        ("LifecycleFrame", "history", "LifecycleHistoryFrameMeta"),
        ("LifecycleFrame", "distribution", "LifecycleDistributionFrameMeta"),
        ("LifecycleFrame", "transitions", "LifecycleTransitionsFrameMeta"),
        ("LifecycleFrame", "dwell", "LifecycleDwellFrameMeta"),
        ("LifecycleFrame", "violations", "LifecycleViolationsFrameMeta"),
        ("SubjectSet", "subjects", "SubjectSetMeta"),
        ("DeltaFrame", "metric", "DeltaFrameMeta"),
        ("DeltaFrame", "cumulative_metric", "CumulativeDeltaFrameMetaV1"),
        ("DeltaFrame", "funnel", "FunnelDeltaFrameMeta"),
        ("AttributionFrame", "metric", "AttributionFrameMeta"),
        (
            "AttributionFrame",
            "funnel_loss_rate",
            "FunnelAttributionFrameMeta",
        ),
        ("ForecastFrame", "forecast", "ForecastFrameMeta"),
        ("CandidateSet", "scored", "ScoredCandidateSetMeta"),
        (
            "CandidateSet",
            "semantic_hypothesis",
            "SemanticHypothesisCandidateSetMeta",
        ),
        ("AssociationResult", "association", "AssociationResultMeta"),
        ("ComponentFrame", "component", "ComponentFrameMeta"),
        ("CoverageFrame", "coverage", "CoverageFrameMeta"),
        (
            "HypothesisTestResult",
            "hypothesis_test",
            "HypothesisTestResultMeta",
        ),
    )


def test_authority_inventory_fields_exist_and_require_normalization_only() -> None:
    assert COMMON_AUTHORITY_FIELDS == (
        "artifact_id",
        "ref",
        "session_id",
        "content_hash",
        "lineage",
        "evidence_digest",
    )
    assert set(COMMON_AUTHORITY_FIELDS) <= set(BaseFrameMeta.model_fields)

    for item in ARTIFACT_AUTHORITY_INVENTORY:
        assert item.meta_type.model_fields["kind"].default == item.frame_kind
        declared_fields = {
            *item.catalog_identity_fields,
            *item.scoped_identity_fields,
            *item.semantic_ref_fields,
            *item.source_identity_fields,
        }
        assert declared_fields <= set(item.meta_type.model_fields), item.meta_type.__name__
        assert item.scoped_identity_fields or item.source_identity_fields
        if item.extraction_mode == "source_lineage":
            assert item.source_identity_fields
        else:
            assert item.scoped_identity_fields
        assert item.schema_action == "normalization_only"


def test_authority_inventory_normalizes_artifact_ref() -> None:
    local_meta = BaseFrameMeta.model_construct(ref="frame:local", artifact_id=None)
    persisted_meta = BaseFrameMeta.model_construct(
        ref="frame:local",
        artifact_id="artifact:persisted",
    )

    assert artifact_authority_ref(local_meta) == "frame:local"
    assert artifact_authority_ref(persisted_meta) == "artifact:persisted"


def test_authority_inventory_remains_analysis_internal() -> None:
    assert "ArtifactAuthorityInventoryEntry" not in mv.__all__
    assert "ARTIFACT_AUTHORITY_INVENTORY" not in mv.__all__
    assert "artifact_authority_ref" not in mv.__all__
    for private_type in (
        ArtifactAuthorityContext,
        ScopedDependencyAuthority,
        CatalogOnlyDependencyAuthority,
        UnresolvedDependencyAuthority,
    ):
        assert private_type.__name__ not in mv.__all__
