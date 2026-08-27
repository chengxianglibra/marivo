"""Private execution for graph-owned non-additive attribution bases."""

from __future__ import annotations

import hashlib
import itertools
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import ibis
import pandas as pd

from marivo.analysis.attribution_contract import (
    AttributionAxisBindingV1,
    DistinctAttributionBasisV1,
    QuantileAttributionBasisV1,
    basis_fingerprint,
)
from marivo.analysis.errors import (
    AttributionBasisMismatchError,
    AttributionDistributionError,
    AttributionMaterializationError,
)
from marivo.analysis.executor.bucketing import apply_time_series_bucket
from marivo.analysis.executor.runner import execute
from marivo.analysis.executor.windowing import (
    datasource_engine_profile,
    datasource_read_timezone,
    resolve_window_time_field,
)
from marivo.analysis.frames._attribution_columns import (
    ATTRIBUTION_AXIS_COLUMN,
    ATTRIBUTION_DRIVER_COLUMN,
    ATTRIBUTION_LEVEL_COLUMN,
    ATTRIBUTION_OTHER_MASK_COLUMN,
    ATTRIBUTION_PATH_COLUMN,
)
from marivo.analysis.frames.attribution import (
    AttributionMethodEvidenceV1,
    AttributionReconciliation,
    AttributionResolutionReconciliationV1,
    AttributionTopKSelectionV1,
    CompleteHierarchyScopeV1,
    DistinctMembershipEvidenceV1,
    HierarchyResolutionEvidenceV1,
    IndependentHierarchyEvidenceV1,
    QuantileReplacementEvidenceV1,
    QuantileResolutionExecutionV1,
    summarize_quantile_resolution_executions,
)
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._attribution_topk import (
    _OTHER_TOKEN,
    AttributionTopKMapV1,
    build_top_k_map,
    build_top_k_map_from_level_scores,
)
from marivo.analysis.intents._metric_graph_plan import plan_metric_graph_observe
from marivo.analysis.intents._observe_catalog import (
    _build_entity_adapter,
    _entity_details,
    _field_details,
)
from marivo.analysis.intents._observe_inputs import (
    _entity_adapter_maps,
    _resolve_timescope,
)
from marivo.analysis.intents._observe_planner_fields import _all_entity_ids
from marivo.analysis.intents._replay import recover_observe_replay
from marivo.analysis.intents._subject_cohort import resolve_subject_cohort
from marivo.analysis.intents.decompose import _add_contribution_shares
from marivo.analysis.intents.observe_planner import _validate_field_expr
from marivo.analysis.session.core import Session
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from marivo.semantic.metric_graph import AggregateNodeV1

_MAX_QUANTILE_PARTITIONS = 64
_MAX_EXACT_QUANTILE_PARTITIONS = 8
_MAX_FREQUENCY_ROWS = 250_000
_PERMUTATION_COUNT = 128
_NATIVE_PERCENTILE_BATCH_SIZE = 128
_QUANTILE_OPERATOR_VERSION = "quantile-replacement/v1"
_RECONCILIATION_TOLERANCE = 1e-9
_NATIVE_PERCENTILE_PERIOD_COLUMN = "__marivo_attribution_period"
_NATIVE_PERCENTILE_CURRENT_PERIOD = "current"
_NATIVE_PERCENTILE_BASELINE_PERIOD = "baseline"


@dataclass(frozen=True)
class PreparedEvidenceV1:
    """Transient replay expression; never persisted or rendered."""

    table: Any
    value_column: str
    value_dtype: str
    axis_columns: tuple[str, ...]
    axis_bindings: tuple[AttributionAxisBindingV1, ...]
    bucket_column: str | None
    datasource_name: str


@dataclass(frozen=True)
class ShapleyPlanV1:
    """Deterministic coalition requests and permutation paths for one game."""

    partition_count: int
    coalitions: tuple[frozenset[int], ...]
    permutation_orders: tuple[tuple[int, ...], ...]
    seed_fingerprint: str | None


@dataclass(frozen=True)
class NonAdditiveAttributionResultV1:
    dataframe: pd.DataFrame
    axis_columns: list[str]
    bucket_column: str | None
    reconciliation: AttributionReconciliation
    method_evidence: AttributionMethodEvidenceV1
    resolution_evidence: HierarchyResolutionEvidenceV1 | None = None
    top_k_selection: AttributionTopKSelectionV1 | None = None


def _top_k_score_frame(
    prepared: PreparedEvidenceV1,
    *,
    session: Session,
) -> pd.DataFrame:
    value = prepared.value_column
    filtered = prepared.table.filter(prepared.table[value].notnull())
    grouped = filtered.group_by(list(prepared.axis_columns)).aggregate(
        __top_k_score=filtered.count()
    )
    return _run_dataframe(grouped, prepared, session)


def _distinct_top_k_level_scores(
    prepared: PreparedEvidenceV1,
    *,
    session: Session,
) -> list[pd.DataFrame]:
    value = prepared.value_column
    filtered = prepared.table.filter(prepared.table[value].notnull())
    return [
        _run_dataframe(
            filtered.group_by(list(prepared.axis_columns[:level])).aggregate(
                __top_k_score=filtered[value].nunique()
            ),
            prepared,
            session,
        )
        for level in range(1, len(prepared.axis_columns) + 1)
    ]


def _build_nonadditive_top_k(
    current: PreparedEvidenceV1,
    baseline: PreparedEvidenceV1,
    *,
    top_k: int | None,
    distinct: bool,
    session: Session,
) -> tuple[AttributionTopKMapV1 | None, AttributionTopKSelectionV1 | None]:
    if top_k is None:
        return None, None
    axis_columns = list(current.axis_columns)
    if distinct:
        current_scores = _distinct_top_k_level_scores(current, session=session)
        baseline_scores = _distinct_top_k_level_scores(baseline, session=session)
        mapping = build_top_k_map_from_level_scores(
            [
                pd.concat([current_level, baseline_level], ignore_index=True)
                for current_level, baseline_level in zip(
                    current_scores, baseline_scores, strict=True
                )
            ],
            axis_columns=axis_columns,
            score_column="__top_k_score",
            limit=top_k,
        )
    else:
        mapping = build_top_k_map(
            _top_k_score_frame(current, session=session),
            _top_k_score_frame(baseline, session=session),
            axis_columns=axis_columns,
            score_column="__top_k_score",
            limit=top_k,
        )
    return mapping, AttributionTopKSelectionV1(
        limit=top_k,
        score_method="observed_membership" if distinct else "non_null_observations",
        original_partition_count=mapping.original_scope_count,
        effective_partition_count=mapping.collapsed_scope_count,
    )


def _value_predicate(field: Any, value: object) -> Any:
    return field.isnull() if _is_missing(value) else field == ibis.literal(value)


def _mapped_parent_predicate(
    table: Any,
    mapping: AttributionTopKMapV1,
    parent: tuple[tuple[str, str], ...],
) -> Any:
    predicate = ibis.literal(True)
    current_parent: tuple[tuple[str, str], ...] = ()
    for index, token in enumerate(parent):
        field = table[mapping.axis_columns[index]]
        kept = mapping.kept_by_level[index].get(current_parent, frozenset())
        if token == _OTHER_TOKEN:
            selected = ibis.literal(False)
            for child in kept:
                selected = selected | _value_predicate(field, mapping.value_by_token[child])
            predicate = predicate & ~selected
        else:
            predicate = predicate & _value_predicate(field, mapping.value_by_token[token])
        current_parent = (*current_parent, token)
    return predicate


def _apply_top_k_table(
    table: Any,
    mapping: AttributionTopKMapV1,
    *,
    level: int,
) -> Any:
    mutations: dict[str, Any] = {}
    mask = ibis.literal(0)
    for index, column in enumerate(mapping.axis_columns[:level]):
        selected = ibis.literal(False)
        field = table[column]
        for parent, children in mapping.kept_by_level[index].items():
            parent_predicate = _mapped_parent_predicate(table, mapping, parent)
            for child in children:
                selected = selected | (
                    parent_predicate & _value_predicate(field, mapping.value_by_token[child])
                )
        mutations[column] = selected.ifelse(field, ibis.null().cast(field.type()))
        mask = mask + selected.ifelse(0, 1 << index)
    mutations[ATTRIBUTION_OTHER_MASK_COLUMN] = mask
    return table.mutate(**mutations)


