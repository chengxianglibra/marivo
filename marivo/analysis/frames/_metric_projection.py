"""frame.metric(id): project one measure out of a multi-metric MetricFrame."""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, cast

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    CrossSessionFrameError,
    FrameCacheCorruptedError,
    MetricArityError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.component import ComponentFrame
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._observe_persist import (
    _attach_metric_component_graph_ref,
    _persist_metric_component_graph_frame,
)
from marivo.analysis.intents._runtime_metric_lowering import lower_metric_inputs
from marivo.analysis.intents.observe import (
    _analysis_axis_for_kind,
    _evaluator_contracts,
    _gen_ref,
    _params_digest,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.runtime_metric import from_replay_payload
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from marivo.semantic.metric_graph import (
    CatalogMetricIdentity,
    ComparableValueSemanticsV1,
    ExpressionPresentationV1,
    MetricArtifactIdentityV1,
    MetricExpressionGraphV1,
    MetricIdentity,
    node_child_ids,
)
from marivo.semantic.metric_graph_canonical import canonical_value, fingerprint
from marivo.semantic.metric_graph_lowering import MetricExpressionForestV1, lower_catalog_metric

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session


def _projected_expression_forest(
    frame: MetricFrame,
    *,
    entry_index: int,
    metric_identity: MetricIdentity,
    session: Session,
) -> MetricExpressionForestV1:
    registry = session.catalog._require_index().registry
    if isinstance(metric_identity, CatalogMetricIdentity):
        return lower_catalog_metric(registry, metric_identity.metric_ref.path)
    observe_params = next(
        (
            step.params
            for step in reversed(frame.lineage.steps)
            if step.intent == "observe" and step.params is not None
        ),
        None,
    )
    replay_expressions = (
        observe_params.get("replay_expressions") if isinstance(observe_params, dict) else None
    )
    if not isinstance(replay_expressions, list) or entry_index >= len(replay_expressions):
        raise FrameCacheCorruptedError(
            message="multi-metric runtime projection is missing its typed replay expression",
            context={"frame_ref": frame.ref, "entry_index": entry_index},
        )
    try:
        expression = from_replay_payload(replay_expressions[entry_index])
    except (TypeError, ValueError) as exc:
        raise FrameCacheCorruptedError(
            message="multi-metric runtime projection has an invalid typed replay expression",
            context={"frame_ref": frame.ref, "entry_index": entry_index},
        ) from exc
    return lower_metric_inputs(registry, (expression,))


def _project_component_graph_payload(
    frame: MetricFrame,
    *,
    entry_index: int,
    projected_graph: MetricExpressionGraphV1,
    projected_presentation: ExpressionPresentationV1,
    session: Session,
) -> dict[str, object]:
    component_ref = frame.meta.component_graph_ref
    if component_ref is None:
        raise FrameCacheCorruptedError(
            message="multi-metric projection requires the persisted recursive component graph",
            context={"frame_ref": frame.ref, "entry_index": entry_index},
        )
    loaded = load_frame(component_ref, session=session)
    if not isinstance(loaded, ComponentFrame) or not isinstance(loaded.meta.component_graph, dict):
        raise FrameCacheCorruptedError(
            message="multi-metric component_graph_ref did not resolve to a component graph",
            context={"frame_ref": frame.ref, "component_graph_ref": component_ref},
        )
    payload = loaded.meta.component_graph
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise FrameCacheCorruptedError(
            message="multi-metric component graph has no typed node records",
            context={"frame_ref": frame.ref, "component_graph_ref": component_ref},
        )
    root_id = projected_graph.roots[0]
    source_prefix = f"root[{entry_index}]"

    def rebase_path(path: str) -> str | None:
        if path == source_prefix:
            return "root[0]"
        if path.startswith(source_prefix + "."):
            return "root[0]" + path[len(source_prefix) :]
        return None

    selected_nodes: list[dict[str, object]] = []
    reachable_ids: set[str] = set()
    graph_nodes = {record.node_id: record.node for record in projected_graph.nodes}

    def collect(node_id: str) -> None:
        if node_id in reachable_ids:
            return
        reachable_ids.add(node_id)
        for child_id in node_child_ids(graph_nodes[node_id]):
            collect(child_id)

    collect(root_id)
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict) or raw_node.get("node_id") not in reachable_ids:
            continue
        occurrence_paths = raw_node.get("occurrence_paths")
        rebased_paths = (
            sorted(
                rebased
                for path in occurrence_paths
                if isinstance(path, str)
                if (rebased := rebase_path(path)) is not None
            )
            if isinstance(occurrence_paths, list)
            else []
        )
        selected_nodes.append({**raw_node, "occurrence_paths": rebased_paths})
    return {
        "schema": "metric-component-graph/v1",
        "root_node_ids": [root_id],
        "nodes": selected_nodes,
        "presentation": canonical_value(projected_presentation),
    }


