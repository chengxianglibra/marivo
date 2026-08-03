"""Bounded ontology-guided semantic hypothesis discovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, cast

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.candidate_identity import semantic_hypothesis_item_id
from marivo.analysis.candidate_lineage import (
    CandidateResolutionIssue,
    InheritedObservationScope,
    SemanticEdgeContext,
    SemanticHypothesisExcludedCounts,
    SemanticHypothesisExclusion,
    SemanticHypothesisExclusionKind,
    SemanticHypothesisResolutionSummary,
)
from marivo.analysis.errors import (
    AmbiguousMetricLineageError,
    AnalysisRepair,
    MissingMetricLineageError,
    OntologyNotConfiguredError,
    OntologyUnavailableError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.identity import make_issue_id, make_scope_fingerprint
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.candidate import (
    CandidateReadinessBinding,
    CandidateSet,
    SemanticHypothesisCandidateSetMeta,
)
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._candidate_columns import build_union_columns, validate_shape_columns
from marivo.analysis.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
)
from marivo.analysis.lineage import LineageStep
from marivo.analysis.session._runtime import persist_job_record, register_frame_artifact
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.introspection.live.model import LiveHelpTarget
from marivo.ontology.types import OntologyEndpointRef, SemanticEdgeIR, SemanticEdgeRef
from marivo.refs import MetricKind, Ref, RefPayloadV1, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import DerivedMetricDetails, SimpleMetricDetails
from marivo.semantic.metric_graph import CatalogMetricIdentity
from marivo.semantic.metric_graph_canonical import fingerprint

_MAX_EXCLUSIONS = 20


def _analysis_repair(
    *,
    kind: Literal["retry", "inspect", "semantic_authoring", "environment"],
    action: str,
    target: str,
    snippet: str | None = None,
) -> AnalysisRepair:
    return AnalysisRepair(
        kind=kind,
        action=action,
        help_target=LiveHelpTarget(
            surface="ontology" if target.startswith("ontology.") else "analysis",
            canonical_id=target.split(".", 1)[1] if "." in target else target,
        ),
        snippet=snippet,
    )


def _source_metric_ref(source: MetricFrame | DeltaFrame) -> Ref[MetricKind]:
    identities: tuple[object, ...]
    if isinstance(source, MetricFrame):
        identities = tuple(source.meta.metric_identities)
    else:
        if not isinstance(source.meta, DeltaFrameMeta) or source.meta.semantic_kind not in {
            "scalar",
            "time_series",
            "segmented",
            "panel",
        }:
            raise SemanticKindMismatchError(
                message="semantic_hypotheses accepts only Metric-derived DeltaFrame shapes",
                expected="scalar|time_series|segmented|panel DeltaFrame",
                received=str(getattr(source.meta, "semantic_kind", "unknown")),
            )
        identities = (
            source.meta.comparison_identity.current,
            source.meta.comparison_identity.baseline,
        )
    payloads = {
        identity.metric_ref
        for identity in identities
        if isinstance(identity, CatalogMetricIdentity)
    }
    if not payloads:
        raise MissingMetricLineageError(
            message="semantic_hypotheses source has no recoverable catalog Metric identity",
            expected="one persisted catalog Metric lineage",
            received="none",
            repair=_analysis_repair(
                kind="retry",
                action="Observe one catalog Metric, or compare observations of that same Metric.",
                target="analysis.observe",
            ),
        )
    if len(payloads) != 1 or any(
        not isinstance(identity, CatalogMetricIdentity) or identity.metric_ref not in payloads
        for identity in identities
    ):
        raise AmbiguousMetricLineageError(
            message="semantic_hypotheses source has multiple Metric identities",
            expected="exactly one catalog Metric lineage",
            received=", ".join(
                sorted(f"{payload.kind.value}:{payload.path}" for payload in payloads)
            )
            or "runtime/mixed identity",
            repair=_analysis_repair(
                kind="retry",
                action="Start from an arity-one MetricFrame or same-Metric DeltaFrame.",
                target="analysis.observe",
            ),
        )
    return ref_factory.metric(next(iter(payloads)).path)


def _scope_from_metric_meta(meta: MetricFrameMeta) -> InheritedObservationScope:
    window = meta.window
    axis_bindings = meta.axis_bindings
    time_binding = next((item for item in axis_bindings if item.role == "time_dimension"), None)
    cohort_value = meta.cohort
    cohort = (
        cast("dict[str, object]", cohort_value.model_dump(mode="json"))
        if cohort_value is not None
        else None
    )
    scope_payload = {
        "schema_version": "inherited-observation-scope/v1",
        "window": window,
        "grain": time_binding.grain if time_binding is not None else None,
        "time_dimension_ref": (time_binding.ref.to_dict() if time_binding is not None else None),
        "axis_refs": [item.ref.to_dict() for item in axis_bindings],
        "slice_predicates": [
            {
                "dimension_ref": item.dimension_ref.to_dict(),
                "value": item.value,
            }
            for item in meta.slice_predicates
        ],
        "cohort": cohort,
        "source_catalog_fingerprint": meta.catalog_definition_fingerprint,
    }
    scope = InheritedObservationScope.model_validate({**scope_payload, "fingerprint": "pending"})
    canonical_scope = scope.model_dump(mode="json", exclude={"fingerprint"})
    return scope.model_copy(update={"fingerprint": make_scope_fingerprint(canonical_scope)})


def _inherited_scope(
    source: MetricFrame | DeltaFrame, session: Session
) -> InheritedObservationScope:
    if isinstance(source, MetricFrame):
        return _scope_from_metric_meta(source.meta)
    current = session.get_frame(source.meta.source_current_ref)
    if not isinstance(current, MetricFrame):
        raise MissingMetricLineageError(
            message="DeltaFrame current source is not a recoverable MetricFrame",
            expected="persisted MetricFrame source_current_ref",
            received=type(current).__name__,
        )
    return _scope_from_metric_meta(current.meta)


def _scope_compatible(
    *, session: Session, metric_ref: Ref[MetricKind], scope: InheritedObservationScope
) -> bool:
    entry = session.catalog.require(metric_ref)
    details = entry.details()
    assert isinstance(details, (SimpleMetricDetails, DerivedMetricDetails))
    dimensions = set(details.candidate_dimensions)
    time_dimensions = set(details.candidate_time_dimensions)
    accepted = dimensions | time_dimensions
    for payload in scope.axis_refs:
        ref = _payload_ref(payload)
        if ref not in accepted:
            return False
    for predicate in scope.slice_predicates:
        ref = _payload_ref(predicate.dimension_ref)
        if ref not in accepted:
            return False
    if scope.time_dimension_ref is not None:
        ref = _payload_ref(scope.time_dimension_ref)
        if ref not in time_dimensions:
            return False
    if scope.cohort is not None:
        subject = scope.cohort.get("subject_entity_ref")
        subject_path = subject.get("path") if isinstance(subject, dict) else None
        if details.root_entity is None or details.root_entity.path != subject_path:
            return False
    return True


def _payload_ref(payload: RefPayloadV1) -> Ref[SemanticKindTag]:
    factory = {
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
    }[payload.kind]
    return factory(payload.path)


def _readiness_fingerprint(session: Session, metric_ref: Ref[MetricKind]) -> tuple[bool, str]:
    report = session.catalog.readiness(refs=[metric_ref])
    payload = {
        "catalog_definition_fingerprint": session.catalog.definition_fingerprint,
        "metric_ref": RefPayloadV1.from_ref(metric_ref).to_dict(),
        "status": report.status,
        "blockers": [{"kind": item.kind, "refs": list(item.refs)} for item in report.blockers],
        "warnings": [{"kind": item.kind, "refs": list(item.refs)} for item in report.warnings],
    }
    return report.status != "blocked", fingerprint(payload)


def _edge_context(edge: SemanticEdgeIR) -> SemanticEdgeContext:
    directed = edge.relation == "influences"
    return SemanticEdgeContext(
        semantic_edge_ref=edge.ref,
        business_definition=edge.context.business_definition or "",
        guardrails=tuple(edge.context.guardrails),
        anchor_role="outcome" if directed else "related_endpoint",
        candidate_role="driver" if directed else "related_endpoint",
    )


def _serialize_ref(ref: Ref[SemanticKindTag]) -> str:
    return json.dumps(RefPayloadV1.from_ref(ref).to_dict(), sort_keys=True, separators=(",", ":"))


def _serialize_edge_ref(edge: SemanticEdgeIR) -> str:
    return json.dumps(edge.ref.to_dict(), sort_keys=True, separators=(",", ":"))


def _issue(
    *, artifact_ref: str, source_ref: str, exclusion: SemanticHypothesisExclusion
) -> CandidateResolutionIssue:
    reason = exclusion.reason
    metric = exclusion.metric_ref
    issue_sources = [source_ref, exclusion.semantic_edge_ref.key]
    if exclusion.candidate_semantic_ref is not None:
        issue_sources.append(
            f"{exclusion.candidate_semantic_ref.kind.value}:{exclusion.candidate_semantic_ref.path}"
        )
    if metric is not None:
        issue_sources.append(f"{metric.kind.value}:{metric.path}")
    if reason == "semantic_not_ready" and metric is not None:
        action = "Inspect current semantic readiness for the excluded Metric."
        snippet = f"session.catalog.readiness(refs=[ms.ref.metric({metric.path!r})]).show()"
        target = "analysis.catalog.metrics"
    elif reason == "incompatible_inherited_scope" and metric is not None:
        action = "Inspect the Metric and use ordinary observe with an explicit compatible scope."
        snippet = f"session.observe(ms.ref.metric({metric.path!r}), ...)"
        target = "analysis.observe"
    elif reason == "no_observable_metric":
        action = "Inspect the edge endpoint and semantic dependency indexes."
        snippet = None
        target = "ontology.authoring"
    else:
        action = "Inspect the ontology edge definition and its source anchor mapping."
        snippet = None
        target = "ontology.authoring"
    return CandidateResolutionIssue(
        issue_id=make_issue_id(
            artifact_id=artifact_ref,
            kind=reason,
            source_refs=tuple(issue_sources),
        ),
        kind=reason,
        source_refs=tuple(issue_sources),
        semantic_edge_ref=exclusion.semantic_edge_ref,
        candidate_semantic_ref=exclusion.candidate_semantic_ref,
        metric_ref=metric,
        historical=False,
        repair=_analysis_repair(
            kind="inspect",
            action=action,
            target=target,
            snippet=snippet,
        ),
    )


def semantic_hypotheses(
    source: MetricFrame | DeltaFrame,
    *,
    limit: int = 50,
    session: Session,
) -> CandidateSet:
    """Discover unscored one-edge Metric hypotheses from a persisted source."""
    ensure_session_writable(session)
    ensure_frame_in_session(source, session=session, label="semantic_hypotheses source")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise SemanticKindMismatchError(
            message="semantic_hypotheses limit must be an integer within 1..200",
            expected="int in [1, 200], excluding bool",
            received=repr(limit),
        )
    source_artifact_ref = source.meta.artifact_id or source.ref
    if session._store.get_artifact(session.id, source_artifact_ref) is None:
        raise SemanticKindMismatchError(
            message="semantic_hypotheses requires a persisted source artifact",
            expected="a committed MetricFrame or Metric-derived DeltaFrame",
            received=source_artifact_ref,
        )
    if session._ontology_state == "absent":
        raise OntologyNotConfiguredError(
            message="ontology-guided discovery requires models/ontology.py",
            expected="a configured valid project ontology",
            received="ontology absent",
            repair=_analysis_repair(
                kind="semantic_authoring",
                action="Author models/ontology.py and inspect the ontology authoring contract.",
                target="ontology.authoring",
            ),
        )
    if session._ontology_state == "unavailable" or session._ontology_catalog is None:
        raise OntologyUnavailableError(
            message="the configured ontology is unavailable for this Session catalog",
            expected="all authored edge endpoints validate against the current catalog",
            received="; ".join(str(item) for item in session._ontology_issues),
            repair=_analysis_repair(
                kind="inspect",
                action="Load the project ontology directly to inspect its validation issues.",
                target="ontology.authoring",
                snippet="mo.load(semantic=session.catalog)",
            ),
        )
    source_metric_ref = _source_metric_ref(source)
    scope = _inherited_scope(source, session)
    anchors = session.catalog._ontology_anchors_for_metric(source_metric_ref)
    anchor_set = set(anchors)
    source_entity = next((ref for ref in anchors if ref.kind is SemanticKind.ENTITY), None)
    ontology = session._ontology_catalog
    rows_by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    contexts: dict[SemanticEdgeRef, SemanticEdgeContext] = {}
    readiness_bindings: dict[RefPayloadV1, str] = {}
    exclusions: list[SemanticHypothesisExclusion] = []
    counts: dict[SemanticHypothesisExclusionKind, int] = {
        "anchor_overlap": 0,
        "source_metric_self_resolution": 0,
        "no_observable_metric": 0,
        "semantic_not_ready": 0,
        "incompatible_inherited_scope": 0,
    }
    examined_edges = 0

    def exclude(value: SemanticHypothesisExclusion) -> None:
        counts[value.reason] += 1
        exclusions.append(value)

    for edge in ontology._edges_for_discovery():
        proposed: Ref[SemanticKindTag] | None = None
        if edge.relation == "influences":
            if edge.target not in anchor_set:
                continue
            examined_edges += 1
            proposed = cast("Ref[SemanticKindTag]", edge.source)
        else:
            matches = tuple(
                endpoint for endpoint in (edge.source, edge.target) if endpoint in anchor_set
            )
            if not matches:
                continue
            examined_edges += 1
            if len(matches) == 2:
                exclude(
                    SemanticHypothesisExclusion(
                        reason="anchor_overlap",
                        semantic_edge_ref=edge.ref,
                        matched_anchor_refs=tuple(
                            RefPayloadV1.from_ref(cast("Ref[SemanticKindTag]", item))
                            for item in (edge.source, edge.target)
                        ),
                    )
                )
                contexts[edge.ref] = _edge_context(edge)
                continue
            proposed = cast(
                "Ref[SemanticKindTag]", edge.target if matches[0] == edge.source else edge.source
            )
        assert proposed is not None
        contexts[edge.ref] = _edge_context(edge)
        proposed_payload = RefPayloadV1.from_ref(proposed)
        resolved = session.catalog._ontology_metrics_for_endpoint(
            cast("OntologyEndpointRef", proposed)
        )
        if not resolved:
            exclude(
                SemanticHypothesisExclusion(
                    reason="no_observable_metric",
                    semantic_edge_ref=edge.ref,
                    candidate_semantic_ref=proposed_payload,
                )
            )
            continue
        for metric_ref in resolved:
            metric_payload = RefPayloadV1.from_ref(metric_ref)
            if metric_ref == source_metric_ref:
                exclude(
                    SemanticHypothesisExclusion(
                        reason="source_metric_self_resolution",
                        semantic_edge_ref=edge.ref,
                        candidate_semantic_ref=proposed_payload,
                        metric_ref=metric_payload,
                    )
                )
                continue
            ready, readiness_fp = _readiness_fingerprint(session, metric_ref)
            if not ready:
                exclude(
                    SemanticHypothesisExclusion(
                        reason="semantic_not_ready",
                        semantic_edge_ref=edge.ref,
                        candidate_semantic_ref=proposed_payload,
                        metric_ref=metric_payload,
                    )
                )
                continue
            compatible = _scope_compatible(session=session, metric_ref=metric_ref, scope=scope)
            if not compatible:
                exclude(
                    SemanticHypothesisExclusion(
                        reason="incompatible_inherited_scope",
                        semantic_edge_ref=edge.ref,
                        candidate_semantic_ref=proposed_payload,
                        metric_ref=metric_payload,
                    )
                )
                continue
            item_id = semantic_hypothesis_item_id(
                source_artifact_ref=source_artifact_ref,
                semantic_edge_ref=edge.ref,
                candidate_semantic_ref=proposed_payload,
                metric_ref=metric_payload,
            )
            key = (edge.ref.key, proposed.key, metric_ref.key)
            rows_by_key[key] = {
                "item_id": item_id,
                "semantic_edge_ref": _serialize_edge_ref(edge),
                "edge_relation": edge.relation,
                "candidate_semantic_ref": _serialize_ref(proposed),
                "metric_ref": _serialize_ref(metric_ref),
            }
            readiness_bindings[metric_payload] = readiness_fp

    ordered_rows = [
        rows_by_key[key]
        for key in sorted(
            rows_by_key,
            key=lambda key: (
                rows_by_key[key]["edge_relation"],
                key[0],
                key[1],
                key[2],
            ),
        )
    ]
    candidate_count = len(ordered_rows)
    emitted_rows = ordered_rows[:limit]
    bounded_exclusions = tuple(exclusions[:_MAX_EXCLUSIONS])
    summary = SemanticHypothesisResolutionSummary(
        examined_edges=examined_edges,
        candidate_count_before_limit=candidate_count,
        emitted_candidates=len(emitted_rows),
        candidate_limit=limit,
        candidates_omitted=candidate_count - len(emitted_rows),
        excluded_counts=SemanticHypothesisExcludedCounts(**counts),
        exclusions=bounded_exclusions,
        exclusions_omitted=max(0, len(exclusions) - _MAX_EXCLUSIONS),
    )
    df = build_union_columns("semantic_hypothesis", emitted_rows)
    validate_shape_columns("semantic_hypothesis", df)
    frame_ref = gen_ref("frame")
    job_ref = gen_ref("job")
    started_at = datetime.now(UTC)
    started = monotonic()
    used_edge_refs = {
        *(json.loads(row["semantic_edge_ref"])["path"] for row in emitted_rows),
        *(item.semantic_edge_ref.path for item in bounded_exclusions),
    }
    edge_contexts = tuple(
        contexts[ref]
        for ref in sorted(contexts, key=lambda ref: ref.path)
        if ref.path in used_edge_refs
    )
    issues = tuple(
        _issue(artifact_ref=frame_ref, source_ref=source_artifact_ref, exclusion=item)
        for item in bounded_exclusions
    )
    params = {"source_ref": source_artifact_ref, "limit": limit}
    meta = SemanticHypothesisCandidateSetMeta(
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=started_at,
        row_count=len(df),
        byte_size=0,
        analysis_purpose=None,
        issues=issues,
        lineage=compose_lineage(
            (source,),
            step=LineageStep(
                intent="discover.semantic_hypotheses",
                job_ref=job_ref,
                inputs=[source_artifact_ref],
                params_digest=params_digest(params),
                params=params,
            ),
        ),
        source_ref=source_artifact_ref,
        source_kind="metric_frame" if isinstance(source, MetricFrame) else "delta_frame",
        source_metric_ref=RefPayloadV1.from_ref(source_metric_ref),
        source_entity_ref=RefPayloadV1.from_ref(source_entity)
        if source_entity is not None
        else None,
        ontology_catalog_fingerprint=ontology.definition_fingerprint,
        semantic_catalog_fingerprint=session.catalog.definition_fingerprint,
        inherited_scope=scope,
        resolution_summary=summary,
        edge_contexts=edge_contexts,
        readiness_bindings=tuple(
            CandidateReadinessBinding(metric_ref=ref, fingerprint=value)
            for ref, value in sorted(readiness_bindings.items(), key=lambda item: item[0].path)
        ),
        upstream_origins=source.meta.candidate_origins,
        candidate_origins=source.meta.candidate_origins,
    )
    frame = CandidateSet(_df=df, meta=meta)
    analysis_axis: Literal["scalar", "time", "segment", "panel"] = (
        "time"
        if source.meta.semantic_kind == "time_series"
        else "segment"
        if source.meta.semantic_kind == "segmented"
        else "panel"
        if source.meta.semantic_kind == "panel"
        else "scalar"
    )
    frame = cast(
        "CandidateSet",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type="discover.semantic_hypotheses",
            inputs=CommitInputs(input_refs=[source_artifact_ref]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors.from_frame(source),
            subject=Subject(
                grain=getattr(source.meta, "grain", None),
                analysis_axis=analysis_axis,
            ),
            extractor_family="candidate_set",
        ),
    )
    register_frame_artifact(session, frame)
    finished_at = datetime.now(UTC)
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "discover.semantic_hypotheses",
            **job_semantics_from_frames(source),
            "analysis_purpose": None,
            "params": params,
            "input_frame_refs": [source_artifact_ref],
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog.semantic_root),
        },
    )
    return frame


__all__ = ["semantic_hypotheses"]