def _source_graph(frame: MetricFrame, basis: Any) -> None:
    graph = frame.meta.expression_graph
    if graph is None:
        raise AttributionMaterializationError(
            message="attribution source frame is missing its expression graph",
            context={
                "recoverability_status": "basis_source_graph_mismatch",
                "expected": basis.authority.expression_graph_fingerprint,
                "received": "missing",
            },
        )
    try:
        basis.authority.validate_graph(graph)
    except ValueError as exc:
        raise AttributionMaterializationError(
            message="attribution source graph does not match the persisted basis",
            context={
                "recoverability_status": "basis_source_graph_mismatch",
                "expected": basis.authority.expression_graph_fingerprint,
                "received": "different graph or aggregate node",
            },
        ) from exc


def _prepared_evidence(
    source: MetricFrame,
    *,
    basis: DistinctAttributionBasisV1 | QuantileAttributionBasisV1,
    axis_ids: list[str],
    session: Session,
) -> PreparedEvidenceV1:
    """Rebuild the governed table/window/slice/cohort plan without aggregating."""
    _source_graph(source, basis)
    replay = recover_observe_replay(source, session=session)
    catalog = session.catalog
    resolver = catalog._semantic_resolver(connections=session._connection_runtime)
    _, _, dataset_irs, dataset_fns = _entity_adapter_maps(
        catalog=catalog,
        resolver=resolver,
        entity_refs=_all_entity_ids(catalog),
    )
    time_dimension_path = replay.time_dimension.path if replay.time_dimension is not None else None
    resolved_window, _ = _resolve_timescope(
        replay.time_scope,
        grain=replay.grain,
        time_dimension=time_dimension_path,
    )
    resolved_cohort = resolve_subject_cohort(
        session=session,
        cohort=replay.cohort,
        consumer="attribute",
    )
    inputs = replay.metric if isinstance(replay.metric, tuple) else (replay.metric,)
    if len(inputs) != 1:
        raise AttributionMaterializationError(
            message="non-additive attribution requires one metric expression root",
            context={"source_ref": source.ref, "metric_arity": len(inputs)},
        )
    graph_plan = plan_metric_graph_observe(
        catalog=catalog,
        session=session,
        metric_inputs=inputs,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=axis_ids,
        where={ref.path: value for ref, value in replay.slice_by.items()},
        resolved_window=resolved_window,
        time_dimension=time_dimension_path,
        subject_cohort=resolved_cohort,
    )
    try:
        basis.authority.validate_graph(graph_plan.graph)
    except ValueError as exc:
        raise AttributionMaterializationError(
            message="active replay graph differs from the persisted attribution authority",
            context={
                "recoverability_status": "basis_source_graph_mismatch",
                "expected": basis.authority.expression_graph_fingerprint,
                "received": "active semantic graph mismatch",
            },
        ) from exc
    leaf = next(
        (
            candidate
            for candidate in graph_plan.leaves
            if candidate.node_id == graph_plan.graph.roots[0]
        ),
        None,
    )
    if leaf is None or not isinstance(leaf.node, AggregateNodeV1):
        raise AttributionMaterializationError(
            message="non-additive attribution could not resolve its aggregate physical leaf",
            context={"source_ref": source.ref},
        )
    plan = leaf.plan.base_plan if hasattr(leaf.plan, "base_plan") else leaf.plan
    table = plan.table
    bucket_column: str | None = None
    if resolved_window is not None and resolved_window.grain is not None:
        root_adapter = _build_entity_adapter(
            catalog,
            resolver,
            _entity_details(catalog, plan.root_entity),
        )
        time_field = resolve_window_time_field(root_adapter, window=resolved_window)
        table = apply_time_series_bucket(
            table,
            field_ir=time_field,
            window=resolved_window,
            report_tz=cast("ZoneInfo", session.report_tz),
            datasource_read_tz=datasource_read_timezone(
                session._connection_runtime, plan.datasource_name
            ),
            profile=datasource_engine_profile(session._connection_runtime, plan.datasource_name),
            dataset_ir=root_adapter,
        )
        bucket_column = "bucket_start"
    axis_columns: list[str] = []
    axis_bindings: list[AttributionAxisBindingV1] = []
    mutations: dict[str, Any] = {}
    registry = catalog._require_index().registry
    for axis_id in axis_ids:
        details = _field_details(catalog, axis_id)
        column = details.name
        dimension = registry.dimensions[axis_id]
        axis_ref = (
            ref_factory.time_dimension(axis_id)
            if dimension.is_time_dimension
            else ref_factory.dimension(axis_id)
        )
        mutations[column] = _validate_field_expr(
            resolver.dimension_on(axis_ref, table),
            field_id=axis_id,
        ).name(column)
        axis_columns.append(column)
        axis_bindings.append(
            AttributionAxisBindingV1(
                ref=RefPayloadV1.from_ref(axis_ref),
                output_column=column,
            )
        )
    if mutations:
        table = table.mutate(**mutations)
    value_column = "__marivo_attribution_value"
    value = resolver.measure_on(
        ref_factory.measure(basis.authority.aggregate_node.target_ref.path), table
    ).name(value_column)
    selected = [*([] if bucket_column is None else [bucket_column]), *axis_columns, value]
    return PreparedEvidenceV1(
        table=table.select(*selected),
        value_column=value_column,
        value_dtype=str(value.type()),
        axis_columns=tuple(axis_columns),
        axis_bindings=tuple(axis_bindings),
        bucket_column=bucket_column,
        datasource_name=plan.datasource_name,
    )


def _run_dataframe(expr: Any, prepared: PreparedEvidenceV1, session: Session) -> pd.DataFrame:
    return execute(
        expr,
        datasource_name=prepared.datasource_name,
        cache=session._connection_runtime,
        session_id=session.id,
    ).df


def _bucket_key(column: str | None, value: object | None) -> tuple[tuple[str, Any], ...]:
    if column is None:
        return ()
    if _is_missing(value):
        value = None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        value = isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        value = item()
    if value is not None and not isinstance(value, (str, int, float, bool)):
        value = str(value)
    return ((column, value),)


def _is_missing(value: object | None) -> bool:
    """Return scalar missingness without accepting pandas vector results."""
    result = pd.isna(cast("Any", value))
    return bool(result) if isinstance(result, bool) else False


def _bucket_rows(df: pd.DataFrame, column: str | None, value: object | None) -> pd.DataFrame:
    if column is None:
        return df
    if _is_missing(value):
        return df[df[column].isna()]
    if isinstance(value, (date, datetime, pd.Timestamp)):
        normalized = pd.to_datetime(df[column], errors="coerce")
        return df[normalized == pd.Timestamp(value)]
    return df[df[column] == value]


def _partition_tuple(row: pd.Series, columns: Sequence[str]) -> tuple[object, ...]:
    return tuple(None if _is_missing(row[column]) else row[column] for column in columns)


def _partition_mask(table: Any, columns: Sequence[str], partition: tuple[object, ...]) -> Any:
    predicates = []
    for column, value in zip(columns, partition, strict=True):
        field = table[column]
        predicates.append(field.isnull() if _is_missing(value) else field == ibis.literal(value))
    predicate = predicates[0]
    for item in predicates[1:]:
        predicate = predicate & item
    return predicate


def _rank_rows(
    df: pd.DataFrame,
    *,
    total_delta: float,
    group_columns: Sequence[str],
) -> pd.DataFrame:
    if df.empty:
        return df
    out = _add_contribution_shares(df.copy(), total_delta=total_delta)
    out = out.sort_values(
        "contribution",
        key=lambda values: values.abs(),
        ascending=False,
        kind="mergesort",
    ).reset_index(drop=True)
    out["rank"] = range(1, len(out) + 1)
    return out[[*group_columns, *[column for column in out.columns if column not in group_columns]]]


def _distinct_scope(
    prepared: PreparedEvidenceV1,
    *,
    axis_columns: Sequence[str],
    session: Session,
    prefix: str,
) -> tuple[pd.DataFrame, int]:
    bucket = [] if prepared.bucket_column is None else [prepared.bucket_column]
    key = prepared.value_column
    memberships = (
        prepared.table.filter(prepared.table[key].notnull())
        .select(*bucket, *axis_columns, key)
        .distinct()
    )
    degrees = memberships.group_by([*bucket, key]).aggregate(
        __membership_degree=memberships.count()
    )
    joined = memberships.left_join(degrees, [*bucket, key])
    grouped = joined.group_by([*bucket, *axis_columns]).aggregate(
        **{
            f"{prefix}_observed_distinct": joined[key].nunique(),
            f"{prefix}_allocated_distinct": (1.0 / joined.__membership_degree).sum(),
        }
    )
    frame = _run_dataframe(grouped, prepared, session)
    overlap_groups = degrees.filter(degrees.__membership_degree > 1)
    overlap_frame = _run_dataframe(
        overlap_groups.aggregate(__overlap_key_count=overlap_groups.count()),
        prepared,
        session,
    )
    overlap_key_count = int(overlap_frame.iloc[0]["__overlap_key_count"])
    return frame, overlap_key_count


