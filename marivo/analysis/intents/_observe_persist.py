"""Frame persistence, sidecar attachment, and evidence commit for observe.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast

from marivo.analysis._semantic_persistence import ComponentBindingV1
from marivo.analysis.evidence.identity import make_component_artifact_id
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.component import (
    ComponentFrame,
    ComponentFrameMeta,
    resolve_role_column_name,
)
from marivo.analysis.frames.coverage import CoverageFrame, CoverageFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._observe_components import _composition_payload
from marivo.analysis.intents._observe_inputs import _analysis_axis_for_kind
from marivo.analysis.session._runtime import persist_frame, register_frame_artifact
from marivo.analysis.session.core import Session
from marivo.refs import Ref, RefPayloadV1
from marivo.semantic.metric_graph import CatalogMetricIdentity, RuntimeExpressionIdentity


def _persist_metric_component_frame(
    *,
    session: Session,
    df: Any,
    parent: MetricFrame,
    metric_ir: Any,
    axes: dict[str, Any],
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    job_ref: str,
    composition_kind: Literal["ratio", "weighted_mean", "linear"] | None = None,
    components: dict[str, str] | None = None,
    linear_terms: tuple[tuple[str, str], ...] | None = None,
    component_graph: dict[str, Any] | None = None,
) -> ComponentFrame:
    # Component decomposition operates on arity-1 metric frames; multi-metric
    # frames are gated out upstream. Narrow metric_id for the ComponentFrameMeta
    # contract which requires a single metric id.
    assert parent.meta.metric_id is not None
    metric_composition = getattr(metric_ir, "composition", None)
    if composition_kind is None:
        assert metric_composition is not None
        resolved_kind = metric_composition.kind
    else:
        resolved_kind = composition_kind
    if components is None:
        assert metric_composition is not None
        resolved_components = {
            k: (v.path if type(v) is Ref else str(v))
            for k, v in metric_composition.components.items()
        }
    else:
        resolved_components = components
    resolved_linear_terms = (
        linear_terms
        if linear_terms is not None
        else (metric_ir.linear_terms if resolved_kind == "linear" else ())
    )
    assert parent.meta.metric_identity is not None
    root_node_ids = tuple(
        str(node_id) for node_id in (component_graph or {}).get("root_node_ids", ())
    )
    registry = session.catalog._require_index().registry
    component_bindings: list[ComponentBindingV1] = []
    for role, target in resolved_components.items():
        column = resolve_role_column_name(resolved_components, role)
        if target in registry.metrics:
            component_bindings.append(
                ComponentBindingV1(
                    role=role,
                    column=column,
                    metric_identity=CatalogMetricIdentity(
                        kind="catalog",
                        metric_ref=RefPayloadV1.from_ref(Ref.metric(target)),
                    ),
                )
            )
        else:
            if not root_node_ids:
                raise ValueError("runtime component binding requires an expression graph root")
            component_bindings.append(
                ComponentBindingV1(
                    role=role,
                    column=column,
                    expression_node_id=root_node_ids[0],
                )
            )
    frame_ref = make_component_artifact_id(parent.ref)
    component = ComponentFrame(
        _df=df.copy(),
        meta=ComponentFrameMeta(
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            lineage=parent.lineage,
            parent_ref=parent.ref,
            parent_kind="metric_frame",
            metric_identity=parent.meta.metric_identity,
            component_bindings=tuple(component_bindings),
            axis_bindings=parent.meta.axis_bindings,
            composition_kind=resolved_kind,
            linear_terms=resolved_linear_terms,
            semantic_kind=semantic_kind,
            root_node_ids=root_node_ids,
            component_graph=component_graph,
        ),
    )
    component.meta = cast("ComponentFrameMeta", persist_frame(session, component))
    return component


def _persist_metric_component_graph_frame(
    *,
    session: Session,
    df: Any,
    parent: MetricFrame,
    axes: dict[str, Any],
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    job_ref: str,
    component_graph: dict[str, Any],
) -> ComponentFrame:
    """Persist the recursive graph sidecar when no immediate decomposition exists."""

    frame_ref = make_component_artifact_id(parent.ref)
    metric_identity = parent.meta.metric_identity
    if metric_identity is None:
        expression_fingerprint = parent.meta.expression_fingerprint
        if not expression_fingerprint:
            raise ValueError("multi-root component graph requires expression_fingerprint")
        metric_identity = RuntimeExpressionIdentity(
            kind="runtime_expression",
            expression_schema="metric-expression/v1",
            expression_fingerprint=expression_fingerprint,
        )
    component = ComponentFrame(
        _df=df.copy(),
        meta=ComponentFrameMeta(
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            lineage=parent.lineage,
            parent_ref=parent.ref,
            parent_kind="metric_frame",
            metric_identity=metric_identity,
            axis_bindings=parent.meta.axis_bindings,
            composition_kind=None,
            semantic_kind=semantic_kind,
            root_node_ids=tuple(
                str(node_id) for node_id in component_graph.get("root_node_ids", ())
            ),
            component_graph=component_graph,
        ),
    )
    component.meta = cast("ComponentFrameMeta", persist_frame(session, component))
    return component


def _attach_metric_component_ref(
    *,
    session: Session,
    parent: MetricFrame,
    component: ComponentFrame,
    metric_ir: Any,
    composition: dict[str, Any] | None = None,
    persist_parent: bool = True,
) -> MetricFrame:
    parent.meta = parent.meta.model_copy(
        update={
            "component_ref": component.ref,
            "component_graph_ref": component.ref,
            "composition": composition or _composition_payload(metric_ir),
        }
    )
    if persist_parent:
        parent.meta = cast("MetricFrameMeta", persist_frame(session, parent))
    return parent


def _attach_metric_component_graph_ref(
    *,
    session: Session,
    parent: MetricFrame,
    component: ComponentFrame,
    persist_parent: bool = True,
) -> MetricFrame:
    """Attach only recursive graph state, without claiming decomposition support."""

    parent.meta = parent.meta.model_copy(update={"component_graph_ref": component.ref})
    if persist_parent:
        parent.meta = cast("MetricFrameMeta", persist_frame(session, parent))
    return parent


def _persist_and_attach_coverage_sidecar(
    *,
    session: Session,
    df: Any,
    parent: MetricFrame,
    job_ref: str,
    persist_parent: bool = True,
    coverage_node_id: str | None = None,
) -> MetricFrame:
    """Persist a CoverageFrame sidecar and attach it to the parent MetricFrame.

    The sidecar's ``coverage_kind`` is dispatched from the coverage DataFrame's
    column shape: a trailing ``window_coverage`` df carries ``expected_span`` /
    ``covered_span`` (and ``sample_interval`` is ``None``); a sampled
    ``time_slot`` df carries ``actual_samples`` / ``expected_samples`` (and
    ``sample_interval`` is the fold's sample interval).
    """
    from marivo.analysis.evidence.identity import make_coverage_artifact_id

    frame_ref = make_coverage_artifact_id(parent.ref, node_id=coverage_node_id)
    # Build coverage summary from the coverage DataFrame
    coverage_ratios = df["coverage_ratio"].tolist() if "coverage_ratio" in df.columns else []
    coverage_summary: dict[str, Any] | None = None
    if coverage_ratios:
        coverage_summary = {
            "min": min(coverage_ratios),
            "avg": sum(coverage_ratios) / len(coverage_ratios),
            "partial_buckets": sum(1 for r in coverage_ratios if r != 1.0),
        }
    is_window_coverage = "expected_span" in df.columns and "covered_span" in df.columns
    if is_window_coverage:
        coverage_kind: Literal["time_slot", "window_coverage"] = "window_coverage"
        sample_interval_val: str | None = None
    else:
        coverage_kind = "time_slot"
        fold_meta = getattr(parent.meta, "fold", None)
        sample_interval_val = (
            fold_meta.get("sample_interval") if isinstance(fold_meta, dict) else None
        )
        if sample_interval_val is None:
            sample_interval_val = "unknown"
    coverage = CoverageFrame(
        _df=df.copy(),
        meta=CoverageFrameMeta(
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            lineage=parent.lineage,
            parent_ref=parent.ref,
            coverage_kind=coverage_kind,
            axes=parent.meta.axes,
            sample_interval=sample_interval_val,
        ),
    )
    coverage.meta = cast("CoverageFrameMeta", persist_frame(session, coverage))
    # Update quality summary with coverage fields
    quality_update: dict[str, Any] = {}
    existing_quality = parent.meta.quality_summary
    if existing_quality is not None:
        quality_update = existing_quality.model_dump()
    if coverage_summary is not None:
        quality_update["sample_coverage_min"] = coverage_summary.get("min")
        quality_update["sample_coverage_avg"] = coverage_summary.get("avg")
        quality_update["sample_coverage_partial_buckets"] = coverage_summary.get("partial_buckets")
    from marivo.analysis.evidence.types import QualitySummary

    updated_quality = QualitySummary(**quality_update) if quality_update else None
    # Attach coverage_ref, coverage_summary, and updated quality to the parent
    parent.meta = parent.meta.model_copy(
        update={
            "coverage_ref": coverage.ref,
            "coverage_summary": coverage_summary,
            "quality_summary": updated_quality,
        }
    )
    if persist_parent:
        parent.meta = cast("MetricFrameMeta", persist_frame(session, parent))
    return parent


def _persist_metric_graph_coverage_sidecars(
    *,
    session: Session,
    parent: MetricFrame,
    execution: Any,
    job_ref: str,
    existing_refs: dict[str, str] | None = None,
) -> dict[str, str]:
    """Persist every node-local coverage payload and return its graph reference."""

    refs = dict(existing_refs or {})
    for node_id, result in sorted(execution.nodes.items()):
        if result.coverage_df is None or node_id in refs:
            continue
        detached_parent = MetricFrame(
            _df=parent._dataframe_copy(),
            meta=parent.meta.model_copy(),
        )
        detached_parent = _persist_and_attach_coverage_sidecar(
            session=session,
            df=result.coverage_df,
            parent=detached_parent,
            job_ref=job_ref,
            persist_parent=False,
            coverage_node_id=node_id,
        )
        assert detached_parent.meta.coverage_ref is not None
        refs[node_id] = detached_parent.meta.coverage_ref
    return refs


def _commit_observe_metric_frame(
    *,
    session: Session,
    frame: MetricFrame,
    params: dict[str, Any],
    metric_id: str | None,
    model_name: str,
    stored_where: dict[str, Any],
    semantic_kind: str,
    subject_grain: str | None = None,
    step_type: str = "observe",
    metric_ids: list[str] | None = None,
    models: list[str] | None = None,
    semantic_anchors: dict[str, Any] | None = None,
) -> MetricFrame:
    """Commit a MetricFrame through the evidence pipeline (shared tail).

    When ``metric_ids`` is provided (arity-N multi-metric path), the anchors
    carry the full metric list while the commit subject keeps ``metric=None``
    — the extractor reads per-measure subjects from ``meta.measures``.
    """
    result = cast(
        "MetricFrame",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type=step_type,
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors.from_frame(frame),
            subject=Subject(
                grain=subject_grain,
                analysis_axis=_analysis_axis_for_kind(semantic_kind),
            ),
            extractor_family="metric_frame",
        ),
    )
    register_frame_artifact(session, result)
    return result


def _meta_additivity(
    value: str | None,
) -> Literal["additive", "semi_additive", "non_additive"] | None:
    """Narrow a catalog additivity string to the MetricFrameMeta literal."""
    if value == "additive":
        return "additive"
    if value == "semi_additive":
        return "semi_additive"
    if value == "non_additive":
        return "non_additive"
    return None


def _meta_aggregation(value: object) -> str | None:
    """Render a semantic aggregation as stable frame metadata."""
    if isinstance(value, str):
        return value
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and value[0] == "percentile"
        and isinstance(value[1], (int, float))
    ):
        return f"percentile({value[1]})"
    return None


def _metric_semantics_payload(
    metric_ir: Any,
    *,
    force_additivity: Literal["additive", "semi_additive", "non_additive"] | None = None,
) -> dict[str, object]:
    """Return output metric semantics that participate in artifact identity."""
    additivity = (
        force_additivity
        if force_additivity is not None
        else _meta_additivity(getattr(metric_ir, "additivity", None))
    )
    status_path = getattr(metric_ir, "status_time_dimension", None)
    return {
        "additivity": additivity,
        "aggregation": _meta_aggregation(getattr(metric_ir, "aggregation", None)),
        "status_time_dimension_ref": (
            RefPayloadV1.from_ref(Ref.time_dimension(status_path)).to_dict()
            if isinstance(status_path, str)
            else None
        ),
    }
