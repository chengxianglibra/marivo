"""Materialize a semantic metric into a MetricFrame."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from time import monotonic
from types import SimpleNamespace
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pandas as pd

from marivo._compat import UTC
from marivo._temporal import (
    FrameTemporalContractV1,
    PeriodCalendarSnapshotV1,
    TemporalResolver,
    TimeAxisTimeZoneV1,
    TimeScope,
    _new_time_scope,
    builtin_grain,
    period_binding_for_grain,
)
from marivo._temporal import (
    Grain as TemporalGrain,
)
from marivo.analysis._cumulative import (
    CUMULATIVE_CONTRACT_VERSION,
    EVALUATION_END_COLUMN,
    canonical_cumulative_metadata,
    cumulative_has_evaluation_contract,
    normalize_cumulative_anchor,
)
from marivo.analysis._semantic_persistence import (
    AxisBindingV1,
    MeasureBindingV1,
    SlicePredicateV1,
)
from marivo.analysis.attribution_contract import (
    AttributionBasisV1,
    basis_fingerprint,
    build_attribution_basis,
)
from marivo.analysis.candidate_lineage import CandidateOrigin
from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
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
from marivo.analysis.executor.windowing import datasource_engine_profile
from marivo.analysis.frames._meta_defaults import (
    compute_analysis_scope,
    observed_data_extent_end,
)
from marivo.analysis.frames._quality import evaluate_frame_quality
from marivo.analysis.frames.base import CURRENT_ARTIFACT_SCHEMA_VERSION
from marivo.analysis.frames.metric import MetricExecutionStatsV1, MetricFrame, MetricFrameMeta
from marivo.analysis.frames.subject import SubjectSet
from marivo.analysis.intents._metric_evaluators import align_metric_children_v1
from marivo.analysis.intents._metric_graph_execute import (
    component_graph_payload_v1,
    execute_metric_graph_observe,
    root_component_frame_v1,
)
from marivo.analysis.intents._metric_graph_plan import plan_metric_graph_observe
from marivo.analysis.intents._observe_base import (  # noqa: F401
    _aggregate_component_contract,
    _execute_base,
    _execute_sampled_base,
    _expression_source_columns,
    _prune_base_observe_projection,
    _resolve_fold_time_field,
    _time_dependency_exprs,
)
from marivo.analysis.intents._observe_catalog import (  # noqa: F401
    _build_entity_adapter,
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
    _bind_metric_forest_temporal_context,
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
    _preflight_observe_temporal_suitability,
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
from marivo.analysis.intents._subject_cohort import resolve_subject_cohort
from marivo.analysis.intents.observe_planner import (
    _planned_metric,
)
from marivo.analysis.intents.sampled_fold import (
    quantile_capability,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.runtime_metric import (
    RuntimeAggregateExpr,
    RuntimeLinearExpr,
    RuntimeMetricExpr,
    RuntimeRatioExpr,
    RuntimeSliceExpr,
    RuntimeWeightedMeanExpr,
    replay_payload,
)
from marivo.analysis.semantic_inputs import (
    normalize_metric_ref_input,
)
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_job_record,
    persist_reused_artifact_job,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.analysis.slice_types import SliceValue
from marivo.analysis.windows.spec import dump_window
from marivo.datasource.ir import QueryParamScalar, QueryParamScalarList
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import (
    DimensionKind,
    MetricKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    TimeDimensionKind,
)
from marivo.refs import (
    ref as ref_factory,
)
from marivo.semantic._metric_resolution import (
    fold_input_to_ir,
    resolve_aggregate_temporal_contract,
)
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    SimpleMetricDetails,
    _SemanticInput,
)
from marivo.semantic.ir import (
    CumulativeComposition,
    LinearComposition,
    RatioComposition,
    linear_additivity_bucket,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CanonicalSliceEntryV1,
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
    WeightedMeanAggregateNodeV1,
    node_child_ids,
)
from marivo.semantic.metric_graph_canonical import canonical_value, fingerprint
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


def _source_binding_params(
    session: Session,
) -> dict[str, dict[str, QueryParamScalar | QueryParamScalarList]]:
    """Return a deterministic, non-secret source-binding identity payload."""

    bindings = session._connection_runtime.source_bindings()
    return {
        entity_id: {name: values[name] for name in sorted(values)}
        for entity_id, values in sorted(bindings.items())
    }


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
        if isinstance(node, AggregateNodeV1 | WeightedMeanAggregateNodeV1 | CatalogBodyLeafV1):
            contracts.add("aggregate-evaluation/v1")
        elif isinstance(node, CumulativeNodeV1):
            contracts.add(f"cumulative-evaluation/v{CUMULATIVE_CONTRACT_VERSION}")
        elif isinstance(node, SliceNodeV1):
            contracts.add("slice-evaluation/v1")
        elif isinstance(node, RatioNodeV1):
            contracts.add("ratio-evaluation/v1")
        elif isinstance(node, LinearNodeV1):
            contracts.add("linear-evaluation/v1")
    return tuple(sorted(contracts))


def _root_graph_additivity(graph_plan: Any) -> str:
    """Resolve the root additivity without executing physical metric leaves."""

    leaf_additivity = {
        leaf.node_id: getattr(leaf.metric_ir, "additivity", "non_additive")
        for leaf in graph_plan.leaves
    }
    nodes = {record.node_id: record.node for record in graph_plan.graph.nodes}
    resolved: dict[str, str] = {}

    def visit(node_id: str) -> str:
        cached = resolved.get(node_id)
        if cached is not None:
            return cached
        if node_id in leaf_additivity:
            value = str(leaf_additivity[node_id])
        else:
            node = nodes[node_id]
            if isinstance(node, SliceNodeV1 | CumulativeNodeV1):
                value = visit(node.child_id)
            elif isinstance(node, RatioNodeV1):
                value = "non_additive"
            elif isinstance(node, LinearNodeV1):
                children = tuple(visit(term.child_id) for term in node.terms)
                value = linear_additivity_bucket(children)
            else:
                value = "non_additive"
        resolved[node_id] = value
        return value

    return visit(graph_plan.graph.roots[0])


def _additivity_supports_sum_rollup(additivity: str | None) -> bool:
    """Return True when a plain ``.sum()`` rollup is safe for ``additivity``.

    ``reaggregatable`` (in the v1 rollup contract) means "there is a known safe
    rollup and it is the ordinary sum over value columns".  Only ``additive``
    values are closed under cross-grain summation.  ``semi_additive`` folds via
    ``fold``/``rollup_fold``, and ``non_additive``/unknown values have no plain
    sum rollup, so they must be conservatively blocked (issue #110).
    """
    return additivity == "additive"


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
    root_index: int = 0,
) -> dict[str, Any] | None:
    """Project recursive cumulative state into the stable frame-level summary."""

    identity = graph_plan.forest.identities[root_index]
    if isinstance(identity, CatalogMetricIdentity):
        return _catalog_cumulative_marker(catalog, identity.metric_ref.path)

    cumulative_leaves = {
        leaf.node_id: leaf
        for leaf in graph_plan.leaves
        if isinstance(leaf.plan, CumulativePhysicalLeafPlanV1)
    }
    if not cumulative_leaves:
        return None
    physical_leaf_ids = {leaf.node_id for leaf in graph_plan.leaves}
    root_id = graph_plan.graph.roots[root_index]
    if root_id in cumulative_leaves:
        return _cumulative_leaf_marker(cumulative_leaves[root_id])

    nodes = {record.node_id: record.node for record in graph_plan.graph.nodes}
    root = nodes[root_id]
    branches: tuple[tuple[str, str], ...]
    if isinstance(root, RatioNodeV1):
        branches = (("numerator", root.numerator_id), ("denominator", root.denominator_id))
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


def _forest_cumulative_marker(
    graph_plan: Any,
    *,
    catalog: Any,
) -> dict[str, Any] | None:
    """Project one cumulative contract across all ordered forest roots."""

    markers = tuple(
        marker
        for index in range(len(graph_plan.forest.identities))
        if (marker := _cumulative_graph_marker(graph_plan, catalog=catalog, root_index=index))
        is not None
    )
    if not markers:
        return None

    components: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for root_index in range(len(graph_plan.forest.identities)):
        marker = _cumulative_graph_marker(graph_plan, catalog=catalog, root_index=root_index)
        if marker is None:
            blockers.append("non_cumulative_component")
            continue
        kind = marker.get("kind")
        if kind == "cumulative":
            components[f"root{root_index}"] = marker
        elif kind == "derived_contains_cumulative":
            nested = marker.get("components")
            if isinstance(nested, Mapping):
                for role, payload in nested.items():
                    if (
                        isinstance(role, str)
                        and isinstance(payload, Mapping)
                        and payload.get("kind") == "cumulative"
                    ):
                        components[f"root{root_index}.{role}"] = dict(payload)
            blocker = marker.get("compare_blocker")
            if isinstance(blocker, str) and blocker:
                blockers.append(blocker)
        else:
            blockers.append("unresolved_component_anchor")
    anchors = [
        normalize_cumulative_anchor(payload.get("anchor")) for payload in components.values()
    ]
    if blockers:
        blocker = (
            "mixed_component_anchors"
            if "mixed_component_anchors" in blockers
            else "non_cumulative_component"
            if "non_cumulative_component" in blockers
            else blockers[0]
        )
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


def _evaluation_timestamp_utc(value: object, *, report_tz: str) -> pd.Timestamp:
    """Interpret a cumulative cutoff in report time and serialize it in UTC."""

    timestamp = pd.Timestamp(cast("Any", value))
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(ZoneInfo(report_tz))
    return timestamp.tz_convert("UTC")


def _bucket_evaluation_end_utc(
    value: object,
    *,
    grain: Any,
    report_tz: str,
    snapshot: PeriodCalendarSnapshotV1 | None = None,
) -> pd.Timestamp:
    """Return one represented bucket's exclusive end in canonical UTC."""

    timestamp = pd.Timestamp(cast("Any", value))
    boundary_timezone = report_tz
    if isinstance(grain, TemporalGrain) and grain.kind == "semantic" and snapshot is not None:
        boundary_timezone = snapshot.boundary_timezone
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(ZoneInfo(boundary_timezone)).tz_localize(None)
    if isinstance(grain, TemporalGrain) and grain.kind == "semantic":
        if snapshot is None or grain.level is None:
            raise ValueError("semantic cumulative evaluation requires a certified snapshot")
        period = TemporalResolver(snapshot).period_on(grain.level, timestamp.date())
        return _evaluation_timestamp_utc(pd.Timestamp(period.end_date), report_tz=boundary_timezone)
    unit = grain.unit
    count = grain.count
    if unit == "second":
        exclusive_end = timestamp + pd.Timedelta(seconds=count)
    elif unit == "minute":
        exclusive_end = timestamp + pd.Timedelta(minutes=count)
    elif unit == "hour":
        exclusive_end = timestamp + pd.Timedelta(hours=count)
    elif unit == "day":
        exclusive_end = timestamp + pd.DateOffset(days=count)
    elif unit == "week":
        exclusive_end = timestamp + pd.DateOffset(weeks=count)
    elif unit == "month":
        exclusive_end = timestamp + pd.DateOffset(months=count)
    elif unit == "quarter":
        exclusive_end = timestamp + pd.DateOffset(months=3 * count)
    elif unit == "year":
        exclusive_end = timestamp + pd.DateOffset(years=count)
    else:  # pragma: no cover - Grain validates the closed unit set.
        raise ValueError(f"unsupported cumulative evaluation grain {unit!r}")
    return _evaluation_timestamp_utc(exclusive_end, report_tz=report_tz)


def _materialize_cumulative_evaluation_end(
    df: pd.DataFrame,
    *,
    cumulative: dict[str, Any] | None,
    axes: dict[str, Any],
    semantic_kind: str,
    resolved_window: Any | None,
    report_tz: str,
) -> pd.DataFrame:
    """Attach the system-owned cutoff coordinate to complete cumulative rows."""

    if EVALUATION_END_COLUMN in df.columns:
        raise SemanticKindMismatchError(
            message="observe output collides with the reserved evaluation_end column",
            expected="metric and axis output columns outside the system-owned namespace",
            received=EVALUATION_END_COLUMN,
            location="session.observe output",
            repair=AnalysisRepair(
                kind="semantic_authoring",
                action=(
                    "Rename the metric or axis output named 'evaluation_end', reload the "
                    "catalog, and re-observe."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            ),
            context={"kind": "ObserveReservedColumnCollision"},
        )
    if not cumulative_has_evaluation_contract(cumulative):
        return df
    if resolved_window is None:
        raise AnalysisError(
            message="cumulative observation requires an evaluation window",
            expected="a resolved observation window with an exclusive end",
            received="no resolved observation window",
            location="session.observe",
            repair=AnalysisRepair(
                kind="retry",
                action="Re-observe the cumulative metric with an explicit time_scope.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            ),
            context={"kind": "CumulativeEvaluationWindowMissing"},
        )
    evaluation_timezone = report_tz
    anchor = normalize_cumulative_anchor(cumulative.get("anchor")) if cumulative else None
    if (
        isinstance(resolved_window.temporal_snapshot, PeriodCalendarSnapshotV1)
        and isinstance(anchor, tuple)
        and anchor[0] == "grain_to_date"
        and isinstance(anchor[1], TemporalGrain)
        and anchor[1].kind == "semantic"
    ):
        evaluation_timezone = resolved_window.temporal_snapshot.boundary_timezone
    if (
        isinstance(resolved_window.grain, TemporalGrain)
        and resolved_window.grain.kind == "semantic"
        and resolved_window.temporal_snapshot is not None
    ):
        evaluation_timezone = resolved_window.temporal_snapshot.boundary_timezone
    if (
        isinstance(resolved_window.semantic_scope, TimeScope)
        and resolved_window.semantic_scope.kind == "temporal_occurrence"
        and resolved_window.semantic_scope.boundary_timezone
    ):
        evaluation_timezone = resolved_window.semantic_scope.boundary_timezone
    window_end = _evaluation_timestamp_utc(
        resolved_window.end,
        report_tz=evaluation_timezone,
    )
    output = df.copy()
    if semantic_kind in {"scalar", "segmented"}:
        output[EVALUATION_END_COLUMN] = pd.Series(
            [window_end] * len(output),
            index=output.index,
            dtype="datetime64[ns, UTC]",
        )
        return output

    time_column = next(
        (
            axis.get("column")
            for axis in axes.values()
            if isinstance(axis, dict) and axis.get("role") == "time"
        ),
        None,
    )
    grain = resolved_window.grain
    if not isinstance(time_column, str) or time_column not in output.columns or grain is None:
        raise AnalysisError(
            message="cumulative time-shaped observation cannot derive evaluation_end",
            expected="a persisted time axis and resolved grain",
            received=f"time_column={time_column!r}, grain={grain!r}",
            location="session.observe",
            repair=AnalysisRepair(
                kind="retry",
                action="Re-observe with an explicit time_dimension and grain.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            ),
            context={"kind": "CumulativeEvaluationAxisMissing"},
        )
    evaluation_ends = [
        min(
            _bucket_evaluation_end_utc(
                value,
                grain=grain,
                report_tz=report_tz,
                snapshot=resolved_window.temporal_snapshot,
            ),
            window_end,
        )
        for value in output[time_column]
    ]
    output[EVALUATION_END_COLUMN] = pd.Series(
        evaluation_ends,
        index=output.index,
        dtype="datetime64[ns, UTC]",
    )
    return output


def _graph_plan_time_axis_timezones(graph_plan: Any) -> tuple[TimeAxisTimeZoneV1, ...]:
    """Return executor-resolved timezone authorities from a metric graph plan."""
    by_dimension: dict[str, TimeAxisTimeZoneV1] = {}
    for leaf in graph_plan.leaves:
        base_plan = leaf.plan.base_plan if hasattr(leaf.plan, "base_plan") else leaf.plan
        authority = base_plan.time_axis_timezone
        if authority is None:
            continue
        existing = by_dimension.get(authority.time_dimension)
        if existing is not None and existing != authority:
            raise AssertionError(
                "one observation time axis resolved conflicting timezone authorities"
            )
        by_dimension[authority.time_dimension] = authority
    return tuple(by_dimension[key] for key in sorted(by_dimension))


def _build_frame_temporal_contract(
    *,
    resolved_window: Any | None,
    cumulative: dict[str, Any] | None,
    frame: pd.DataFrame,
    report_timezone: str,
    time_axis_timezones: tuple[TimeAxisTimeZoneV1, ...] = (),
) -> FrameTemporalContractV1 | None:
    """Persist one closed temporal authority beside every time-shaped frame."""
    if resolved_window is None:
        return None
    semantic_scope = getattr(resolved_window, "semantic_scope", None)
    if isinstance(semantic_scope, TimeScope):
        scope = semantic_scope
        scope_contract = scope.contract()
    else:
        scope = _new_time_scope(start=resolved_window.start, end=resolved_window.end)
        try:
            scope_contract = scope.contract()
        except ValueError as exc:
            if "non-empty" in str(exc):
                # Existing analysis callers may intentionally observe an empty
                # date window to exercise alignment/coverage behavior.  That
                # interval cannot be represented by the strict temporal
                # contract, so leave the optional contract absent.
                return None
            # The public observe boundary accepts a date-only start with a
            # datetime end for partial buckets. Promote both bounds to
            # datetimes for the persisted contract without changing the
            # requested interval.
            if "mix date and datetime" not in str(exc):
                raise
            start = pd.Timestamp(resolved_window.start)
            end = pd.Timestamp(resolved_window.end)
            if start.tzinfo is None and end.tzinfo is not None:
                start = start.tz_localize(end.tzinfo)
            elif start.tzinfo is not None and end.tzinfo is None:
                end = end.tz_localize(start.tzinfo)
            scope = _new_time_scope(
                start=start.to_pydatetime(),
                end=end.to_pydatetime(),
            )
            scope_contract = scope.contract()
    observation_period = None
    if resolved_window.grain is not None:
        observation_boundary_timezone = report_timezone
        if (
            isinstance(resolved_window.grain, TemporalGrain)
            and resolved_window.grain.kind == "semantic"
        ):
            if resolved_window.temporal_snapshot is None:
                raise ValueError("semantic observation grain requires a certified snapshot")
            observation_boundary_timezone = resolved_window.temporal_snapshot.boundary_timezone
        observation_period = period_binding_for_grain(
            resolved_window.grain,
            snapshot=resolved_window.temporal_snapshot,
            boundary_timezone=observation_boundary_timezone,
        )
    reset_period = None
    anchor = normalize_cumulative_anchor(cumulative.get("anchor")) if cumulative else None
    if isinstance(anchor, tuple) and anchor[0] == "grain_to_date":
        reset_grain = anchor[1]
        if isinstance(reset_grain, str):
            reset_grain = builtin_grain(reset_grain)
        if isinstance(reset_grain, TemporalGrain):
            reset_boundary_timezone = report_timezone
            if reset_grain.kind == "semantic":
                if resolved_window.temporal_snapshot is None:
                    raise ValueError("semantic reset grain requires a certified snapshot")
                reset_boundary_timezone = resolved_window.temporal_snapshot.boundary_timezone
            reset_period = period_binding_for_grain(
                reset_grain,
                snapshot=resolved_window.temporal_snapshot,
                boundary_timezone=reset_boundary_timezone,
            )
    output_keys: tuple[Any, ...] = ()
    period_key_absence_reason: str | None = None
    if "period_key" in frame.columns:
        output_keys = tuple(frame["period_key"].tolist())
    elif observation_period is not None and observation_period.kind == "semantic_period":
        # A certified calendar grain was requested but the executed frame has no
        # period_key column, so period labels could not be attached.  Surface the
        # gap explicitly instead of returning a silently empty key list.
        period_key_absence_reason = (
            "semantic observation grain active but frame has no period_key column; "
            "certified period labels were not attached"
        )
    data_extent_end: date | datetime | None = None
    if "data_extent_end" in frame.columns and len(frame) > 0:
        candidate = frame["data_extent_end"].iloc[0]
        if candidate is not None and not pd.isna(candidate):
            data_extent_end = candidate
    elif "bucket_start" in frame.columns and len(frame) > 0:
        data_extent_end = observed_data_extent_end(
            pd.to_datetime(frame["bucket_start"]).dropna(),
            tz=report_timezone,
        )
    return FrameTemporalContractV1(
        time_scope=scope_contract,
        observation_period=observation_period,
        cumulative_reset_period=reset_period,
        actual_start=scope_contract.start,
        actual_end=scope_contract.end,
        data_extent_end=data_extent_end,
        output_period_keys=output_keys,
        period_key_absence_reason=period_key_absence_reason,
        display_timezone=report_timezone,
        time_axis_timezones=time_axis_timezones,
    )


def observe(
    metrics: (
        _SemanticInput[MetricKind]
        | RuntimeMetricExpr
        | list[_SemanticInput[MetricKind] | RuntimeMetricExpr]
        | tuple[_SemanticInput[MetricKind] | RuntimeMetricExpr, ...]
    ),
    *,
    time_scope: TimeScope | None = None,
    grain: TemporalGrain | None = None,
    dimensions: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None = None,
    slice_by: Mapping[
        _SemanticInput[DimensionKind | TimeDimensionKind],
        SliceValue,
    ]
    | None = None,
    time_dimension: _SemanticInput[TimeDimensionKind] | None = None,
    expect_shape: SemanticShape | None = None,
    cohort: SubjectSet | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
    _candidate_origins: tuple[CandidateOrigin, ...] = (),
    _candidate_input_refs: tuple[str, ...] = (),
) -> MetricFrame:
    from marivo.analysis.frames.candidate import OntologyMetricCandidate

    if isinstance(metrics, (list, tuple)):
        if any(isinstance(item, OntologyMetricCandidate) for item in metrics):
            from marivo.analysis.errors import CandidateNotObservableError

            raise CandidateNotObservableError(
                message="OntologyMetricCandidate must be observed as one exact selected value",
                expected="session.observe(candidate)",
                received="candidate inside a list or tuple",
            )
        metric_items: list[_SemanticInput[MetricKind] | RuntimeMetricExpr] = list(metrics)
        if not metric_items:
            raise SemanticKindMismatchError(
                message="observe requires at least one metric",
                context={"argument": "metrics", "got": "empty sequence"},
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
                cohort=cohort,
                analysis_purpose=analysis_purpose,
                session=session,
            )
        single_metric: _SemanticInput[MetricKind] | RuntimeMetricExpr = metric_items[0]
    else:
        single_metric = metrics
    if session is None:
        session = require_current_session()
    ensure_session_can_execute(session)
    catalog = session.catalog
    catalog._require_index()
    source_binding_params = _source_binding_params(session)
    resolved_cohort = resolve_subject_cohort(
        session=session,
        cohort=cohort,
        consumer="observe",
    )
    metric_ir: Any
    planner_scope: set[str]
    normalized_metric: Ref[MetricKind] | RuntimeMetricExpr
    if isinstance(
        single_metric,
        RuntimeAggregateExpr
        | RuntimeSliceExpr
        | RuntimeRatioExpr
        | RuntimeWeightedMeanExpr
        | RuntimeLinearExpr,
    ):
        normalized_metric = single_metric
        is_catalog_root = False
        metric_id = "runtime.pending"
        model_name = "runtime"
        metric_name = normalized_metric.label
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
        normalized_metric = normalize_metric_ref_input(
            catalog,
            single_metric,
            argument="observe.metrics",
        )
        is_catalog_root = True
        metric_id = _normalize_metric_boundary(catalog, normalized_metric)
        model_name, metric_name = metric_id.split(".", 1)
        metric_details = _catalog_object(catalog, metric_id, SemanticKind.METRIC).details()
        assert isinstance(metric_details, (SimpleMetricDetails, DerivedMetricDetails))
        metric_ir = _planned_metric(metric_details)
        planner_scope = _metric_planner_scope(catalog, metric_ir)
    single_metric = normalized_metric
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
    resolved_window, original_timescope = _resolve_timescope(
        time_scope,
        grain=grain,
        time_dimension=time_dimension_id,
        catalog=catalog,
    )
    from marivo.analysis.intents._observe_inputs import _bind_metric_temporal_context

    resolved_window = _bind_metric_temporal_context(catalog, resolved_window, metric_ir)
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    # For semi-additive simple/derived metrics, inject the preferred status time
    # axis into the window if not already specified so downstream resolution
    # picks the status axis.  The same rule drives multi-metric observe (issue
    # #36); keep it in one place via _preferred_status_time_dimension_for_metric.
    if (
        time_dimension_id is None
        and resolved_window is not None
        and resolved_window.time_dimension is None
    ):
        status_time_dimension = _preferred_status_time_dimension_for_metric(
            catalog, normalized_metric, metric_ir
        )
        if status_time_dimension is not None:
            resolved_window, original_timescope = _resolve_timescope(
                time_scope,
                grain=grain,
                time_dimension=status_time_dimension,
                catalog=catalog,
            )
            resolved_window = _bind_metric_temporal_context(catalog, resolved_window, metric_ir)

    planner_time_dimension_id = (
        resolved_window.time_dimension if resolved_window is not None else time_dimension_id
    )
    _preflight_observe_temporal_suitability(
        catalog,
        metric_inputs=(normalized_metric,),
        resolved_window=resolved_window,
        supplied_time_dimension=time_dimension_id,
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
        resolver = catalog._semantic_resolver(connections=session._connection_runtime)
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
                subject_cohort=resolved_cohort,
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
                elif isinstance(root_node_for_ir, LinearNodeV1):
                    components = {
                        f"term{index}": term.child_id
                        for index, term in enumerate(root_node_for_ir.terms)
                    }
                    metric_ir = SimpleNamespace(
                        semantic_id=metric_id,
                        name=metric_name,
                        domain=model_name,
                        metric_type="runtime",
                        entities=(),
                        aggregation=None,
                        additivity=_root_graph_additivity(graph_plan),
                        status_time_dimension=None,
                        time_fold=None,
                        unit=None,
                        composition=SimpleNamespace(
                            kind="linear",
                            components=components,
                        ),
                        linear_terms=tuple(
                            (
                                "+" if term.coefficient == 1.0 else "-",
                                term.child_id,
                            )
                            for term in root_node_for_ir.terms
                        ),
                    )
            graph_nodes = {record.node_id: record.node for record in graph_plan.graph.nodes}
            attribution_basis: AttributionBasisV1 | None = None
            root_node = graph_nodes.get(graph_plan.graph.roots[0])
            root_leaf = next(
                (leaf for leaf in graph_plan.leaves if leaf.node_id == graph_plan.graph.roots[0]),
                None,
            )
            if isinstance(root_node, AggregateNodeV1) and root_leaf is not None:
                base_plan = (
                    root_leaf.plan.base_plan
                    if hasattr(root_leaf.plan, "base_plan")
                    else root_leaf.plan
                )
                try:
                    source_dtype = str(
                        resolver.measure_on(
                            ref_factory.measure(root_node.target_ref.path),
                            base_plan.table,
                        ).type()
                    )
                except Exception:
                    source_dtype = "unknown"
                attribution_basis = build_attribution_basis(
                    graph_plan.graph,
                    source_dtype=source_dtype,
                    engine_profile=datasource_engine_profile(
                        session._connection_runtime,
                        graph_plan.datasource_name,
                    ),
                )
            cumulative_meta = _cumulative_graph_marker(graph_plan, catalog=catalog)
            cumulative_payload = (
                canonical_cumulative_metadata(cumulative_meta)
                if cumulative_meta is not None
                else None
            )
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
                "replay_expression": replay_payload(single_metric),
                "timescope": params_timescope,
                "dimension_refs": _dimension_ref_payloads(catalog, dimension_refs),
                "slice_predicates": canonical_value(_slice_predicates(catalog, stored_where)),
                "metric_graph": canonical_value(graph_plan.graph),
                "semantic_dependency_digest": canonical_value(graph_plan.forest.dependency_digest),
                "presentation": canonical_value(graph_plan.forest.presentation),
                "datasource_compatibility_domain": graph_plan.datasource_name,
                "version_resolutions": version_resolutions,
                "warnings": list(graph_plan.warnings),
                "lineage_metadata": graph_plan.lineage_metadata,
                "metric_semantics": _metric_semantics_payload(metric_ir),
                "attribution_basis": (
                    attribution_basis.model_dump(mode="json")
                    if attribution_basis is not None
                    else None
                ),
                "cohort": (
                    resolved_cohort.binding.model_dump(mode="json")
                    if resolved_cohort is not None
                    else None
                ),
                **({"source_bindings": source_binding_params} if source_binding_params else {}),
            }
            if resolved_window is not None:
                temporal_contract = _build_frame_temporal_contract(
                    resolved_window=resolved_window,
                    cumulative=cumulative_meta,
                    frame=pd.DataFrame(),
                    report_timezone=session.report_tz_name,
                    time_axis_timezones=_graph_plan_time_axis_timezones(graph_plan),
                )
                if temporal_contract is not None:
                    params["temporal_contract"] = temporal_contract.model_dump(mode="json")
            if _candidate_origins:
                params["candidate_origins"] = [
                    origin.model_dump(mode="json") for origin in _candidate_origins
                ]
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
            aggregate_component_contract = _aggregate_component_contract(metric_ir)
            if aggregate_component_contract is not None:
                params["component_lowering"] = aggregate_component_contract
            if cumulative_meta is not None:
                params["cumulative_contract_version"] = CUMULATIVE_CONTRACT_VERSION
                params["cumulative"] = cumulative_payload
                if cumulative_has_evaluation_contract(cumulative_meta):
                    params["evaluation_end_column"] = EVALUATION_END_COLUMN
            if any(isinstance(node, RatioNodeV1) for node in graph_nodes.values()):
                params["zero_division"] = "null"
            anchor_time_ref = _status_time_dimension_payload(planner_time_dimension_id)
            commit_anchors = CommitSemanticAnchors(
                catalog_definition_fingerprint=session.catalog.definition_fingerprint,
                semantic_dependency_digest=graph_plan.forest.dependency_digest,
                metric_identities=graph_plan.forest.identities,
                axis_refs=tuple(
                    RefPayloadV1.from_ref(ref_factory.dimension(path)) for path in dimension_refs
                )
                + ((anchor_time_ref,) if anchor_time_ref is not None else ()),
                slice_predicates=_slice_predicates(catalog, stored_where),
            )
            artifact_cache_key = _observe_artifact_cache_key(
                graph_plan=graph_plan,
                params=params,
                semantic_anchors=commit_anchors.payload,
            )
            cached_frame, starting_snapshot_token = _lookup_snapshot_verified_artifact(
                session=session,
                graph_plan=graph_plan,
                cache_key=artifact_cache_key,
            )
            if cached_frame is not None:
                session._connection_runtime.take_captured_queries()
                _raise_on_empty_slice_result(cached_frame, where_by_id)
                persist_reused_artifact_job(
                    session,
                    intent="observe",
                    analysis_purpose=analysis_purpose,
                    params=params,
                    input_frame_refs=(
                        [resolved_cohort.binding.artifact_ref]
                        if resolved_cohort is not None
                        else []
                    ),
                    output_frame_ref=cached_frame.meta.artifact_id or cached_frame.ref,
                    semantics=_observe_job_semantics(cached_frame),
                    started_at=started_at,
                    started_monotonic=started,
                    semantic_project_root=str(session.catalog.semantic_root),
                )
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
        session._connection_runtime.take_captured_queries()
        snapshot_fingerprint, coverage_fingerprint = _execution_snapshot_fingerprints(
            graph_execution
        )
        params["snapshot_fingerprint"] = snapshot_fingerprint
        params["coverage_fingerprint"] = coverage_fingerprint
        persisted_axis_bindings = _axis_bindings(catalog, graph_execution.roots[0].axes)
        commit_anchors = CommitSemanticAnchors(
            catalog_definition_fingerprint=session.catalog.definition_fingerprint,
            semantic_dependency_digest=graph_plan.forest.dependency_digest,
            metric_identities=graph_plan.forest.identities,
            axis_refs=tuple(binding.ref for binding in persisted_axis_bindings),
            slice_predicates=_slice_predicates(catalog, stored_where),
        )
        prospective_id = compute_prospective_artifact_id(
            step_type="observe",
            inputs=CommitInputs(
                input_refs=[
                    *(
                        [resolved_cohort.binding.artifact_ref]
                        if resolved_cohort is not None
                        else []
                    ),
                    *_candidate_input_refs,
                ]
            ),
            params=CommitParams(values=params),
            semantic_anchors=commit_anchors,
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
            # The numeric artifact identity dedups, but every invocation must
            # keep an independent, recoverable job record carrying its own
            # analysis_purpose (issue #38).  The frame meta is not rewritten,
            # so the artifact keeps its original producer/purpose.
            persist_reused_artifact_job(
                session,
                intent="observe",
                analysis_purpose=analysis_purpose,
                params=params,
                input_frame_refs=[
                    *(
                        [resolved_cohort.binding.artifact_ref]
                        if resolved_cohort is not None
                        else []
                    ),
                    *_candidate_input_refs,
                ],
                output_frame_ref=cached_frame.meta.artifact_id or cached_frame.ref,
                semantics=_observe_job_semantics(cached_frame),
                started_at=started_at,
                started_monotonic=started,
                semantic_project_root=str(session.catalog.semantic_root),
            )
            return _mark_artifact_deduplicated(cached_frame)
        finished_at = datetime.now(UTC)
        # The evidence artifact id is already deterministic at this point. Use
        # it before persisting sidecars so they retain the final parent ref.
        frame_ref = prospective_id
        job_ref = _gen_ref("job")
        root_execution = graph_execution.roots[0]
        materialized_frame = _materialize_cumulative_evaluation_end(
            root_execution.frame,
            cumulative=cumulative_meta,
            axes=root_execution.axes,
            semantic_kind=root_execution.semantic_kind,
            resolved_window=resolved_window,
            report_tz=session.report_tz_name,
        )
        folded_leaves = [
            leaf
            for leaf in graph_plan.leaves
            if getattr(leaf.metric_ir, "time_fold", None) is not None
        ]
        fold_meta = None
        if folded_leaves:
            if metric_ir.metric_type == "simple":
                fold_meta = _build_fold_meta(
                    metric_ir,
                    catalog,
                    temporal_fold=getattr(folded_leaves[0].plan, "temporal_fold", None),
                )
            else:
                fold_meta = {
                    "time_fold": "derived",
                    "component_folds": [
                        {
                            "component_metric_id": leaf.metric_id,
                            "time_fold": leaf.metric_ir.time_fold.label(),
                            "fold_kind": leaf.metric_ir.time_fold.kind,
                            "status_time_dimension": leaf.metric_ir.status_time_dimension,
                            "fold_strategy": getattr(
                                getattr(leaf.plan, "temporal_fold", None),
                                "strategy",
                                None,
                            ),
                            "identity_keys": list(
                                getattr(
                                    getattr(leaf.plan, "temporal_fold", None),
                                    "identity_columns",
                                    (),
                                )
                            ),
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
                "dimension_refs": _dimension_ref_payloads(catalog, dimension_refs),
                "slice_predicates": canonical_value(_slice_predicates(catalog, stored_where)),
                "report_tz": session.report_tz_name,
                "cohort": (
                    resolved_cohort.binding.model_dump(mode="json")
                    if resolved_cohort is not None
                    else None
                ),
                **({"source_bindings": source_binding_params} if source_binding_params else {}),
            }
        )
        key_fields = tuple(
            MetricKeyFieldV1(
                name=column,
                dtype=(
                    _stable_key_dtype(materialized_frame[column])
                    if cumulative_meta is not None
                    else str(materialized_frame[column].dtype)
                ),
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
        comparable_global_slice = _comparable_slice(catalog, stored_where)
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
            "dependency_fingerprint": graph_plan.forest.dependency_digest.digest,
            "snapshot_fingerprint": snapshot_fingerprint,
            "coverage_fingerprint": coverage_fingerprint,
            "presentation_fingerprint": presentation_fingerprint,
            "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
            "attribution_basis_fingerprint": basis_fingerprint(attribution_basis),
        }
        artifact_identity = MetricArtifactIdentityV1(
            schema="metric-artifact/v1",
            metric_identities=(metric_identity,),
            scope_fingerprint=scope_fingerprint,
            source_domain_fingerprint=graph_plan.source_domain.profile_fingerprint,
            dependency_fingerprint=graph_plan.forest.dependency_digest.digest,
            snapshot_fingerprint=snapshot_fingerprint,
            coverage_fingerprint=coverage_fingerprint,
            presentation_fingerprint=presentation_fingerprint,
            artifact_schema_version=CURRENT_ARTIFACT_SCHEMA_VERSION,
            attribution_basis_fingerprint=basis_fingerprint(attribution_basis),
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
            catalog_definition_fingerprint=session.catalog.definition_fingerprint,
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(materialized_frame),
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="observe",
                        job_ref=job_ref,
                        inputs=[
                            *(
                                [resolved_cohort.binding.artifact_ref]
                                if resolved_cohort is not None
                                else []
                            ),
                            *_candidate_input_refs,
                        ],
                        params_digest=_params_digest(params),
                        analysis_purpose=analysis_purpose,
                        params=params,
                    )
                ]
            ),
            candidate_origins=_candidate_origins,
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
            axis_bindings=_axis_bindings(session.catalog, root_execution.axes),
            slice_predicates=_slice_predicates(session.catalog, stored_where),
            status_time_dimension_ref=_status_time_dimension_payload(
                getattr(metric_ir, "status_time_dimension", None)
            ),
            axes=root_execution.axes,
            measure={"name": metric_name},
            measure_bindings=(
                MeasureBindingV1(
                    identity=metric_identity,
                    value_column="value",
                    display_name=metric_name,
                    unit=root_execution.unit,
                    unit_state=root_execution.unit_state,
                    additivity=_meta_additivity(root_execution.additivity),
                    aggregation=_meta_aggregation(metric_ir.aggregation),
                    reaggregatable=(
                        fold_meta is None
                        and cumulative_meta is None
                        and _additivity_supports_sum_rollup(root_execution.additivity)
                    ),
                    status_time_dimension_ref=_status_time_dimension_payload(
                        getattr(metric_ir, "status_time_dimension", None)
                    ),
                    cumulative=cumulative_payload,
                ),
            ),
            window=dump_window(resolved_window),
            report_tz=session.report_tz_name,
            where=stored_where,
            semantic_kind=root_execution.semantic_kind,
            semantic_model=model_name,
            unit=root_execution.unit,
            unit_state=root_execution.unit_state,
            fold=fold_meta,
            reaggregatable=(
                fold_meta is None
                and cumulative_meta is None
                and _additivity_supports_sum_rollup(root_execution.additivity)
            ),
            additivity=_meta_additivity(root_execution.additivity),
            aggregation=_meta_aggregation(metric_ir.aggregation),
            status_time_dimension=getattr(metric_ir, "status_time_dimension", None),
            cumulative=cumulative_payload,
            temporal_contract=_build_frame_temporal_contract(
                resolved_window=resolved_window,
                cumulative=cumulative_meta,
                frame=materialized_frame,
                report_timezone=session.report_tz_name,
                time_axis_timezones=_graph_plan_time_axis_timezones(graph_plan),
            ),
            zero_denominator_rows=root_execution.quality.zero_division_rows,
            cohort=resolved_cohort.binding if resolved_cohort is not None else None,
            rollup_fold=("last" if cumulative_has_evaluation_contract(cumulative_meta) else None),
            quantile_mode=quantile_mode,
            quantile_method=quantile_method,
            attribution_basis=attribution_basis,
        )
        frame = MetricFrame(_df=materialized_frame, meta=meta)
        frame.meta = frame.meta.model_copy(
            update={"issues": _unit_capability_issues(frame, root_execution)}
        )
        evaluate_frame_quality(frame, artifact_id=frame.ref)
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
            aggregate_contract = root_execution.aggregate_component_contract
            if aggregate_contract is not None:
                aggregate_components = aggregate_contract["components"]
                assert isinstance(aggregate_components, dict)
                aggregate_component_df = root_execution.aggregate_component_df.copy()
                if "value" in aggregate_component_df.columns:
                    aggregate_component_df = aggregate_component_df.rename(
                        columns={"value": metric_name}
                    )
                component = _persist_metric_component_frame(
                    session=session,
                    df=aggregate_component_df,
                    parent=frame,
                    metric_ir=metric_ir,
                    axes=root_execution.axes,
                    semantic_kind=root_execution.semantic_kind,
                    job_ref=job_ref,
                    composition_kind="weighted_mean",
                    components={
                        str(role): str(value) for role, value in aggregate_components.items()
                    },
                    component_graph=component_graph,
                )
                frame = _attach_metric_component_ref(
                    session=session,
                    parent=frame,
                    component=component,
                    metric_ir=metric_ir,
                    composition=aggregate_contract,
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
            input_refs=[
                *([resolved_cohort.binding.artifact_ref] if resolved_cohort is not None else []),
                *_candidate_input_refs,
            ],
        )
        _output_ref = frame.meta.artifact_id or frame.ref
        persist_job_record(
            session,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "observe",
                **_observe_job_semantics(frame),
                "analysis_purpose": analysis_purpose,
                "params": params,
                "input_frame_refs": [
                    *(
                        [resolved_cohort.binding.artifact_ref]
                        if resolved_cohort is not None
                        else []
                    ),
                    *_candidate_input_refs,
                ],
                "output_frame_ref": _output_ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "reused_artifact": False,
                "error": None,
                "semantic_project_root": str(session.catalog.semantic_root),
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
    metric_inputs: tuple[Ref[MetricKind] | RuntimeMetricExpr, ...],
    identities: tuple[Any, ...],
    *,
    reserved_columns: frozenset[str] = frozenset(),
) -> list[str]:
    """Resolve unique public output columns for a metric forest.

    Each requested name (catalog short name or runtime label) becomes a public
    value-column handle.  The resolved names must be globally unique and must
    not collide with any axis key column or another metric, otherwise the
    output would silently overwrite an axis or lose a metric (issue #37).  A
    requested name equal to a reserved axis column fails closed with a
    semantic-authoring repair; a later duplicate is disambiguated by a suffix
    that is itself checked against the reserved namespace.
    """
    requested: list[str] = []
    for index, (metric_input, identity) in enumerate(zip(metric_inputs, identities, strict=True)):
        if isinstance(identity, CatalogMetricIdentity):
            requested.append(identity.metric_ref.path.rsplit(".", 1)[-1])
        else:
            if not isinstance(
                metric_input,
                RuntimeAggregateExpr
                | RuntimeSliceExpr
                | RuntimeRatioExpr
                | RuntimeWeightedMeanExpr
                | RuntimeLinearExpr,
            ):
                raise AssertionError(f"runtime metric identity at index {index} has no expression")
            requested.append(metric_input.label)
    used: set[str] = set(reserved_columns)
    result: list[str] = []
    for name in requested:
        if name in reserved_columns:
            raise SemanticKindMismatchError(
                message=(f"observe metric output label conflicts with an axis column: {name!r}"),
                expected="a metric label outside the observe output reserved namespace",
                received=repr(name),
                location="session.observe metrics",
                repair=AnalysisRepair(
                    kind="semantic_authoring",
                    action=(
                        f"Rename the metric label {name!r} (or the colliding axis) to a "
                        "non-reserved semantic name, reload the catalog, then re-observe."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
                ),
                context={
                    "argument": "metrics",
                    "reason": "output_column_collision",
                    "colliding_column": name,
                    "reserved_columns": sorted(reserved_columns),
                },
            )
        candidate = name
        suffix = 1
        while candidate in used:
            suffix += 1
            candidate = f"{name}_{suffix}"
        used.add(candidate)
        result.append(candidate)
    return result


def _preferred_status_time_dimension_for_metric(
    catalog: Any,
    metric_input: Ref[MetricKind] | RuntimeMetricExpr,
    metric_ir: Any | None = None,
) -> str | None:
    """Resolve the status time axis a metric wants to observe on.

    Mirrors the single-metric observe injection: a folded simple metric prefers
    its effective status_time_dimension; a derived or runtime expression
    recursively requires one shared axis across all folded leaves.

    ``metric_ir`` may be passed in by a caller that already resolved the metric
    (single-metric observe), avoiding a redundant catalog parse.
    """
    registry = catalog._require_index().registry

    def catalog_axes(current: Any, *, active: frozenset[str]) -> set[str]:
        semantic_id = str(current.semantic_id)
        if semantic_id in active:
            raise AssertionError(f"metric composition cycle reached observe: {semantic_id}")
        if (
            getattr(current, "time_fold", None) is not None
            and getattr(current, "status_time_dimension", None) is not None
        ):
            return {str(current.status_time_dimension)}
        if current.metric_type != "derived" or current.composition is None:
            return set()
        axes: set[str] = set()
        next_active = active | {semantic_id}
        for component_id in current.composition.components.values():
            component_details = _catalog_object(
                catalog, component_id, SemanticKind.METRIC
            ).details()
            assert isinstance(component_details, (SimpleMetricDetails, DerivedMetricDetails))
            axes.update(catalog_axes(_planned_metric(component_details), active=next_active))
        return axes

    def runtime_axes(value: Ref[MetricKind] | RuntimeMetricExpr) -> set[str]:
        if isinstance(value, RuntimeAggregateExpr):
            measure = registry.measures.get(value.measure.path)
            if measure is None:
                return set()
            temporal_contract = resolve_aggregate_temporal_contract(
                measure.additivity,
                fold_override=fold_input_to_ir(value.fold),
            )
            return (
                {temporal_contract.status_time_dimension}
                if temporal_contract is not None
                else set()
            )
        if isinstance(value, RuntimeSliceExpr):
            return runtime_axes(value.metric)
        if isinstance(value, RuntimeRatioExpr):
            return runtime_axes(value.numerator) | runtime_axes(value.denominator)
        if isinstance(value, RuntimeLinearExpr):
            axes: set[str] = set()
            for component in (*value.add, *value.subtract):
                axes.update(runtime_axes(component))
            return axes
        if isinstance(value, RuntimeWeightedMeanExpr):
            return set()
        normalized = normalize_metric_ref_input(catalog, value, argument="observe.metrics")
        metric_id = _normalize_metric_boundary(catalog, normalized)
        details = _catalog_object(catalog, metric_id, SemanticKind.METRIC).details()
        assert isinstance(details, (SimpleMetricDetails, DerivedMetricDetails))
        return catalog_axes(_planned_metric(details), active=frozenset())

    is_runtime_metric = isinstance(
        metric_input,
        RuntimeAggregateExpr
        | RuntimeSliceExpr
        | RuntimeRatioExpr
        | RuntimeWeightedMeanExpr
        | RuntimeLinearExpr,
    )
    if is_runtime_metric:
        axes = runtime_axes(metric_input)
    elif metric_ir is not None:
        axes = catalog_axes(metric_ir, active=frozenset())
    else:
        axes = runtime_axes(metric_input)
    if len(axes) > 1:
        raise SemanticKindMismatchError(
            message="one metric expression contains conflicting status time dimensions",
            expected="one shared status time dimension across all folded leaves",
            received=", ".join(sorted(axes)),
            location="observe.metrics",
            repair=AnalysisRepair(
                kind="semantic_authoring",
                action=(
                    "Align the folded component metrics to one governed status-time axis, "
                    "reload the catalog, then re-observe."
                ),
                help_target=(
                    LiveHelpTarget(surface="analysis", canonical_id="runtime_metric")
                    if is_runtime_metric
                    else LiveHelpTarget(surface="semantic", canonical_id="objects.metric")
                ),
            ),
            context={"conflicting_status_time_dimensions": sorted(axes)},
        )
    return next(iter(axes), None)


def _resolve_forest_status_time_dimension(
    catalog: Any,
    metric_inputs: tuple[Ref[MetricKind] | RuntimeMetricExpr, ...],
) -> str | None:
    """Resolve the shared status time axis for a multi-metric forest.

    All metrics in one observe share a single time axis.  The preferred status
    time dimension is resolved per root and must agree; a disagreement fails
    closed with a typed error rather than silently picking one (issue #36).
    """
    preferred: set[str] = set()
    for metric_input in metric_inputs:
        axis = _preferred_status_time_dimension_for_metric(catalog, metric_input)
        if axis is not None:
            preferred.add(axis)
    if not preferred:
        return None
    if len(preferred) > 1:
        raise SemanticKindMismatchError(
            message=(
                "observe metric roots prefer conflicting status time dimensions; "
                "pass an explicit time_dimension"
            ),
            expected="one shared status time dimension across the metric roots",
            received=", ".join(sorted(preferred)),
            location="observe.time_dimension",
            repair=AnalysisRepair(
                kind="retry",
                action=(
                    "Pass an explicit time_dimension that both metric roots "
                    "agree on, then re-observe."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            ),
            context={"conflicting_status_time_dimensions": sorted(preferred)},
        )
    return next(iter(preferred))


def _observe_metric_forest(
    metric_inputs: tuple[_SemanticInput[MetricKind] | RuntimeMetricExpr, ...],
    *,
    time_scope: TimeScope | None,
    grain: TemporalGrain | None,
    dimensions: list[_SemanticInput[DimensionKind | TimeDimensionKind]] | None,
    slice_by: Mapping[
        _SemanticInput[DimensionKind | TimeDimensionKind],
        SliceValue,
    ]
    | None,
    time_dimension: _SemanticInput[TimeDimensionKind] | None,
    expect_shape: SemanticShape | None,
    cohort: SubjectSet | None,
    analysis_purpose: str | None,
    session: Session | None,
) -> MetricFrame:
    """Materialize one arity-N catalog/runtime forest through the shared graph."""
    if session is None:
        session = require_current_session()
    ensure_session_can_execute(session)
    catalog = session.catalog
    catalog._require_index()
    source_binding_params = _source_binding_params(session)
    resolved_cohort = resolve_subject_cohort(
        session=session,
        cohort=cohort,
        consumer="observe",
    )
    normalized_metric_inputs: list[Ref[MetricKind] | RuntimeMetricExpr] = []
    for metric_input in metric_inputs:
        if isinstance(
            metric_input,
            RuntimeAggregateExpr
            | RuntimeSliceExpr
            | RuntimeRatioExpr
            | RuntimeWeightedMeanExpr
            | RuntimeLinearExpr,
        ):
            normalized_metric_inputs.append(metric_input)
        else:
            normalized_metric_inputs.append(
                normalize_metric_ref_input(
                    catalog,
                    metric_input,
                    argument="observe.metrics",
                )
            )
    canonical_metric_inputs = tuple(normalized_metric_inputs)
    catalog_root_keys = [
        metric_input.key for metric_input in canonical_metric_inputs if type(metric_input) is Ref
    ]
    duplicate_root_keys = sorted(
        key for key in set(catalog_root_keys) if catalog_root_keys.count(key) > 1
    )
    if duplicate_root_keys:
        raise SemanticKindMismatchError(
            message="observe metric roots must be distinct after semantic input normalization",
            expected="unique catalog metric roots",
            received=", ".join(duplicate_root_keys),
            location="observe.metrics",
            context={"duplicate_metric_refs": duplicate_root_keys},
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
        catalog=catalog,
    )
    resolved_window = _bind_metric_forest_temporal_context(
        catalog,
        resolved_window,
        canonical_metric_inputs,
    )
    is_time_series = resolved_window is not None and resolved_window.grain is not None
    # A multi-metric forest shares one time axis.  When the caller did not pick
    # an explicit time_dimension, prefer the status time axis of a semi-additive
    # simple/derived root (mirroring single-metric observe) so folded derived
    # metrics are not rejected as temporally ambiguous (issue #36).
    if (
        time_dimension_id is None
        and resolved_window is not None
        and resolved_window.time_dimension is None
    ):
        forest_status_time_dimension = _resolve_forest_status_time_dimension(
            catalog, canonical_metric_inputs
        )
        if forest_status_time_dimension is not None:
            resolved_window, original_timescope = _resolve_timescope(
                time_scope,
                grain=grain,
                time_dimension=forest_status_time_dimension,
                catalog=catalog,
            )
            resolved_window = _bind_metric_forest_temporal_context(
                catalog,
                resolved_window,
                canonical_metric_inputs,
            )
            is_time_series = resolved_window is not None and resolved_window.grain is not None
    _preflight_observe_temporal_suitability(
        catalog,
        metric_inputs=canonical_metric_inputs,
        resolved_window=resolved_window,
        supplied_time_dimension=time_dimension_id,
    )
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
    resolver = catalog._semantic_resolver(connections=session._connection_runtime)
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
            metric_inputs=canonical_metric_inputs,
            dataset_irs=all_dataset_irs,
            dataset_fns=all_dataset_fns,
            dimensions=dimension_refs,
            where=where_by_id,
            resolved_window=resolved_window,
            time_dimension=(
                resolved_window.time_dimension if resolved_window is not None else time_dimension_id
            ),
            subject_cohort=resolved_cohort,
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
        cumulative_meta = _forest_cumulative_marker(graph_plan, catalog=catalog)
        cumulative_payload = (
            canonical_cumulative_metadata(cumulative_meta) if cumulative_meta is not None else None
        )
        root_cumulative_meta = tuple(
            _cumulative_graph_marker(graph_plan, catalog=catalog, root_index=index)
            for index in range(len(graph_plan.forest.identities))
        )
        root_cumulative_payloads = tuple(
            canonical_cumulative_metadata(marker) if marker is not None else None
            for marker in root_cumulative_meta
        )
        params_timescope = (
            {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "report_tz": session.report_tz_name,
            }
            if resolved_window is not None
            else None
        )
        params = {
            "metric_identities": canonical_value(graph_plan.forest.identities),
            "replay_expressions": [replay_payload(item) for item in canonical_metric_inputs],
            "timescope": params_timescope,
            "dimension_refs": _dimension_ref_payloads(session.catalog, dimension_refs),
            "slice_predicates": canonical_value(_slice_predicates(session.catalog, stored_where)),
            "metric_graph": canonical_value(graph_plan.graph),
            "semantic_dependency_digest": canonical_value(graph_plan.forest.dependency_digest),
            "presentation": canonical_value(graph_plan.forest.presentation),
            "datasource_compatibility_domain": canonical_value(graph_plan.source_domain),
            "lineage_metadata": graph_plan.lineage_metadata,
            "warnings": list(graph_plan.warnings),
            "cohort": (
                resolved_cohort.binding.model_dump(mode="json")
                if resolved_cohort is not None
                else None
            ),
            **({"source_bindings": source_binding_params} if source_binding_params else {}),
        }
        if resolved_window is not None:
            temporal_contract = _build_frame_temporal_contract(
                resolved_window=resolved_window,
                cumulative=cumulative_meta,
                frame=pd.DataFrame(),
                report_timezone=session.report_tz_name,
                time_axis_timezones=_graph_plan_time_axis_timezones(graph_plan),
            )
            if temporal_contract is not None:
                params["temporal_contract"] = temporal_contract.model_dump(mode="json")
        if cumulative_meta is not None:
            params["cumulative_contract_version"] = CUMULATIVE_CONTRACT_VERSION
            params["cumulative"] = cumulative_payload
            if cumulative_has_evaluation_contract(cumulative_meta):
                params["evaluation_end_column"] = EVALUATION_END_COLUMN
        anchor_time_path = (
            resolved_window.time_dimension if resolved_window is not None else time_dimension_id
        )
        anchor_time_ref = _status_time_dimension_payload(anchor_time_path)
        commit_anchors = CommitSemanticAnchors(
            catalog_definition_fingerprint=session.catalog.definition_fingerprint,
            semantic_dependency_digest=graph_plan.forest.dependency_digest,
            metric_identities=graph_plan.forest.identities,
            axis_refs=tuple(
                RefPayloadV1.from_ref(ref_factory.dimension(path)) for path in dimension_refs
            )
            + ((anchor_time_ref,) if anchor_time_ref is not None else ()),
            slice_predicates=_slice_predicates(session.catalog, stored_where),
        )
        artifact_cache_key = _observe_artifact_cache_key(
            graph_plan=graph_plan,
            params=params,
            semantic_anchors=commit_anchors.payload,
        )
        cached_frame, starting_snapshot_token = _lookup_snapshot_verified_artifact(
            session=session,
            graph_plan=graph_plan,
            cache_key=artifact_cache_key,
        )
        if cached_frame is not None:
            session._connection_runtime.take_captured_queries()
            persist_reused_artifact_job(
                session,
                intent="observe",
                analysis_purpose=analysis_purpose,
                params=params,
                input_frame_refs=(
                    [resolved_cohort.binding.artifact_ref] if resolved_cohort is not None else []
                ),
                output_frame_ref=cached_frame.meta.artifact_id or cached_frame.ref,
                semantics=_observe_job_semantics(cached_frame),
                started_at=started_at,
                started_monotonic=started,
                semantic_project_root=str(session.catalog.semantic_root),
            )
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
    session._connection_runtime.take_captured_queries()
    snapshot_fingerprint, coverage_fingerprint = _execution_snapshot_fingerprints(execution)
    params["snapshot_fingerprint"] = snapshot_fingerprint
    params["coverage_fingerprint"] = coverage_fingerprint
    persisted_axis_bindings = _axis_bindings(session.catalog, execution.roots[0].axes)
    # The output columns must not collide with any axis key column.  Resolve
    # them against the executed axis schema and fail closed if a runtime label
    # equals a dimension/time bucket column (issue #37).
    axis_columns = frozenset(
        axis_column
        for axis in execution.roots[0].axes.values()
        if isinstance(axis, dict) and isinstance((axis_column := axis.get("column")), str)
    )
    output_columns = _forest_output_columns(
        canonical_metric_inputs,
        graph_plan.forest.identities,
        reserved_columns=axis_columns | {EVALUATION_END_COLUMN},
    )
    params["output_columns"] = output_columns
    commit_anchors = CommitSemanticAnchors(
        catalog_definition_fingerprint=session.catalog.definition_fingerprint,
        semantic_dependency_digest=graph_plan.forest.dependency_digest,
        metric_identities=graph_plan.forest.identities,
        axis_refs=tuple(binding.ref for binding in persisted_axis_bindings),
        slice_predicates=_slice_predicates(session.catalog, stored_where),
    )
    prospective_id = compute_prospective_artifact_id(
        step_type="observe",
        inputs=CommitInputs(
            input_refs=(
                [resolved_cohort.binding.artifact_ref] if resolved_cohort is not None else []
            )
        ),
        params=CommitParams(values=params),
        semantic_anchors=commit_anchors,
    )
    if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
        cached_forest_frame = cast("MetricFrame", load_frame(prospective_id, session=session))
        _remember_snapshot_verified_artifact(
            session=session,
            graph_plan=graph_plan,
            cache_key=artifact_cache_key,
            starting_token=starting_snapshot_token,
            artifact_ref=prospective_id,
        )
        # Every invocation keeps an independent job with its own purpose, even
        # when the artifact identity dedups (issue #38).
        persist_reused_artifact_job(
            session,
            intent="observe",
            analysis_purpose=analysis_purpose,
            params=params,
            input_frame_refs=(
                [resolved_cohort.binding.artifact_ref] if resolved_cohort is not None else []
            ),
            output_frame_ref=cached_forest_frame.meta.artifact_id or cached_forest_frame.ref,
            semantics=_observe_job_semantics(cached_forest_frame),
            started_at=started_at,
            started_monotonic=started,
            semantic_project_root=str(session.catalog.semantic_root),
        )
        return _mark_artifact_deduplicated(cached_forest_frame)
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
    merged = _materialize_cumulative_evaluation_end(
        merged,
        cumulative=cumulative_meta,
        axes=execution.roots[0].axes,
        semantic_kind=execution.roots[0].semantic_kind,
        resolved_window=resolved_window,
        report_tz=session.report_tz_name,
    )
    finished_at = datetime.now(UTC)
    # Bind sidecars to the final evidence identity, not a disposable build ref.
    frame_ref = prospective_id
    job_ref = _gen_ref("job")
    expression_fingerprint = fingerprint(graph_plan.graph.roots)
    presentation_fingerprint = fingerprint(graph_plan.forest.presentation)
    scope_fingerprint = fingerprint(
        {
            "timescope": params_timescope,
            "dimension_refs": _dimension_ref_payloads(session.catalog, dimension_refs),
            "slice_predicates": canonical_value(_slice_predicates(session.catalog, stored_where)),
            "report_tz": session.report_tz_name,
            "cohort": (
                resolved_cohort.binding.model_dump(mode="json")
                if resolved_cohort is not None
                else None
            ),
            **({"source_bindings": source_binding_params} if source_binding_params else {}),
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
    comparable_global_slice = _comparable_slice(session.catalog, stored_where)
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
        "dependency_fingerprint": graph_plan.forest.dependency_digest.digest,
        "snapshot_fingerprint": snapshot_fingerprint,
        "coverage_fingerprint": coverage_fingerprint,
        "presentation_fingerprint": presentation_fingerprint,
        "artifact_schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
        "attribution_basis_fingerprint": None,
    }
    artifact_identity = MetricArtifactIdentityV1(
        schema="metric-artifact/v1",
        metric_identities=graph_plan.forest.identities,
        scope_fingerprint=scope_fingerprint,
        source_domain_fingerprint=graph_plan.source_domain.profile_fingerprint,
        dependency_fingerprint=graph_plan.forest.dependency_digest.digest,
        snapshot_fingerprint=snapshot_fingerprint,
        coverage_fingerprint=coverage_fingerprint,
        presentation_fingerprint=presentation_fingerprint,
        artifact_schema_version=CURRENT_ARTIFACT_SCHEMA_VERSION,
        attribution_basis_fingerprint=None,
        fingerprint=fingerprint(artifact_payload),
    )
    measure_bindings = tuple(
        MeasureBindingV1(
            identity=identity,
            value_column=output_column,
            display_name=output_column,
            unit=root.unit,
            unit_state=root.unit_state,
            additivity=_meta_additivity(root.additivity),
            aggregation=_meta_aggregation(root.aggregation),
            reaggregatable=(
                root.fold is None
                and root_cumulative_meta[index] is None
                and _additivity_supports_sum_rollup(root.additivity)
            ),
            cumulative=root_cumulative_payloads[index],
        )
        for index, (identity, output_column, root) in enumerate(
            zip(
                graph_plan.forest.identities,
                output_columns,
                execution.roots,
                strict=True,
            )
        )
    )
    measures = [
        {
            "metric_id": (
                binding.identity.metric_ref.path
                if isinstance(binding.identity, CatalogMetricIdentity)
                else f"runtime:{binding.identity.expression_fingerprint}"
            ),
            "name": binding.display_name,
            "column": binding.value_column,
            "unit": binding.unit,
            "unit_state": canonical_value(binding.unit_state)
            if binding.unit_state is not None
            else None,
            "additivity": binding.additivity,
            "aggregation": binding.aggregation,
            "status_time_dimension": (
                binding.status_time_dimension_ref.path
                if binding.status_time_dimension_ref is not None
                else None
            ),
            "reaggregatable": binding.reaggregatable,
            "cumulative": binding.cumulative,
        }
        for binding in measure_bindings
    ]
    meta = MetricFrameMeta(
        kind="metric_frame",
        catalog_definition_fingerprint=session.catalog.definition_fingerprint,
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
                    inputs=(
                        [resolved_cohort.binding.artifact_ref]
                        if resolved_cohort is not None
                        else []
                    ),
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
        axis_bindings=_axis_bindings(session.catalog, first_root.axes),
        slice_predicates=_slice_predicates(session.catalog, stored_where),
        axes=first_root.axes,
        measure={},
        measures=measures,
        measure_bindings=measure_bindings,
        window=dump_window(resolved_window),
        report_tz=session.report_tz_name,
        where=stored_where,
        semantic_kind=first_root.semantic_kind,
        semantic_model=model_name,
        unit=None,
        unit_state=None,
        reaggregatable=all(binding.reaggregatable for binding in measure_bindings),
        additivity=None,
        zero_denominator_rows=None,
        cohort=resolved_cohort.binding if resolved_cohort is not None else None,
        cumulative=cumulative_payload,
        temporal_contract=_build_frame_temporal_contract(
            resolved_window=resolved_window,
            cumulative=cumulative_meta,
            frame=merged,
            report_timezone=session.report_tz_name,
            time_axis_timezones=_graph_plan_time_axis_timezones(graph_plan),
        ),
        rollup_fold=("last" if cumulative_has_evaluation_contract(cumulative_meta) else None),
    )
    frame = MetricFrame(_df=merged, meta=meta)
    evaluate_frame_quality(frame, artifact_id=frame.ref)
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
        metric_ids=[
            (
                binding.identity.metric_ref.path
                if isinstance(binding.identity, CatalogMetricIdentity)
                else f"runtime:{binding.identity.expression_fingerprint}"
            )
            for binding in measure_bindings
        ],
        models=[model_name],
        input_refs=([resolved_cohort.binding.artifact_ref] if resolved_cohort is not None else []),
    )
    output_ref = frame.meta.artifact_id or frame.ref
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "observe",
            **_observe_job_semantics(frame),
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": (
                [resolved_cohort.binding.artifact_ref] if resolved_cohort is not None else []
            ),
            "output_frame_ref": output_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "reused_artifact": False,
            "error": None,
            "semantic_project_root": str(session.catalog.semantic_root),
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


def _axis_bindings(catalog: Any, axes: dict[str, Any]) -> tuple[AxisBindingV1, ...]:
    registry = catalog._require_index().registry
    bindings: list[AxisBindingV1] = []
    for axis in axes.values():
        if not isinstance(axis, dict):
            continue
        path = axis.get("ref")
        column = axis.get("column")
        if not isinstance(path, str) or not isinstance(column, str):
            continue
        dimension = registry.dimensions.get(path)
        if dimension is None:
            continue
        ref = (
            ref_factory.time_dimension(path)
            if dimension.is_time_dimension
            else ref_factory.dimension(path)
        )
        bindings.append(
            AxisBindingV1(
                ref=RefPayloadV1.from_ref(ref),
                column=column,
                role="time_dimension" if dimension.is_time_dimension else "dimension",
                grain=axis.get("grain") if isinstance(axis.get("grain"), str) else None,
            )
        )
    return tuple(sorted(bindings, key=lambda item: item.ref.path))


def _slice_predicates(catalog: Any, where: dict[str, Any]) -> tuple[SlicePredicateV1, ...]:
    registry = catalog._require_index().registry
    predicates: list[SlicePredicateV1] = []
    for path, value in sorted(where.items()):
        dimension = registry.dimensions.get(path)
        if dimension is None:
            continue
        ref = (
            ref_factory.time_dimension(path)
            if dimension.is_time_dimension
            else ref_factory.dimension(path)
        )
        predicates.append(
            SlicePredicateV1(
                dimension_ref=RefPayloadV1.from_ref(ref),
                value=value,
            )
        )
    return tuple(predicates)


def _dimension_ref_payloads(catalog: Any, paths: list[str]) -> list[dict[str, str]]:
    registry = catalog._require_index().registry
    payloads: list[dict[str, str]] = []
    for path in paths:
        dimension = registry.dimensions[path]
        ref = (
            ref_factory.time_dimension(path)
            if dimension.is_time_dimension
            else ref_factory.dimension(path)
        )
        payloads.append(RefPayloadV1.from_ref(ref).to_dict())
    return payloads


def _stable_key_dtype(series: pd.Series) -> str:
    """Return a logical key dtype independent of pandas string storage choices."""

    dtype = str(series.dtype)
    if dtype not in {"object", "str", "string"}:
        return dtype
    inferred = pd.api.types.infer_dtype(series, skipna=True)
    if inferred in {"string", "unicode", "empty"}:
        return "string"
    return dtype


def _comparable_slice(catalog: Any, where: dict[str, Any]) -> tuple[CanonicalSliceEntryV1, ...]:
    return tuple(
        CanonicalSliceEntryV1(
            dimension_ref=predicate.dimension_ref,
            value=fingerprint(predicate.value),
        )
        for predicate in _slice_predicates(catalog, where)
    )


def _status_time_dimension_payload(path: str | None) -> RefPayloadV1 | None:
    return RefPayloadV1.from_ref(ref_factory.time_dimension(path)) if path is not None else None


def _observe_job_semantics(frame: MetricFrame) -> dict[str, Any]:
    identities = frame.meta.metric_identities

    def subject(identity: object) -> dict[str, Any]:
        if isinstance(identity, CatalogMetricIdentity):
            return {
                "kind": "catalog_metric",
                "metric_ref": identity.metric_ref.to_dict(),
            }
        expression_fingerprint = getattr(identity, "expression_fingerprint", None)
        expression_schema = getattr(identity, "expression_schema", None)
        if not isinstance(expression_fingerprint, str) or not isinstance(expression_schema, str):
            raise TypeError("observe metric identity is not persistable")
        return {
            "kind": "runtime_expression",
            "expression_schema": expression_schema,
            "expression_fingerprint": expression_fingerprint,
        }

    time_refs = [
        binding.ref.to_dict()
        for binding in frame.meta.axis_bindings
        if binding.role == "time_dimension"
    ]
    payload: dict[str, Any] = {
        "catalog_definition_fingerprint": frame.meta.catalog_definition_fingerprint,
        "semantic_dependency_digest": canonical_value(frame.meta.semantic_dependency_digest),
        "dimension_refs": [
            binding.ref.to_dict()
            for binding in frame.meta.axis_bindings
            if binding.role == "dimension"
        ],
        "slice_predicates": canonical_value(frame.meta.slice_predicates),
        "time_dimension_ref": time_refs[0] if time_refs else None,
    }
    if len(identities) == 1:
        payload["subject"] = subject(identities[0])
    else:
        payload["subjects"] = [subject(identity) for identity in identities]
    return payload