def _merge_scope_rows(
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    axis_columns: Sequence[str],
    bucket_column: str | None,
    current_bucket: object | None,
    baseline_bucket: object | None,
) -> pd.DataFrame:
    current_rows = _bucket_rows(current, bucket_column, current_bucket).drop(
        columns=[] if bucket_column is None else [bucket_column]
    )
    baseline_rows = _bucket_rows(baseline, bucket_column, baseline_bucket).drop(
        columns=[] if bucket_column is None else [bucket_column]
    )
    merged = current_rows.merge(
        baseline_rows,
        on=list(axis_columns),
        how="outer",
    )
    value_columns = [
        "current_observed_distinct",
        "baseline_observed_distinct",
        "current_allocated_distinct",
        "baseline_allocated_distinct",
    ]
    for column in value_columns:
        if column not in merged:
            merged[column] = 0.0
        merged[column] = merged[column].fillna(0.0)
    merged["contribution"] = (
        merged["current_allocated_distinct"] - merged["baseline_allocated_distinct"]
    )
    if bucket_column is not None:
        merged.insert(0, bucket_column, cast("Any", current_bucket))
    return merged


def _endpoint_buckets(
    endpoint: DeltaFrame,
    *,
    bucket_column: str | None,
) -> list[tuple[object | None, object | None, float, float, float]]:
    frame = endpoint._dataframe_copy()
    required_columns = {"current", "baseline", "delta"}
    missing_columns = sorted(required_columns - set(frame.columns))
    if missing_columns:
        raise AttributionMaterializationError(
            message="independent endpoint delta is missing required value columns",
            context={"endpoint_ref": endpoint.ref, "missing_columns": missing_columns},
        )

    def numeric_value(row: pd.Series, column: str) -> float:
        raw_value = row[column]
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise AttributionMaterializationError(
                message="independent endpoint contains a non-numeric value",
                context={
                    "endpoint_ref": endpoint.ref,
                    "column": column,
                    "value_type": type(raw_value).__name__,
                },
            ) from exc
        if not math.isfinite(value):
            raise AttributionMaterializationError(
                message="independent endpoint contains a non-finite value",
                context={
                    "endpoint_ref": endpoint.ref,
                    "column": column,
                    "value": repr(raw_value),
                },
            )
        return value

    if bucket_column is None:
        if len(frame) != 1:
            raise AttributionMaterializationError(
                message="scalar attribution endpoint must contain one row",
                context={"endpoint_ref": endpoint.ref, "row_count": len(frame)},
            )
        row = frame.iloc[0]
        return [
            (
                None,
                None,
                numeric_value(row, "current"),
                numeric_value(row, "baseline"),
                numeric_value(row, "delta"),
            )
        ]
    baseline_column = endpoint.meta.alignment.get("baseline_bucket_column", f"{bucket_column}_b")
    if baseline_column not in frame.columns:
        baseline_column = bucket_column
    return [
        (
            row[bucket_column],
            row[baseline_column],
            numeric_value(row, "current"),
            numeric_value(row, "baseline"),
            numeric_value(row, "delta"),
        )
        for _, row in frame.iterrows()
    ]


def _resolution_rows(
    piece: pd.DataFrame,
    *,
    all_axis_columns: Sequence[str],
    prefix_columns: Sequence[str],
) -> pd.DataFrame:
    level = len(prefix_columns)
    out = piece.copy()
    for column in all_axis_columns[level:]:
        out[column] = pd.NA

    def display_value(row: pd.Series, column: str, axis_position: int) -> object:
        if ATTRIBUTION_OTHER_MASK_COLUMN in row.index and int(
            row[ATTRIBUTION_OTHER_MASK_COLUMN]
        ) & (1 << axis_position):
            return "Other"
        return row[column]

    out.insert(
        0,
        ATTRIBUTION_PATH_COLUMN,
        out.apply(
            lambda row: " > ".join(
                str(display_value(row, column, index))
                for index, column in enumerate(prefix_columns)
            ),
            axis=1,
        ),
    )
    out.insert(
        0,
        ATTRIBUTION_DRIVER_COLUMN,
        out.apply(
            lambda row: display_value(row, prefix_columns[-1], level - 1),
            axis=1,
        ),
    )
    out.insert(0, ATTRIBUTION_AXIS_COLUMN, prefix_columns[-1])
    out.insert(0, ATTRIBUTION_LEVEL_COLUMN, level)
    ordered = [
        ATTRIBUTION_LEVEL_COLUMN,
        ATTRIBUTION_AXIS_COLUMN,
        ATTRIBUTION_DRIVER_COLUMN,
        ATTRIBUTION_PATH_COLUMN,
        *all_axis_columns,
    ]
    bucket_columns = [column for column in out.columns if column == "bucket_start"]
    if bucket_columns:
        ordered = [bucket_columns[0], *ordered]
    return out[[*ordered, *[column for column in out.columns if column not in ordered]]]


def attribute_distinct(
    *,
    current: MetricFrame,
    baseline: MetricFrame,
    endpoint_delta: DeltaFrame,
    basis: DistinctAttributionBasisV1,
    axis_ids: list[str],
    mode: Literal["joint", "hierarchy"] | None,
    source_delta_ref: str,
    top_k: int | None,
    session: Session,
) -> NonAdditiveAttributionResultV1:
    current_prepared = _prepared_evidence(current, basis=basis, axis_ids=axis_ids, session=session)
    baseline_prepared = _prepared_evidence(
        baseline, basis=basis, axis_ids=axis_ids, session=session
    )
    if (
        current_prepared.axis_columns != baseline_prepared.axis_columns
        or current_prepared.bucket_column != baseline_prepared.bucket_column
    ):
        raise AttributionBasisMismatchError(
            message="current and baseline attribution replay axes differ",
            expected=repr(current_prepared.axis_columns),
            received=repr(baseline_prepared.axis_columns),
            location="session.attribute replay",
        )
    all_axes = list(current_prepared.axis_columns)
    top_k_map, top_k_selection = _build_nonadditive_top_k(
        current_prepared,
        baseline_prepared,
        top_k=top_k,
        distinct=True,
        session=session,
    )
    resolutions = (
        [all_axes]
        if mode != "hierarchy"
        else [all_axes[:level] for level in range(1, len(all_axes) + 1)]
    )
    bucket_column = current_prepared.bucket_column
    endpoint_buckets = _endpoint_buckets(endpoint_delta, bucket_column=bucket_column)
    output_pieces: list[pd.DataFrame] = []
    reconciliations: list[AttributionResolutionReconciliationV1] = []
    overlap_count = 0
    for prefix_axes in resolutions:
        partition_axes = [*prefix_axes]
        current_scope = current_prepared
        baseline_scope = baseline_prepared
        if top_k_map is not None:
            partition_axes.append(ATTRIBUTION_OTHER_MASK_COLUMN)
            current_scope = replace(
                current_prepared,
                table=_apply_top_k_table(current_prepared.table, top_k_map, level=len(prefix_axes)),
            )
            baseline_scope = replace(
                baseline_prepared,
                table=_apply_top_k_table(
                    baseline_prepared.table, top_k_map, level=len(prefix_axes)
                ),
            )
        current_rows, current_overlap = _distinct_scope(
            current_scope,
            axis_columns=partition_axes,
            session=session,
            prefix="current",
        )
        baseline_rows, baseline_overlap = _distinct_scope(
            baseline_scope,
            axis_columns=partition_axes,
            session=session,
            prefix="baseline",
        )
        overlap_count += current_overlap + baseline_overlap
        for current_bucket, baseline_bucket, _, _, target_delta in endpoint_buckets:
            piece = _merge_scope_rows(
                current_rows,
                baseline_rows,
                axis_columns=partition_axes,
                bucket_column=bucket_column,
                current_bucket=current_bucket,
                baseline_bucket=baseline_bucket,
            )
            piece = _rank_rows(
                piece,
                total_delta=target_delta,
                group_columns=[
                    *([] if bucket_column is None else [bucket_column]),
                    *prefix_axes,
                    *([ATTRIBUTION_OTHER_MASK_COLUMN] if top_k_map is not None else []),
                ],
            )
            contribution_sum = float(piece["contribution"].sum())
            residual = target_delta - contribution_sum
            if abs(residual) > _RECONCILIATION_TOLERANCE:
                raise AttributionDistributionError(
                    message="distinct membership allocation did not reproduce its endpoint",
                    expected=f"residual <= {_RECONCILIATION_TOLERANCE}",
                    received=(
                        f"abs_residual={abs(residual)!r} target_delta={target_delta!r} "
                        f"contribution_sum={contribution_sum!r} "
                        f"current_bucket={current_bucket!r} baseline_bucket={baseline_bucket!r}"
                    ),
                    location="session.attribute distinct reconciliation",
                    context={
                        "reason": "endpoint_reproduction_mismatch",
                        "current_bucket": repr(current_bucket),
                        "baseline_bucket": repr(baseline_bucket),
                        "partition_count": len(piece),
                        "target_delta": target_delta,
                        "contribution_sum": contribution_sum,
                    },
                )
            prefix_refs = tuple(
                current_prepared.axis_bindings[index].ref for index in range(len(prefix_axes))
            )
            reconciliations.append(
                AttributionResolutionReconciliationV1(
                    axis_refs=prefix_refs,
                    bucket_key=_bucket_key(bucket_column, current_bucket),
                    partition_count=len(piece),
                    total_delta=target_delta,
                    contribution_sum=contribution_sum,
                    residual=residual,
                    max_abs_residual=abs(residual),
                )
            )
            if mode == "hierarchy":
                piece = _resolution_rows(
                    piece,
                    all_axis_columns=all_axes,
                    prefix_columns=prefix_axes,
                )
            output_pieces.append(piece)
    output = pd.concat(output_pieces, ignore_index=True) if output_pieces else pd.DataFrame()
    deepest = [item for item in reconciliations if len(item.axis_refs) == len(all_axes)]
    common = AttributionReconciliation(
        partition_count=sum(item.partition_count for item in deepest),
        total_delta=sum(item.total_delta for item in deepest),
        contribution_sum=sum(item.contribution_sum for item in deepest),
        residual=sum(item.residual for item in deepest),
        max_abs_residual=max((item.max_abs_residual for item in deepest), default=0.0),
    )
    hierarchy = (
        IndependentHierarchyEvidenceV1(
            scope=CompleteHierarchyScopeV1(),
            resolution_reconciliations=tuple(reconciliations),
        )
        if mode == "hierarchy"
        else None
    )
    evidence = DistinctMembershipEvidenceV1(
        source_basis_fingerprint=cast("str", basis_fingerprint(basis)),
        overlap_key_count=overlap_count,
    )
    return NonAdditiveAttributionResultV1(
        dataframe=output,
        axis_columns=all_axes,
        bucket_column=bucket_column,
        reconciliation=common,
        method_evidence=evidence,
        resolution_evidence=hierarchy,
        top_k_selection=top_k_selection,
    )


