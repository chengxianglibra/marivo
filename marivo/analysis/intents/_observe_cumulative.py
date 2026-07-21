"""Cumulative observe execution: as-of-end, trailing, and count_distinct first-seen.

Internal to ``marivo.analysis.intents`` — extracted from ``observe``.
"""

from __future__ import annotations

from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import ibis

from marivo.analysis.errors import AnalysisError
from marivo.analysis.executor.bucketing import (
    apply_time_series_bucket,
    bucket_start_expr,
    ensure_bucket_start_timestamp,
)
from marivo.analysis.executor.runner import apply_slice_to_dataset, execute
from marivo.analysis.executor.windowing import (
    datasource_engine_profile,
    datasource_read_timezone,
    resolve_window_time_field,
)
from marivo.analysis.intents._observe_catalog import (
    _build_entity_adapter,
    _catalog_object,
    _entity_details,
    _field_details,
)
from marivo.analysis.intents._observe_dense import (
    _align_to_grain_start,
    _bucket_date_range,
    _dense_cumulative_frame,
    _grain_to_date_dense_frame,
    _require_grain_to_date_compat,
    _trailing_coverage_df,
    _trailing_rolling_frame,
)
from marivo.analysis.intents._observe_inputs import _metric_expr, _Result
from marivo.analysis.intents._observe_planner_joins import _validate_field_expr
from marivo.analysis.intents._observe_planner_types import CumulativePhysicalLeafPlanV1
from marivo.analysis.session.core import Session
from marivo.analysis.windows.grain import _TRUNCATE_CODE, Grain, ensure_grain_supported
from marivo.analysis.windows.spec import AbsoluteWindow
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import SemanticKind

_WEIGHTED_MEAN_NUMERATOR = "__weighted_mean_numerator"
_WEIGHTED_MEAN_WEIGHT = "__weighted_mean_weight"


def _base_aggregation_name(metric_ir: Any) -> str:
    """Return the aggregation name string (e.g. 'sum', 'count_distinct')."""
    if getattr(metric_ir, "weighted_mean", None) is not None:
        return "weighted_mean"
    agg = getattr(metric_ir, "aggregation", None)
    return str(agg) if agg is not None else ""


def _flow_aggregations(
    *,
    catalog: Any,
    resolver: Any,
    metric_ir: Any,
    metric_datasets: tuple[str, ...],
    table: Any,
) -> dict[str, Any]:
    """Build additive flow expressions, preserving weighted-mean components."""
    spec = getattr(metric_ir, "weighted_mean", None)
    if spec is None:
        return {
            "value": _metric_expr(
                catalog,
                resolver,
                metric_ir.semantic_id,
                metric_datasets,
                dict.fromkeys(metric_datasets, table),
            )
        }
    numerator, weight, _value = resolver.weighted_mean_aggregates_on(
        ref_factory.measure(spec.value),
        ref_factory.measure(spec.weight),
        table,
    )
    return {
        _WEIGHTED_MEAN_NUMERATOR: numerator,
        _WEIGHTED_MEAN_WEIGHT: weight,
    }


def _weighted_mean_from_accumulated_components(df: Any) -> Any:
    """Project accumulated numerator/weight components to one public value."""
    denominator = df[_WEIGHTED_MEAN_WEIGHT]
    result = df.copy()
    result["value"] = result[_WEIGHTED_MEAN_NUMERATOR] / denominator.mask(denominator == 0)
    return result.drop(columns=[_WEIGHTED_MEAN_NUMERATOR, _WEIGHTED_MEAN_WEIGHT])


def _base_measure_ref(metric_ir: Any) -> str | None:
    """Return the base metric's measure semantic_id, or None for count-without-measure."""
    return getattr(metric_ir, "measure", None)


def _count_distinct_key_expr(resolver: Any, metric_ir: Any, table: Any) -> Any:
    """Resolve the measure column expression for a count_distinct first-seen rewrite."""
    measure_ref = _base_measure_ref(metric_ir)
    if measure_ref is None:
        raise AnalysisError(
            message="cumulative count_distinct requires a measure-backed base metric",
            context={"metric": getattr(metric_ir, "semantic_id", None)},
        )
    return resolver.measure_on(ref_factory.measure(measure_ref), table)


def _apply_where_to_raw_table(
    table: Any,
    planned_where: list[Any],
    *,
    dataset_ir: Any,
) -> Any:
    """Re-apply where/slice_by predicates to a fresh (unwindowed) table.

    The ``base_plan.table`` has window + where/slice_by filters baked in.
    When ``_execute_cumulative`` needs a raw table with only the where/slice_by
    filters (no window), it fetches a fresh table and calls this helper to
    re-apply the same predicates.
    """
    if not planned_where:
        return table
    where_dict: dict[str, Any] = {}
    for entry in planned_where:
        if getattr(entry.field, "physical", False):
            table = table.filter(table[entry.field.name] == entry.value)
        else:
            where_dict[entry.field.name] = entry.value
    if not where_dict:
        return table
    return apply_slice_to_dataset(table, where_dict, dataset_ir=dataset_ir)


# Upper bound on the memtable-spine expansion join size for trailing
# count_distinct. The join produces up to (W_buckets * display_buckets * rows)
# rows; a runaway expansion (e.g. year-scale trailing at hour grain) is rejected
# before execution with a teaching error rather than exhausting memory.


_MAX_TRAILING_DISTINCT_EXPANSION = 1_000_000


