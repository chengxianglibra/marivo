"""Explicit governed re-entry from a selected ontology candidate."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from marivo.analysis.candidate_lineage import CandidateOrigin, merge_candidate_origins
from marivo.analysis.errors import (
    AnalysisRepair,
    CandidateNotObservableError,
)
from marivo.analysis.evidence.identity import make_scope_fingerprint
from marivo.analysis.frames.candidate import CandidateSet, OntologyMetricCandidate
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.frames.subject import SubjectSet
from marivo.analysis.intents.observe import observe
from marivo.analysis.intents.semantic_hypotheses import (
    _edge_context,
    _payload_ref,
    _readiness_fingerprint,
    _scope_compatible,
)
from marivo.analysis.session.core import Session
from marivo.analysis.slice_types import SliceValue
from marivo.analysis.windows.grain import to_temporal_grain
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import DimensionKind, MetricKind, Ref, SemanticKind, TimeDimensionKind
from marivo.semantic.catalog import _SemanticInput


def _not_observable(message: str, *, received: str) -> CandidateNotObservableError:
    return CandidateNotObservableError(
        message=message,
        expected="a selected candidate whose persisted and live fingerprints still match",
        received=received,
        repair=AnalysisRepair(
            kind="retry",
            action="Rerun semantic_hypotheses and select one current item id.",
            help_target=LiveHelpTarget(
                surface="analysis", canonical_id="discover.semantic_hypotheses"
            ),
        ),
    )


def observe_candidate(
    candidate: OntologyMetricCandidate,
    *,
    analysis_purpose: str | None,
    session: Session,
) -> MetricFrame:
    """Revalidate and observe one exact OntologyMetricCandidate with inherited scope."""
    if type(candidate) is not OntologyMetricCandidate:
        raise _not_observable(
            "candidate observation requires an exact OntologyMetricCandidate",
            received=type(candidate).__name__,
        )
    try:
        recovered = session.get_frame(candidate.candidate_set_ref)
    except Exception as error:
        raise _not_observable(
            "candidate set artifact is unavailable in this Session",
            received=str(error),
        ) from error
    if not isinstance(recovered, CandidateSet) or recovered.meta.shape != "semantic_hypothesis":
        raise _not_observable(
            "candidate_set_ref does not resolve to a semantic-hypothesis CandidateSet",
            received=type(recovered).__name__,
        )
    selected = recovered.select(item_id=candidate.item_id)
    if type(selected) is not OntologyMetricCandidate or selected != candidate:
        raise _not_observable(
            "candidate payload does not match its persisted selected item",
            received="forged or stale candidate fields",
        )
    if (
        session._ontology_state != "ready"
        or session._ontology_catalog is None
        or session._ontology_catalog.definition_fingerprint
        != candidate.ontology_catalog_fingerprint
    ):
        raise _not_observable(
            "live ontology fingerprint no longer matches candidate creation",
            received=session._ontology_state,
        )
    if session.catalog.definition_fingerprint != candidate.semantic_catalog_fingerprint:
        raise _not_observable(
            "live semantic catalog fingerprint no longer matches candidate creation",
            received=session.catalog.definition_fingerprint,
        )
    assert session._ontology_catalog is not None
    live_edge = next(
        (
            edge
            for edge in session._ontology_catalog._edges_for_discovery()
            if edge.ref == candidate.semantic_edge_ref
        ),
        None,
    )
    if live_edge is None:
        raise _not_observable(
            "candidate semantic edge is absent from the matching live ontology",
            received=candidate.semantic_edge_ref.key,
        )
    source_metric_ref = cast("Ref[MetricKind]", _payload_ref(candidate.source_metric_ref))
    candidate_semantic_ref = _payload_ref(candidate.candidate_semantic_ref)
    anchors = set(session.catalog._ontology_anchors_for_metric(source_metric_ref))
    edge_matches = (
        live_edge.relation == candidate.edge_relation
        and _edge_context(live_edge) == candidate.edge_context
        and (
            live_edge.target in anchors and live_edge.source == candidate_semantic_ref
            if live_edge.relation == "influences"
            else (
                (live_edge.source in anchors) != (live_edge.target in anchors)
                and candidate_semantic_ref
                == (live_edge.target if live_edge.source in anchors else live_edge.source)
            )
        )
    )
    if not edge_matches:
        raise _not_observable(
            "candidate payload does not match the current ontology edge semantics",
            received=candidate.semantic_edge_ref.key,
        )
    scope_payload = candidate.inherited_scope.model_dump(mode="json", exclude={"fingerprint"})
    if make_scope_fingerprint(scope_payload) != candidate.inherited_scope.fingerprint:
        raise _not_observable(
            "candidate inherited scope fingerprint is invalid",
            received=candidate.inherited_scope.fingerprint,
        )
    metric_ref = cast("Ref[MetricKind]", _payload_ref(candidate.metric_ref))
    ready, readiness_fingerprint = _readiness_fingerprint(session, metric_ref)
    if not ready or readiness_fingerprint != candidate.readiness_fingerprint:
        raise _not_observable(
            "candidate Metric readiness changed after discovery",
            received=readiness_fingerprint,
        )
    scope = candidate.inherited_scope
    compatible = _scope_compatible(
        session=session,
        metric_ref=metric_ref,
        scope=candidate.inherited_scope,
    )
    if not compatible:
        raise _not_observable(
            "candidate Metric no longer accepts the exact inherited scope",
            received=scope.fingerprint,
        )
    dimensions: list[_SemanticInput[DimensionKind | TimeDimensionKind]] = [
        cast("Ref[DimensionKind | TimeDimensionKind]", _payload_ref(payload))
        for payload in scope.axis_refs
        if payload.kind is SemanticKind.DIMENSION
    ]
    time_dimension = (
        cast("Ref[TimeDimensionKind]", _payload_ref(scope.time_dimension_ref))
        if scope.time_dimension_ref is not None
        else None
    )
    slice_by: Mapping[
        _SemanticInput[DimensionKind | TimeDimensionKind],
        SliceValue,
    ] = {
        cast("Ref[DimensionKind | TimeDimensionKind]", _payload_ref(item.dimension_ref)): cast(
            "SliceValue", item.value
        )
        for item in scope.slice_predicates
    }
    cohort: SubjectSet | None = None
    if scope.cohort is not None:
        artifact_ref = scope.cohort.get("artifact_ref")
        if not isinstance(artifact_ref, str):
            raise _not_observable(
                "candidate cohort binding has no artifact_ref",
                received=repr(artifact_ref),
            )
        cohort_frame = session.get_frame(artifact_ref)
        if not isinstance(cohort_frame, SubjectSet):
            raise _not_observable(
                "candidate cohort artifact is not a SubjectSet",
                received=type(cohort_frame).__name__,
            )
        cohort = cohort_frame
    origin = CandidateOrigin(
        ontology_catalog_fingerprint=candidate.ontology_catalog_fingerprint,
        semantic_catalog_fingerprint=candidate.semantic_catalog_fingerprint,
        candidate_set_ref=candidate.candidate_set_ref,
        item_id=candidate.item_id,
        semantic_edge_ref=candidate.semantic_edge_ref,
        edge_relation=candidate.edge_relation,
        source_metric_ref=candidate.source_metric_ref,
        candidate_semantic_ref=candidate.candidate_semantic_ref,
        metric_ref=candidate.metric_ref,
        edge_context=candidate.edge_context,
        inherited_scope_fingerprint=scope.fingerprint,
        readiness_fingerprint=candidate.readiness_fingerprint,
    )
    origins = merge_candidate_origins(candidate.upstream_origins, (origin,))
    window = dict(scope.window) if scope.window is not None else None
    if window is not None:
        # Persisted MetricFrame windows use the resolved AbsoluteWindow shape;
        # observe's public TimeScope input accepts only start/end.
        window.pop("kind", None)
        window.pop("grain", None)
        window.pop("time_dimension", None)
    if window is None:
        time_scope = None
    else:
        from marivo.analysis import time_scope as make_time_scope

        start = window.get("start")
        end = window.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise TypeError("candidate scope window must contain ISO start/end strings")
        time_scope = make_time_scope(start=start, end=end)
    return observe(
        metric_ref,
        time_scope=time_scope,
        grain=to_temporal_grain(scope.grain),
        dimensions=dimensions,
        slice_by=slice_by,
        time_dimension=time_dimension,
        cohort=cohort,
        analysis_purpose=analysis_purpose,
        session=session,
        _candidate_origins=origins,
        _candidate_input_refs=(candidate.candidate_set_ref,),
    )


__all__ = ["observe_candidate"]