def weighted_linear_quantile(values: pd.DataFrame, *, q: float) -> float:
    """Evaluate DuckDB-compatible Type-7 quantile from value-frequency rows."""
    if values.empty:
        raise AttributionDistributionError(
            message="quantile coalition has no non-null distribution",
            expected="at least one non-null value in every evaluated coalition",
            received="empty distribution",
            location="session.attribute quantile coalition",
            context={"reason": "empty_coalition_distribution"},
        )
    ordered = values.groupby("value", dropna=False, as_index=False)["frequency"].sum()
    ordered = ordered[ordered["value"].notna()].sort_values("value", kind="mergesort")
    count = int(ordered["frequency"].sum())
    if count <= 0:
        raise AttributionDistributionError(
            message="quantile coalition has no non-null distribution",
            expected="at least one non-null value in every evaluated coalition",
            received="empty distribution",
            location="session.attribute quantile coalition",
            context={"reason": "empty_coalition_distribution"},
        )
    position = (count - 1) * q
    low_rank = math.floor(position)
    high_rank = math.ceil(position)

    def value_at(rank: int) -> float:
        running = 0
        for _, row in ordered.iterrows():
            running += int(row["frequency"])
            if rank < running:
                return float(row["value"])
        raise AssertionError("weighted quantile rank exceeded the distribution")

    low = value_at(low_rank)
    high = value_at(high_rank)
    return low + (position - low_rank) * (high - low)


def _distribution_for_coalition(
    current: dict[tuple[object, ...], pd.DataFrame],
    baseline: dict[tuple[object, ...], pd.DataFrame],
    partitions: Sequence[tuple[object, ...]],
    selected: frozenset[int],
) -> pd.DataFrame:
    pieces = [
        (current if index in selected else baseline).get(partition)
        for index, partition in enumerate(partitions)
    ]
    retained = [piece for piece in pieces if piece is not None and not piece.empty]
    return (
        pd.concat(retained, ignore_index=True)
        if retained
        else pd.DataFrame(columns=["value", "frequency"])
    )


def _exact_shapley(
    current: dict[tuple[object, ...], pd.DataFrame],
    baseline: dict[tuple[object, ...], pd.DataFrame],
    partitions: Sequence[tuple[object, ...]],
    *,
    q: float,
) -> tuple[list[float], list[float]]:
    count = len(partitions)
    values: dict[frozenset[int], float] = {}

    def coalition(selected: frozenset[int]) -> float:
        if selected not in values:
            values[selected] = weighted_linear_quantile(
                _distribution_for_coalition(current, baseline, partitions, selected),
                q=q,
            )
        return values[selected]

    contributions = [0.0] * count
    denominator = math.factorial(count)
    for index in range(count):
        others = [item for item in range(count) if item != index]
        for size in range(count):
            weight = math.factorial(size) * math.factorial(count - size - 1) / denominator
            for subset in itertools.combinations(others, size):
                selected = frozenset(subset)
                contributions[index] += weight * (
                    coalition(selected | {index}) - coalition(selected)
                )
    return contributions, [0.0] * count


def _permutation_shapley(
    current: dict[tuple[object, ...], pd.DataFrame],
    baseline: dict[tuple[object, ...], pd.DataFrame],
    partitions: Sequence[tuple[object, ...]],
    *,
    q: float,
    seed_material: str,
) -> tuple[list[float], list[float], str]:
    digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest, 16))
    samples: list[list[float]] = [[] for _ in partitions]
    for _ in range(_PERMUTATION_COUNT):
        order = list(range(len(partitions)))
        rng.shuffle(order)
        selected: frozenset[int] = frozenset()
        previous = weighted_linear_quantile(
            _distribution_for_coalition(current, baseline, partitions, selected), q=q
        )
        for index in order:
            selected = selected | {index}
            current_value = weighted_linear_quantile(
                _distribution_for_coalition(current, baseline, partitions, selected),
                q=q,
            )
            samples[index].append(current_value - previous)
            previous = current_value
    means = [sum(values) / len(values) for values in samples]
    standard_errors = []
    for values, mean in zip(samples, means, strict=True):
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        standard_errors.append(math.sqrt(variance / len(values)))
    return means, standard_errors, f"sha256:{digest}"


def _frequency_frame(
    prepared: PreparedEvidenceV1,
    *,
    session: Session,
) -> pd.DataFrame:
    bucket = [] if prepared.bucket_column is None else [prepared.bucket_column]
    value = prepared.value_column
    filtered = prepared.table.filter(prepared.table[value].notnull())
    expression = filtered.group_by([*bucket, *prepared.axis_columns, value]).aggregate(
        frequency=filtered.count()
    )
    frame = _run_dataframe(expression, prepared, session).rename(columns={value: "value"})
    if len(frame) > _MAX_FREQUENCY_ROWS:
        raise AttributionDistributionError(
            message="quantile value-frequency evidence exceeds the execution limit",
            expected=f"frequency_rows <= {_MAX_FREQUENCY_ROWS}",
            received=f"frequency_rows={len(frame)}",
            location="session.attribute quantile evidence",
            context={"reason": "frequency_row_limit_exceeded"},
        )
    return frame


def _quantile_partition_keys(
    prepared: PreparedEvidenceV1,
    *,
    session: Session,
) -> pd.DataFrame:
    """Materialize only governed partition keys for cardinality admission."""
    columns = [
        *([] if prepared.bucket_column is None else [prepared.bucket_column]),
        *prepared.axis_columns,
    ]
    filtered = prepared.table.filter(prepared.table[prepared.value_column].notnull())
    return _run_dataframe(filtered.select(*columns).distinct(), prepared, session)


