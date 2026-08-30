"""Strict metadata-only decoding for current analysis Artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping

from marivo._temporal import _trusted_time_scope_validation
from marivo.analysis.frames.association import AssociationResultMeta
from marivo.analysis.frames.attribution import (
    AttributionFrameMeta,
    FunnelAttributionFrameMeta,
)
from marivo.analysis.frames.base import (
    CURRENT_ARTIFACT_SCHEMA_VERSION,
    BaseFrameMeta,
)
from marivo.analysis.frames.candidate import (
    CandidateSetMeta,
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

CURRENT_METRIC_META_FIELDS = frozenset(
    {
        "metric_identity",
        "metric_identities",
        "catalog_definition_fingerprint",
        "expression_graph_ref",
        "expression_graph",
        "expression_fingerprint",
        "semantic_dependency_digest",
        "presentation_ref",
        "presentation",
        "presentation_fingerprint",
        "artifact_identity",
        "key_schema",
        "source_compatibility_domain",
        "component_ref",
        "replay_graph_ref",
        "comparable_value_semantics_ref",
        "comparable_value_semantics",
        "execution_stats",
        "unit_state",
        "axis_bindings",
        "slice_predicates",
        "status_time_dimension_ref",
    }
)

_BASE_META_CLASSES: dict[str, type[BaseFrameMeta]] = {
    "metric_frame": MetricFrameMeta,
    "delta_frame": DeltaFrameMeta,
    "attribution_frame": AttributionFrameMeta,
    "candidate_set": CandidateSetMeta,
    "association_result": AssociationResultMeta,
    "hypothesis_test_result": HypothesisTestResultMeta,
    "forecast_frame": ForecastFrameMeta,
    "event_frame": EventFrameMeta,
    "lifecycle_frame": LifecycleHistoryFrameMeta,
    "subject_set": SubjectSetMeta,
    "component_frame": ComponentFrameMeta,
    "coverage_frame": CoverageFrameMeta,
}

_EVENT_META_CLASSES: dict[str, type[BaseFrameMeta]] = {
    "journey": EventFrameMeta,
    "funnel": EventFunnelFrameMeta,
    "time_to_event": EventTimeToEventFrameMeta,
}

_LIFECYCLE_META_CLASSES: dict[str, type[BaseFrameMeta]] = {
    "history": LifecycleHistoryFrameMeta,
    "distribution": LifecycleDistributionFrameMeta,
    "transitions": LifecycleTransitionsFrameMeta,
    "dwell": LifecycleDwellFrameMeta,
    "violations": LifecycleViolationsFrameMeta,
}


def _meta_class(payload: Mapping[str, object]) -> type[BaseFrameMeta]:
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in _BASE_META_CLASSES:
        raise ValueError(f"unsupported Artifact kind {kind!r}")
    if kind == "event_frame":
        semantic_kind = payload.get("semantic_kind")
        if not isinstance(semantic_kind, str) or semantic_kind not in _EVENT_META_CLASSES:
            raise ValueError(f"unsupported EventFrame semantic_kind {semantic_kind!r}")
        return _EVENT_META_CLASSES[semantic_kind]
    if kind == "lifecycle_frame":
        semantic_kind = payload.get("semantic_kind")
        if not isinstance(semantic_kind, str) or semantic_kind not in _LIFECYCLE_META_CLASSES:
            raise ValueError(f"unsupported LifecycleFrame semantic_kind {semantic_kind!r}")
        return _LIFECYCLE_META_CLASSES[semantic_kind]
    if kind == "delta_frame":
        if payload.get("semantic_kind") == "funnel":
            return FunnelDeltaFrameMeta
        if payload.get("cumulative") is not None:
            return CumulativeDeltaFrameMetaV1
    if kind == "attribution_frame" and payload.get("semantic_kind") == "funnel_loss_rate":
        return FunnelAttributionFrameMeta
    if kind == "candidate_set" and payload.get("shape") == "semantic_hypothesis":
        return SemanticHypothesisCandidateSetMeta
    return _BASE_META_CLASSES[kind]


def _validate_exact_current_fields(payload: Mapping[str, object]) -> None:
    kind = payload.get("kind")
    if kind == "metric_frame":
        missing = sorted(CURRENT_METRIC_META_FIELDS - set(payload))
        if missing:
            raise ValueError(f"missing current MetricFrame fields: {', '.join(missing)}")
    if kind == "delta_frame" and payload.get("semantic_kind") != "funnel":
        if "comparison_identity" not in payload:
            raise ValueError("missing current DeltaFrame comparison_identity")
        if "attribution_basis" not in payload:
            raise ValueError("missing current DeltaFrame attribution_basis")
        if payload.get("cumulative") is not None:
            if payload.get("artifact_schema") != "cumulative-delta/v1":
                raise ValueError("unsupported cumulative DeltaFrame artifact_schema")
            if "cumulative_attribution" not in payload:
                raise ValueError("missing cumulative DeltaFrame attribution state")
    if (
        kind == "attribution_frame"
        and payload.get("semantic_kind") != "funnel_loss_rate"
        and payload.get("row_contract_version")
        not in {
            "generic-attribution-rows/v3",
            "cumulative-flow-attribution-rows/v1",
        }
    ):
        raise ValueError("unsupported AttributionFrame row_contract_version")


def parse_current_artifact_meta(payload: Mapping[str, object]) -> BaseFrameMeta:
    """Decode one exact current Artifact metadata payload without reading rows."""

    if payload.get("artifact_schema_version") != CURRENT_ARTIFACT_SCHEMA_VERSION:
        raise ValueError(f"artifact_schema_version must be {CURRENT_ARTIFACT_SCHEMA_VERSION!r}")
    _validate_exact_current_fields(payload)
    meta_cls = _meta_class(payload)
    with _trusted_time_scope_validation():
        parsed = meta_cls.model_validate_json(json.dumps(dict(payload)))
    if parsed.created_at.tzinfo is None:
        raise ValueError("Artifact created_at must be timezone-aware")
    return parsed


__all__: list[str] = []
