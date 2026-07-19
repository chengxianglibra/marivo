"""Materialize a semantic metric into a MetricFrame."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace
from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis._cumulative import (
    CUMULATIVE_CONTRACT_VERSION,
    normalize_cumulative_anchor,
)
from marivo.analysis.errors import (
    SemanticKindMismatchError,
    SliceEmptyResultError,
)
from marivo.analysis.evidence.identity import make_issue_id
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
)
from marivo.analysis.evidence.types import ArtifactIssue, DataQualityIssue
from marivo.analysis.executor.runner import (
    normalize_slice_for_storage,
)
from marivo.analysis.executor.windowing import (
    datasource_engine_profile,
)
from marivo.analysis.frames._meta_defaults import compute_analysis_scope
from marivo.analysis.frames.base import CURRENT_ARTIFACT_SCHEMA_VERSION
from marivo.analysis.frames.metric import MetricExecutionStatsV1, MetricFrame, MetricFrameMeta
from marivo.analysis.intents._metric_evaluators import align_metric_children_v1
from marivo.analysis.intents._metric_graph_execute import (
    component_graph_payload_v1,
    execute_metric_graph_observe,
    root_component_frame_v1,
)
from marivo.analysis.intents._metric_graph_plan import plan_metric_graph_observe
from marivo.analysis.intents._observe_base import (  # noqa: F401
    _execute_base,
    _execute_sampled_base,
    _expression_source_columns,
    _mean_component_contract,
    _prune_base_observe_projection,
    _resolve_fold_time_field,
    _time_dependency_exprs,
)
from marivo.analysis.intents._observe_catalog import (  # noqa: F401
    _build_entity_adapter,
    _catalog_id,
    _catalog_kind,
    _catalog_object,
    _DimensionIRAdapter,
    _entity_details,
    _EntityIRAdapter,
    _field_details,
    _fields_for_entity,
    _TimeFieldMetaAdapter,
)
from marivo.analysis.intents._observe_components import (  # noqa: F401
    _COMPONENT_AWARE_COMPOSITIONS,
    _DIVISION_DENOMINATOR_ROLES,
    _add_fold_metadata_to_component_df,
    _component_frame_df,
    _component_parent_columns,
    _composition_payload,
    _evaluate_composition_on_frame,
    _is_component_aware_composition,
    _require_component_role_column,
    _role_to_column_name,
)
from marivo.analysis.intents._observe_cumulative import (  # noqa: F401
    _MAX_TRAILING_DISTINCT_EXPANSION,
    _apply_where_to_raw_table,
    _base_aggregation_name,
    _base_measure_ref,
    _count_distinct_key_expr,
    _execute_cumulative,
    _execute_trailing_additive,
    _execute_trailing_distinct,
)
from marivo.analysis.intents._observe_dense import (  # noqa: F401
    _FIXED_GRAINS,
    _GRAIN_PANDAS_FREQ,
    _align_to_grain_start,
    _bucket_date_range,
    _dense_cumulative_frame,
    _fixed_grain_seconds_for_coverage,
    _grain_to_date_dense_frame,
    _require_grain_to_date_compat,
    _trailing_coverage_df,
    _trailing_rolling_frame,
    _trunc_series_to_grain,
)
from marivo.analysis.intents._observe_derived import _build_fold_meta
from marivo.analysis.intents._observe_inputs import (  # noqa: F401
    _analysis_axis_for_kind,
    _backend_for_datasource,
    _dump_dimensions,
    _entity_adapter_maps,
    _gen_ref,
    _metric_expr,
    _metric_planner_scope,
    _normalize_dimension_boundary,
    _normalize_dimension_list_boundary,
    _normalize_metric_boundary,
    _normalize_time_dimension_boundary,
    _normalize_where_boundary,
    _params_digest,
    _resolve_timescope,
    _Result,
    _validate_dimension_ids,
)
from marivo.analysis.intents._observe_persist import (
    _attach_metric_component_graph_ref,
    _attach_metric_component_ref,
    _commit_observe_metric_frame,
    _meta_additivity,
    _meta_aggregation,
    _metric_semantics_payload,
    _persist_and_attach_coverage_sidecar,
    _persist_metric_component_frame,
    _persist_metric_component_graph_frame,
    _persist_metric_graph_coverage_sidecars,
)
from marivo.analysis.intents._observe_planner_fields import _all_entity_ids
from marivo.analysis.intents._observe_planner_types import CumulativePhysicalLeafPlanV1
from marivo.analysis.intents._shape import SemanticShape, observe_output_shape
from marivo.analysis.intents.observe_planner import (
    _planned_metric,
)
from marivo.analysis.intents.sampled_fold import (
    quantile_capability,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.runtime_metric import (
    RuntimeAggregateExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    replay_payload,
)
from marivo.analysis.semantic_inputs import (
    AnalysisDimensionRef,
    ObserveMetricInput,
    normalize_metric_input,
)
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_job_record,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.slice_types import SliceValue
from marivo.analysis.windows.spec import (
    GrainInput,
    TimeScopeInput,
    dump_window,
)
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    SemanticKind,
    SimpleMetricDetails,
)
from marivo.semantic.ir import (
    CumulativeComposition,
    LinearComposition,
    RatioComposition,
    WeightedAverageComposition,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CatalogBodyLeafV1,
    CatalogMetricIdentity,
    ComparableValueSemanticsV1,
    CumulativeNodeV1,
    LinearNodeV1,
    MetricArtifactIdentityV1,
    MetricKeyFieldV1,
    MetricKeySchemaV1,
    RatioNodeV1,
    SliceNodeV1,
    WeightedAverageNodeV1,
    node_child_ids,
)
from marivo.semantic.metric_graph_canonical import canonical_value, fingerprint
from marivo.semantic.refs import MetricRef, TimeDimensionRef
from marivo.semantic.unit_algebra import UnknownUnitV2

# Symbols that remain importable from this module for ``derive`` /
# ``transform`` / ``frames._metric_projection`` / tests after
# extraction into private submodules. ``__all__`` also satisfies mypy's
# ``no_implicit_reexport``.
__all__ = [
    "_analysis_axis_for_kind",
    "_build_entity_adapter",
    "_catalog_object",
    "_commit_observe_metric_frame",
    "_dump_dimensions",
    "_entity_adapter_maps",
    "_entity_details",
    "_evaluate_composition_on_frame",
    "_field_details",
    "_gen_ref",
    "_meta_additivity",
    "_meta_aggregation",
    "_metric_expr",
    "_metric_planner_scope",
    "_normalize_dimension_boundary",
    "_normalize_dimension_list_boundary",
    "_normalize_time_dimension_boundary",
    "_normalize_where_boundary",
    "_params_digest",
    "_persist_and_attach_coverage_sidecar",
    "_resolve_timescope",
    "_validate_dimension_ids",
    "observe",
]
# attributes like ``fn``, ``fields``, ``is_time``, and ``time_meta``. These
# adapters are intentionally narrow: they are built from catalog details and
# call resolver.dimension_on(...), never SemanticProject sidecar callables.


# ---------------------------------------------------------------------------
# Observe intent
# ---------------------------------------------------------------------------


def _dataframe_snapshot_payload(df: Any) -> dict[str, Any]:
    """Return a deterministic value snapshot for one materialized node frame."""

    hashes = pd.util.hash_pandas_object(df, index=True, categorize=True)
    return {
        "columns": [str(column) for column in df.columns],
        "dtypes": [str(dtype) for dtype in df.dtypes],
        "row_hashes": [int(value) for value in hashes.tolist()],
    }


def _execution_snapshot_fingerprints(execution: Any) -> tuple[str, str]:
    """Fingerprint complete node values and node-local coverage after execution."""

    node_values = [
        {
            "node_id": node_id,
            "frame": _dataframe_snapshot_payload(result.frame),
        }
        for node_id, result in sorted(execution.nodes.items())
    ]
    node_coverage = [
        {
            "node_id": node_id,
            "coverage": _dataframe_snapshot_payload(result.coverage_df),
        }
        for node_id, result in sorted(execution.nodes.items())
        if result.coverage_df is not None
    ]
    return fingerprint(node_values), fingerprint(node_coverage)


def _unit_capability_issues(frame: MetricFrame, root_execution: Any) -> tuple[ArtifactIssue, ...]:
    if root_execution.unit_capability_issue is None or not isinstance(
        root_execution.unit_state, UnknownUnitV2
    ):
        return ()
    source_ref = f"{frame.ref}#{root_execution.node_id}"
    issue = DataQualityIssue(
        issue_id=make_issue_id(
            artifact_id=frame.ref,
            kind="unit_capability_unknown",
            source_refs=(source_ref,),
        ),
        kind="unit_capability_unknown",
        severity="warning",
        source_refs=(source_ref,),
        check_id="metric_unit_known",
        observed_value=root_execution.unit_capability_issue,
        expectation="metric unit is known before unit-dependent downstream analysis",
        evaluated_scope=compute_analysis_scope(frame),
    )
    return (issue,)


def _execution_stats(graph_plan: Any, execution: Any) -> MetricExecutionStatsV1:
    root_origins: tuple[Literal["catalog", "runtime"], ...] = tuple(
        "catalog" if isinstance(identity, CatalogMetricIdentity) else "runtime"
        for identity in graph_plan.forest.identities
    )
    blockers = tuple(
        sorted(
            {
                root.unit_capability_issue
                for root in execution.roots
                if root.unit_capability_issue is not None
            }
        )
    )
    return MetricExecutionStatsV1(
        root_origins=root_origins,
        physical_execution_count=execution.physical_execution_count,
        cse_reused_occurrences=max(
            0,
            len(graph_plan.graph.occurrences) - len(graph_plan.graph.nodes),
        ),
        downstream_blockers=blockers,
    )


def _mark_cache_hit(frame: MetricFrame) -> MetricFrame:
    stats = frame.meta.execution_stats
    if stats is not None:
        frame.meta = frame.meta.model_copy(
            update={
                "execution_stats": stats.model_copy(
                    update={
                        "cache_hit": True,
                        "artifact_deduplicated": False,
                        "physical_execution_count": 0,
                    }
                )
            }
        )
    return frame


def _mark_artifact_deduplicated(frame: MetricFrame) -> MetricFrame:
    """Mark post-execution artifact identity reuse without claiming a cache hit."""

    stats = frame.meta.execution_stats
    if stats is not None:
        frame.meta = frame.meta.model_copy(
            update={
                "execution_stats": stats.model_copy(
                    update={"cache_hit": False, "artifact_deduplicated": True}
                )
            }
        )
    return frame


def _observe_artifact_cache_key(
    *,
    graph_plan: Any,
    params: dict[str, Any],
    semantic_anchors: dict[str, Any],
) -> str:
    """Build the strict pre-execution key for snapshot-verified artifact reuse."""

    return fingerprint(
        {
            "graph": graph_plan.graph,
            "dependency_digest": graph_plan.forest.dependency_digest,
            "source_domain": graph_plan.source_domain,
            "params": params,
            "semantic_anchors": semantic_anchors,
        }
    )


def _lookup_snapshot_verified_artifact(
    *,
    session: Session,
    graph_plan: Any,
    cache_key: str,
) -> tuple[MetricFrame | None, str | None]:
    token = session._connection_runtime.source_snapshot_token(graph_plan.datasource_name)
    if token is None:
        return None, None
    artifact_ref = session._connection_runtime.cached_metric_artifact(cache_key, token)
    if artifact_ref is None or not frame_exists_on_disk(session._layout.frames_dir, artifact_ref):
        return None, token
    return cast("MetricFrame", load_frame(artifact_ref, session=session)), token


def _remember_snapshot_verified_artifact(
    *,
    session: Session,
    graph_plan: Any,
    cache_key: str,
    starting_token: str | None,
    artifact_ref: str,
) -> None:
    if starting_token is None:
        return
    finishing_token = session._connection_runtime.source_snapshot_token(graph_plan.datasource_name)
    if finishing_token != starting_token:
        return
    session._connection_runtime.remember_metric_artifact(
        cache_key,
        starting_token,
        artifact_ref,
    )


def _cumulative_leaf_marker(leaf: Any) -> dict[str, Any]:
    plan = cast("CumulativePhysicalLeafPlanV1", leaf.plan)
    return {
        "kind": "cumulative",
        "base": plan.base_metric_ir.semantic_id,
        "over": plan.composition.over,
        "anchor": plan.composition.anchor,
        "components": None,
    }


def _evaluator_contracts(graph_plan: Any) -> tuple[str, ...]:
    contracts: set[str] = set()
    for record in graph_plan.graph.nodes:
        node = record.node
        if isinstance(node, AggregateNodeV1 | CatalogBodyLeafV1):
            contracts.add("aggregate-evaluation/v1")
        elif isinstance(node, CumulativeNodeV1):
            contracts.add(f"cumulative-evaluation/v{CUMULATIVE_CONTRACT_VERSION}")
        elif isinstance(node, SliceNodeV1):
            contracts.add("slice-evaluation/v1")
        elif isinstance(node, RatioNodeV1):
            contracts.add("ratio-evaluation/v1")
        elif isinstance(node, WeightedAverageNodeV1):
            contracts.add("weighted-average-evaluation/v1")
        elif isinstance(node, LinearNodeV1):
            contracts.add("linear-evaluation/v1")
    return tuple(sorted(contracts))


def _catalog_cumulative_marker(catalog: Any, metric_id: str) -> dict[str, Any] | None:
    metric = catalog._require_index().registry.metrics[metric_id]
    composition = metric.composition
    if isinstance(composition, CumulativeComposition):
        return {
            "kind": "cumulative",
            "base": composition.base,
            "over": composition.over,
            "anchor": composition.anchor,
            "components": None,
        }
    branches: tuple[tuple[str, str], ...]
    if isinstance(composition, RatioComposition):
        branches = (("numerator", composition.numerator), ("denominator", composition.denominator))
    elif isinstance(composition, WeightedAverageComposition):
        branches = (("value", composition.value), ("weight", composition.weight))
    elif isinstance(composition, LinearComposition):
        branches = tuple(
            (f"term{index}", term.metric) for index, term in enumerate(composition.terms)
        )
    else:
        return None
    components: dict[str, dict[str, Any]] = {}
    non_cumulative_roles: list[str] = []
    for role, component_id in branches:
        marker = _catalog_cumulative_marker(catalog, component_id)
        if marker is None:
            non_cumulative_roles.append(role)
        else:
            components[role] = marker
    if not components:
        return None
    anchors = [normalize_cumulative_anchor(value.get("anchor")) for value in components.values()]
    nested_blocker = next(
        (
            value.get("compare_blocker")
            for value in components.values()
            if value.get("compare_blocker")
        ),
        None,
    )
    if non_cumulative_roles:
        blocker = "non_cumulative_component"
        common_anchor = None
    elif nested_blocker is not None:
        blocker = nested_blocker
        common_anchor = None
    elif any(anchor is None for anchor in anchors):
        blocker = "unresolved_component_anchor"
        common_anchor = None
    elif anchors and any(anchor != anchors[0] for anchor in anchors[1:]):
        blocker = "mixed_component_anchors"
        common_anchor = None
    else:
        blocker = None
        common_anchor = anchors[0] if anchors else None
    return {
        "kind": "derived_contains_cumulative",
        "anchor": common_anchor,
        "compare_blocker": blocker,
        "components": components,
    }


def _cumulative_graph_marker(
    graph_plan: Any,
    *,
    catalog: Any,
) -> dict[str, Any] | None:
    """Project recursive cumulative state into the stable frame-level summary."""

    identity = graph_plan.forest.identities[0]
    if isinstance(identity, CatalogMetricIdentity):
        return _catalog_cumulative_marker(catalog, identity.metric_id)

    cumulative_leaves = {
        leaf.node_id: leaf
        for leaf in graph_plan.leaves
        if isinstance(leaf.plan, CumulativePhysicalLeafPlanV1)
    }
    if not cumulative_leaves:
        return None
    physical_leaf_ids = {leaf.node_id for leaf in graph_plan.leaves}
    root_id = graph_plan.graph.roots[0]
    if root_id in cumulative_leaves:
        return _cumulative_leaf_marker(cumulative_leaves[root_id])

    nodes = {record.node_id: record.node for record in graph_plan.graph.nodes}
    root = nodes[root_id]
    branches: tuple[tuple[str, str], ...]
    if isinstance(root, RatioNodeV1):
        branches = (("numerator", root.numerator_id), ("denominator", root.denominator_id))
    elif isinstance(root, WeightedAverageNodeV1):
        branches = (("value", root.value_id), ("weight", root.weight_id))
    elif isinstance(root, LinearNodeV1):
        branches = tuple((f"term{index}", term.child_id) for index, term in enumerate(root.terms))
    else:
        branches = (("root", root_id),)

    def reachable_physical(node_id: str) -> set[str]:
        if node_id in physical_leaf_ids:
            return {node_id}
        node = nodes[node_id]
        children = node_child_ids(node)
        if not children:
            return {node_id}
        result: set[str] = set()
        for child_id in children:
            result.update(reachable_physical(child_id))
        return result

    components: dict[str, dict[str, Any]] = {}
    non_cumulative_roles: list[str] = []
    for role, child_id in branches:
        physical_ids = reachable_physical(child_id)
        branch_cumulative = [
            cumulative_leaves[node_id] for node_id in physical_ids if node_id in cumulative_leaves
        ]
        if len(branch_cumulative) == 1 and len(physical_ids) == 1:
            components[role] = _cumulative_leaf_marker(branch_cumulative[0])
        else:
            non_cumulative_roles.append(role)

    anchors = [normalize_cumulative_anchor(value.get("anchor")) for value in components.values()]
    if non_cumulative_roles:
        blocker = "non_cumulative_component"
        common_anchor = None
    elif any(anchor is None for anchor in anchors):
        blocker = "unresolved_component_anchor"
        common_anchor = None
    elif anchors and any(anchor != anchors[0] for anchor in anchors[1:]):
        blocker = "mixed_component_anchors"
        common_anchor = None
    else:
        blocker = None
        common_anchor = anchors[0] if anchors else None
    return {
        "kind": "derived_contains_cumulative",
        "anchor": common_anchor,
        "compare_blocker": blocker,
        "components": components,
    }


def observe(
    metric: ObserveMetricInput | list[ObserveMetricInput] | tuple[ObserveMetricInput, ...],
    *,
    time_scope: TimeScopeInput = None,
    grain: GrainInput = None,
    dimensions: list[AnalysisDimensionRef] | None = None,
    slice_by: Mapping[AnalysisDimensionRef, SliceValue] | None = None,
    time_dimension: TimeDimensionRef | None = None,
    expect_shape: SemanticShape | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> MetricFrame:
    if isinstance(metric, (list, tuple)):
        metric_items: list[ObserveMetricInput] = list(metric)
        if not metric_items:
            raise SemanticKindMismatchError(
                message="observe requires at least one metric",
                context={"argument": "metric", "got": "empty sequence"},
            )
        if len(metric_items) > 1:
            return _observe_metric_forest(
                tuple(metric_items),
                time_scope=time_scope,
                grain=grain,
                dimensions=dimensions,
                slice_by=slice_by,
                time_dimension=time_dimension,
                expect_shape=expect_shape,
                analysis_purpose=analysis_purpose,
                session=session,
            )
        single_metric: ObserveMetricInput = metric_items[0]
    else:
        single_metric = metric
    if session is None:
        session = require_current_session()
    ensure_session_writable(session)
    catalog = session.catalog
    catalog._require_index()
    metric_ir: Any
    is_catalog_root = isinstance(single_metric, MetricRef)
    if is_catalog_root:
        assert isinstance(single_metric, MetricRef)
        metric_id = _normalize_metric_boundary(catalog, single_metric)
        model_name, metric_name = metric_id.split(".", 1)
        metric_details = _catalog_object(catalog, metric_id, SemanticKind.METRIC).details()
        assert isinstance(metric_details, (SimpleMetricDetails, DerivedMetricDetails))
        metric_ir = _planned_metric(metric_details)
        planner_scope = _metric_planner_scope(catalog, metric_ir)
    elif isinstance(single_metric, RuntimeAggregateExpr | RuntimeSliceExpr | RuntimeRatioExpr):
        metric_id = "runtime.pending"
        model_name = "runtime"
        metric_name = single_metric.label or "runtime_metric"
        metric_ir = SimpleNamespace(
            semantic_id=metric_id,
            name=metric_name,
            domain=model_name,
            metric_type="runtime",
            entities=(),
            aggregation=None,
            additivity="non_additive",
            status_time_dimension=None,
            time_fold=None,
            composition=None,
            unit=None,
        )
        planner_scope = set()
    else:
        raise SemanticKindMismatchError(
            message=(
                "metric requires exact MetricRef or RuntimeMetricExpr; "
                f"got {type(single_metric).__name__}."
            ),
            hint="Pass loaded_metric.ref or a value returned by mv.runtime_metric.*.",
            context={
                "argument": "metric",
                "expected_type": "MetricRef or RuntimeMetricExpr",
                "actual_type": type(single_metric).__name__,
                "repair": "Use loaded_metric.ref.",
            },
        )
    time_dimension_id = (
        _normalize_time_dimension_boundary(catalog, time_dimension)
        if time_dimension is not None
        else None
    )
    where_by_id = _normalize_where_boundary(catalog, slice_by, scoped_entity_refs=planner_scope)
    dimension_ids = _normalize_dimension_list_boundary(
        catalog,
        dimensions,
        scoped_entity_refs=planner_scope,
    )
    resolver = catalog._resolver(connections=session._connection_runtime)
    resolved_window, original_timescope = _resolve_timescope(
        time_scope,
        grain=grain,
        time_dimension=time_dimension_id,
    )
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    # For semi-additive metrics, inject status_time_dimension into the window if
    # not already specified so downstream resolution picks the status axis.
    if (
        getattr(metric_ir, "additivity", None) == "semi_additive"
        and metric_ir.status_time_dimension is not None
        and time_dimension_id is None
        and resolved_window is not None
        and resolved_window.time_dimension is None
    ):
        resolved_window, original_timescope = _resolve_timescope(
            time_scope,
            grain=grain,
            time_dimension=metric_ir.status_time_dimension,
        )

    # For derived metrics with semi-additive components, inject the first
    # component's status_time_dimension so the planner resolves the status axis.
    if (
        metric_ir.metric_type == "derived"
        and time_dimension_id is None
        and resolved_window is not None
        and resolved_window.time_dimension is None
    ):
        for _role, _comp_id in metric_ir.composition.components.items():
            _comp_details = _catalog_object(catalog, _comp_id, SemanticKind.METRIC).details()
            assert isinstance(_comp_details, (SimpleMetricDetails, DerivedMetricDetails))
            _comp_ir = _planned_metric(_comp_details)
            if (
                getattr(_comp_ir, "additivity", None) == "semi_additive"
                and getattr(_comp_ir, "status_time_dimension", None) is not None
            ):
                resolved_window, original_timescope = _resolve_timescope(
                    time_scope,
                    grain=grain,
                    time_dimension=_comp_ir.status_time_dimension,
                )
                break

    planner_time_dimension_id = (
        resolved_window.time_dimension if resolved_window is not None else time_dimension_id
    )

    started_at = datetime.now(UTC)
    started = monotonic()
    stored_where = normalize_slice_for_storage(where_by_id)
    dimension_refs = _validate_dimension_ids(dimension_ids)
    if expect_shape is not None:
        predicted_shape = observe_output_shape(
            has_grain=is_time_series, has_dimensions=bool(dimension_refs)
        )
        if predicted_shape != expect_shape:
            raise SemanticKindMismatchError(
                message=(
                    f"observe will produce semantic_shape {predicted_shape!r} for these "
                    f"inputs, but expect_shape={expect_shape!r} was requested"
                ),
                context={
                    "intent": "observe",
                    "predicted_semantic_shape": predicted_shape,
                    "expect_shape": expect_shape,
                },
            )
    if metric_ir.metric_type in {"simple", "derived", "runtime"}:
        all_entity_refs = _all_entity_ids(catalog)
        _, _, all_dataset_irs, all_dataset_fns = _entity_adapter_maps(
            catalog=catalog,
            resolver=resolver,
            entity_refs=all_entity_refs,
        )
        session._connection_runtime.begin_query_capture()
        try:
            graph_plan = plan_metric_graph_observe(
                catalog=catalog,
                session=session,
                metric_inputs=(single_metric,),
                dataset_irs=all_dataset_irs,
                dataset_fns=all_dataset_fns,
                dimensions=dimension_refs,
                where=where_by_id,
                resolved_window=resolved_window,
                time_dimension=planner_time_dimension_id,
            )
            if not is_catalog_root:
                registry = catalog._require_index().registry
                leaf_domains = {
                    registry.entities[base_plan.root_entity].domain
                    for leaf in graph_plan.leaves
                    for base_plan in (
                        leaf.plan.base_plan if hasattr(leaf.plan, "base_plan") else leaf.plan,
                    )
                }
                if len(leaf_domains) != 1:
                    raise SemanticKindMismatchError(
                        message="Runtime metric expressions must resolve to one semantic model.",
                        context={"models": sorted(leaf_domains)},
                    )
                model_name = next(iter(leaf_domains))
                metric_id = f"runtime:{graph_plan.graph.roots[0]}"
                root_node_for_ir = {
                    record.node_id: record.node for record in graph_plan.graph.nodes
                }[graph_plan.graph.roots[0]]
                root_leaf = next(
                    (
                        leaf
                        for leaf in graph_plan.leaves
                        if leaf.node_id == graph_plan.graph.roots[0]
                    ),
                    None,
                )
                if root_leaf is not None:
                    metric_ir = root_leaf.metric_ir
                    metric_name = getattr(single_metric, "label", None) or metric_ir.name
                elif isinstance(root_node_for_ir, RatioNodeV1):
                    metric_ir = SimpleNamespace(
                        semantic_id=metric_id,
                        name=metric_name,
                        domain=model_name,
                        metric_type="runtime",
                        entities=(),
                        aggregation=None,
                        additivity="non_additive",
                        status_time_dimension=None,
                        time_fold=None,
                        unit=None,
                        composition=SimpleNamespace(
                            kind="ratio",
                            components={
                                "numerator": root_node_for_ir.numerator_id,
                                "denominator": root_node_for_ir.denominator_id,
                            },
                        ),
                    )
            graph_nodes = {record.node_id: record.node for record in graph_plan.graph.nodes}
            cumulative_meta = _cumulative_graph_marker(graph_plan, catalog=catalog)
            params_timescope = None
            if resolved_window is not None:
                params_timescope = {
                    "original": original_timescope,
                    "resolved": dump_window(resolved_window),
                    "report_tz": session.report_tz_name,
                }
            version_resolutions = []
            for leaf in graph_plan.leaves:
                base_plan = leaf.plan.base_plan if hasattr(leaf.plan, "base_plan") else leaf.plan
                version_resolutions.extend(
                    base_plan.lineage_metadata.get("version_resolutions", [])
                )
            params = {
                "metric": metric_id,
                "replay_expression": replay_payload(single_metric),
                "timescope": params_timescope,
                "dimensions": _dump_dimensions(dimension_refs),
                "where": stored_where,
                "metric_graph": canonical_value(graph_plan.graph),
                "semantic_dependency_digest": canonical_value(graph_plan.forest.dependency_digest),
                "presentation": canonical_value(graph_plan.forest.presentation),
                "datasource_compatibility_domain": graph_plan.datasource_name,
                "version_resolutions": version_resolutions,
                "warnings": list(graph_plan.warnings),
                "lineage_metadata": graph_plan.lineage_metadata,
                "metric_semantics": _metric_semantics_payload(metric_ir),
            }
            root_leaf_lineage = (
                graph_plan.lineage_metadata["physical_leaves"][0]["lineage_metadata"]
                if graph_plan.lineage_metadata["physical_leaves"]
                else {}
            )
            params.update(
                {
                    "relationships": root_leaf_lineage.get("relationships") or [],
                    "fanout_policy": root_leaf_lineage.get("fanout_policy"),
                    "fanouts": root_leaf_lineage.get("fanouts") or [],
                }
            )
            mean_component_contract = _mean_component_contract(metric_ir)
            if mean_component_contract is not None:
                params["component_lowering"] = mean_component_contract
            if cumulative_meta is not None:
                params["cumulative_contract_version"] = CUMULATIVE_CONTRACT_VERSION
                params["cumulative"] = cumulative_meta
            if any(isinstance(node, RatioNodeV1) for node in graph_nodes.values()):
                params["zero_division"] = "null"
            semantic_anchors = {"metric_id": metric_id, "model": model_name}
            artifact_cache_key = _observe_artifact_cache_key(
                graph_plan=graph_plan,
                params=params,
                semantic_anchors=semantic_anchors,
            )
            cached_frame, starting_snapshot_token = _lookup_snapshot_verified_artifact(
                session=session,
                graph_plan=graph_plan,
                cache_key=artifact_cache_key,
            )
            if cached_frame is not None:
                session._connection_runtime.take_captured_queries()
                _raise_on_empty_slice_result(cached_frame, where_by_id)
                return _mark_cache_hit(cached_frame)
            graph_execution = execute_metric_graph_observe(
                graph_plan,
                catalog=catalog,
                resolver=resolver,
                session=session,
                resolved_window=resolved_window,
            )
        except BaseException:
            session._connection_runtime.take_captured_queries()
            raise
        captured_queries = session._connection_runtime.take_captured_queries()
        snapshot_fingerprint, coverage_fingerprint = _execution_snapshot_fingerprints(
            graph_execution
        )
        params["snapshot_fingerprint"] = snapshot_fingerprint
        params["coverage_fingerprint"] = coverage_fingerprint
        prospective_id = compute_prospective_artifact_id(
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(values=semantic_anchors),
        )
        if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
            cached_frame = cast("MetricFrame", load_frame(prospective_id, session=session))
            _remember_snapshot_verified_artifact(
                session=session,
                graph_plan=graph_plan,
                cache_key=artifact_cache_key,
                starting_token=starting_snapshot_token,
                artifact_ref=prospective_id,
            )
            _raise_on_empty_slice_result(cached_frame, where_by_id)
            return _mark_artifact_deduplicated(cached_frame)
        finished_at = datetime.now(UTC)
        # The evidence artifact id is already deterministic at this point. Use
        # it before persisting sidecars so they retain the final parent ref.
        frame_ref = prospective_id
        job_ref = _gen_ref("job")
        root_execution = graph_execution.roots[0]
        folded_leaves = [
            leaf
            for leaf in graph_plan.leaves
            if getattr(leaf.metric_ir, "time_fold", None) is not None
        ]
        fold_meta = None
        if folded_leaves:
            if is_catalog_root and metric_ir.metric_type == "simple":
                fold_meta = _build_fold_meta(metric_ir, catalog)
            else:
                fold_meta = {
                    "time_fold": "derived",
                    "component_folds": [
                        {
                            "component_metric_id": leaf.metric_id,
                            "time_fold": leaf.metric_ir.time_fold.label(),
                            "fold_kind": leaf.metric_ir.time_fold.kind,
                            "status_time_dimension": leaf.metric_ir.status_time_dimension,
                        }
                        for leaf in folded_leaves
                    ],
                    "sample_interval": None,
                }
        metric_identity = graph_plan.forest.identities[0]
        presentation_fingerprint = fingerprint(graph_plan.forest.presentation)
        scope_fingerprint = fingerprint(
            {
                "timescope": params_timescope,
                "dimensions": _dump_dimensions(dimension_refs),
                "where": stored_where,
                "report_tz": session.report_tz_name,
            }
        )
        key_fields = tuple(
            MetricKeyFieldV1(
                name=column,
                dtype=str(root_execution.frame[column].dtype),
                # Key nullability is a stable contract, not a fact inferred
                # from one observed window.  Composite outer alignment and
                # nullable source dimensions can both produce null keys even
                # when the current result happens not to contain one.
                nullable=True,
            )
            for column in root_execution.key_columns
        )
        key_schema = MetricKeySchemaV1(
            schema="metric-key-schema/v1",
            fields=key_fields,
            fingerprint=fingerprint(key_fields),
        )
        comparable_global_slice = tuple(
            (key, fingerprint(value)) for key, value in sorted(stored_where.items())
        )
        comparable_fold = fingerprint(fold_meta) if fold_meta is not None else None
        comparable_payload = {
            "expression_fingerprint": graph_plan.graph.roots[0],
            "evaluator_contracts": _evaluator_contracts(graph_plan),
            "global_slice": comparable_global_slice,
            "key_schema_fingerprint": key_schema.fingerprint,
            "unit": root_execution.unit,
            "fold": comparable_fold,
            "source_domain_fingerprint": graph_plan.source_domain.profile_fingerprint,
            "definition_transform_fingerprint": None,
        }
        comparable_semantics = ComparableValueSemanticsV1(
            schema="comparable-value-semantics/v1",
            expression_fingerprint=graph_plan.graph.roots[0],
            evaluator_contracts=_evaluator_contracts(graph_plan),
            global_slice=comparable_global_slice,
            key_schema_fingerprint=key_schema.fingerprint,
            unit=root_execution.unit,
            fold=comparable_fold,
            source_domain_fingerprint=graph_plan.source_domain.profile_fingerprint,
            definition_transform_fingerprint=None,
            fingerprint=fingerprint(comparable_payload),
        )
        artifact_identity_payload = {
            "metric_identities": (metric_identity,),
            "scope_fingerprint": scope_fingerprint,
            "source_domain_fingerprint": graph_plan.source_domain.profile_fingerprint,
            "dependency_fingerprint": graph_plan.forest.dependency_digest.fingerprint,
            "snapshot_fingerprint": snapshot_fingerprint,
            "coverage_fingerprint": coverage_fingerprint,
            "presentation_fingerprint": presentation_fingerprint,
            "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
        }
        artifact_identity = MetricArtifactIdentityV1(
            schema="metric-artifact/v1",
            metric_identities=(metric_identity,),
            scope_fingerprint=scope_fingerprint,
            source_domain_fingerprint=graph_plan.source_domain.profile_fingerprint,
            dependency_fingerprint=graph_plan.forest.dependency_digest.fingerprint,
            snapshot_fingerprint=snapshot_fingerprint,
            coverage_fingerprint=coverage_fingerprint,
            presentation_fingerprint=presentation_fingerprint,
            artifact_schema_version=CURRENT_ARTIFACT_SCHEMA_VERSION,
            fingerprint=fingerprint(artifact_identity_payload),
        )
        quantile_mode = None
        quantile_method = None
        time_fold = getattr(metric_ir, "time_fold", None)
        if time_fold is not None and time_fold.kind == "percentile":
            capability = quantile_capability(
                datasource_engine_profile(
                    session._connection_runtime,
                    graph_plan.datasource_name,
                )
            )
            quantile_mode = capability.mode
            quantile_method = capability.method
        meta = MetricFrameMeta(
            kind="metric_frame",
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(root_execution.frame),
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="observe",
                        job_ref=job_ref,
                        inputs=[],
                        params_digest=_params_digest(params),
                        analysis_purpose=analysis_purpose,
                        params=params,
                    )
                ]
            ),
            metric_id=metric_id,
            metric_identity=metric_identity,
            metric_identities=(metric_identity,),
            expression_graph=graph_plan.graph,
            expression_fingerprint=graph_plan.graph.roots[0],
            semantic_dependency_digest=graph_plan.forest.dependency_digest,
            presentation=graph_plan.forest.presentation,
            presentation_fingerprint=presentation_fingerprint,
            artifact_identity=artifact_identity,
            key_schema=key_schema,
            source_compatibility_domain=graph_plan.source_domain,
            comparable_value_semantics=comparable_semantics,
            execution_stats=_execution_stats(graph_plan, graph_execution),
            axes=root_execution.axes,
            measure={"name": metric_name},
            window=dump_window(resolved_window),
            where=stored_where,
            semantic_kind=root_execution.semantic_kind,
            semantic_model=model_name,
            unit=root_execution.unit,
            unit_state=root_execution.unit_state,
            fold=fold_meta,
            reaggregatable=fold_meta is None and cumulative_meta is None,
            additivity=_meta_additivity(root_execution.additivity),
            aggregation=_meta_aggregation(metric_ir.aggregation),
            status_time_dimension=metric_ir.status_time_dimension,
            cumulative=cumulative_meta,
            zero_denominator_rows=root_execution.quality.zero_division_rows,
            rollup_fold=(
                "last"
                if cumulative_meta is not None and cumulative_meta["kind"] == "cumulative"
                else None
            ),
            quantile_mode=quantile_mode,
            quantile_method=quantile_method,
        )
        frame = MetricFrame(_df=root_execution.frame, meta=meta)
        frame.meta = frame.meta.model_copy(
            update={"issues": _unit_capability_issues(frame, root_execution)}
        )
        grain_token = (
            resolved_window.grain.to_token()
            if resolved_window is not None and resolved_window.grain is not None
            else None
        )
        if root_execution.coverage_df is not None:
            frame = _persist_and_attach_coverage_sidecar(
                session=session,
                df=root_execution.coverage_df,
                parent=frame,
                job_ref=job_ref,
                persist_parent=False,
            )
        coverage_refs = (
            {graph_plan.graph.roots[0]: frame.meta.coverage_ref}
            if frame.meta.coverage_ref is not None
            else {}
        )
        coverage_refs = _persist_metric_graph_coverage_sidecars(
            session=session,
            parent=frame,
            execution=graph_execution,
            job_ref=job_ref,
            existing_refs=coverage_refs,
        )
        component_df = root_component_frame_v1(
            graph_execution,
            graph_plan,
            root_index=0,
            metric_ir=metric_ir,
        )
        component_graph = component_graph_payload_v1(
            graph_execution,
            graph_plan,
            coverage_refs=coverage_refs,
        )
        if component_df is not None:
            component = _persist_metric_component_frame(
                session=session,
                df=component_df,
                parent=frame,
                metric_ir=metric_ir,
                axes=root_execution.axes,
                semantic_kind=root_execution.semantic_kind,
                job_ref=job_ref,
                component_graph=component_graph,
            )
            frame = _attach_metric_component_ref(
                session=session,
                parent=frame,
                component=component,
                metric_ir=metric_ir,
                persist_parent=False,
            )
        elif root_execution.aggregate_component_df is not None:
            mean_contract = _mean_component_contract(metric_ir)
            if mean_contract is not None:
                mean_components = mean_contract["components"]
                assert isinstance(mean_components, dict)
                component = _persist_metric_component_frame(
                    session=session,
                    df=root_execution.aggregate_component_df,
                    parent=frame,
                    metric_ir=metric_ir,
                    axes=root_execution.axes,
                    semantic_kind=root_execution.semantic_kind,
                    job_ref=job_ref,
                    composition_kind="weighted_average",
                    components={str(role): str(value) for role, value in mean_components.items()},
                    component_graph=component_graph,
                )
                frame = _attach_metric_component_ref(
                    session=session,
                    parent=frame,
                    component=component,
                    metric_ir=metric_ir,
                    composition=mean_contract,
                    persist_parent=False,
                )
            else:
                component = _persist_metric_component_graph_frame(
                    session=session,
                    df=root_execution.frame,
                    parent=frame,
                    axes=root_execution.axes,
                    semantic_kind=root_execution.semantic_kind,
                    job_ref=job_ref,
                    component_graph=component_graph,
                )
                frame = _attach_metric_component_graph_ref(
                    session=session,
                    parent=frame,
                    component=component,
                    persist_parent=False,
                )
        else:
            component = _persist_metric_component_graph_frame(
                session=session,
                df=root_execution.frame,
                parent=frame,
                axes=root_execution.axes,
                semantic_kind=root_execution.semantic_kind,
                job_ref=job_ref,
                component_graph=component_graph,
            )
            frame = _attach_metric_component_graph_ref(
                session=session,
                parent=frame,
                component=component,
                persist_parent=False,
            )
        frame = _commit_observe_metric_frame(
            session=session,
            frame=frame,
            params=params,
            metric_id=metric_id,
            model_name=model_name,
            stored_where=stored_where,
            semantic_kind=root_execution.semantic_kind,
            subject_grain=grain_token,
        )
        _output_ref = frame.meta.artifact_id or frame.ref
        persist_job_record(
            session,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "observe",
                "analysis_purpose": analysis_purpose,
                "params": params,
                "input_frame_refs": [],
                "output_frame_ref": _output_ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": str(session.catalog.semantic_root),
                "semantic_model": model_name,
                "queries": [{**qe.to_dict(), "output_ref": _output_ref} for qe in captured_queries],
            },
        )
        _remember_snapshot_verified_artifact(
            session=session,
            graph_plan=graph_plan,
            cache_key=artifact_cache_key,
            starting_token=starting_snapshot_token,
            artifact_ref=frame.ref,
        )
        _raise_on_empty_slice_result(frame, where_by_id)
        return frame

    raise AssertionError(f"unsupported planned metric type {metric_ir.metric_type!r}")


def _forest_output_columns(
    metric_inputs: tuple[ObserveMetricInput, ...],
    identities: tuple[Any, ...],
) -> list[str]:
    requested: list[str] = []
    for index, (metric_input, identity) in enumerate(zip(metric_inputs, identities, strict=True)):
        if isinstance(identity, CatalogMetricIdentity):
            requested.append(identity.metric_id.rsplit(".", 1)[-1])
        else:
            requested.append(getattr(metric_input, "label", None) or f"runtime_metric_{index + 1}")
    counts: dict[str, int] = {}
    result: list[str] = []
    for name in requested:
        counts[name] = counts.get(name, 0) + 1
        result.append(name if counts[name] == 1 else f"{name}_{counts[name]}")
    return result


def _observe_metric_forest(
    metric_inputs: tuple[ObserveMetricInput, ...],
    *,
    time_scope: TimeScopeInput,
    grain: GrainInput,
    dimensions: list[AnalysisDimensionRef] | None,
    slice_by: Mapping[AnalysisDimensionRef, SliceValue] | None,
    time_dimension: TimeDimensionRef | None,
    expect_shape: SemanticShape | None,
    analysis_purpose: str | None,
    session: Session | None,
) -> MetricFrame:
    """Materialize one arity-N catalog/runtime forest through the shared graph."""
    if session is None:
        session = require_current_session()
    ensure_session_writable(session)
    catalog = session.catalog
    catalog._require_index()
    for metric_input in metric_inputs:
        if isinstance(metric_input, MetricRef):
            normalize_metric_input(catalog, metric_input)
        elif not isinstance(
            metric_input, RuntimeAggregateExpr | RuntimeSliceExpr | RuntimeRatioExpr
        ):
            raise SemanticKindMismatchError(
                message=(
                    "observe metric sequences require exact MetricRef or RuntimeMetricExpr "
                    f"items; got {type(metric_input).__name__}."
                ),
                context={"argument": "metric", "actual_type": type(metric_input).__name__},
            )
    time_dimension_id = (
        _normalize_time_dimension_boundary(catalog, time_dimension)
        if time_dimension is not None
        else None
    )
    dimension_ids = _normalize_dimension_list_boundary(
        catalog,
        dimensions,
        scoped_entity_refs=set(),
    )
    dimension_refs = _validate_dimension_ids(dimension_ids)
    where_by_id = _normalize_where_boundary(catalog, slice_by, scoped_entity_refs=set())
    stored_where = normalize_slice_for_storage(where_by_id)
    resolved_window, original_timescope = _resolve_timescope(
        time_scope,
        grain=grain,
        time_dimension=time_dimension_id,
    )
    is_time_series = resolved_window is not None and resolved_window.grain is not None
    if expect_shape is not None:
        predicted_shape = observe_output_shape(
            has_grain=is_time_series,
            has_dimensions=bool(dimension_refs),
        )
        if predicted_shape != expect_shape:
            raise SemanticKindMismatchError(
                message=(
                    f"observe will produce semantic_shape {predicted_shape!r}, "
                    f"but expect_shape={expect_shape!r} was requested"
                ),
                context={
                    "predicted_semantic_shape": predicted_shape,
                    "expect_shape": expect_shape,
                },
            )
    resolver = catalog._resolver(connections=session._connection_runtime)
    all_entity_refs = _all_entity_ids(catalog)
    _, _, all_dataset_irs, all_dataset_fns = _entity_adapter_maps(
        catalog=catalog,
        resolver=resolver,
        entity_refs=all_entity_refs,
    )
    started_at = datetime.now(UTC)
    started = monotonic()
    session._connection_runtime.begin_query_capture()
    try:
        graph_plan = plan_metric_graph_observe(
            catalog=catalog,
            session=session,
            metric_inputs=metric_inputs,
            dataset_irs=all_dataset_irs,
            dataset_fns=all_dataset_fns,
            dimensions=dimension_refs,
            where=where_by_id,
            resolved_window=resolved_window,
            time_dimension=(
                resolved_window.time_dimension if resolved_window is not None else time_dimension_id
            ),
        )
        registry = catalog._require_index().registry
        models = {
            registry.entities[base_plan.root_entity].domain
            for leaf in graph_plan.leaves
            for base_plan in (
                leaf.plan.base_plan if hasattr(leaf.plan, "base_plan") else leaf.plan,
            )
        }
        if len(models) != 1:
            raise SemanticKindMismatchError(
                message="A metric expression forest must resolve to one semantic model.",
                context={"models": sorted(models)},
            )
        model_name = next(iter(models))
        params_timescope = (
            {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "report_tz": session.report_tz_name,
            }
            if resolved_window is not None
            else None
        )
        output_columns = _forest_output_columns(metric_inputs, graph_plan.forest.identities)
        params = {
            "metric_identities": canonical_value(graph_plan.forest.identities),
            "replay_expressions": [replay_payload(item) for item in metric_inputs],
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimension_refs),
            "where": stored_where,
            "metric_graph": canonical_value(graph_plan.graph),
            "semantic_dependency_digest": canonical_value(graph_plan.forest.dependency_digest),
            "presentation": canonical_value(graph_plan.forest.presentation),
            "datasource_compatibility_domain": canonical_value(graph_plan.source_domain),
            "lineage_metadata": graph_plan.lineage_metadata,
            "warnings": list(graph_plan.warnings),
            "output_columns": output_columns,
        }
        anchors = {
            "metric_identities": canonical_value(graph_plan.forest.identities),
            "model": model_name,
        }
        artifact_cache_key = _observe_artifact_cache_key(
            graph_plan=graph_plan,
            params=params,
            semantic_anchors=anchors,
        )
        cached_frame, starting_snapshot_token = _lookup_snapshot_verified_artifact(
            session=session,
            graph_plan=graph_plan,
            cache_key=artifact_cache_key,
        )
        if cached_frame is not None:
            session._connection_runtime.take_captured_queries()
            return _mark_cache_hit(cached_frame)
        execution = execute_metric_graph_observe(
            graph_plan,
            catalog=catalog,
            resolver=resolver,
            session=session,
            resolved_window=resolved_window,
        )
    except BaseException:
        session._connection_runtime.take_captured_queries()
        raise
    captured_queries = session._connection_runtime.take_captured_queries()
    snapshot_fingerprint, coverage_fingerprint = _execution_snapshot_fingerprints(execution)
    params["snapshot_fingerprint"] = snapshot_fingerprint
    params["coverage_fingerprint"] = coverage_fingerprint
    prospective_id = compute_prospective_artifact_id(
        step_type="observe",
        inputs=CommitInputs(input_refs=[]),
        params=CommitParams(values=params),
        semantic_anchors=CommitSemanticAnchors(values=anchors),
    )
    if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
        _remember_snapshot_verified_artifact(
            session=session,
            graph_plan=graph_plan,
            cache_key=artifact_cache_key,
            starting_token=starting_snapshot_token,
            artifact_ref=prospective_id,
        )
        return _mark_artifact_deduplicated(
            cast("MetricFrame", load_frame(prospective_id, session=session))
        )
    first_root = execution.roots[0]
    for root in execution.roots[1:]:
        if (
            root.key_columns != first_root.key_columns
            or root.semantic_kind != first_root.semantic_kind
        ):
            raise SemanticKindMismatchError(
                message="All observed metric roots must share one output shape and axis schema.",
                context={
                    "root_node_id": root.node_id,
                    "expected_key_columns": first_root.key_columns,
                    "actual_key_columns": root.key_columns,
                },
            )
    aligned, key_columns, _alignment_quality = align_metric_children_v1(
        tuple((f"root{index}", root.frame) for index, root in enumerate(execution.roots))
    )
    merged = aligned[list(key_columns)].copy() if key_columns else aligned.iloc[:, 0:0].copy()
    for index, output_column in enumerate(output_columns):
        merged[output_column] = aligned[f"__marivo_value_root{index}"]
    finished_at = datetime.now(UTC)
    # Bind sidecars to the final evidence identity, not a disposable build ref.
    frame_ref = prospective_id
    job_ref = _gen_ref("job")
    expression_fingerprint = fingerprint(graph_plan.graph.roots)
    presentation_fingerprint = fingerprint(graph_plan.forest.presentation)
    scope_fingerprint = fingerprint(
        {
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimension_refs),
            "where": stored_where,
            "report_tz": session.report_tz_name,
        }
    )
    key_fields = tuple(
        MetricKeyFieldV1(
            name=column,
            dtype=str(merged[column].dtype),
            nullable=True,
        )
        for column in key_columns
    )
    key_schema = MetricKeySchemaV1(
        schema="metric-key-schema/v1",
        fields=key_fields,
        fingerprint=fingerprint(key_fields),
    )
    comparable_global_slice: tuple[tuple[str, Any], ...] = tuple(
        (key, fingerprint(value)) for key, value in sorted(stored_where.items())
    )
    comparable_payload = {
        "expression_fingerprint": expression_fingerprint,
        "evaluator_contracts": _evaluator_contracts(graph_plan),
        "global_slice": comparable_global_slice,
        "key_schema_fingerprint": key_schema.fingerprint,
        "unit": None,
        "fold": None,
        "source_domain_fingerprint": graph_plan.source_domain.profile_fingerprint,
        "definition_transform_fingerprint": None,
    }
    comparable_semantics = ComparableValueSemanticsV1(
        schema="comparable-value-semantics/v1",
        expression_fingerprint=expression_fingerprint,
        evaluator_contracts=_evaluator_contracts(graph_plan),
        global_slice=comparable_global_slice,
        key_schema_fingerprint=key_schema.fingerprint,
        unit=None,
        fold=None,
        source_domain_fingerprint=graph_plan.source_domain.profile_fingerprint,
        definition_transform_fingerprint=None,
        fingerprint=fingerprint(comparable_payload),
    )
    artifact_payload = {
        "metric_identities": graph_plan.forest.identities,
        "scope_fingerprint": scope_fingerprint,
        "source_domain_fingerprint": graph_plan.source_domain.profile_fingerprint,
        "dependency_fingerprint": graph_plan.forest.dependency_digest.fingerprint,
        "snapshot_fingerprint": snapshot_fingerprint,
        "coverage_fingerprint": coverage_fingerprint,
        "presentation_fingerprint": presentation_fingerprint,
        "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
    }
    artifact_identity = MetricArtifactIdentityV1(
        schema="metric-artifact/v1",
        metric_identities=graph_plan.forest.identities,
        scope_fingerprint=scope_fingerprint,
        source_domain_fingerprint=graph_plan.source_domain.profile_fingerprint,
        dependency_fingerprint=graph_plan.forest.dependency_digest.fingerprint,
        snapshot_fingerprint=snapshot_fingerprint,
        coverage_fingerprint=coverage_fingerprint,
        presentation_fingerprint=presentation_fingerprint,
        artifact_schema_version=CURRENT_ARTIFACT_SCHEMA_VERSION,
        fingerprint=fingerprint(artifact_payload),
    )
    measures = [
        {
            "metric_id": (
                identity.metric_id
                if isinstance(identity, CatalogMetricIdentity)
                else f"runtime:{identity.expression_fingerprint}"
            ),
            "name": output_column,
            "column": output_column,
            "unit": root.unit,
            "unit_state": canonical_value(root.unit_state),
            "additivity": _meta_additivity(root.additivity),
            "aggregation": None,
            "status_time_dimension": None,
            "reaggregatable": root.fold is None,
        }
        for identity, output_column, root in zip(
            graph_plan.forest.identities,
            output_columns,
            execution.roots,
            strict=True,
        )
    ]
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(merged),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="observe",
                    job_ref=job_ref,
                    inputs=[],
                    params_digest=_params_digest(params),
                    analysis_purpose=analysis_purpose,
                    params=params,
                )
            ]
        ),
        metric_id=None,
        metric_identity=None,
        metric_identities=graph_plan.forest.identities,
        expression_graph=graph_plan.graph,
        expression_fingerprint=expression_fingerprint,
        semantic_dependency_digest=graph_plan.forest.dependency_digest,
        presentation=graph_plan.forest.presentation,
        presentation_fingerprint=presentation_fingerprint,
        artifact_identity=artifact_identity,
        key_schema=key_schema,
        source_compatibility_domain=graph_plan.source_domain,
        comparable_value_semantics=comparable_semantics,
        execution_stats=_execution_stats(graph_plan, execution),
        axes=first_root.axes,
        measure={},
        measures=measures,
        window=dump_window(resolved_window),
        where=stored_where,
        semantic_kind=first_root.semantic_kind,
        semantic_model=model_name,
        unit=None,
        unit_state=None,
        reaggregatable=all(bool(item["reaggregatable"]) for item in measures),
        additivity=None,
        zero_denominator_rows=None,
    )
    frame = MetricFrame(_df=merged, meta=meta)
    frame.meta = frame.meta.model_copy(
        update={
            "issues": tuple(
                issue for root in execution.roots for issue in _unit_capability_issues(frame, root)
            )
        }
    )
    coverage_refs = _persist_metric_graph_coverage_sidecars(
        session=session,
        parent=frame,
        execution=execution,
        job_ref=job_ref,
    )
    component_graph = component_graph_payload_v1(
        execution,
        graph_plan,
        coverage_refs=coverage_refs,
    )
    component = _persist_metric_component_graph_frame(
        session=session,
        df=merged,
        parent=frame,
        axes=first_root.axes,
        semantic_kind=first_root.semantic_kind,
        job_ref=job_ref,
        component_graph=component_graph,
    )
    frame = _attach_metric_component_graph_ref(
        session=session,
        parent=frame,
        component=component,
        persist_parent=False,
    )
    frame = _commit_observe_metric_frame(
        session=session,
        frame=frame,
        params=params,
        metric_id=None,
        model_name=model_name,
        stored_where=stored_where,
        semantic_kind=first_root.semantic_kind,
        subject_grain=(
            resolved_window.grain.to_token()
            if resolved_window is not None and resolved_window.grain is not None
            else None
        ),
        metric_ids=[str(item["metric_id"]) for item in measures],
        models=[model_name],
        semantic_anchors=anchors,
    )
    output_ref = frame.meta.artifact_id or frame.ref
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "observe",
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": [],
            "output_frame_ref": output_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog.semantic_root),
            "semantic_model": model_name,
            "queries": [
                {**query.to_dict(), "output_ref": output_ref} for query in captured_queries
            ],
        },
    )
    _remember_snapshot_verified_artifact(
        session=session,
        graph_plan=graph_plan,
        cache_key=artifact_cache_key,
        starting_token=starting_snapshot_token,
        artifact_ref=frame.ref,
    )
    return frame


def _raise_on_empty_slice_result(
    frame: MetricFrame,
    where_by_id: dict[str, SliceValue],
) -> None:
    """Raise SliceEmptyResultError when slice_by yields zero rows.

    A 0-row result under slice_by is almost always a mismatched slice value or
    an empty time window; surface it as a typed error with a reminder instead
    of returning a silent empty frame. This reads only the already-computed
    ``row_count`` — it never scans the source to verify whether a slice value
    exists, which would be too costly on very large tables. See issue #26.
    """
    if not where_by_id:
        return
    if frame.meta.row_count != 0:
        return
    dimensions = list(where_by_id.keys())
    raise SliceEmptyResultError(
        message=(
            f"slice_by on dimension(s) {dimensions!r} produced 0 rows. Verify the "
            "slice_by values and time_scope against the source data."
        ),
        context={"slice_dimensions": dimensions},
    )