def _preflight_quantile_partition_limit(
    *,
    current_keys: pd.DataFrame,
    baseline_keys: pd.DataFrame,
    resolutions: Sequence[Sequence[str]],
    bucket_column: str | None,
    endpoint_buckets: Sequence[tuple[object | None, object | None, float, float, float]],
) -> None:
    """Reject oversized games before any value-frequency evidence is materialized."""
    for prefix_axes in resolutions:
        for current_bucket, baseline_bucket, _, _, _ in endpoint_buckets:
            current_rows = _bucket_rows(current_keys, bucket_column, current_bucket)
            baseline_rows = _bucket_rows(baseline_keys, bucket_column, baseline_bucket)
            partition_rows = pd.concat(
                [current_rows[list(prefix_axes)], baseline_rows[list(prefix_axes)]],
                ignore_index=True,
            ).drop_duplicates()
            partition_count = len(partition_rows)
            if partition_count > _MAX_QUANTILE_PARTITIONS:
                raise AttributionDistributionError(
                    message="quantile attribution partition count exceeds the execution limit",
                    expected=f"partitions <= {_MAX_QUANTILE_PARTITIONS}",
                    received=f"partitions={partition_count}",
                    location="session.attribute quantile partition admission",
                    context={
                        "reason": "partition_limit_exceeded",
                        "axis_prefix": list(prefix_axes),
                        "repair": "choose a coarser or lower-cardinality axis",
                    },
                )


def _partition_distributions(
    frame: pd.DataFrame,
    *,
    axis_columns: Sequence[str],
) -> dict[tuple[object, ...], pd.DataFrame]:
    result: dict[tuple[object, ...], pd.DataFrame] = {}
    if frame.empty:
        return result
    for partition, rows in frame.groupby(list(axis_columns), dropna=False, sort=True):
        values = partition if isinstance(partition, tuple) else (partition,)
        key = tuple(None if _is_missing(value) else value for value in values)
        result[key] = rows[["value", "frequency"]].reset_index(drop=True)
    return result


def attribute_exact_quantile(
    *,
    current: MetricFrame,
    baseline: MetricFrame,
    endpoint_delta: DeltaFrame,
    basis: QuantileAttributionBasisV1,
    axis_ids: list[str],
    mode: Literal["joint", "hierarchy"] | None,
    source_delta_ref: str,
    top_k: int | None,
    session: Session,
) -> NonAdditiveAttributionResultV1:
    current_prepared = _prepared_evidence(current, basis=basis, axis_ids=axis_ids, session=session)
    baseline_prepared = _prepared_evidence(
        baseline, basis=basis, axis_ids=axis_ids, session=session
    )
    if current_prepared.datasource_name != baseline_prepared.datasource_name:
        raise AttributionBasisMismatchError(
            message="quantile attribution sources use different datasource adapters",
            expected=current_prepared.datasource_name,
            received=baseline_prepared.datasource_name,
            location="session.attribute quantile replay",
        )
    all_axes = list(current_prepared.axis_columns)
    top_k_map, top_k_selection = _build_nonadditive_top_k(
        current_prepared,
        baseline_prepared,
        top_k=top_k,
        distinct=False,
        session=session,
    )
    resolutions = (
        [all_axes]
        if mode != "hierarchy"
        else [all_axes[:level] for level in range(1, len(all_axes) + 1)]
    )
    bucket_column = current_prepared.bucket_column
    endpoint_buckets = _endpoint_buckets(endpoint_delta, bucket_column=bucket_column)
    current_keys = _quantile_partition_keys(current_prepared, session=session)
    baseline_keys = _quantile_partition_keys(baseline_prepared, session=session)
    for prefix_axes in resolutions:
        partition_axes = [*prefix_axes]
        scoped_current_keys = current_keys
        scoped_baseline_keys = baseline_keys
        if top_k_map is not None:
            partition_axes.append(ATTRIBUTION_OTHER_MASK_COLUMN)
            scoped_current_keys = top_k_map.map_frame(current_keys, level=len(prefix_axes))
            scoped_baseline_keys = top_k_map.map_frame(baseline_keys, level=len(prefix_axes))
        _preflight_quantile_partition_limit(
            current_keys=scoped_current_keys,
            baseline_keys=scoped_baseline_keys,
            resolutions=[partition_axes],
            bucket_column=bucket_column,
            endpoint_buckets=endpoint_buckets,
        )
    current_frequency = _frequency_frame(current_prepared, session=session)
    baseline_frequency = _frequency_frame(baseline_prepared, session=session)
    if len(current_frequency) + len(baseline_frequency) > _MAX_FREQUENCY_ROWS:
        raise AttributionDistributionError(
            message="quantile value-frequency evidence exceeds the call limit",
            expected=f"frequency_rows <= {_MAX_FREQUENCY_ROWS}",
            received=f"frequency_rows={len(current_frequency) + len(baseline_frequency)}",
            location="session.attribute quantile evidence",
            context={"reason": "frequency_row_limit_exceeded"},
        )
    output_pieces: list[pd.DataFrame] = []
    reconciliations: list[AttributionResolutionReconciliationV1] = []
    q = basis.effective_q
    for prefix_axes in resolutions:
        partition_axes = [*prefix_axes]
        if top_k_map is not None:
            partition_axes.append(ATTRIBUTION_OTHER_MASK_COLUMN)
        for current_bucket, baseline_bucket, _, _, target_delta in endpoint_buckets:
            current_rows = _bucket_rows(current_frequency, bucket_column, current_bucket)
            baseline_rows = _bucket_rows(baseline_frequency, bucket_column, baseline_bucket)
            if top_k_map is not None:
                current_rows = top_k_map.map_frame(current_rows, level=len(prefix_axes))
                baseline_rows = top_k_map.map_frame(baseline_rows, level=len(prefix_axes))
                current_rows = (
                    current_rows.groupby([*partition_axes, "value"], dropna=False, sort=False)[
                        "frequency"
                    ]
                    .sum()
                    .reset_index()
                )
                baseline_rows = (
                    baseline_rows.groupby([*partition_axes, "value"], dropna=False, sort=False)[
                        "frequency"
                    ]
                    .sum()
                    .reset_index()
                )
            current_distributions = _partition_distributions(
                current_rows, axis_columns=partition_axes
            )
            baseline_distributions = _partition_distributions(
                baseline_rows, axis_columns=partition_axes
            )
            partitions = sorted(
                set(current_distributions) | set(baseline_distributions),
                key=lambda values: tuple(repr(value) for value in values),
            )
            partition_count = len(partitions)
            if partition_count > _MAX_QUANTILE_PARTITIONS:
                raise AttributionDistributionError(
                    message="quantile attribution partition count exceeds the execution limit",
                    expected=f"partitions <= {_MAX_QUANTILE_PARTITIONS}",
                    received=f"partitions={partition_count}",
                    location="session.attribute quantile partition admission",
                    context={
                        "reason": "partition_limit_exceeded",
                        "axis_prefix": prefix_axes,
                        "repair": "choose a coarser or lower-cardinality axis",
                    },
                )
            if partition_count == 0:
                raise AttributionDistributionError(
                    message="quantile endpoint has no distribution partitions",
                    expected="at least one current or baseline partition",
                    received="partitions=0",
                    location="session.attribute quantile endpoint",
                    context={"reason": "empty_coalition_distribution"},
                )
            seed_material = "|".join(
                [
                    source_delta_ref,
                    *(
                        binding.ref.path
                        for binding in current_prepared.axis_bindings[: len(prefix_axes)]
                    ),
                    repr(current_bucket),
                    str(len(prefix_axes)),
                    _QUANTILE_OPERATOR_VERSION,
                ]
            )
            if partition_count <= _MAX_EXACT_QUANTILE_PARTITIONS:
                contributions, standard_errors = _exact_shapley(
                    current_distributions,
                    baseline_distributions,
                    partitions,
                    q=q,
                )
                seed_fingerprint = None
            else:
                contributions, standard_errors, seed_fingerprint = _permutation_shapley(
                    current_distributions,
                    baseline_distributions,
                    partitions,
                    q=q,
                    seed_material=seed_material,
                )
            execution = _quantile_execution_evidence(seed_fingerprint=seed_fingerprint)
            rows = []
            for partition, contribution, standard_error in zip(
                partitions, contributions, standard_errors, strict=True
            ):
                current_count = int(
                    current_distributions.get(partition, pd.DataFrame(columns=["frequency"]))[
                        "frequency"
                    ].sum()
                )
                baseline_count = int(
                    baseline_distributions.get(partition, pd.DataFrame(columns=["frequency"]))[
                        "frequency"
                    ].sum()
                )
                row = dict(zip(partition_axes, partition, strict=True))
                row.update(
                    {
                        "current_count": current_count,
                        "baseline_count": baseline_count,
                        "contribution": contribution,
                        "contribution_std_error": standard_error,
                    }
                )
                rows.append(row)
            piece = pd.DataFrame(rows)
            if bucket_column is not None:
                piece.insert(0, bucket_column, cast("Any", current_bucket))
            piece = _rank_rows(
                piece,
                total_delta=target_delta,
                group_columns=[
                    *([] if bucket_column is None else [bucket_column]),
                    *prefix_axes,
                    *([ATTRIBUTION_OTHER_MASK_COLUMN] if top_k_map is not None else []),
                ],
            )
            contribution_sum = float(piece["contribution"].sum())
            residual = target_delta - contribution_sum
            if abs(residual) > _RECONCILIATION_TOLERANCE:
                raise AttributionDistributionError(
                    message="quantile replacement game did not reproduce its endpoint",
                    expected=f"residual <= {_RECONCILIATION_TOLERANCE}",
                    received=f"abs_residual={abs(residual)!r}",
                    location="session.attribute quantile endpoint reproduction",
                    context={"reason": "endpoint_reproduction_mismatch"},
                )
            prefix_refs = tuple(
                binding.ref for binding in current_prepared.axis_bindings[: len(prefix_axes)]
            )
            reconciliations.append(
                AttributionResolutionReconciliationV1(
                    axis_refs=prefix_refs,
                    bucket_key=_bucket_key(bucket_column, current_bucket),
                    partition_count=partition_count,
                    total_delta=target_delta,
                    contribution_sum=contribution_sum,
                    residual=residual,
                    max_abs_residual=abs(residual),
                    quantile_execution=execution,
                )
            )
            if mode == "hierarchy":
                piece = _resolution_rows(
                    piece,
                    all_axis_columns=all_axes,
                    prefix_columns=prefix_axes,
                )
            output_pieces.append(piece)
    output = pd.concat(output_pieces, ignore_index=True) if output_pieces else pd.DataFrame()
    deepest = [item for item in reconciliations if len(item.axis_refs) == len(all_axes)]
    common = AttributionReconciliation(
        partition_count=sum(item.partition_count for item in deepest),
        total_delta=sum(item.total_delta for item in deepest),
        contribution_sum=sum(item.contribution_sum for item in deepest),
        residual=sum(item.residual for item in deepest),
        max_abs_residual=max((item.max_abs_residual for item in deepest), default=0.0),
    )
    hierarchy = (
        IndependentHierarchyEvidenceV1(
            scope=CompleteHierarchyScopeV1(),
            resolution_reconciliations=tuple(reconciliations),
        )
        if mode == "hierarchy"
        else None
    )
    reproduction = basis.reproduction
    if reproduction.status != "reproducible":
        raise AssertionError("blocked quantile basis reached exact execution")
    evidence_summary = summarize_quantile_resolution_executions(reconciliations)
    evidence = QuantileReplacementEvidenceV1(
        q=q,
        source_mode=reproduction.source_mode,
        source_method=reproduction.source_method,
        distribution_representation=reproduction.distribution_representation,
        **evidence_summary,
        source_error_bound=None,
        scope_reconciliations=tuple(reconciliations),
    )
    return NonAdditiveAttributionResultV1(
        dataframe=output,
        axis_columns=all_axes,
        bucket_column=bucket_column,
        reconciliation=common,
        method_evidence=evidence,
        resolution_evidence=hierarchy,
        top_k_selection=top_k_selection,
    )