def project_metric(frame: MetricFrame, metric_id: str) -> MetricFrame:
    """Project one metric out of a multi-metric frame as an arity-1 MetricFrame.

    Args:
        frame: The source MetricFrame (arity >= 1).
        metric_id: Bare metric id (e.g. ``"sales.revenue"``) carried by the frame.

    Returns:
        An arity-1 MetricFrame with the shared axes and the projected metric's
        values in the canonical ``value`` column. On an arity-1 frame, returns
        ``self`` when the id matches. On a cache hit, returns the persisted
        artifact without re-computing.

    Raises:
        MetricArityError: When ``metric_id`` is not carried by the frame.
        CrossSessionFrameError: When the frame's owning session is not current.
    """
    entries = frame.measures_meta()
    by_id = {entry["metric_id"]: entry for entry in entries}
    if metric_id not in by_id:
        raise MetricArityError(
            message=f"frame carries no metric {metric_id!r}",
            hint=f"available metrics: {sorted(by_id)!r}",
            context={"metric": metric_id, "metrics": sorted(by_id)},
        )
    if frame.arity == 1:
        return frame

    session = require_current_session()
    if frame.meta.session_id != session.id:
        raise CrossSessionFrameError(
            message=(f"frame belongs to session {frame.meta.session_id!r}, not {session.id!r}"),
        )

    entry = by_id[metric_id]
    entry_index = entries.index(entry)
    observe_params = next(
        (
            step.params
            for step in reversed(frame.lineage.steps)
            if step.intent == "observe" and isinstance(step.params, dict)
        ),
        None,
    )
    replay_expressions = (
        observe_params.get("replay_expressions") if isinstance(observe_params, dict) else None
    )
    if not isinstance(replay_expressions, list) or entry_index >= len(replay_expressions):
        raise FrameCacheCorruptedError(
            message="multi-metric frame is missing its typed projection replay expression",
            context={"frame_ref": frame.ref, "metric_id": metric_id},
        )
    parent_artifact = frame.meta.artifact_id or frame.meta.ref
    params = {
        "replay_expression": replay_expressions[entry_index],
    }
    prospective_id = compute_prospective_artifact_id(
        step_type="select_metric",
        inputs=CommitInputs(input_refs=[parent_artifact]),
        params=CommitParams(values=params),
        semantic_anchors=CommitSemanticAnchors.from_frame(frame),
    )
    if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
        return cast("MetricFrame", load_frame(prospective_id, session=session))

    axis_columns = [axis["column"] for axis in frame.meta.axes.values() if "column" in axis]
    df = frame._dataframe_copy()[[*axis_columns, entry["column"]]].rename(
        columns={entry["column"]: MetricFrame.VALUE_COLUMN}
    )

    started_at = datetime.now(UTC)
    started = monotonic()
    # The projected component graph must name the deterministic parent
    # identity that the evidence commit publishes.
    frame_ref = prospective_id
    job_ref = _gen_ref("job")
    grain_token: str | None = None
    window = frame.meta.window
    if isinstance(window, dict):
        grain_token = window.get("grain")
    metric_identity = frame.meta.metric_identities[entry_index]
    projected_forest = _projected_expression_forest(
        frame,
        entry_index=entry_index,
        metric_identity=metric_identity,
        session=session,
    )
    projected_graph = projected_forest.graph
    projected_dependency_digest = projected_forest.dependency_digest
    projected_presentation = projected_forest.presentation
    evaluator_contracts = _evaluator_contracts(projected_forest)
    expression_fingerprint = projected_graph.roots[0]
    comparable = frame.meta.comparable_value_semantics
    key_schema = frame.meta.key_schema
    projected_comparable = None
    if expression_fingerprint is not None and comparable is not None and key_schema is not None:
        comparable_payload = {
            "expression_fingerprint": expression_fingerprint,
            "evaluator_contracts": evaluator_contracts,
            "global_slice": comparable.global_slice,
            "key_schema_fingerprint": key_schema.fingerprint,
            "unit": entry["unit"],
            "fold": None,
            "source_domain_fingerprint": comparable.source_domain_fingerprint,
            "definition_transform_fingerprint": None,
        }
        projected_comparable = ComparableValueSemanticsV1(
            schema="comparable-value-semantics/v1",
            expression_fingerprint=expression_fingerprint,
            evaluator_contracts=evaluator_contracts,
            global_slice=comparable.global_slice,
            key_schema_fingerprint=key_schema.fingerprint,
            unit=entry["unit"],
            fold=None,
            source_domain_fingerprint=comparable.source_domain_fingerprint,
            definition_transform_fingerprint=None,
            fingerprint=fingerprint(comparable_payload),
        )
    parent_artifact_identity = frame.meta.artifact_identity
    projected_artifact_identity = None
    if parent_artifact_identity is not None:
        presentation_fingerprint = fingerprint(projected_presentation)
        artifact_payload = {
            "metric_identities": (metric_identity,),
            "scope_fingerprint": parent_artifact_identity.scope_fingerprint,
            "source_domain_fingerprint": parent_artifact_identity.source_domain_fingerprint,
            "dependency_fingerprint": projected_dependency_digest.digest,
            "snapshot_fingerprint": parent_artifact_identity.snapshot_fingerprint,
            "coverage_fingerprint": parent_artifact_identity.coverage_fingerprint,
            "presentation_fingerprint": presentation_fingerprint,
            "artifact_schema_version": parent_artifact_identity.artifact_schema_version,
        }
        projected_artifact_identity = MetricArtifactIdentityV1(
            schema="metric-artifact/v1",
            metric_identities=(metric_identity,),
            scope_fingerprint=parent_artifact_identity.scope_fingerprint,
            source_domain_fingerprint=parent_artifact_identity.source_domain_fingerprint,
            dependency_fingerprint=projected_dependency_digest.digest,
            snapshot_fingerprint=parent_artifact_identity.snapshot_fingerprint,
            coverage_fingerprint=parent_artifact_identity.coverage_fingerprint,
            presentation_fingerprint=presentation_fingerprint,
            artifact_schema_version=parent_artifact_identity.artifact_schema_version,
            fingerprint=fingerprint(artifact_payload),
        )
    meta = MetricFrameMeta(
        kind="metric_frame",
        catalog_definition_fingerprint=frame.meta.catalog_definition_fingerprint,
        ref=frame_ref,
        session_id=session.id,
        project_root=frame.meta.project_root,
        produced_by_job=job_ref,
        analysis_purpose=frame.meta.analysis_purpose,
        created_at=started_at,
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                *frame.meta.lineage.steps,
                LineageStep(
                    intent="select_metric",
                    job_ref=job_ref,
                    inputs=[parent_artifact],
                    params_digest=_params_digest(params),
                    analysis_purpose=frame.meta.analysis_purpose,
                    params=params,
                ),
            ]
        ),
        metric_id=metric_id,
        metric_identity=metric_identity,
        metric_identities=(metric_identity,),
        expression_graph=projected_graph,
        expression_fingerprint=expression_fingerprint,
        semantic_dependency_digest=projected_dependency_digest,
        presentation=projected_presentation,
        presentation_fingerprint=(
            fingerprint(projected_presentation) if projected_presentation is not None else None
        ),
        artifact_identity=projected_artifact_identity,
        key_schema=key_schema,
        source_compatibility_domain=frame.meta.source_compatibility_domain,
        comparable_value_semantics=projected_comparable,
        execution_stats=(
            frame.meta.execution_stats.model_copy(
                update={
                    "root_origins": (
                        "catalog"
                        if isinstance(metric_identity, CatalogMetricIdentity)
                        else "runtime",
                    ),
                    "cache_hit": False,
                    "replay_used": False,
                }
            )
            if frame.meta.execution_stats is not None
            else None
        ),
        axis_bindings=frame.meta.axis_bindings,
        slice_predicates=frame.meta.slice_predicates,
        status_time_dimension_ref=(
            RefPayloadV1.from_ref(ref_factory.time_dimension(status_time_dimension))
            if isinstance(
                status_time_dimension := entry.get("status_time_dimension"),
                str,
            )
            else None
        ),
        axes=frame.meta.axes,
        measure={"name": entry["name"]},
        window=frame.meta.window,
        where=frame.meta.where,
        semantic_kind=frame.meta.semantic_kind,
        semantic_model=metric_id.split(".", 1)[0],
        unit=entry["unit"],
        unit_state=entry.get("unit_state"),
        reaggregatable=bool(entry["reaggregatable"]),
        additivity=entry["additivity"],
        aggregation=entry.get("aggregation"),
        status_time_dimension=entry.get("status_time_dimension"),
        cumulative=frame.meta.cumulative,
    )
    projected = MetricFrame(_df=df, meta=meta)
    component_graph = _project_component_graph_payload(
        frame,
        entry_index=entry_index,
        projected_graph=projected_graph,
        projected_presentation=projected_presentation,
        session=session,
    )
    result = cast(
        "MetricFrame",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=projected,
            step_type="select_metric",
            inputs=CommitInputs(input_refs=[parent_artifact]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors.from_frame(projected),
            subject=Subject(
                grain=grain_token,
                analysis_axis=_analysis_axis_for_kind(frame.meta.semantic_kind),
            ),
            extractor_family="projection",
        ),
    )
    component = _persist_metric_component_graph_frame(
        session=session,
        df=df,
        parent=result,
        axes=frame.meta.axes,
        semantic_kind=frame.meta.semantic_kind,
        job_ref=job_ref,
        component_graph=component_graph,
    )
    result = _attach_metric_component_graph_ref(
        session=session,
        parent=result,
        component=component,
        persist_parent=True,
    )
    register_frame_artifact(session, result)
    finished_at = datetime.now(UTC)
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "select_metric",
            **job_semantics_from_frames(result),
            "analysis_purpose": frame.meta.analysis_purpose,
            "params": params,
            "input_frame_refs": [parent_artifact],
            "output_frame_ref": result.meta.artifact_id or result.ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog.semantic_root),
            "queries": [],
        },
    )
    return result