def _execute_trailing_distinct(
    *,
    plan: CumulativePhysicalLeafPlanV1,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow,
    time_dimension_ir: Any,
    root_adapter: Any,
    dimension_names: list[str],
    resolved_dimensions: list[tuple[Any, Any]],
) -> tuple[
    Any,
    dict[str, Any],
    Literal["scalar", "time_series", "segmented", "panel"],
    Any | None,
]:
    """Execute a trailing (rolling N) cumulative for a count_distinct base.

    Each display bucket's value is the distinct count of the base key over the
    trailing span. Because distinct counts are not additive over buckets, this
    cannot reuse the additive rolling-sum path. Instead it builds an inline ibis
    memtable spine (one row per display bucket), joins the filtered source so
    each event fans out to every bucket whose trailing span contains it, then
    groups by ``(bucket_start, dims)`` with ``nunique``. Empty buckets fill 0
    via the spine. A bucket-count cap guards against runaway expansion.

    The compiled SQL is a plain JOIN + GROUP BY + count_distinct (no window
    functions): the join is a cross join between the spine memtable and the
    filtered source, filtered by a half-open range predicate.
    """
    import pandas as pd

    assert resolved_window.grain is not None
    base_plan = plan.base_plan
    base_metric_ir = plan.base_metric_ir
    primary_datasource = base_plan.datasource_name
    anchor = plan.composition.anchor
    trailing_count = anchor[1]
    trailing_unit = anchor[2]

    # 1. Integer-multiple rule (same guard as the additive path).
    span_grain = Grain(count=1, unit=trailing_unit)
    span_seconds = trailing_count * span_grain.width_seconds()
    grain_seconds = resolved_window.grain.width_seconds()
    if span_seconds % grain_seconds != 0:
        raise AnalysisError(
            message=(
                f"trailing(count={trailing_count}, unit={trailing_unit!r}) span "
                f"({span_seconds}s) is not an integer multiple of query grain "
                f"{resolved_window.grain.to_token()!r} ({grain_seconds}s)."
            ),
            hint=(
                "Choose a trailing span that divides evenly into the query "
                "grain (e.g. trailing(count=7, unit='day') at day or hour grain)."
            ),
            context={
                "anchor": anchor,
                "span_seconds": span_seconds,
                "grain": resolved_window.grain.to_token(),
                "grain_seconds": grain_seconds,
            },
        )
    w_buckets = span_seconds // grain_seconds

    # 2. Display buckets (the spine). One memtable row per display bucket.
    display_bucket_values = _bucket_date_range(resolved_window)
    display_bucket_count = len(display_bucket_values)
    # Cap the expansion: W_buckets * display_buckets is the worst-case fan-out
    # per source row. Reject before executing when this exceeds the limit.
    expansion = w_buckets * display_bucket_count
    if expansion > _MAX_TRAILING_DISTINCT_EXPANSION:
        raise AnalysisError(
            message=(
                f"trailing count_distinct expansion too large: the join would "
                f"produce up to {expansion} bucket-event combinations (cap "
                f"{_MAX_TRAILING_DISTINCT_EXPANSION})."
            ),
            hint=(
                "Reduce the trailing span, the query window, or move to a "
                "coarser grain. For wide ranges prefer all_history or "
                "grain_to_date over a count_distinct base."
            ),
            context={
                "anchor": anchor,
                "w_buckets": w_buckets,
                "display_buckets": display_bucket_count,
                "expansion": expansion,
                "cap": _MAX_TRAILING_DISTINCT_EXPANSION,
            },
        )

    # 3. Filtered source: raw entity table with where/slice_by filters applied,
    #    projected with dimension columns. The time filter is applied via the
    #    join predicate (not here) so each event can fan out to multiple buckets.
    raw_table = resolver.table(
        _catalog_object(catalog, base_plan.root_entity, SemanticKind.ENTITY).ref
    )
    raw_table = _apply_where_to_raw_table(raw_table, base_plan.where, dataset_ir=root_adapter)
    time_expr_raw = time_dimension_ir.fn(raw_table)
    time_expr_name = time_expr_raw.get_name()
    # Restrict to events that could land in any display bucket's span to bound
    # the join: [display_start - span, display_end).
    window_start_ts = pd.Timestamp(resolved_window.start)
    window_end_ts = pd.Timestamp(resolved_window.end)
    fetch_start_ts = window_start_ts - pd.Timedelta(seconds=span_seconds)
    fetch_start = fetch_start_ts.strftime("%Y-%m-%dT%H:%M:%S")
    fetch_end = window_end_ts.strftime("%Y-%m-%dT%H:%M:%S")
    source_table = raw_table.filter(
        (time_expr_raw >= ibis.literal(fetch_start).cast(time_expr_raw.type()))
        & (time_expr_raw < ibis.literal(fetch_end).cast(time_expr_raw.type()))
    )
    # Project dimension columns onto the source for grouping.
    if dimension_names:
        dimension_exprs = {
            field_ir.name: _validate_field_expr(
                resolver.dimension_on(
                    _field_details(catalog, field_ir.semantic_id).ref, source_table
                ),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        source_table = source_table.mutate(**dimension_exprs)

    # Resolve the distinct key expression on the (filtered, projected) source.
    key_expr = _count_distinct_key_expr(resolver, base_metric_ir, source_table)
    key_name = key_expr.get_name()
    # Ensure the key column is materialized on the source for the join + nunique.
    if key_name not in source_table.columns:
        source_table = source_table.mutate(_distinct_key=key_expr)
        key_name = "_distinct_key"

    # 4. Memtable spine: one row per display bucket, with the trailing span
    #    boundaries precomputed. The span for bucket B (start = B, end = B +
    #    grain) is the half-open interval [B - (span - grain), B + grain): it
    #    reaches back (W-1)*grain from the bucket start and includes the full
    #    current bucket, for a total span of W*grain. This is the SAME window the
    #    additive trailing path uses (a W_buckets-wide rolling window ending at
    #    the bucket's end boundary, per the brief sketch (bucket_end - span,
    #    bucket_end]): sum and count_distinct define the same trailing window, so
    #    a key active on the last day of a window is counted in every bucket
    #    whose span contains it and drops out once the span no longer reaches it.
    span_lead_seconds = span_seconds - grain_seconds
    spine_df = pd.DataFrame(
        {
            "bucket_start": pd.to_datetime(display_bucket_values),
        }
    )
    spine = ibis.memtable(spine_df)
    spine = spine.mutate(
        _span_start=(spine["bucket_start"] - ibis.interval(seconds=int(span_lead_seconds))),
        _span_end=(spine["bucket_start"] + ibis.interval(seconds=int(grain_seconds))),
    )

    # 5. Cross join + range filter. A plain inner join on a tautology followed
    #    by the half-open range predicate compiles to JOIN + WHERE (no window
    #    functions). Each event fans out to every bucket whose span contains it.
    joined = source_table.cross_join(spine)
    joined = joined.filter(
        (source_table[time_expr_name] >= spine["_span_start"])
        & (source_table[time_expr_name] < spine["_span_end"])
    )

    # 6. Per (bucket, dims) distinct count. nunique drops NULL keys (v1 parity).
    group_names = ["bucket_start", *dimension_names]
    aggregated = (
        joined.group_by(group_names)
        .aggregate(value=joined[key_name].nunique())
        .order_by(group_names)
        .select(*group_names, "value")
    )
    result = execute(
        aggregated,
        datasource_name=primary_datasource,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    agg_df = result.df
    if "bucket_start" in agg_df:
        agg_df["bucket_start"] = ensure_bucket_start_timestamp(
            agg_df["bucket_start"],
            time_meta=time_dimension_ir.time_meta,
            dataset_ir=root_adapter,
            grain=resolved_window.grain,
            report_tz=cast("ZoneInfo", session.report_tz),
            backend_datetime_decode_policy=result.backend_datetime_decode_policy,
        )

    # 7. Densify: left-join the spine onto the aggregated result so buckets with
    #    no matching events fill 0 (true zero, not missing).
    if dimension_names:
        dim_combos = (
            agg_df[dimension_names].drop_duplicates()
            if not agg_df.empty
            else pd.DataFrame(columns=dimension_names)
        )
        bucket_df = pd.DataFrame({"bucket_start": display_bucket_values})
        spine_full = bucket_df.merge(dim_combos, how="cross")
        dense_df = spine_full.merge(
            agg_df[["bucket_start", *dimension_names, "value"]],
            on=["bucket_start", *dimension_names],
            how="left",
        )
    else:
        bucket_df = pd.DataFrame({"bucket_start": display_bucket_values})
        dense_df = bucket_df.merge(
            agg_df[["bucket_start", "value"]],
            on="bucket_start",
            how="left",
        )
    dense_df["value"] = dense_df["value"].fillna(0)
    dense_df = dense_df.sort_values(["bucket_start", *dimension_names]).reset_index(drop=True)

    # 8. Data-start scalar query: min(over) under the same filters. Buckets whose
    #    span reaches before the data start are labeled partial in coverage.
    data_start: Any = None
    min_expr = time_expr_raw.min()
    data_start_result = execute(
        raw_table.aggregate(value=min_expr),
        datasource_name=primary_datasource,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    if not data_start_result.df.empty:
        data_start = data_start_result.df.iloc[0]["value"]

    coverage_df = _trailing_coverage_df(
        dense_df=dense_df,
        bucket_values=display_bucket_values,
        data_start=data_start,
        span_seconds=span_seconds,
    )

    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": resolved_window.grain.to_token(),
            "time_dimension": time_dimension_ir.name,
            "ref": time_dimension_ir.semantic_id,
        },
        **{
            field_ir.name: {
                "role": "dimension",
                "column": field_ir.name,
                "ref": field_ir.semantic_id,
            }
            for _, field_ir in resolved_dimensions
        },
    }
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = (
        "panel" if dimension_names else "time_series"
    )
    return _Result(dense_df), axes, semantic_kind, coverage_df


def _execute_trailing_additive(
    *,
    plan: CumulativePhysicalLeafPlanV1,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow,
    time_dimension_ir: Any,
    root_adapter: Any,
    read_tz: Any,
    profile: Any,
    dimension_names: list[str],
    resolved_dimensions: list[tuple[Any, Any]],
    agg: str,
) -> tuple[
    Any,
    dict[str, Any],
    Literal["scalar", "time_series", "segmented", "panel"],
    Any | None,
]:
    """Execute a trailing (rolling N) cumulative for sum/count base aggregates.

    Each bucket's value is the base aggregation over the W_buckets-wide span
    ending at that bucket's end boundary. Flow:

    1. Integer-multiple rule: ``W_buckets = span_seconds / grain.width_seconds()``
       must be an integer.
    2. Extended fetch over ``[window.start - span, window.end)``: aggregate per
       bucket over the extended range.
    3. Densify over the extended range, fill 0 (empty = true zero, NOT
       carry-forward), rolling sum with ``min_periods=1`` (partial windows show
       actual values), clip back to ``[window.start, window.end)``.
    4. Data-start scalar query: one extra ``min(over)`` under the same filters.
       Buckets whose window reaches before the data start are labeled ``partial``
       in coverage.
    """
    import pandas as pd

    assert resolved_window.grain is not None
    base_plan = plan.base_plan
    base_metric_ir = plan.base_metric_ir
    metric_datasets = tuple(base_metric_ir.entities)
    primary_datasource = base_plan.datasource_name
    anchor = plan.composition.anchor
    trailing_count = anchor[1]
    trailing_unit = anchor[2]

    # count_distinct uses a memtable-spine expansion join (one row per display
    # bucket) so each bucket's value is the distinct count over its trailing
    # span. This cannot be a per-bucket distinct sum (semantically wrong for a
    # rolling distinct window), so it has its own execution path.
    if agg == "count_distinct":
        return _execute_trailing_distinct(
            plan=plan,
            catalog=catalog,
            resolver=resolver,
            session=session,
            resolved_window=resolved_window,
            time_dimension_ir=time_dimension_ir,
            root_adapter=root_adapter,
            dimension_names=dimension_names,
            resolved_dimensions=resolved_dimensions,
        )

    # 1. Integer-multiple rule: W_buckets must be an integer. Fixed-size units
    # always satisfy this, but guard anyway with a teaching error.
    span_grain = Grain(count=1, unit=trailing_unit)
    span_seconds = trailing_count * span_grain.width_seconds()
    grain_seconds = resolved_window.grain.width_seconds()
    if span_seconds % grain_seconds != 0:
        raise AnalysisError(
            message=(
                f"trailing(count={trailing_count}, unit={trailing_unit!r}) span "
                f"({span_seconds}s) is not an integer multiple of query grain "
                f"{resolved_window.grain.to_token()!r} ({grain_seconds}s)."
            ),
            hint=(
                "Choose a trailing span that divides evenly into the query "
                "grain (e.g. trailing(count=7, unit='day') at day or hour grain)."
            ),
            context={
                "anchor": anchor,
                "span_seconds": span_seconds,
                "grain": resolved_window.grain.to_token(),
                "grain_seconds": grain_seconds,
            },
        )
    w_buckets = span_seconds // grain_seconds

    # 2. Extended fetch window: [window.start - span, window.end).
    window_start_ts = pd.Timestamp(resolved_window.start)
    window_end_ts = pd.Timestamp(resolved_window.end)
    fetch_start_ts = window_start_ts - pd.Timedelta(seconds=span_seconds)
    fetch_start = fetch_start_ts.strftime("%Y-%m-%dT%H:%M:%S")
    extended_window = AbsoluteWindow(
        start=fetch_start,
        end=resolved_window.end,
        grain=resolved_window.grain,
        time_dimension=resolved_window.time_dimension,
    )

    # Build a raw table filtered to the extended fetch range, then bucket it.
    # base_plan.table is already window-filtered to [window.start, window.end),
    # so we re-resolve the raw entity table and re-apply where/slice_by filters.
    raw_table = resolver.table(
        _catalog_object(catalog, base_plan.root_entity, SemanticKind.ENTITY).ref
    )
    raw_table = _apply_where_to_raw_table(raw_table, base_plan.where, dataset_ir=root_adapter)
    time_expr_raw = time_dimension_ir.fn(raw_table)
    fetch_table = raw_table.filter(
        (time_expr_raw >= ibis.literal(fetch_start).cast(time_expr_raw.type()))
        & (time_expr_raw < ibis.literal(resolved_window.end).cast(time_expr_raw.type()))
    )
    bucketed_table = apply_time_series_bucket(
        fetch_table,
        field_ir=time_dimension_ir,
        window=extended_window,
        report_tz=cast("ZoneInfo", session.report_tz),
        datasource_read_tz=read_tz,
        profile=profile,
        dataset_ir=root_adapter,
    )
    if dimension_names:
        dimension_exprs = {
            field_ir.name: _validate_field_expr(
                resolver.dimension_on(
                    _field_details(catalog, field_ir.semantic_id).ref, bucketed_table
                ),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        bucketed_table = bucketed_table.mutate(**dimension_exprs)

    # Flow aggregation per bucket over the extended range.
    flow_aggregations = _flow_aggregations(
        catalog=catalog,
        resolver=resolver,
        metric_ir=base_metric_ir,
        metric_datasets=metric_datasets,
        table=bucketed_table,
    )
    group_names_flow = ["bucket_start", *dimension_names]
    flow_grouped = (
        bucketed_table.group_by(group_names_flow)
        .aggregate(**flow_aggregations)
        .order_by(group_names_flow)
        .select(*group_names_flow, *flow_aggregations)
    )
    flow_result = execute(
        flow_grouped,
        datasource_name=primary_datasource,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    flow_df = flow_result.df
    if "bucket_start" in flow_df:
        flow_df["bucket_start"] = ensure_bucket_start_timestamp(
            flow_df["bucket_start"],
            time_meta=time_dimension_ir.time_meta,
            dataset_ir=root_adapter,
            grain=resolved_window.grain,
            report_tz=cast("ZoneInfo", session.report_tz),
            backend_datetime_decode_policy=flow_result.backend_datetime_decode_policy,
        )

    # 3. Densify + fill 0 + rolling sum (min_periods=1) + clip to display window.
    # The dense spine covers the extended fetch range so rolling windows at the
    # display-start boundary have their full span; the result is clipped back.
    extended_bucket_values = _bucket_date_range(extended_window)
    if getattr(base_metric_ir, "weighted_mean", None) is not None:
        numerator_df = _trailing_rolling_frame(
            flow_df=flow_df,
            bucket_values=extended_bucket_values,
            dimension_columns=dimension_names,
            w_buckets=w_buckets,
            display_start=window_start_ts,
            display_end=window_end_ts,
            value_column=_WEIGHTED_MEAN_NUMERATOR,
        )
        weight_df = _trailing_rolling_frame(
            flow_df=flow_df,
            bucket_values=extended_bucket_values,
            dimension_columns=dimension_names,
            w_buckets=w_buckets,
            display_start=window_start_ts,
            display_end=window_end_ts,
            value_column=_WEIGHTED_MEAN_WEIGHT,
        )
        merge_keys = ["bucket_start", *dimension_names]
        dense_df = _weighted_mean_from_accumulated_components(
            numerator_df.merge(weight_df, on=merge_keys, how="outer")
        )
    else:
        dense_df = _trailing_rolling_frame(
            flow_df=flow_df,
            bucket_values=extended_bucket_values,
            dimension_columns=dimension_names,
            w_buckets=w_buckets,
            display_start=window_start_ts,
            display_end=window_end_ts,
        )

    # 4. Data-start scalar query: min(over) under the same filters. Buckets
    # whose window reaches before the data start are labeled 'partial'.
    data_start: Any = None
    min_expr = time_expr_raw.min()
    data_start_result = execute(
        raw_table.aggregate(value=min_expr),
        datasource_name=primary_datasource,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    if not data_start_result.df.empty:
        data_start = data_start_result.df.iloc[0]["value"]

    display_bucket_values = _bucket_date_range(resolved_window)
    coverage_df = _trailing_coverage_df(
        dense_df=dense_df,
        bucket_values=display_bucket_values,
        data_start=data_start,
        span_seconds=span_seconds,
    )

    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": resolved_window.grain.to_token(),
            "time_dimension": time_dimension_ir.name,
            "ref": time_dimension_ir.semantic_id,
        },
        **{
            field_ir.name: {
                "role": "dimension",
                "column": field_ir.name,
                "ref": field_ir.semantic_id,
            }
            for _, field_ir in resolved_dimensions
        },
    }
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = (
        "panel" if dimension_names else "time_series"
    )
    return _Result(dense_df), axes, semantic_kind, coverage_df


def _execute_cumulative(
    plan: CumulativePhysicalLeafPlanV1,
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[
    Any,
    dict[str, Any],
    Literal["scalar", "time_series", "segmented", "panel"],
    Any | None,
]:
    """Execute a graph-native cumulative physical leaf.

    Returns (result, axes, semantic_kind, coverage_df_or_None).

    For scalar/segmented (no time grain): one query up to window end
    (as-of-end strategy).

    For time-series/panel (with time grain): baseline query (all history
    before window start) + flow query (per-bucket aggregation within window)
    + dense spine + cumsum in pandas.

    For trailing (rolling N): extended fetch over [start - span, end), per-bucket
    aggregation, densify + fill 0 + rolling sum (min_periods=1), clip to
    [start, end). Empty windows are true zero; partial windows show actual
    values. A data-start scalar query marks partial buckets in coverage.

    For count_distinct: first-seen rewrite (find first occurrence of each
    distinct key, bucket by that first-seen timestamp, count per bucket).
    """
    base_plan = plan.base_plan
    base_metric_ir = plan.base_metric_ir
    metric_datasets = tuple(base_metric_ir.entities)
    primary_datasource = base_plan.datasource_name
    read_tz = datasource_read_timezone(session._connection_runtime, primary_datasource)
    profile = datasource_engine_profile(session._connection_runtime, primary_datasource)
    root_adapter = _build_entity_adapter(
        catalog,
        resolver,
        _entity_details(catalog, base_plan.root_entity),
    )
    resolved_dimensions = [
        (dimension.field.entity, dimension.field) for dimension in base_plan.dimensions
    ]
    dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
    agg = _base_aggregation_name(base_metric_ir)
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    # Resolve the cumulative anchor from the plan's composition (the real
    # CumulativeComposition on the metric IR). Falls back to all_history when
    # the plan was built without a composition (adapter-only paths).
    plan_composition = getattr(plan, "composition", None)
    anchor = getattr(plan_composition, "anchor", "all_history") or "all_history"
    is_grain_to_date = isinstance(anchor, tuple) and anchor[0] == "grain_to_date"
    reset_grain = anchor[1] if is_grain_to_date else None
    is_trailing = isinstance(anchor, tuple) and anchor[0] == "trailing"

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"

    # Trailing requires a time grain: a rolling window over scalar/segmented
    # shapes is undefined. Point the agent at a plain windowed observe instead.
    if is_trailing and not is_time_series:
        raise AnalysisError(
            message=(
                "trailing(count=..., unit=...) requires a time grain; a rolling "
                "window is undefined for a scalar or segmented observe. Use a "
                "plain windowed session.observe(...) with time_scope for a "
                "single windowed value."
            ),
            hint="Pass grain='day' (or another grain) to observe a trailing rolling window.",
            context={"anchor": anchor},
        )

    if not is_time_series:
        # --- Scalar/segmented: as-of-end strategy ---
        # For cumulative as-of-end, we want all events up to (but not
        # including) window.end.  The window is [start, end), but for
        # cumulative we want everything before end, regardless of start.
        raw_table = resolver.table(
            _catalog_object(catalog, base_plan.root_entity, SemanticKind.ENTITY).ref
        )
        # Re-apply where/slice_by filters that base_plan.table already has.
        raw_table = _apply_where_to_raw_table(raw_table, base_plan.where, dataset_ir=root_adapter)
        if resolved_window is not None:
            time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
            time_expr = time_dimension_ir.fn(raw_table)
            if is_grain_to_date and reset_grain is not None:
                # grain_to_date scalar boundary rule: the value is the
                # period-to-date total for the reset period containing the
                # final included instant. end is exclusive under [start, end);
                # the final included instant belongs to the period containing
                # (end - epsilon). When end is exactly on a reset boundary
                # (e.g. end='2026-08-01'), that is the PRIOR period (July),
                # so full-July aggregates July, not empty August.
                import pandas as pd  # local: scalar boundary derivation

                end_ts = pd.Timestamp(resolved_window.end)
                included_end = end_ts - pd.Timedelta(microseconds=1)
                period_start = _align_to_grain_start(included_end, reset_grain)
                raw_table = raw_table.filter(
                    (time_expr >= ibis.literal(period_start).cast(time_expr.type()))
                    & (time_expr < ibis.literal(resolved_window.end).cast(time_expr.type()))
                )
            else:
                # all_history as-of-end: everything before window.end.
                raw_table = raw_table.filter(
                    time_expr < ibis.literal(resolved_window.end).cast(time_expr.type())
                )
        # Apply dimension projections on the raw table
        if resolved_dimensions:
            dimension_exprs = {
                field_ir.name: _validate_field_expr(
                    resolver.dimension_on(
                        _field_details(catalog, field_ir.semantic_id).ref, raw_table
                    ),
                    field_id=field_ir.semantic_id,
                ).name(field_ir.name)
                for _, field_ir in resolved_dimensions
            }
            raw_table = raw_table.mutate(**dimension_exprs)
            dataset_tables = dict.fromkeys(metric_datasets, raw_table)
            metric_expr = _metric_expr(
                catalog, resolver, base_metric_ir.semantic_id, metric_datasets, dataset_tables
            )
            grouped_expr = (
                raw_table.group_by(dimension_names)
                .aggregate(value=metric_expr)
                .order_by(dimension_names)
                .select(*dimension_names, "value")
            )
            result = execute(
                grouped_expr,
                datasource_name=primary_datasource,
                cache=session._connection_runtime,
                session_id=session.id,
            )
            axes = {
                field_ir.name: {
                    "role": "dimension",
                    "column": field_ir.name,
                    "ref": field_ir.semantic_id,
                }
                for _, field_ir in resolved_dimensions
            }
            semantic_kind = "segmented"
        else:
            dataset_tables = dict.fromkeys(metric_datasets, raw_table)
            metric_expr = _metric_expr(
                catalog, resolver, base_metric_ir.semantic_id, metric_datasets, dataset_tables
            )
            grouped_expr = raw_table.aggregate(value=metric_expr)
            result = execute(
                grouped_expr,
                datasource_name=primary_datasource,
                cache=session._connection_runtime,
                session_id=session.id,
            )
            semantic_kind = "scalar"
        return result, axes, semantic_kind, None

    # --- Time-series/panel: baseline + flow + dense spine + cumsum ---
    assert resolved_window is not None
    assert resolved_window.grain is not None

    time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
    base = (
        time_dimension_ir.time_meta.granularity if time_dimension_ir.time_meta else None
    ) or "day"
    ensure_grain_supported(resolved_window.grain, base)

    # grain_to_date grain-compatibility guard (teaching error): week query
    # grain under month/quarter/year reset is illegal because week buckets
    # straddle reset boundaries. Applies to sum/count and count_distinct.
    if is_grain_to_date and reset_grain is not None:
        _require_grain_to_date_compat(resolved_window.grain.unit, reset_grain)

    if is_trailing:
        return _execute_trailing_additive(
            plan=plan,
            catalog=catalog,
            resolver=resolver,
            session=session,
            resolved_window=resolved_window,
            time_dimension_ir=time_dimension_ir,
            root_adapter=root_adapter,
            read_tz=read_tz,
            profile=profile,
            dimension_names=dimension_names,
            resolved_dimensions=resolved_dimensions,
            agg=agg,
        )

    # Build the bucketed table from the window-filtered table (base_plan.table)
    bucketed_table = apply_time_series_bucket(
        base_plan.table,
        field_ir=time_dimension_ir,
        window=resolved_window,
        report_tz=cast("ZoneInfo", session.report_tz),
        datasource_read_tz=read_tz,
        profile=profile,
        dataset_ir=root_adapter,
    )
    # Apply dimension projections on the bucketed table
    if resolved_dimensions:
        dimension_exprs = {
            field_ir.name: _validate_field_expr(
                resolver.dimension_on(
                    _field_details(catalog, field_ir.semantic_id).ref, bucketed_table
                ),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        bucketed_table = bucketed_table.mutate(**dimension_exprs)

    # Get the raw entity table for baseline query
    raw_table = resolver.table(
        _catalog_object(catalog, base_plan.root_entity, SemanticKind.ENTITY).ref
    )
    # Re-apply where/slice_by filters that base_plan.table already has.
    raw_table = _apply_where_to_raw_table(raw_table, base_plan.where, dataset_ir=root_adapter)
    time_expr_raw = time_dimension_ir.fn(raw_table)
    # Baseline: all history before window.start
    baseline_table = raw_table.filter(
        time_expr_raw < ibis.literal(resolved_window.start).cast(time_expr_raw.type())
    )
    if resolved_dimensions:
        dimension_exprs_raw = {
            field_ir.name: _validate_field_expr(
                resolver.dimension_on(
                    _field_details(catalog, field_ir.semantic_id).ref, baseline_table
                ),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        baseline_table = baseline_table.mutate(**dimension_exprs_raw)

    if agg == "count_distinct":
        # First-seen rewrite: find first occurrence of each distinct key
        # across ALL history up to window.end, bucket by that first-seen
        # timestamp, count per bucket.  Keys first-seen before window.start
        # go into the baseline; keys first-seen within the window go into
        # the flow.
        #
        # grain_to_date variant (period-scoped first-seen): the dedup groups by
        # (distinct key, slice dims, period_key) where period_key truncates the
        # event time to the reset grain. An entity counts once per reset period
        # and re-counts at each boundary. The seed (baseline) is scoped to the
        # first reset period only; the post-process reuses the grain_to_date
        # reset-partitioned cumsum from Task 4 (the period_key IS the reset
        # partition, derived consistently on both sides).

        # Build a combined raw table: all history up to window.end
        combined_raw = resolver.table(
            _catalog_object(catalog, base_plan.root_entity, SemanticKind.ENTITY).ref
        )
        # Re-apply where/slice_by filters that base_plan.table already has
        # (v1 rule: filters apply before dedup).
        combined_raw = _apply_where_to_raw_table(
            combined_raw, base_plan.where, dataset_ir=root_adapter
        )
        combined_time_expr = time_dimension_ir.fn(combined_raw)
        combined_raw = combined_raw.filter(
            combined_time_expr < ibis.literal(resolved_window.end).cast(combined_time_expr.type())
        )
        if resolved_dimensions:
            combined_dim_exprs = {
                field_ir.name: _validate_field_expr(
                    resolver.dimension_on(
                        _field_details(catalog, field_ir.semantic_id).ref, combined_raw
                    ),
                    field_id=field_ir.semantic_id,
                ).name(field_ir.name)
                for _, field_ir in resolved_dimensions
            }
            combined_raw = combined_raw.mutate(**combined_dim_exprs)

        # Find first-seen per distinct key (+ dimensions). For grain_to_date a
        # period_key column is added so an entity re-counts once per reset
        # period. NOTE: this period_key is derived through the SQL/ibis path
        # (combined_time_expr.truncate(_TRUNCATE_CODE[reset_grain])), while the
        # cumsum reset partition in _grain_to_date_dense_frame is derived
        # through the pandas path (_trunc_series_to_grain on bucket_start).
        # These are two INDEPENDENT truncation implementations that must agree
        # per reset grain (week/month/quarter/year). The alignment is
        # semantic-by-convention, NOT code-shared: a future change to either
        # path must keep them in sync, or the dedup period will silently
        # misalign with the reset partition. Cross-grain alignment tests
        # (e.g. quarter reset) guard against such divergence.
        combined_key_expr = _count_distinct_key_expr(resolver, base_metric_ir, combined_raw)
        combined_key_name = combined_key_expr.get_name()
        if is_grain_to_date and reset_grain is not None:
            period_key_expr = combined_time_expr.truncate(_TRUNCATE_CODE[reset_grain]).name(
                "period_key"
            )
            first_seen = combined_raw.group_by(
                [combined_key_name, *dimension_names, period_key_expr]
            ).aggregate(first_seen_ts=combined_time_expr.min())
        else:
            first_seen = combined_raw.group_by([combined_key_name, *dimension_names]).aggregate(
                first_seen_ts=combined_time_expr.min()
            )

        if is_grain_to_date and reset_grain is not None:
            # Seed: count keys first-seen in the FIRST reset period only, with
            # first_seen_ts < window.start. Entities first-seen in earlier
            # periods do NOT carry into the window's first period (they reset).
            # Skipped (empty) when window.start is on a reset boundary, so a
            # boundary-started window runs ONE query (no seed), matching the
            # additive path.
            import pandas as pd  # local: seed period derivation

            window_start_ts = pd.Timestamp(resolved_window.start)
            first_period_start = _align_to_grain_start(window_start_ts, reset_grain)
            if first_period_start < window_start_ts:
                baseline_first_seen = first_seen.filter(
                    (
                        first_seen["period_key"]
                        == ibis.literal(first_period_start).cast(first_seen["period_key"].type())
                    )
                    & (
                        first_seen["first_seen_ts"]
                        < ibis.literal(resolved_window.start).cast(
                            first_seen["first_seen_ts"].type()
                        )
                    )
                )
                baseline_group_keys = list(dimension_names)
                if baseline_group_keys:
                    baseline_grouped = (
                        baseline_first_seen.group_by(baseline_group_keys)
                        .aggregate(value=baseline_first_seen[combined_key_name].count())
                        .order_by(baseline_group_keys)
                        .select(*baseline_group_keys, "value")
                    )
                else:
                    baseline_grouped = baseline_first_seen.aggregate(
                        value=baseline_first_seen[combined_key_name].count()
                    )
                baseline_result = execute(
                    baseline_grouped,
                    datasource_name=primary_datasource,
                    cache=session._connection_runtime,
                    session_id=session.id,
                )
                baseline_df = baseline_result.df
            else:
                baseline_df = pd.DataFrame(columns=[*dimension_names, "value"])
        else:
            # Baseline: count keys first-seen before window.start (all history)
            baseline_first_seen = first_seen.filter(
                first_seen["first_seen_ts"]
                < ibis.literal(resolved_window.start).cast(first_seen["first_seen_ts"].type())
            )
            baseline_group_keys = list(dimension_names)
            if baseline_group_keys:
                baseline_grouped = (
                    baseline_first_seen.group_by(baseline_group_keys)
                    .aggregate(value=baseline_first_seen[combined_key_name].count())
                    .order_by(baseline_group_keys)
                    .select(*baseline_group_keys, "value")
                )
            else:
                baseline_grouped = baseline_first_seen.aggregate(
                    value=baseline_first_seen[combined_key_name].count()
                )
            baseline_result = execute(
                baseline_grouped,
                datasource_name=primary_datasource,
                cache=session._connection_runtime,
                session_id=session.id,
            )
            baseline_df = baseline_result.df

        # Flow: count keys first-seen within [window.start, window.end), bucketed
        flow_first_seen = first_seen.filter(
            (
                first_seen["first_seen_ts"]
                >= ibis.literal(resolved_window.start).cast(first_seen["first_seen_ts"].type())
            )
            & (
                first_seen["first_seen_ts"]
                < ibis.literal(resolved_window.end).cast(first_seen["first_seen_ts"].type())
            )
        )
        # Bucket the first-seen timestamp
        flow_bucketed = flow_first_seen.mutate(
            bucket_start=bucket_start_expr(flow_first_seen["first_seen_ts"], resolved_window.grain)
        )
        group_keys_flow = ["bucket_start", *dimension_names]
        flow_grouped = (
            flow_bucketed.group_by(group_keys_flow)
            .aggregate(value=flow_bucketed[combined_key_name].count())
            .order_by(group_keys_flow)
            .select(*group_keys_flow, "value")
        )
        flow_result = execute(
            flow_grouped,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        flow_df = flow_result.df
        if "bucket_start" in flow_df:
            flow_df["bucket_start"] = ensure_bucket_start_timestamp(
                flow_df["bucket_start"],
                time_meta=time_dimension_ir.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=flow_result.backend_datetime_decode_policy,
            )
    else:
        # sum / count: baseline = aggregate of events before window start.
        # Flow = aggregate of events per bucket within window.
        flow_aggregations = _flow_aggregations(
            catalog=catalog,
            resolver=resolver,
            metric_ir=base_metric_ir,
            metric_datasets=metric_datasets,
            table=bucketed_table,
        )
        group_names_flow = ["bucket_start", *dimension_names]
        flow_grouped = (
            bucketed_table.group_by(group_names_flow)
            .aggregate(**flow_aggregations)
            .order_by(group_names_flow)
            .select(*group_names_flow, *flow_aggregations)
        )
        flow_result = execute(
            flow_grouped,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        flow_df = flow_result.df
        if "bucket_start" in flow_df:
            flow_df["bucket_start"] = ensure_bucket_start_timestamp(
                flow_df["bucket_start"],
                time_meta=time_dimension_ir.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=flow_result.backend_datetime_decode_policy,
            )

        if is_grain_to_date and reset_grain is not None:
            # Seed: aggregate over [period_start(window.start), window.start)
            # per slice dims — a bounded scan feeding ONLY the first period's
            # buckets. Skipped (empty) when window.start is on a reset
            # boundary, so a boundary-started window runs ONE query.
            import pandas as pd  # local: seed period derivation

            window_start_ts = pd.Timestamp(resolved_window.start)
            period_start = _align_to_grain_start(window_start_ts, reset_grain)
            baseline_df = pd.DataFrame(columns=[*dimension_names, *flow_aggregations])
            if period_start < window_start_ts:
                seed_table = raw_table.filter(
                    (time_expr_raw >= ibis.literal(period_start).cast(time_expr_raw.type()))
                    & (
                        time_expr_raw
                        < ibis.literal(resolved_window.start).cast(time_expr_raw.type())
                    )
                )
                if resolved_dimensions:
                    seed_dim_exprs = {
                        field_ir.name: _validate_field_expr(
                            resolver.dimension_on(
                                _field_details(catalog, field_ir.semantic_id).ref, seed_table
                            ),
                            field_id=field_ir.semantic_id,
                        ).name(field_ir.name)
                        for _, field_ir in resolved_dimensions
                    }
                    seed_table = seed_table.mutate(**seed_dim_exprs)
                seed_aggregations = _flow_aggregations(
                    catalog=catalog,
                    resolver=resolver,
                    metric_ir=base_metric_ir,
                    metric_datasets=metric_datasets,
                    table=seed_table,
                )
                seed_group_keys = list(dimension_names)
                if seed_group_keys:
                    seed_grouped = (
                        seed_table.group_by(seed_group_keys)
                        .aggregate(**seed_aggregations)
                        .order_by(seed_group_keys)
                        .select(*seed_group_keys, *seed_aggregations)
                    )
                else:
                    seed_grouped = seed_table.aggregate(**seed_aggregations)
                seed_result = execute(
                    seed_grouped,
                    datasource_name=primary_datasource,
                    cache=session._connection_runtime,
                    session_id=session.id,
                )
                baseline_df = seed_result.df
        else:
            baseline_aggregations = _flow_aggregations(
                catalog=catalog,
                resolver=resolver,
                metric_ir=base_metric_ir,
                metric_datasets=metric_datasets,
                table=baseline_table,
            )
            baseline_group_keys = list(dimension_names)
            if baseline_group_keys:
                baseline_grouped = (
                    baseline_table.group_by(baseline_group_keys)
                    .aggregate(**baseline_aggregations)
                    .order_by(baseline_group_keys)
                    .select(*baseline_group_keys, *baseline_aggregations)
                )
            else:
                baseline_grouped = baseline_table.aggregate(**baseline_aggregations)
            baseline_result = execute(
                baseline_grouped,
                datasource_name=primary_datasource,
                cache=session._connection_runtime,
                session_id=session.id,
            )
            baseline_df = baseline_result.df

    # Build dense spine and cumsum
    bucket_values = _bucket_date_range(resolved_window)
    if getattr(base_metric_ir, "weighted_mean", None) is not None:
        if is_grain_to_date and reset_grain is not None:
            numerator_df = _grain_to_date_dense_frame(
                seed_df=baseline_df,
                flow_df=flow_df,
                bucket_values=bucket_values,
                dimension_columns=dimension_names,
                reset_grain=reset_grain,
                value_column=_WEIGHTED_MEAN_NUMERATOR,
            )
            weight_df = _grain_to_date_dense_frame(
                seed_df=baseline_df,
                flow_df=flow_df,
                bucket_values=bucket_values,
                dimension_columns=dimension_names,
                reset_grain=reset_grain,
                value_column=_WEIGHTED_MEAN_WEIGHT,
            )
        else:
            numerator_df = _dense_cumulative_frame(
                baseline_df=baseline_df,
                flow_df=flow_df,
                bucket_values=bucket_values,
                dimension_columns=dimension_names,
                value_column=_WEIGHTED_MEAN_NUMERATOR,
            )
            weight_df = _dense_cumulative_frame(
                baseline_df=baseline_df,
                flow_df=flow_df,
                bucket_values=bucket_values,
                dimension_columns=dimension_names,
                value_column=_WEIGHTED_MEAN_WEIGHT,
            )
        dense_df = _weighted_mean_from_accumulated_components(
            numerator_df.merge(weight_df, on=["bucket_start", *dimension_names], how="outer")
        )
    elif is_grain_to_date and reset_grain is not None:
        dense_df = _grain_to_date_dense_frame(
            seed_df=baseline_df,
            flow_df=flow_df,
            bucket_values=bucket_values,
            dimension_columns=dimension_names,
            reset_grain=reset_grain,
        )
    else:
        dense_df = _dense_cumulative_frame(
            baseline_df=baseline_df,
            flow_df=flow_df,
            bucket_values=bucket_values,
            dimension_columns=dimension_names,
        )

    # Set axes
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": resolved_window.grain.to_token(),
            "time_dimension": time_dimension_ir.name,
            "ref": time_dimension_ir.semantic_id,
        },
        **{
            field_ir.name: {
                "role": "dimension",
                "column": field_ir.name,
                "ref": field_ir.semantic_id,
            }
            for _, field_ir in resolved_dimensions
        },
    }
    semantic_kind = "panel" if dimension_names else "time_series"

    return _Result(dense_df), axes, semantic_kind, None


# ``_FIXED_UNIT_SECONDS`` is imported from ``marivo.analysis.windows.grain``
# so there is a single source of truth for fixed-grain second-widths.