def _or_partition_predicates(
    table: Any,
    columns: Sequence[str],
    partitions: Sequence[tuple[object, ...]],
    *,
    partition_masks: dict[tuple[object, ...], Any] | None = None,
) -> Any:
    if not partitions:
        return ibis.literal(False)
    masks = partition_masks if partition_masks is not None else {}

    def mask_for(partition: tuple[object, ...]) -> Any:
        mask = masks.get(partition)
        if mask is None:
            mask = _partition_mask(table, columns, partition)
            masks[partition] = mask
        return mask

    predicate = mask_for(partitions[0])
    for partition in partitions[1:]:
        predicate = predicate | mask_for(partition)
    return predicate


def _native_percentile_scope(
    prepared: PreparedEvidenceV1,
    *,
    bucket_value: object | None,
) -> Any:
    table = prepared.table
    if prepared.bucket_column is not None:
        bucket_field = table[prepared.bucket_column]
        table = table.filter(
            bucket_field.isnull()
            if _is_missing(bucket_value)
            else bucket_field == ibis.literal(bucket_value)
        )
    table = table.filter(table[prepared.value_column].notnull())
    return table


def _native_percentile_period_samples(
    current_values: Any,
    baseline_values: Any,
    *,
    columns: Sequence[str],
) -> Any:
    current = current_values.select(
        *columns,
        **{_NATIVE_PERCENTILE_PERIOD_COLUMN: ibis.literal(_NATIVE_PERCENTILE_CURRENT_PERIOD)},
    )
    baseline = baseline_values.select(
        *columns,
        **{_NATIVE_PERCENTILE_PERIOD_COLUMN: ibis.literal(_NATIVE_PERCENTILE_BASELINE_PERIOD)},
    )
    return ibis.union(current, baseline, distinct=False)


def _native_percentile_partition_counts(
    current_values: Any,
    baseline_values: Any,
    *,
    axis_columns: Sequence[str],
    prepared: PreparedEvidenceV1,
    session: Session,
) -> tuple[
    dict[tuple[object, ...], int],
    dict[tuple[object, ...], int],
]:
    samples = _native_percentile_period_samples(
        current_values,
        baseline_values,
        columns=[*axis_columns, prepared.value_column],
    )
    expression = samples.group_by([_NATIVE_PERCENTILE_PERIOD_COLUMN, *axis_columns]).aggregate(
        __count=samples[prepared.value_column].count()
    )
    frame = _run_dataframe(expression, prepared, session)
    counts: dict[str, dict[tuple[object, ...], int]] = {
        _NATIVE_PERCENTILE_CURRENT_PERIOD: {},
        _NATIVE_PERCENTILE_BASELINE_PERIOD: {},
    }
    for _, row in frame.iterrows():
        period = str(row[_NATIVE_PERCENTILE_PERIOD_COLUMN])
        if period not in counts:
            raise AssertionError(f"unexpected native percentile period {period!r}")
        counts[period][_partition_tuple(row, axis_columns)] = int(row["__count"])
    return (
        counts[_NATIVE_PERCENTILE_CURRENT_PERIOD],
        counts[_NATIVE_PERCENTILE_BASELINE_PERIOD],
    )


def _map_native_counts(
    counts: dict[tuple[object, ...], int],
    *,
    mapping: AttributionTopKMapV1,
    prefix_axes: list[str],
) -> tuple[
    dict[tuple[object, ...], int],
    dict[tuple[object, ...], tuple[tuple[object, ...], ...]],
]:
    rows = [
        {**dict(zip(prefix_axes, partition, strict=True)), "__count": count}
        for partition, count in counts.items()
    ]
    frame = mapping.map_frame(
        pd.DataFrame(rows, columns=[*prefix_axes, "__count"]),
        level=len(prefix_axes),
    )
    partition_columns = [*prefix_axes, ATTRIBUTION_OTHER_MASK_COLUMN]
    grouped = (
        frame.groupby(partition_columns, dropna=False, sort=False)["__count"].sum().reset_index()
    )
    mapped_counts = {
        _partition_tuple(row, partition_columns): int(row["__count"])
        for _, row in grouped.iterrows()
    }
    members: dict[tuple[object, ...], list[tuple[object, ...]]] = {}
    for raw_partition, (_, row) in zip(counts, frame.iterrows(), strict=True):
        effective = _partition_tuple(row, partition_columns)
        members.setdefault(effective, []).append(raw_partition)
    return mapped_counts, {key: tuple(value) for key, value in members.items()}


def _plan_shapley(
    partition_count: int,
    *,
    seed_material: str,
) -> ShapleyPlanV1:
    full_coalition = frozenset(range(partition_count))
    if partition_count <= _MAX_EXACT_QUANTILE_PARTITIONS:
        exact_coalitions = tuple(
            frozenset(selected)
            for size in range(1, partition_count)
            for selected in itertools.combinations(range(partition_count), size)
        )
        return ShapleyPlanV1(
            partition_count=partition_count,
            coalitions=exact_coalitions,
            permutation_orders=(),
            seed_fingerprint=None,
        )
    digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest, 16))
    permutation_orders: list[tuple[int, ...]] = []
    sampled_coalitions: set[frozenset[int]] = set()
    for _ in range(_PERMUTATION_COUNT):
        order = list(range(partition_count))
        rng.shuffle(order)
        permutation_orders.append(tuple(order))
        selected: frozenset[int] = frozenset()
        for index in order:
            selected = selected | {index}
            if selected != full_coalition:
                sampled_coalitions.add(selected)
    return ShapleyPlanV1(
        partition_count=partition_count,
        coalitions=tuple(
            sorted(
                sampled_coalitions,
                key=lambda selected: (len(selected), tuple(sorted(selected))),
            )
        ),
        permutation_orders=tuple(permutation_orders),
        seed_fingerprint=f"sha256:{digest}",
    )


def _shapley_from_values(
    plan: ShapleyPlanV1,
    *,
    coalition_values: dict[frozenset[int], int | float],
) -> tuple[list[float], list[float], str | None]:
    partition_count = plan.partition_count
    if plan.seed_fingerprint is None:
        contributions = [0.0] * partition_count
        denominator = math.factorial(partition_count)
        for index in range(partition_count):
            others = [item for item in range(partition_count) if item != index]
            for size in range(partition_count):
                weight = (
                    math.factorial(size) * math.factorial(partition_count - size - 1) / denominator
                )
                for subset in itertools.combinations(others, size):
                    selected_subset = frozenset(subset)
                    contributions[index] += weight * (
                        coalition_values[selected_subset | {index}]
                        - coalition_values[selected_subset]
                    )
        return contributions, [0.0] * partition_count, None
    samples: list[list[float]] = [[] for _ in range(partition_count)]
    for order in plan.permutation_orders:
        selected: frozenset[int] = frozenset()
        previous = coalition_values[selected]
        for index in order:
            selected = selected | {index}
            current = coalition_values[selected]
            samples[index].append(current - previous)
            previous = current
    means = [sum(item_samples) / len(item_samples) for item_samples in samples]
    standard_errors = []
    for item_samples, mean in zip(samples, means, strict=True):
        variance = sum((value - mean) ** 2 for value in item_samples) / (len(item_samples) - 1)
        standard_errors.append(math.sqrt(variance / len(item_samples)))
    return means, standard_errors, plan.seed_fingerprint


def _quantile_execution_evidence(*, seed_fingerprint: str | None) -> QuantileResolutionExecutionV1:
    if seed_fingerprint is None:
        return QuantileResolutionExecutionV1(
            coalition="exact_shapley",
            permutation_count=0,
        )
    return QuantileResolutionExecutionV1(
        coalition="permutation_shapley",
        permutation_count=_PERMUTATION_COUNT,
        deterministic_seed_fingerprint=seed_fingerprint,
    )


def _native_percentile_partition_members(
    partitions: Sequence[tuple[object, ...]],
    selected: frozenset[int],
    *,
    partition_members: dict[tuple[object, ...], tuple[tuple[object, ...], ...]] | None,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    current_groups = [partitions[index] for index in sorted(selected)]
    baseline_groups = [
        partition for index, partition in enumerate(partitions) if index not in selected
    ]

    def expand(groups: Sequence[tuple[object, ...]]) -> list[tuple[object, ...]]:
        return [
            raw
            for partition in groups
            for raw in (
                partition_members.get(partition, ())
                if partition_members is not None
                else (partition,)
            )
        ]

    return expand(current_groups), expand(baseline_groups)


def trino_native_percentile_coalitions_expression(
    current_values: Any,
    baseline_values: Any,
    *,
    coalitions: Sequence[frozenset[int]],
    partitions: Sequence[tuple[object, ...]],
    partition_members: dict[tuple[object, ...], tuple[tuple[object, ...], ...]] | None,
    prefix_axes: Sequence[str],
    value_column: str,
    q: float,
) -> Any:
    """Compile one batch of Trino native approx_percentile replacement coalitions."""
    samples = _native_percentile_period_samples(
        current_values,
        baseline_values,
        columns=[*prefix_axes, value_column],
    )
    period_field = samples[_NATIVE_PERCENTILE_PERIOD_COLUMN]
    value_field = samples[value_column]
    current_period = period_field == _NATIVE_PERCENTILE_CURRENT_PERIOD
    baseline_period = period_field == _NATIVE_PERCENTILE_BASELINE_PERIOD
    partition_masks: dict[tuple[object, ...], Any] = {}
    partition_predicates: dict[tuple[tuple[object, ...], ...], Any] = {}

    def predicate_for(partitions: Sequence[tuple[object, ...]]) -> Any:
        key = tuple(partitions)
        predicate = partition_predicates.get(key)
        if predicate is None:
            predicate = _or_partition_predicates(
                samples,
                prefix_axes,
                key,
                partition_masks=partition_masks,
            )
            partition_predicates[key] = predicate
        return predicate

    aggregates = {}
    for index, selected in enumerate(coalitions):
        current_partitions, baseline_partitions = _native_percentile_partition_members(
            partitions,
            selected,
            partition_members=partition_members,
        )
        current_predicate = current_period & predicate_for(current_partitions)
        baseline_predicate = baseline_period & predicate_for(baseline_partitions)
        aggregates[f"coalition_{index}"] = value_field.approx_quantile(
            q,
            where=current_predicate | baseline_predicate,
        )
    return samples.aggregate(**aggregates)


def _native_percentile_coalition_values(
    *,
    plan: ShapleyPlanV1,
    partitions: Sequence[tuple[object, ...]],
    partition_members: dict[tuple[object, ...], tuple[tuple[object, ...], ...]] | None,
    current_values: Any,
    baseline_values: Any,
    current_endpoint: float,
    baseline_endpoint: float,
    prefix_axes: Sequence[str],
    q: float,
    prepared: PreparedEvidenceV1,
    session: Session,
) -> dict[frozenset[int], int | float]:
    full_coalition = frozenset(range(len(partitions)))
    evaluated: dict[frozenset[int], int | float] = {
        frozenset(): baseline_endpoint,
        full_coalition: current_endpoint,
    }
    for offset in range(0, len(plan.coalitions), _NATIVE_PERCENTILE_BATCH_SIZE):
        batch = plan.coalitions[offset : offset + _NATIVE_PERCENTILE_BATCH_SIZE]
        expression = trino_native_percentile_coalitions_expression(
            current_values,
            baseline_values,
            coalitions=batch,
            partitions=partitions,
            partition_members=partition_members,
            prefix_axes=prefix_axes,
            value_column=prepared.value_column,
            q=q,
        )
        frame = _run_dataframe(expression, prepared, session)
        for index, selected in enumerate(batch):
            value = frame.iloc[0][f"coalition_{index}"] if len(frame) else None
            if value is None or pd.isna(value):
                raise AttributionDistributionError(
                    message="native percentile coalition has no distribution",
                    expected="at least one non-null value in every evaluated coalition",
                    received="empty distribution",
                    location="session.attribute native percentile coalition",
                    context={"reason": "empty_coalition_distribution"},
                )
            scalar_value = value.item() if hasattr(value, "item") else value
            if isinstance(scalar_value, bool) or not isinstance(scalar_value, (int, float)):
                raise AttributionDistributionError(
                    message="native percentile coalition returned a non-numeric quantile",
                    expected="a numeric Trino approx_percentile result",
                    received=type(scalar_value).__name__,
                    location="session.attribute native percentile coalition",
                )
            if not math.isfinite(float(scalar_value)):
                raise AttributionDistributionError(
                    message="native percentile coalition returned a non-finite quantile",
                    expected="a finite Trino approx_percentile result",
                    received=repr(scalar_value),
                    location="session.attribute native percentile coalition",
                )
            evaluated[selected] = scalar_value
    return evaluated


def attribute_native_percentile_quantile(
    *,
    current: MetricFrame,
    baseline: MetricFrame,
    endpoint_delta: DeltaFrame,
    basis: QuantileAttributionBasisV1,
    axis_ids: list[str],
    mode: Literal["joint", "hierarchy"] | None,
    source_delta_ref: str,
    top_k: int | None,
    session: Session,
) -> NonAdditiveAttributionResultV1:
    """Evaluate Trino replacement coalitions with native approx_percentile."""
    current_prepared = _prepared_evidence(current, basis=basis, axis_ids=axis_ids, session=session)
    baseline_prepared = _prepared_evidence(
        baseline, basis=basis, axis_ids=axis_ids, session=session
    )
    reproduction = basis.reproduction
    if reproduction.status != "reproducible":
        raise AssertionError("blocked native percentile basis reached execution")
    for prepared in (current_prepared, baseline_prepared):
        if prepared.value_dtype != reproduction.source_dtype:
            raise AttributionBasisMismatchError(
                message=(
                    "active native percentile value type differs from the persisted "
                    "attribution basis"
                ),
                expected=reproduction.source_dtype,
                received=prepared.value_dtype,
                location="session.attribute native percentile replay",
            )
    if (
        current_prepared.datasource_name != baseline_prepared.datasource_name
        or current_prepared.axis_columns != baseline_prepared.axis_columns
        or current_prepared.bucket_column != baseline_prepared.bucket_column
    ):
        raise AttributionBasisMismatchError(
            message="current and baseline native percentile replay scopes differ",
            expected=(
                f"datasource={current_prepared.datasource_name!r} "
                f"axes={current_prepared.axis_columns!r} "
                f"bucket={current_prepared.bucket_column!r}"
            ),
            received=(
                f"datasource={baseline_prepared.datasource_name!r} "
                f"axes={baseline_prepared.axis_columns!r} "
                f"bucket={baseline_prepared.bucket_column!r}"
            ),
            location="session.attribute native percentile replay",
        )
    all_axes = list(current_prepared.axis_columns)
    top_k_map, top_k_selection = _build_nonadditive_top_k(
        current_prepared,
        baseline_prepared,
        top_k=top_k,
        distinct=False,
        session=session,
    )
    resolutions = (
        [all_axes]
        if mode != "hierarchy"
        else [all_axes[:level] for level in range(1, len(all_axes) + 1)]
    )
    bucket_column = current_prepared.bucket_column
    endpoint_buckets = _endpoint_buckets(endpoint_delta, bucket_column=bucket_column)
    output_pieces: list[pd.DataFrame] = []
    reconciliations: list[AttributionResolutionReconciliationV1] = []
    q = basis.effective_q
    for prefix_axes in resolutions:
        partition_axes = [*prefix_axes]
        if top_k_map is not None:
            partition_axes.append(ATTRIBUTION_OTHER_MASK_COLUMN)
        for (
            current_bucket,
            baseline_bucket,
            current_endpoint,
            baseline_endpoint,
            target_delta,
        ) in endpoint_buckets:
            current_values = _native_percentile_scope(current_prepared, bucket_value=current_bucket)
            baseline_values = _native_percentile_scope(
                baseline_prepared, bucket_value=baseline_bucket
            )
            raw_current_counts, raw_baseline_counts = _native_percentile_partition_counts(
                current_values,
                baseline_values,
                axis_columns=prefix_axes,
                prepared=current_prepared,
                session=session,
            )
            partition_members: dict[tuple[object, ...], tuple[tuple[object, ...], ...]] | None = (
                None
            )
            if top_k_map is not None:
                current_counts, current_members = _map_native_counts(
                    raw_current_counts, mapping=top_k_map, prefix_axes=prefix_axes
                )
                baseline_counts, baseline_members = _map_native_counts(
                    raw_baseline_counts, mapping=top_k_map, prefix_axes=prefix_axes
                )
                merged_members: dict[tuple[object, ...], list[tuple[object, ...]]] = {}
                for source in (current_members, baseline_members):
                    for partition, members in source.items():
                        target = merged_members.setdefault(partition, [])
                        for member in members:
                            if member not in target:
                                target.append(member)
                partition_members = {
                    partition: tuple(members) for partition, members in merged_members.items()
                }
            else:
                current_counts = raw_current_counts
                baseline_counts = raw_baseline_counts
            partitions = sorted(
                set(current_counts) | set(baseline_counts),
                key=lambda values: tuple(repr(value) for value in values),
            )
            partition_count = len(partitions)
            if partition_count == 0 or partition_count > _MAX_QUANTILE_PARTITIONS:
                reason = (
                    "empty_coalition_distribution"
                    if partition_count == 0
                    else "partition_limit_exceeded"
                )
                raise AttributionDistributionError(
                    message="native percentile attribution partition admission failed",
                    expected=f"1 <= partitions <= {_MAX_QUANTILE_PARTITIONS}",
                    received=f"partitions={partition_count}",
                    location="session.attribute native percentile partition admission",
                    context={"reason": reason, "axis_prefix": prefix_axes},
                )
            seed_material = "|".join(
                [
                    source_delta_ref,
                    *(
                        binding.ref.path
                        for binding in current_prepared.axis_bindings[: len(prefix_axes)]
                    ),
                    repr(current_bucket),
                    str(len(prefix_axes)),
                    _QUANTILE_OPERATOR_VERSION,
                ]
            )
            shapley_plan = _plan_shapley(
                partition_count,
                seed_material=seed_material,
            )
            coalition_values = _native_percentile_coalition_values(
                plan=shapley_plan,
                partitions=partitions,
                partition_members=partition_members,
                current_values=current_values,
                baseline_values=baseline_values,
                current_endpoint=current_endpoint,
                baseline_endpoint=baseline_endpoint,
                prefix_axes=prefix_axes,
                q=q,
                prepared=current_prepared,
                session=session,
            )
            contributions, standard_errors, seed_fingerprint = _shapley_from_values(
                shapley_plan,
                coalition_values=coalition_values,
            )
            execution = _quantile_execution_evidence(seed_fingerprint=seed_fingerprint)
            rows = []
            for partition, contribution, standard_error in zip(
                partitions, contributions, standard_errors, strict=True
            ):
                row = dict(zip(partition_axes, partition, strict=True))
                row.update(
                    {
                        "current_count": current_counts.get(partition, 0),
                        "baseline_count": baseline_counts.get(partition, 0),
                        "contribution": contribution,
                        "contribution_std_error": standard_error,
                    }
                )
                rows.append(row)
            piece = pd.DataFrame(rows)
            if bucket_column is not None:
                piece.insert(0, bucket_column, cast("Any", current_bucket))
            piece = _rank_rows(
                piece,
                total_delta=target_delta,
                group_columns=[
                    *([] if bucket_column is None else [bucket_column]),
                    *prefix_axes,
                    *([ATTRIBUTION_OTHER_MASK_COLUMN] if top_k_map is not None else []),
                ],
            )
            contribution_sum = float(piece["contribution"].sum())
            reproduction_delta = (
                coalition_values[frozenset(range(partition_count))] - coalition_values[frozenset()]
            )
            residual = target_delta - contribution_sum
            endpoint_residual = target_delta - reproduction_delta
            if max(abs(residual), abs(endpoint_residual)) > _RECONCILIATION_TOLERANCE:
                raise AttributionDistributionError(
                    message=(
                        "native percentile coalition endpoints do not reproduce observed endpoints"
                    ),
                    expected=f"residual <= {_RECONCILIATION_TOLERANCE}",
                    received=(
                        f"allocation_residual={abs(residual)!r} "
                        f"endpoint_residual={abs(endpoint_residual)!r}"
                    ),
                    location="session.attribute native percentile endpoint reproduction",
                    context={"reason": "endpoint_reproduction_mismatch"},
                )
            prefix_refs = tuple(
                binding.ref for binding in current_prepared.axis_bindings[: len(prefix_axes)]
            )
            reconciliations.append(
                AttributionResolutionReconciliationV1(
                    axis_refs=prefix_refs,
                    bucket_key=_bucket_key(bucket_column, current_bucket),
                    partition_count=partition_count,
                    total_delta=target_delta,
                    contribution_sum=contribution_sum,
                    residual=residual,
                    max_abs_residual=abs(residual),
                    quantile_execution=execution,
                )
            )
            if mode == "hierarchy":
                piece = _resolution_rows(
                    piece,
                    all_axis_columns=all_axes,
                    prefix_columns=prefix_axes,
                )
            output_pieces.append(piece)
    output = pd.concat(output_pieces, ignore_index=True) if output_pieces else pd.DataFrame()
    deepest = [item for item in reconciliations if len(item.axis_refs) == len(all_axes)]
    common = AttributionReconciliation(
        partition_count=sum(item.partition_count for item in deepest),
        total_delta=sum(item.total_delta for item in deepest),
        contribution_sum=sum(item.contribution_sum for item in deepest),
        residual=sum(item.residual for item in deepest),
        max_abs_residual=max((item.max_abs_residual for item in deepest), default=0.0),
    )
    evidence_summary = summarize_quantile_resolution_executions(reconciliations)
    evidence = QuantileReplacementEvidenceV1(
        q=q,
        source_mode=reproduction.source_mode,
        source_method=reproduction.source_method,
        distribution_representation=reproduction.distribution_representation,
        **evidence_summary,
        source_error_bound=None,
        scope_reconciliations=tuple(reconciliations),
    )
    hierarchy = (
        IndependentHierarchyEvidenceV1(
            scope=CompleteHierarchyScopeV1(),
            resolution_reconciliations=tuple(reconciliations),
        )
        if mode == "hierarchy"
        else None
    )
    return NonAdditiveAttributionResultV1(
        dataframe=output,
        axis_columns=all_axes,
        bucket_column=bucket_column,
        reconciliation=common,
        method_evidence=evidence,
        resolution_evidence=hierarchy,
        top_k_selection=top_k_selection,
    )


__all__ = [
    "NonAdditiveAttributionResultV1",
    "attribute_distinct",
    "attribute_exact_quantile",
    "attribute_native_percentile_quantile",
    "trino_native_percentile_coalitions_expression",
    "weighted_linear_quantile",
]
