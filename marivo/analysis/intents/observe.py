"""Materialize a semantic metric into a MetricFrame."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import ibis
from ibis.expr.operations.relations import Field

from marivo.analysis.errors import (
    AnalysisError,
    MetricNotFoundError,
    MetricShapeUnsupportedError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.identity import make_component_artifact_id
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.executor.bucketing import (
    apply_time_series_bucket,
    bucket_start_expr,
    ensure_bucket_start_timestamp,
)
from marivo.analysis.executor.runner import (
    apply_slice_to_dataset,
    execute,
    normalize_slice_for_storage,
)
from marivo.analysis.executor.windowing import (
    datasource_engine_profile,
    datasource_read_timezone,
    resolve_window_time_field,
)
from marivo.analysis.frames.component import (
    ComponentFrame,
    ComponentFrameMeta,
    resolve_role_column_name,
    resolve_role_columns,
)
from marivo.analysis.frames.coverage import CoverageFrame, CoverageFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._shape import SemanticShape, observe_output_shape
from marivo.analysis.intents._types import SliceValue
from marivo.analysis.intents.observe_planner import (
    BaseObservePlan,
    ComponentPlan,
    CumulativeObservePlan,
    DerivedObservePlan,
    _planned_metric,
    _validate_field_expr,
    plan_base_observe,
    plan_observe,
)
from marivo.analysis.intents.sampled_fold import (
    compile_fold,
    ensure_sampled_grain_supported,
    quantile_capability,
    sample_interval_token,
    sample_point_table,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.semantic_inputs import (
    DimensionInput,
    MetricInput,
    normalize_dimension_input,
    normalize_metric_input,
)
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import (
    persist_frame,
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.windows.grain import Grain, ensure_grain_supported
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    GrainInput,
    TimeScopeInput,
    dump_window,
    make_absolute_window,
    normalize_timescope_input,
)
from marivo.refs import SemanticRef
from marivo.semantic.catalog import (
    DerivedMetricDetails,
    DimensionDetails,
    EntityDetails,
    SemanticKind,
    SimpleMetricDetails,
    TimeDimensionDetails,
)
from marivo.semantic.ir import HourPrefixParse

# ---------------------------------------------------------------------------
# catalog-details -> runner adapter types
# ---------------------------------------------------------------------------
# The runner.py window helpers still expect old-style field metadata with
# attributes like ``fn``, ``fields``, ``is_time``, and ``time_meta``. These
# adapters are intentionally narrow: they are built from catalog details and
# call resolver.dimension_on(...), never SemanticProject sidecar callables.


class _TimeFieldMetaAdapter:
    """Adapter that mimics the old TimeFieldMeta for runner.py."""

    def __init__(
        self,
        data_type: str,
        granularity: str,
        format: str | None = None,
        required_prefix: str | None = None,
        timezone: str | None = None,
        parse_kind: str | None = None,
        semantic_id: str | None = None,
        name: str | None = None,
    ) -> None:
        self.data_type = data_type
        self.granularity = granularity
        self.format = format
        self.required_prefix = required_prefix
        self.timezone = timezone
        self.parse_kind = parse_kind
        self.semantic_id = semantic_id
        self.name = name


class _DimensionIRAdapter:
    """Adapter that mimics the old DimensionIR for runner.py."""

    def __init__(
        self,
        semantic_id: str,
        name: str,
        dataset_name: str,
        fn: Any,
        *,
        is_time: bool = False,
        is_default: bool = False,
        time_meta: _TimeFieldMetaAdapter | None = None,
        sample_interval: Any | None = None,
    ) -> None:
        self.semantic_id = semantic_id
        self.name = name
        self.dataset_name = dataset_name
        self.fn = fn
        self.is_time = is_time
        self.is_default = is_default
        self.time_meta = time_meta
        self.sample_interval = sample_interval


class _EntityIRAdapter:
    """Adapter that mimics the old EntityIR shape for runner.py window helpers."""

    def __init__(
        self,
        name: str,
        fn: Any,
        datasource_name: str,
        fields: dict[str, _DimensionIRAdapter],
    ) -> None:
        self.name = name
        self.fn = fn
        self.datasource_name = datasource_name
        self.fields = fields


def _catalog_id(ref: str, kind: SemanticKind) -> str:
    return f"{kind.value}.{ref}"


def _catalog_kind(catalog: Any, ref: str) -> SemanticKind | None:
    return cast("SemanticKind | None", catalog._resolve_kind_of(ref, catalog._require_ready()))


def _catalog_object(catalog: Any, ref: str, kind: SemanticKind) -> Any:
    return catalog.get(_catalog_id(ref, kind))


def _entity_details(catalog: Any, ref: str) -> EntityDetails:
    details = _catalog_object(catalog, ref, SemanticKind.ENTITY).details()
    if not isinstance(details, EntityDetails):
        raise MetricNotFoundError(message=f"entity {ref!r} not found", details={"entity": ref})
    return details


def _field_details(catalog: Any, ref: str) -> DimensionDetails | TimeDimensionDetails:
    kind = _catalog_kind(catalog, ref)
    if kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        raise SemanticKindMismatchError(
            message=f"field {ref!r} is not a dimension or time dimension",
            details={"ref": ref, "actual_kind": str(kind) if kind is not None else None},
        )
    details = _catalog_object(catalog, ref, kind).details()
    if not isinstance(details, (DimensionDetails, TimeDimensionDetails)):
        raise SemanticKindMismatchError(
            message=f"field {ref!r} is not a dimension or time dimension",
            details={"ref": ref, "actual_kind": getattr(details, "kind", None)},
        )
    return details


def _fields_for_entity(
    catalog: Any, entity_ref: str
) -> list[DimensionDetails | TimeDimensionDetails]:
    fields: list[DimensionDetails | TimeDimensionDetails] = []
    for kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
        for obj in catalog.list(str(kind), scope=f"entity.{entity_ref}"):
            details = obj.details()
            if isinstance(details, (DimensionDetails, TimeDimensionDetails)):
                fields.append(details)
    return fields


def _build_entity_adapter(
    catalog: Any,
    resolver: Any,
    entity: EntityDetails,
) -> _EntityIRAdapter:
    def _source_fn(_backend: Any, *, _ref: SemanticRef = entity.ref) -> Any:
        return resolver.table(_ref)

    field_adapters: dict[str, _DimensionIRAdapter] = {}
    for field in _fields_for_entity(catalog, entity.ref.id):
        field_ref = field.ref

        def _field_fn(table_arg: Any, *, _ref: SemanticRef = field_ref) -> Any:
            return resolver.dimension_on(_ref, table_arg)

        if isinstance(field, TimeDimensionDetails):
            is_time = True
            # For hour_prefix fields, look up the companion field name
            # from the catalog's internal IR registry.
            required_prefix: str | None = None
            if field.parse_kind == "hour_prefix":
                reg = catalog._reg
                dim_ir = reg.dimensions.get(field.ref.id) if reg else None
                if dim_ir is not None and isinstance(dim_ir.parse, HourPrefixParse):
                    required_prefix = dim_ir.parse.prefix
            # Resolve data_type: when the IR no longer carries data_type on
            # strptime/hour_prefix, infer from parse_kind so the adapter has a
            # usable value before the runner's _ensure_resolved_data_type runs.
            if field.data_type is not None:
                effective_data_type = field.data_type
            elif field.parse_kind in ("strptime", "hour_prefix"):
                effective_data_type = "string"
            elif field.parse_kind is not None:
                effective_data_type = field.parse_kind  # date/datetime/timestamp
            else:
                effective_data_type = "date"  # deferred — resolved later by runner
            time_meta = _TimeFieldMetaAdapter(
                data_type=effective_data_type,
                granularity=field.granularity or "day",
                format=field.format,
                required_prefix=required_prefix,
                timezone=field.timezone,
                parse_kind=field.parse_kind,
                semantic_id=field.ref.id,
                name=field.name,
            )
        else:
            is_time = False
            time_meta = None
        adapter = _DimensionIRAdapter(
            semantic_id=field.ref.id,
            name=field.name,
            dataset_name=entity.name,
            fn=_field_fn,
            is_time=is_time,
            is_default=getattr(field, "is_default", False),
            time_meta=time_meta,
            sample_interval=getattr(field, "sample_interval", None),
        )
        field_adapters[field.name] = adapter
    return _EntityIRAdapter(
        name=entity.name,
        fn=_source_fn,
        datasource_name=entity.datasource.id,
        fields=field_adapters,
    )


# ---------------------------------------------------------------------------
# Observe intent
# ---------------------------------------------------------------------------


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _params_digest(params: dict[str, Any]) -> str:
    body = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


# ---------------------------------------------------------------------------
# Component-aware composition helpers
# ---------------------------------------------------------------------------

_COMPONENT_AWARE_COMPOSITIONS = {"ratio", "weighted_average", "linear"}


def _is_component_aware_composition(metric_ir: Any) -> bool:
    composition = getattr(metric_ir, "composition", None)
    kind = getattr(composition, "kind", None)
    return isinstance(kind, str) and kind in _COMPONENT_AWARE_COMPOSITIONS


def _composition_payload(metric_ir: Any) -> dict[str, Any] | None:
    if not _is_component_aware_composition(metric_ir):
        return None
    return {
        "kind": metric_ir.composition.kind,
        "components": dict(metric_ir.composition.components),
    }


def _role_to_column_name(metric_ir: Any, role: str) -> str:
    return resolve_role_column_name(metric_ir.composition.components, role)


def _component_parent_columns(metric_ir: Any) -> list[str]:
    return resolve_role_columns(metric_ir.composition.components)


def _component_frame_df(
    *,
    raw_df: Any,
    metric_ir: Any,
    axes_columns: list[str],
    metric_value_column: str,
) -> Any:
    role_columns = _component_parent_columns(metric_ir)
    for role, col in zip(metric_ir.composition.components, role_columns, strict=True):
        _require_component_role_column(metric_ir, role, col, raw_df)
    selected = [*axes_columns, *role_columns, metric_value_column]
    return raw_df[selected][[*axes_columns, *role_columns, metric_value_column]]


def _add_fold_metadata_to_component_df(
    df: Any,
    metric_ir: Any,
    component_plans: list[Any],
    merge_keys: list[str],
    metric_name: str,
) -> Any:
    """Transform wide-format component df to long format with fold metadata columns.

    Each component role becomes a separate row with component_metric_id, time_fold,
    and status_time_dimension columns.
    """
    pandas = __import__("pandas")
    role_columns = _component_parent_columns(metric_ir)
    # Build a mapping from role column name to component plan metadata
    role_to_meta: dict[str, dict[str, Any]] = {}
    for cp in component_plans:
        role = cp.role
        col_name = resolve_role_column_name(metric_ir.composition.components, role)
        role_to_meta[col_name] = {
            "component_metric_id": cp.component_metric_ir.semantic_id.rsplit(".", 1)[-1],
            "time_fold": (
                cp.component_metric_ir.time_fold.label()
                if getattr(cp.component_metric_ir, "time_fold", None)
                else None
            ),
            "status_time_dimension": getattr(cp.component_metric_ir, "status_time_dimension", None),
        }
    # Melt the wide-format df into long format
    long_frames: list[Any] = []
    for col_name in role_columns:
        meta = role_to_meta.get(
            col_name,
            {
                "component_metric_id": col_name,
                "time_fold": None,
                "status_time_dimension": None,
            },
        )
        subset = df[[*merge_keys, col_name, metric_name]].copy()
        subset = subset.rename(columns={col_name: "value"})
        subset["component_metric_id"] = meta["component_metric_id"]
        subset["time_fold"] = meta["time_fold"]
        subset["status_time_dimension"] = meta["status_time_dimension"]
        long_frames.append(subset)
    result = pandas.concat(long_frames, ignore_index=True)
    if merge_keys:
        result = result.sort_values([*merge_keys, "component_metric_id"]).reset_index(drop=True)
    return result


def _persist_metric_component_frame(
    *,
    session: Session,
    df: Any,
    parent: MetricFrame,
    metric_ir: Any,
    axes: dict[str, Any],
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"],
    job_ref: str,
) -> ComponentFrame:
    # Component decomposition operates on arity-1 metric frames; multi-metric
    # frames are gated out upstream. Narrow metric_id for the ComponentFrameMeta
    # contract which requires a single metric id.
    assert parent.meta.metric_id is not None
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
            metric_id=parent.meta.metric_id,
            composition_kind=metric_ir.composition.kind,
            components={
                k: (v.id if isinstance(v, SemanticRef) else str(v))
                for k, v in metric_ir.composition.components.items()
            },
            linear_terms=metric_ir.linear_terms if metric_ir.composition.kind == "linear" else (),
            axes=axes,
            semantic_kind=semantic_kind,
            semantic_model=parent.meta.semantic_model,
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
) -> MetricFrame:
    parent.meta = parent.meta.model_copy(
        update={
            "component_ref": component.ref,
            "composition": _composition_payload(metric_ir),
        }
    )
    parent.meta = cast("MetricFrameMeta", persist_frame(session, parent))
    return parent


def _persist_and_attach_coverage_sidecar(
    *,
    session: Session,
    df: Any,
    parent: MetricFrame,
    job_ref: str,
) -> MetricFrame:
    """Persist a CoverageFrame sidecar and attach it to the parent MetricFrame."""
    from marivo.analysis.evidence.identity import make_coverage_artifact_id

    frame_ref = make_coverage_artifact_id(parent.ref)
    # Build coverage summary from the coverage DataFrame
    coverage_ratios = df["coverage_ratio"].tolist() if "coverage_ratio" in df.columns else []
    coverage_summary: dict[str, Any] | None = None
    if coverage_ratios:
        coverage_summary = {
            "min": min(coverage_ratios),
            "avg": sum(coverage_ratios) / len(coverage_ratios),
            "partial_buckets": sum(1 for r in coverage_ratios if r != 1.0),
        }
    sample_interval_val = None
    fold_meta = getattr(parent.meta, "fold", None)
    if isinstance(fold_meta, dict):
        sample_interval_val = fold_meta.get("sample_interval")
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
            coverage_kind="time_slot",
            axes=parent.meta.axes,
            sample_interval=sample_interval_val or "unknown",
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
    parent.meta = cast("MetricFrameMeta", persist_frame(session, parent))
    return parent


def _resolve_timescope(
    timescope: TimeScopeInput,
    *,
    grain: GrainInput,
    time_dimension: str | None,
) -> tuple[AbsoluteWindow | None, dict[str, Any] | None]:
    timescope_in = normalize_timescope_input(timescope)
    resolved = make_absolute_window(timescope_in, grain=grain, time_dimension=time_dimension)
    original = timescope_in.model_dump(mode="json") if timescope_in is not None else None
    return resolved, original


def _validate_dimension_ids(dimensions: list[str] | None) -> list[str]:
    if dimensions is None:
        return []

    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for dimension in dimensions:
        if dimension in seen:
            duplicate_ids.add(dimension)
        seen.add(dimension)
    if duplicate_ids:
        raise SemanticKindMismatchError(
            message="observe dimensions must not contain duplicate dimension ids",
            details={
                "expected_kind": "unique dimension ids",
                "got_kind": "duplicate dimension ids",
                "duplicate_dimensions": sorted(duplicate_ids),
            },
        )
    return dimensions


def _evaluate_composition_on_frame(metric_ir: Any, frame: Any) -> Any:
    kind = metric_ir.composition.kind
    if kind == "ratio":
        num_col = _role_to_column_name(metric_ir, "numerator")
        den_col = _role_to_column_name(metric_ir, "denominator")
        return frame[num_col] / frame[den_col]
    if kind == "weighted_average":
        value_col = _role_to_column_name(metric_ir, "value")
        weight_col = _role_to_column_name(metric_ir, "weight")
        return frame[value_col] / frame[weight_col]
    if kind == "linear":
        terms = metric_ir.composition.components
        acc = None
        for i, role in enumerate(sorted(terms, key=lambda r: int(r.removeprefix("term")))):
            col = frame[_role_to_column_name(metric_ir, role)]
            # Determine sign from linear_terms: sign is the first element of each tuple
            sign_str = metric_ir.linear_terms[i][0] if metric_ir.linear_terms else "+"
            signed = col if sign_str == "+" else -col
            acc = signed if acc is None else acc + signed
        return acc
    raise MetricShapeUnsupportedError(
        message=f"unsupported derived metric composition kind {kind!r}",
        details={
            "kind": "DerivedMetricCompositionUnsupported",
            "metric": metric_ir.semantic_id,
            "composition_kind": kind,
        },
    )


def _require_component_role_column(
    metric_ir: Any,
    role: str,
    column_name: str,
    frame: Any,
) -> Any:
    component_id = metric_ir.composition.components.get(role)
    if component_id is None:
        raise MetricShapeUnsupportedError(
            message=f"derived metric {metric_ir.semantic_id!r} is missing component role {role!r}",
            details={
                "kind": "DerivedMetricComponentMissing",
                "metric": metric_ir.semantic_id,
                "role": role,
            },
        )
    if column_name not in frame:
        raise MetricShapeUnsupportedError(
            message=(
                f"derived metric {metric_ir.semantic_id!r} component role column "
                f"{column_name!r} (role {role!r}) is missing"
            ),
            details={
                "kind": "DerivedMetricComponentColumnMissing",
                "metric": metric_ir.semantic_id,
                "role": role,
                "component_metric": component_id,
                "column": column_name,
            },
        )
    return frame[column_name]


class _Result:
    """Minimal result holder used by _execute_base and _execute_derived."""

    def __init__(self, df: Any) -> None:
        self.df = df
        self.row_count = len(df)


# ---------------------------------------------------------------------------
# Cumulative observe execution helpers
# ---------------------------------------------------------------------------


def _base_aggregation_name(metric_ir: Any) -> str:
    """Return the aggregation name string (e.g. 'sum', 'count_distinct')."""
    agg = getattr(metric_ir, "aggregation", None)
    return str(agg) if agg is not None else ""


def _base_measure_ref(metric_ir: Any) -> str | None:
    """Return the base metric's measure semantic_id, or None for count-without-measure."""
    return getattr(metric_ir, "measure", None)


def _count_distinct_key_expr(resolver: Any, metric_ir: Any, table: Any) -> Any:
    """Resolve the measure column expression for a count_distinct first-seen rewrite."""
    measure_ref = _base_measure_ref(metric_ir)
    if measure_ref is None:
        raise AnalysisError(
            message="cumulative count_distinct requires a measure-backed base metric",
            details={"metric": getattr(metric_ir, "semantic_id", None)},
        )
    return resolver.measure_on(measure_ref, table)


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
        where_dict[entry.field.name] = entry.value
    if not where_dict:
        return table
    return apply_slice_to_dataset(table, where_dict, dataset_ir=dataset_ir)


_GRAIN_PANDAS_FREQ: dict[str, str] = {
    "second": "s",
    "minute": "min",
    "hour": "h",
    "day": "D",
    "week": "W-MON",
    "month": "MS",
    "quarter": "QS",
    "year": "YS",
}

# Fixed-frequency grains that can use Timestamp.floor() for alignment.
_FIXED_GRAINS: frozenset[str] = frozenset({"second", "minute", "hour", "day"})


def _align_to_grain_start(ts: Any, unit: str, count: int = 1) -> Any:
    """Truncate a timestamp to the start of its grain-period.

    For fixed grains (second/minute/hour/day) with ``count == 1`` this uses
    ``Timestamp.floor``.  For fixed sub-day grains with ``count > 1`` the
    alignment replicates the day-anchored offset logic in
    :func:`bucket_start_expr` so that the spine bucket-start values match the
    SQL-level buckets exactly.

    For calendar grains (week/month/quarter/year) the boundary is computed
    explicitly because ``floor`` does not support non-fixed frequencies.
    """
    import pandas as pd

    if unit in _FIXED_GRAINS:
        if count > 1 and unit in ("second", "minute", "hour"):
            width = count * _FIXED_UNIT_SECONDS[unit]
            day_start = ts.floor("D")
            elapsed = int((ts - day_start).total_seconds())
            offset = (elapsed // width) * width
            return day_start + pd.Timedelta(seconds=offset)
        return ts.floor(_GRAIN_PANDAS_FREQ[unit])
    if unit == "week":
        days_since_monday = ts.weekday()  # 0=Monday
        return (ts - pd.Timedelta(days=days_since_monday)).normalize()
    if unit == "month":
        return pd.Timestamp(year=ts.year, month=ts.month, day=1)
    if unit == "quarter":
        quarter_start_month = ((ts.month - 1) // 3) * 3 + 1
        return pd.Timestamp(year=ts.year, month=quarter_start_month, day=1)
    if unit == "year":
        return pd.Timestamp(year=ts.year, month=1, day=1)
    # Should not reach here for valid Grain units.
    raise ValueError(f"unsupported grain unit for alignment: {unit!r}")


def _bucket_date_range(window: Any) -> list[Any]:
    """Generate a list of bucket-start timestamps for a window at the given grain.

    The window is half-open [start, end); we emit one bucket per grain
    interval from start (inclusive, truncated to the grain boundary) to
    end (exclusive).  The bucket-start values align with what
    :func:`bucket_start_expr` produces at the SQL level so that the dense
    spine matches the flow query buckets.
    """
    import pandas as pd

    start = pd.Timestamp(window.start)
    end = pd.Timestamp(window.end)
    grain = window.grain
    if grain is None:
        return [start]
    unit = grain.unit
    count = grain.count
    freq = f"{count}{_GRAIN_PANDAS_FREQ[unit]}" if count > 1 else _GRAIN_PANDAS_FREQ[unit]
    # Truncate start to the grain boundary so the first bucket matches
    # what bucket_start_expr produces for events in the first partial bucket.
    aligned_start = _align_to_grain_start(start, unit, count)
    bucket_index = pd.date_range(aligned_start, end, freq=freq, inclusive="left")
    return list(bucket_index)


def _dense_cumulative_frame(
    *,
    baseline_df: Any,
    flow_df: Any,
    bucket_values: list[Any],
    dimension_columns: list[str],
    value_column: str = "value",
) -> Any:
    """Build a dense cumulative DataFrame from baseline + flow.

    baseline_df: per-slice baseline values (all history before window start).
    flow_df: per-bucket-per-slice flow values (within window).
    bucket_values: dense list of bucket_start timestamps.
    dimension_columns: dimension column names for panel/segmented shapes.

    Returns a DataFrame with columns [bucket_start, *dimension_columns, value]
    where value = baseline + cumsum(flow) within each slice.
    """
    import pandas as pd

    key_columns = list(dimension_columns)
    if key_columns:
        combos = pd.concat(
            [
                baseline_df[key_columns]
                if not baseline_df.empty
                else pd.DataFrame(columns=key_columns),
                flow_df[key_columns] if not flow_df.empty else pd.DataFrame(columns=key_columns),
            ],
            ignore_index=True,
        ).drop_duplicates()
    else:
        combos = pd.DataFrame({"__single__": [0]})
    bucket_df = pd.DataFrame({"bucket_start": bucket_values})
    spine = bucket_df.merge(combos, how="cross") if key_columns else bucket_df.assign(__single__=0)

    baseline = baseline_df.copy()
    flow = flow_df.copy()
    if not key_columns:
        baseline["__single__"] = 0
        flow["__single__"] = 0
    merge_keys = key_columns or ["__single__"]
    seed = (
        baseline.groupby(merge_keys, dropna=False)[value_column].sum().reset_index(name="_baseline")
    )
    out = spine.merge(seed, on=merge_keys, how="left")
    out = out.merge(
        flow[["bucket_start", *merge_keys, value_column]],
        on=["bucket_start", *merge_keys],
        how="left",
    )
    out["_baseline"] = out["_baseline"].fillna(0)
    out[value_column] = out[value_column].fillna(0)
    out = out.sort_values([*merge_keys, "bucket_start"])
    out[value_column] = (
        out.groupby(merge_keys, dropna=False)[value_column].cumsum() + out["_baseline"]
    )
    out = out.drop(columns=["_baseline"])
    if "__single__" in out.columns:
        out = out.drop(columns=["__single__"])
    return out.sort_values(["bucket_start", *key_columns]).reset_index(drop=True)


def _execute_cumulative(
    plan: CumulativeObservePlan,
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[
    Any,
    dict[str, Any],
    Literal["scalar", "time_series", "segmented", "panel"],
]:
    """Execute a CumulativeObservePlan.

    Returns (result, axes, semantic_kind).

    For scalar/segmented (no time grain): one query up to window end
    (as-of-end strategy).

    For time-series/panel (with time grain): baseline query (all history
    before window start) + flow query (per-bucket aggregation within window)
    + dense spine + cumsum in pandas.

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

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"

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
                field_ir.name: {"role": "dimension", "column": field_ir.name}
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
        return result, axes, semantic_kind

    # --- Time-series/panel: baseline + flow + dense spine + cumsum ---
    assert resolved_window is not None
    assert resolved_window.grain is not None

    time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
    base = (
        time_dimension_ir.time_meta.granularity if time_dimension_ir.time_meta else None
    ) or "day"
    ensure_grain_supported(resolved_window.grain, base)

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

        # Build a combined raw table: all history up to window.end
        combined_raw = resolver.table(
            _catalog_object(catalog, base_plan.root_entity, SemanticKind.ENTITY).ref
        )
        # Re-apply where/slice_by filters that base_plan.table already has.
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

        # Find first-seen per distinct key (+ dimensions)
        combined_key_expr = _count_distinct_key_expr(resolver, base_metric_ir, combined_raw)
        combined_key_name = combined_key_expr.get_name()
        first_seen = combined_raw.group_by([combined_key_name, *dimension_names]).aggregate(
            first_seen_ts=combined_time_expr.min()
        )

        # Baseline: count keys first-seen before window.start
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
        flow_dataset_tables = dict.fromkeys(metric_datasets, bucketed_table)
        flow_metric_expr = _metric_expr(
            catalog, resolver, base_metric_ir.semantic_id, metric_datasets, flow_dataset_tables
        )
        group_names_flow = ["bucket_start", *dimension_names]
        flow_grouped = (
            bucketed_table.group_by(group_names_flow)
            .aggregate(value=flow_metric_expr)
            .order_by(group_names_flow)
            .select(*group_names_flow, "value")
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

        baseline_dataset_tables = dict.fromkeys(metric_datasets, baseline_table)
        baseline_metric_expr = _metric_expr(
            catalog, resolver, base_metric_ir.semantic_id, metric_datasets, baseline_dataset_tables
        )
        baseline_group_keys = list(dimension_names)
        if baseline_group_keys:
            baseline_grouped = (
                baseline_table.group_by(baseline_group_keys)
                .aggregate(value=baseline_metric_expr)
                .order_by(baseline_group_keys)
                .select(*baseline_group_keys, "value")
            )
        else:
            baseline_grouped = baseline_table.aggregate(value=baseline_metric_expr)
        baseline_result = execute(
            baseline_grouped,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        baseline_df = baseline_result.df

    # Build dense spine and cumsum
    bucket_values = _bucket_date_range(resolved_window)
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
        },
        **{
            field_ir.name: {"role": "dimension", "column": field_ir.name}
            for _, field_ir in resolved_dimensions
        },
    }
    semantic_kind = "panel" if dimension_names else "time_series"

    return _Result(dense_df), axes, semantic_kind


_FIXED_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
}


def _fixed_grain_seconds_for_coverage(count: int, unit: str) -> int:
    """Convert a grain (count, unit) to total seconds for coverage expected_samples calculation."""
    return count * _FIXED_UNIT_SECONDS.get(unit, 0)


def _execute_sampled_base(
    plan: BaseObservePlan,
    metric_ir: Any,
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
    """Two-phase execution for sampled semi-additive metrics.

    Phase A: aggregate spatially within each sample point.
    Phase B: fold over time (mean/min/max/first/last).

    Returns (result, axes, semantic_kind, coverage_df_or_None).
    """
    assert metric_ir.status_time_dimension is not None
    time_dimension_ir = _resolve_fold_time_field(catalog, metric_ir.status_time_dimension)
    root_adapter = _build_entity_adapter(
        catalog,
        resolver,
        _entity_details(catalog, plan.root_entity),
    )
    root_time_adapter = root_adapter.fields.get(time_dimension_ir.name)
    if root_time_adapter is None:
        # Try by iterating time dimensions
        for _fname, fadapter in root_adapter.fields.items():
            if fadapter.is_time and fadapter.semantic_id == metric_ir.status_time_dimension:
                root_time_adapter = fadapter
                break
    if root_time_adapter is None:
        raise MetricNotFoundError(
            message=f"time field adapter for '{metric_ir.status_time_dimension}' not found",
            details={"status_time_dimension": metric_ir.status_time_dimension},
        )
    sample_interval = root_time_adapter.sample_interval
    assert sample_interval is not None
    assert root_time_adapter.time_meta is not None
    ensure_sampled_grain_supported(
        requested_grain=resolved_window.grain if resolved_window is not None else None,
        time_meta=root_time_adapter.time_meta,
        sample_interval=sample_interval,
    )
    sample_grain = Grain(count=sample_interval.count, unit=sample_interval.unit)
    read_tz = datasource_read_timezone(session._connection_runtime, plan.datasource_name)
    profile = datasource_engine_profile(session._connection_runtime, plan.datasource_name)
    table = sample_point_table(
        plan.table,
        time_field_ir=root_time_adapter,
        sample_grain=sample_grain,
        report_tz=cast("ZoneInfo", session.report_tz),
        datasource_read_tz=read_tz,
        window=resolved_window,
        dataset_ir=root_adapter,
        profile=profile,
    )
    dimension_names = [dimension.column for dimension in plan.dimensions]
    metric_datasets = tuple(metric_ir.entities)
    dataset_tables = dict.fromkeys(metric_datasets, table)
    metric_expr = _metric_expr(
        catalog, resolver, metric_ir.semantic_id, metric_datasets, dataset_tables
    )
    phase_a = table.group_by(["sample_point", *dimension_names]).aggregate(value=metric_expr)
    is_time_series = resolved_window is not None and resolved_window.grain is not None
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        phase_b_source = phase_a.mutate(
            bucket_start=bucket_start_expr(phase_a.sample_point, resolved_window.grain)
        )
        group_names = ["bucket_start", *dimension_names]
    else:
        phase_b_source = phase_a
        group_names = list(dimension_names)
    folded_value = compile_fold(
        phase_b_source.value, phase_b_source.sample_point, metric_ir.time_fold
    )
    if group_names:
        grouped_expr = (
            phase_b_source.group_by(group_names)
            .aggregate(value=folded_value)
            .order_by(group_names)
            .select(*group_names, "value")
        )
    else:
        grouped_expr = phase_b_source.aggregate(value=folded_value).select("value")
    result = execute(
        grouped_expr,
        datasource_name=plan.datasource_name,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    # --- Coverage sidecar: count distinct sample points per bucket ---
    coverage_df: Any | None = None
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        coverage_expr = phase_b_source.group_by(group_names).aggregate(
            actual_samples=phase_b_source.sample_point.nunique()
        )
        coverage_result = execute(
            coverage_expr,
            datasource_name=plan.datasource_name,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        coverage_df = coverage_result.df
        # Ensure bucket_start is normalized the same way as the main frame
        if "bucket_start" in coverage_df.columns:
            coverage_df["bucket_start"] = ensure_bucket_start_timestamp(
                coverage_df["bucket_start"],
                time_meta=root_time_adapter.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=coverage_result.backend_datetime_decode_policy,
            )
        # Compute expected_samples from bucket duration and sample interval
        bucket_seconds = _fixed_grain_seconds_for_coverage(
            resolved_window.grain.count, resolved_window.grain.unit
        )
        interval_seconds = _fixed_grain_seconds_for_coverage(
            sample_interval.count, sample_interval.unit
        )
        expected = bucket_seconds // interval_seconds if interval_seconds > 0 else 0
        coverage_df["expected_samples"] = expected
        coverage_df["coverage_ratio"] = coverage_df["actual_samples"] / expected
        coverage_df["coverage_status"] = coverage_df["coverage_ratio"].apply(
            lambda r: "complete" if r == 1.0 else "partial"
        )
    if "bucket_start" in result.df and resolved_window is not None:
        assert resolved_window.grain is not None
        result.df["bucket_start"] = ensure_bucket_start_timestamp(
            result.df["bucket_start"],
            time_meta=root_time_adapter.time_meta,
            dataset_ir=root_adapter,
            grain=resolved_window.grain,
            report_tz=cast("ZoneInfo", session.report_tz),
            backend_datetime_decode_policy=result.backend_datetime_decode_policy,
        )
    axes = dict(plan.axes_metadata)
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        axes["time"] = {
            "role": "time",
            "column": "bucket_start",
            "grain": resolved_window.grain.to_token(),
            "time_dimension": root_time_adapter.name,
        }
    if is_time_series and dimension_names:
        semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "panel"
    elif is_time_series:
        semantic_kind = "time_series"
    elif dimension_names:
        semantic_kind = "segmented"
    else:
        semantic_kind = "scalar"
    return result, axes, semantic_kind, coverage_df


def _resolve_fold_time_field(catalog: Any, status_time_dimension_id: str) -> TimeDimensionDetails:
    details = _field_details(catalog, status_time_dimension_id)
    if not isinstance(details, TimeDimensionDetails):
        raise SemanticKindMismatchError(
            message=f"status time dimension {status_time_dimension_id!r} is not a time dimension",
            details={"status_time_dimension": status_time_dimension_id},
        )
    return details


def _expression_source_columns(expr: Any) -> set[str] | None:
    try:
        nodes = expr.op().find_topmost(lambda node: isinstance(node, Field))
    except Exception:
        return None
    columns: set[str] = set()
    for node in nodes:
        name = getattr(node, "name", None)
        if isinstance(name, str) and name:
            columns.add(name)
    return columns


def _time_dependency_exprs(table: Any, *, time_field_ir: Any, dataset_ir: Any) -> list[Any]:
    expressions = [time_field_ir.fn(table)]
    time_meta = getattr(time_field_ir, "time_meta", None)
    required_prefix = getattr(time_meta, "required_prefix", None)
    if required_prefix:
        for field in dataset_ir.fields.values():
            if field.name == required_prefix or field.semantic_id == required_prefix:
                expressions.append(field.fn(table))
                break
    return expressions


def _prune_base_observe_projection(
    plan: BaseObservePlan,
    metric_ir: Any,
    *,
    catalog: Any,
    resolver: Any,
    resolved_window: AbsoluteWindow | None,
) -> BaseObservePlan:
    table = plan.table
    metric_datasets = tuple(metric_ir.entities)
    try:
        dataset_tables = dict.fromkeys(metric_datasets, table)
        expressions = [
            _metric_expr(catalog, resolver, metric_ir.semantic_id, metric_datasets, dataset_tables)
        ]
        root_adapter = _build_entity_adapter(
            catalog,
            resolver,
            _entity_details(catalog, plan.root_entity),
        )
        if resolved_window is not None and resolved_window.grain is not None:
            time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
            expressions.extend(
                _time_dependency_exprs(
                    table,
                    time_field_ir=time_dimension_ir,
                    dataset_ir=root_adapter,
                )
            )
        for dimension in plan.dimensions:
            dimension_ref = _field_details(catalog, dimension.field.semantic_id).ref
            expressions.append(
                _validate_field_expr(
                    resolver.dimension_on(dimension_ref, table),
                    field_id=dimension.field.semantic_id,
                )
            )

        required_columns: set[str] = set()
        for expr in expressions:
            columns = _expression_source_columns(expr)
            if columns is None:
                return plan
            required_columns.update(columns)

        available_columns = set(table.columns)
        if not required_columns or not required_columns.issubset(available_columns):
            return plan
        selected_columns = [column for column in table.columns if column in required_columns]
        if not selected_columns:
            return plan
        pruned_table = table.select(*selected_columns)
    except Exception:
        return plan

    return replace(
        plan,
        table=pruned_table,
        dataset_tables=dict.fromkeys(metric_datasets, pruned_table),
    )


def _execute_base(
    plan: BaseObservePlan,
    metric_ir: Any,
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    dimensions: list[Any] | None,
    resolved_window: AbsoluteWindow | None,
) -> tuple[Any, dict[str, Any], Literal["scalar", "time_series", "segmented", "panel"], Any | None]:
    """Execute a BaseObservePlan and return (result, axes, semantic_kind, coverage_df_or_None)."""
    if getattr(metric_ir, "time_fold", None) is not None:
        return _execute_sampled_base(
            plan,
            metric_ir,
            catalog=catalog,
            resolver=resolver,
            session=session,
            resolved_window=resolved_window,
        )
    metric_datasets = tuple(metric_ir.entities)
    primary_datasource = plan.datasource_name
    read_tz = datasource_read_timezone(session._connection_runtime, primary_datasource)
    profile = datasource_engine_profile(session._connection_runtime, primary_datasource)
    plan = _prune_base_observe_projection(
        plan,
        metric_ir,
        catalog=catalog,
        resolver=resolver,
        resolved_window=resolved_window,
    )
    dataset_tables = plan.dataset_tables
    resolved_dimensions = [
        (dimension.field.entity, dimension.field) for dimension in plan.dimensions
    ]
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"

    if is_time_series and resolved_window is not None and resolved_dimensions:
        root_adapter = _build_entity_adapter(
            catalog,
            resolver,
            _entity_details(catalog, plan.root_entity),
        )
        time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
        if resolved_window.grain is not None:
            base = (
                time_dimension_ir.time_meta.granularity if time_dimension_ir.time_meta else None
            ) or "day"
            ensure_grain_supported(resolved_window.grain, base)
        bucketed_table = apply_time_series_bucket(
            plan.table,
            field_ir=time_dimension_ir,
            window=resolved_window,
            report_tz=cast("ZoneInfo", session.report_tz),
            datasource_read_tz=read_tz,
            profile=profile,
            dataset_ir=root_adapter,
        )
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
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
        dataset_tables = dict.fromkeys(metric_datasets, bucketed_table)
        metric_expr = _metric_expr(
            catalog, resolver, metric_ir.semantic_id, metric_datasets, dataset_tables
        )
        group_names = ["bucket_start", *dimension_names]
        grouped_expr = (
            bucketed_table.group_by(group_names)
            .aggregate(value=metric_expr)
            .order_by(group_names)
            .select(*group_names, "value")
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        if "bucket_start" in result.df:
            result.df["bucket_start"] = ensure_bucket_start_timestamp(
                result.df["bucket_start"],
                time_meta=time_dimension_ir.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=result.backend_datetime_decode_policy,
            )
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_dimension": time_dimension_ir.name,
            },
            **{
                field_ir.name: {"role": "dimension", "column": field_ir.name}
                for _, field_ir in resolved_dimensions
            },
        }
        semantic_kind = "panel"
    elif is_time_series and resolved_window is not None:
        root_adapter = _build_entity_adapter(
            catalog,
            resolver,
            _entity_details(catalog, plan.root_entity),
        )
        time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
        if resolved_window.grain is not None:
            base = (
                time_dimension_ir.time_meta.granularity if time_dimension_ir.time_meta else None
            ) or "day"
            ensure_grain_supported(resolved_window.grain, base)
        bucketed_table = apply_time_series_bucket(
            plan.table,
            field_ir=time_dimension_ir,
            window=resolved_window,
            report_tz=cast("ZoneInfo", session.report_tz),
            datasource_read_tz=read_tz,
            profile=profile,
            dataset_ir=root_adapter,
        )
        dataset_tables = dict.fromkeys(metric_datasets, bucketed_table)
        metric_expr = _metric_expr(
            catalog, resolver, metric_ir.semantic_id, metric_datasets, dataset_tables
        )
        grouped_expr = (
            bucketed_table.group_by("bucket_start")
            .aggregate(value=metric_expr)
            .order_by("bucket_start")
            .select("bucket_start", "value")
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        if "bucket_start" in result.df:
            result.df["bucket_start"] = ensure_bucket_start_timestamp(
                result.df["bucket_start"],
                time_meta=time_dimension_ir.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=result.backend_datetime_decode_policy,
            )
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_dimension": time_dimension_ir.name,
            }
        }
        semantic_kind = "time_series"
    elif resolved_dimensions:
        table = plan.table
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: _validate_field_expr(
                resolver.dimension_on(_field_details(catalog, field_ir.semantic_id).ref, table),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        table = table.mutate(**dimension_exprs)
        dataset_tables = dict.fromkeys(metric_datasets, table)
        metric_expr = _metric_expr(
            catalog, resolver, metric_ir.semantic_id, metric_datasets, dataset_tables
        )
        grouped_expr = (
            table.group_by(dimension_names)
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
            field_ir.name: {"role": "dimension", "column": field_ir.name}
            for _, field_ir in resolved_dimensions
        }
        semantic_kind = "segmented"
    else:
        metric_expr = _metric_expr(
            catalog, resolver, metric_ir.semantic_id, metric_datasets, dataset_tables
        )
        grouped_expr = plan.table.aggregate(value=metric_expr)
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._connection_runtime,
            session_id=session.id,
        )
    return result, axes, semantic_kind, None


def _execute_folded_component(
    cp: ComponentPlan,
    component_name: str,
    component_datasets: tuple[str, ...],
    dim_columns: list[str],
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[Any, Any | None]:
    """Execute a single folded component through the two-phase sampled path.

    Returns (component_df, coverage_df_or_None).
    """
    component_metric_ir = cp.component_metric_ir
    assert component_metric_ir.status_time_dimension is not None
    time_dimension_ir = _resolve_fold_time_field(catalog, component_metric_ir.status_time_dimension)
    root_adapter = _build_entity_adapter(
        catalog,
        resolver,
        _entity_details(catalog, cp.base_plan.root_entity),
    )
    root_time_adapter = root_adapter.fields.get(time_dimension_ir.name)
    if root_time_adapter is None:
        for _fname, fadapter in root_adapter.fields.items():
            if (
                fadapter.is_time
                and fadapter.semantic_id == component_metric_ir.status_time_dimension
            ):
                root_time_adapter = fadapter
                break
    if root_time_adapter is None:
        raise MetricNotFoundError(
            message=f"time field adapter for '{component_metric_ir.status_time_dimension}' not found",
            details={"status_time_dimension": component_metric_ir.status_time_dimension},
        )
    sample_interval = root_time_adapter.sample_interval
    assert sample_interval is not None
    assert root_time_adapter.time_meta is not None
    ensure_sampled_grain_supported(
        requested_grain=resolved_window.grain if resolved_window is not None else None,
        time_meta=root_time_adapter.time_meta,
        sample_interval=sample_interval,
    )
    sample_grain = Grain(count=sample_interval.count, unit=sample_interval.unit)
    read_tz = datasource_read_timezone(session._connection_runtime, cp.base_plan.datasource_name)
    profile = datasource_engine_profile(session._connection_runtime, cp.base_plan.datasource_name)
    table = sample_point_table(
        cp.base_plan.table,
        time_field_ir=root_time_adapter,
        sample_grain=sample_grain,
        report_tz=cast("ZoneInfo", session.report_tz),
        datasource_read_tz=read_tz,
        window=resolved_window,
        dataset_ir=root_adapter,
        profile=profile,
    )
    dimension_names = [dimension.column for dimension in cp.base_plan.dimensions]
    dataset_tables = dict.fromkeys(component_datasets, table)
    metric_expr = _metric_expr(
        catalog,
        resolver,
        component_metric_ir.semantic_id,
        component_datasets,
        dataset_tables,
    )
    phase_a = table.group_by(["sample_point", *dimension_names]).aggregate(value=metric_expr)
    is_time_series = resolved_window is not None and resolved_window.grain is not None
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        phase_b_source = phase_a.mutate(
            bucket_start=bucket_start_expr(phase_a.sample_point, resolved_window.grain)
        )
        group_names = ["bucket_start", *dimension_names]
    else:
        phase_b_source = phase_a
        group_names = list(dimension_names)
    folded_value = compile_fold(
        phase_b_source.value, phase_b_source.sample_point, component_metric_ir.time_fold
    )
    if group_names:
        grouped_expr = (
            phase_b_source.group_by(group_names)
            .aggregate(**{component_name: folded_value})
            .order_by(group_names)
            .select(*group_names, component_name)
        )
    else:
        grouped_expr = phase_b_source.aggregate(**{component_name: folded_value}).select(
            component_name
        )
    result = execute(
        grouped_expr,
        datasource_name=cp.base_plan.datasource_name,
        cache=session._connection_runtime,
        session_id=session.id,
    )
    df = result.df
    # Normalize bucket_start
    if "bucket_start" in df and resolved_window is not None:
        assert resolved_window.grain is not None
        df["bucket_start"] = ensure_bucket_start_timestamp(
            df["bucket_start"],
            time_meta=root_time_adapter.time_meta,
            dataset_ir=root_adapter,
            grain=resolved_window.grain,
            report_tz=cast("ZoneInfo", session.report_tz),
            backend_datetime_decode_policy=result.backend_datetime_decode_policy,
        )
    # Coverage sidecar
    coverage_df: Any | None = None
    if is_time_series and resolved_window is not None:
        assert resolved_window.grain is not None
        coverage_expr = phase_b_source.group_by(group_names).aggregate(
            actual_samples=phase_b_source.sample_point.nunique()
        )
        coverage_result = execute(
            coverage_expr,
            datasource_name=cp.base_plan.datasource_name,
            cache=session._connection_runtime,
            session_id=session.id,
        )
        coverage_df = coverage_result.df
        if "bucket_start" in coverage_df.columns:
            coverage_df["bucket_start"] = ensure_bucket_start_timestamp(
                coverage_df["bucket_start"],
                time_meta=root_time_adapter.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                report_tz=cast("ZoneInfo", session.report_tz),
                backend_datetime_decode_policy=coverage_result.backend_datetime_decode_policy,
            )
        bucket_seconds = _fixed_grain_seconds_for_coverage(
            resolved_window.grain.count, resolved_window.grain.unit
        )
        interval_seconds = _fixed_grain_seconds_for_coverage(
            sample_interval.count, sample_interval.unit
        )
        expected = bucket_seconds // interval_seconds if interval_seconds > 0 else 0
        coverage_df["expected_samples"] = expected
        coverage_df["coverage_ratio"] = coverage_df["actual_samples"] / expected
        coverage_df["coverage_status"] = coverage_df["coverage_ratio"].apply(
            lambda r: "complete" if r == 1.0 else "partial"
        )
    return df, coverage_df


def _merge_component_coverages(
    component_coverages: list[Any],
    merge_keys: list[str],
) -> Any:
    """Merge coverage DataFrames from folded components.

    Uses min(actual_samples), max(expected_samples), min(coverage_ratio)
    across components per bucket.
    """
    pandas = __import__("pandas")
    if not component_coverages:
        return None
    if len(component_coverages) == 1:
        return component_coverages[0]
    merged = component_coverages[0]
    for cov_df in component_coverages[1:]:
        if merge_keys:
            merged = pandas.merge(merged, cov_df, on=merge_keys, how="outer", suffixes=("", "_r"))
            # Take min of actual_samples, max of expected_samples, min of coverage_ratio
            actual_cols = [
                c for c in merged.columns if c == "actual_samples" or c == "actual_samples_r"
            ]
            expected_cols = [
                c for c in merged.columns if c == "expected_samples" or c == "expected_samples_r"
            ]
            ratio_cols = [
                c for c in merged.columns if c == "coverage_ratio" or c == "coverage_ratio_r"
            ]
            status_cols = [
                c for c in merged.columns if c == "coverage_status" or c == "coverage_status_r"
            ]

            merged["actual_samples"] = merged[actual_cols].min(axis=1)
            merged["expected_samples"] = merged[expected_cols].max(axis=1)
            merged["coverage_ratio"] = merged[ratio_cols].min(axis=1)
            merged["coverage_status"] = (
                merged["coverage_ratio"].eq(1.0).map({True: "complete", False: "partial"})
            )
            # Drop merged-in suffix columns
            drop_cols = [
                c
                for c in actual_cols + expected_cols + ratio_cols + status_cols
                if c != "actual_samples"
                and c != "expected_samples"
                and c != "coverage_ratio"
                and c != "coverage_status"
            ]
            merged = merged.drop(columns=drop_cols)
        else:
            # No merge keys — scalar coverage, combine as single row
            merged = pandas.DataFrame(
                {
                    "actual_samples": [
                        min(merged["actual_samples"].iloc[0], cov_df["actual_samples"].iloc[0])
                    ],
                    "expected_samples": [
                        max(merged["expected_samples"].iloc[0], cov_df["expected_samples"].iloc[0])
                    ],
                    "coverage_ratio": [
                        min(merged["coverage_ratio"].iloc[0], cov_df["coverage_ratio"].iloc[0])
                    ],
                    "coverage_status": [
                        "complete"
                        if min(merged["coverage_ratio"].iloc[0], cov_df["coverage_ratio"].iloc[0])
                        == 1.0
                        else "partial"
                    ],
                }
            )
    return merged


def _execute_derived(
    plan: DerivedObservePlan,
    metric_ir: Any,
    *,
    catalog: Any,
    resolver: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[
    Any,
    Any | None,
    dict[str, Any],
    Literal["scalar", "time_series", "segmented", "panel"],
    Any | None,
]:
    """Execute a DerivedObservePlan.

    Returns (result, component_df, axes, semantic_kind, derived_coverage_df_or_None).
    """
    pandas = __import__("pandas")
    metric_name = metric_ir.name
    component_frames: list[Any] = []
    component_coverages: list[Any] = []
    dim_columns = list(
        dict.fromkeys(d.column for cp in plan.component_plans for d in cp.base_plan.dimensions)
    )
    has_time = any(
        cp.base_plan.axes_metadata.get("time") is not None for cp in plan.component_plans
    )
    merge_keys = (["bucket_start"] if has_time else []) + dim_columns

    for cp in plan.component_plans:
        component_name = _role_to_column_name(metric_ir, cp.role)
        component_datasets = tuple(cp.component_metric_ir.entities)
        component_time_fold = getattr(cp.component_metric_ir, "time_fold", None)

        if isinstance(cp.base_plan, CumulativeObservePlan):
            # Cumulative component: execute via _execute_cumulative and
            # rename the "value" column to the component name so the
            # result is shape-compatible with other component DataFrames.
            cum_result, _cum_axes, _cum_kind = _execute_cumulative(
                cp.base_plan,
                catalog=catalog,
                resolver=resolver,
                session=session,
                resolved_window=resolved_window,
            )
            df = cum_result.df.rename(columns={"value": component_name})
            component_frames.append(df)
            continue

        if component_time_fold is not None and has_time:
            # Folded component: execute through two-phase sampled path
            df, coverage_df = _execute_folded_component(
                cp=cp,
                component_name=component_name,
                component_datasets=component_datasets,
                dim_columns=dim_columns,
                catalog=catalog,
                resolver=resolver,
                session=session,
                resolved_window=resolved_window,
            )
            if coverage_df is not None:
                component_coverages.append(coverage_df)
        else:
            # Standard (non-folded) component path
            table = cp.base_plan.table
            if has_time:
                assert (
                    resolved_window is not None
                )  # narrowing: has_time implies resolved_window is set
                root_adapter = _build_entity_adapter(
                    catalog,
                    resolver,
                    _entity_details(catalog, cp.base_plan.root_entity),
                )
                time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
                if resolved_window.grain is not None:
                    base = (
                        time_dimension_ir.time_meta.granularity
                        if time_dimension_ir.time_meta
                        else None
                    ) or "day"
                    ensure_grain_supported(resolved_window.grain, base)
                read_tz = datasource_read_timezone(
                    session._connection_runtime, cp.base_plan.datasource_name
                )
                profile = datasource_engine_profile(
                    session._connection_runtime, cp.base_plan.datasource_name
                )
                table = apply_time_series_bucket(
                    table,
                    field_ir=time_dimension_ir,
                    window=resolved_window,
                    report_tz=cast("ZoneInfo", session.report_tz),
                    datasource_read_tz=read_tz,
                    profile=profile,
                    dataset_ir=root_adapter,
                )
                group_names = ["bucket_start", *dim_columns]
            else:
                group_names = list(dim_columns)
            # The planner already widened all component datasets into a single table,
            # so map every component dataset to the same resolver input table.
            component_dataset_tables = dict.fromkeys(component_datasets, table)
            metric_expr = _metric_expr(
                catalog,
                resolver,
                cp.component_metric_ir.semantic_id,
                component_datasets,
                component_dataset_tables,
            )
            if group_names:
                grouped_expr = (
                    table.group_by(group_names)
                    .aggregate(**{component_name: metric_expr})
                    .order_by(group_names)
                    .select(*group_names, component_name)
                )
            else:
                grouped_expr = table.aggregate(**{component_name: metric_expr}).select(
                    component_name
                )
            df_result = execute(
                grouped_expr,
                datasource_name=cp.base_plan.datasource_name,
                cache=session._connection_runtime,
                session_id=session.id,
            )
            df = df_result.df
            if has_time and "bucket_start" in df:
                df["bucket_start"] = ensure_bucket_start_timestamp(
                    df["bucket_start"],
                    time_meta=time_dimension_ir.time_meta,
                    dataset_ir=root_adapter,
                    grain=resolved_window.grain if resolved_window else None,
                    report_tz=cast("ZoneInfo", session.report_tz),
                    backend_datetime_decode_policy=df_result.backend_datetime_decode_policy,
                )
        component_frames.append(df)

    if not component_frames:
        merged = pandas.DataFrame(columns=[*merge_keys, "value"])
    else:
        merged = component_frames[0]
        for frame in component_frames[1:]:
            if merge_keys:
                merged = pandas.merge(merged, frame, on=merge_keys, how="outer")
            else:
                merged = pandas.concat([merged, frame], axis=1)
    merged["value"] = _evaluate_composition_on_frame(metric_ir, merged)
    if merge_keys:
        result_df = merged[[*merge_keys, "value"]].sort_values(merge_keys).reset_index(drop=True)
    else:
        result_df = merged[["value"]]

    component_df: Any | None = None
    if _is_component_aware_composition(metric_ir):
        # ComponentFrame keeps the metric name as its value column so that the
        # melt in _add_fold_metadata_to_component_df (which renames role
        # columns to "value") does not collide with the metric value column.
        component_df = _component_frame_df(
            raw_df=merged.rename(columns={"value": metric_name}),
            metric_ir=metric_ir,
            axes_columns=merge_keys,
            metric_value_column=metric_name,
        )
        if merge_keys:
            component_df = component_df.sort_values(merge_keys).reset_index(drop=True)
        # Add fold metadata columns for folded components (long format rows)
        _any_component_folded = any(
            getattr(cp.component_metric_ir, "time_fold", None) is not None
            for cp in plan.component_plans
        )
        if _any_component_folded:
            component_df = _add_fold_metadata_to_component_df(
                component_df,
                metric_ir,
                plan.component_plans,
                merge_keys,
                metric_name,
            )

    # --- Merge coverage from folded components ---
    derived_coverage_df: Any | None = None
    if component_coverages:
        derived_coverage_df = _merge_component_coverages(component_coverages, merge_keys)

    if has_time and dim_columns:
        semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "panel"
    elif has_time:
        semantic_kind = "time_series"
    elif dim_columns:
        semantic_kind = "segmented"
    else:
        semantic_kind = "scalar"

    axes: dict[str, Any] = {}
    if has_time and plan.component_plans and resolved_window is not None:
        # Resolve time dimension for axes metadata.
        # Prefer the status_time_dimension from any folded component, otherwise
        # fall back to the standard time dimension resolution.
        fold_time_dim_name: str | None = None
        for cp in plan.component_plans:
            cp_fold = getattr(cp.component_metric_ir, "status_time_dimension", None)
            if cp_fold is not None:
                fold_time_dim_name = cp_fold
                break
        if fold_time_dim_name is not None:
            axes["time"] = {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_dimension": fold_time_dim_name.rsplit(".", 1)[-1]
                if "." in fold_time_dim_name
                else fold_time_dim_name,
            }
        else:
            first_cp = plan.component_plans[0]
            root_adapter = _build_entity_adapter(
                catalog,
                resolver,
                _entity_details(catalog, first_cp.base_plan.root_entity),
            )
            time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
            axes["time"] = {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_dimension": time_dimension_ir.name,
            }
    for col in dim_columns:
        axes[col] = {"role": "dimension", "column": col}

    return _Result(result_df), component_df, axes, semantic_kind, derived_coverage_df


def _dump_dimensions(dimensions: list[str] | None) -> list[dict[str, Any]] | None:
    if dimensions is None:
        return None
    return [{"semantic_id": dimension} for dimension in dimensions]


def _backend_for_datasource(session: Session, datasource_name: str) -> tuple[str, Any]:
    return datasource_name, session._connection_runtime.get_or_create(datasource_name)


def _build_fold_meta(metric_ir: Any, catalog: Any) -> dict[str, Any]:
    """Build fold metadata dict for a folded metric's MetricFrameMeta."""
    sample_interval_token_val: str | None = None
    if metric_ir.status_time_dimension is not None:
        tf = _resolve_fold_time_field(catalog, metric_ir.status_time_dimension)
        si = getattr(tf, "sample_interval", None)
        if si is not None:
            sample_interval_token_val = sample_interval_token(si)
    return {
        "time_fold": metric_ir.time_fold.label(),
        "status_time_dimension": metric_ir.status_time_dimension,
        "sample_interval": sample_interval_token_val,
    }


def _build_derived_fold_meta(derived_plan: DerivedObservePlan, catalog: Any) -> dict[str, Any]:
    """Build fold metadata dict for a derived metric with folded components."""
    component_folds: list[dict[str, Any]] = []
    sample_interval_token_val: str | None = None
    for cp in derived_plan.component_plans:
        cp_ir = cp.component_metric_ir
        if getattr(cp_ir, "time_fold", None) is None:
            continue
        fold_entry: dict[str, Any] = {
            "component_metric_id": cp_ir.semantic_id,
            "time_fold": cp_ir.time_fold.label(),
            "status_time_dimension": cp_ir.status_time_dimension,
        }
        component_folds.append(fold_entry)
        # Capture sample_interval from the first folded component
        if sample_interval_token_val is None and cp_ir.status_time_dimension is not None:
            tf = _resolve_fold_time_field(catalog, cp_ir.status_time_dimension)
            si = getattr(tf, "sample_interval", None)
            if si is not None:
                sample_interval_token_val = sample_interval_token(si)
    return {
        "time_fold": "derived",
        "component_folds": component_folds,
        "sample_interval": sample_interval_token_val,
    }


def _cumulative_marker_for_plan(plan: CumulativeObservePlan, catalog: Any) -> dict[str, Any]:
    """Build per-component cumulative marker for a CumulativeObservePlan.

    Resolves the real ``over`` from the catalog registry, mirroring the
    direct cumulative path.  ``plan.over`` and
    ``plan.base_metric_ir.composition.over`` are both ``None`` for
    per-component plans because the metric IR is a ``_MetricDetailsAdapter``
    whose ``composition.over`` defaults to ``None``.
    """
    over = plan.over
    if over is None and catalog._reg is not None:
        real_ir = catalog._reg.metrics.get(plan.metric_ir.semantic_id)
        if real_ir is not None and real_ir.composition is not None:
            over = getattr(real_ir.composition, "over", None)
    return {
        "kind": "cumulative",
        "base": plan.base_metric_ir.semantic_id,
        "over": over,
        "anchor": "all_history",
        "components": None,
    }


def _derived_cumulative_marker(plan: DerivedObservePlan, catalog: Any) -> dict[str, Any] | None:
    """Return derived-contains-cumulative metadata if any component is cumulative."""
    components: dict[str, dict[str, Any]] = {
        cp.role: _cumulative_marker_for_plan(cp.base_plan, catalog)
        for cp in plan.component_plans
        if isinstance(cp.base_plan, CumulativeObservePlan)
    }
    if not components:
        return None
    return {"kind": "derived_contains_cumulative", "components": components}


def _metric_expr(
    catalog: Any,
    resolver: Any,
    metric_id: str,
    metric_datasets: tuple[str, ...],
    dataset_tables: dict[str, Any],
) -> Any:
    return resolver.metric_on(
        _catalog_object(catalog, metric_id, SemanticKind.METRIC).ref,
        *(dataset_tables[dataset_name] for dataset_name in metric_datasets),
    )


def _entity_adapter_maps(
    *,
    catalog: Any,
    resolver: Any,
    entity_refs: set[str],
) -> tuple[dict[str, EntityDetails], dict[str, Any], dict[str, Any], dict[str, Any]]:
    entity_details = {
        entity_ref: _entity_details(catalog, entity_ref) for entity_ref in entity_refs
    }
    dataset_irs = {
        entity_ref: _build_entity_adapter(catalog, resolver, entity)
        for entity_ref, entity in entity_details.items()
    }
    dataset_fns = {entity_ref: adapter.fn for entity_ref, adapter in dataset_irs.items()}
    return entity_details, {}, dataset_irs, dataset_fns


def _normalize_metric_boundary(catalog: Any, metric: MetricInput) -> str:
    return normalize_metric_input(catalog, metric)


def _normalize_dimension_boundary(
    catalog: Any,
    dimension: DimensionInput,
    *,
    argument: str,
    scoped_entity_refs: set[str] | None = None,
) -> str:
    return normalize_dimension_input(catalog, dimension, argument=argument)


def _normalize_dimension_list_boundary(
    catalog: Any,
    dimensions: list[DimensionInput] | None,
    *,
    scoped_entity_refs: set[str],
) -> list[str] | None:
    if dimensions is None:
        return None
    return [
        _normalize_dimension_boundary(
            catalog,
            dimension,
            argument="dimensions",
            scoped_entity_refs=scoped_entity_refs,
        )
        for dimension in dimensions
    ]


def _normalize_where_boundary(
    catalog: Any,
    where: dict[DimensionInput, SliceValue] | None,
    *,
    scoped_entity_refs: set[str],
) -> dict[str, SliceValue]:
    if where is None:
        return {}
    return {
        _normalize_dimension_boundary(
            catalog,
            key,
            argument="slice_by",
            scoped_entity_refs=scoped_entity_refs,
        ): value
        for key, value in where.items()
    }


def _metric_planner_scope(catalog: Any, metric_ir: Any) -> set[str]:
    scoped = set(metric_ir.entities)
    root = getattr(metric_ir, "root_entity", None)
    if isinstance(root, str) and root:
        scoped.add(root)
    if metric_ir.metric_type == "derived":
        for component_id in metric_ir.composition.components.values():
            component_details = _catalog_object(
                catalog, component_id, SemanticKind.METRIC
            ).details()
            if isinstance(component_details, (SimpleMetricDetails, DerivedMetricDetails)):
                component_ir = _planned_metric(component_details)
                scoped.update(component_ir.entities)
                component_root = getattr(component_ir, "root_entity", None)
                if isinstance(component_root, str) and component_root:
                    scoped.add(component_root)
    return scoped


def observe(
    metric: MetricInput | list[MetricInput] | tuple[MetricInput, ...],
    *,
    time_scope: TimeScopeInput = None,
    grain: GrainInput = None,
    dimensions: list[DimensionInput] | None = None,
    slice_by: dict[DimensionInput, SliceValue] | None = None,
    time_dimension: DimensionInput | None = None,
    expect_shape: SemanticShape | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> MetricFrame:
    if isinstance(metric, (list, tuple)):
        metric_items: list[MetricInput] = list(metric)
        if not metric_items:
            raise SemanticKindMismatchError(
                message="observe requires at least one metric",
                details={"argument": "metric", "got": "empty sequence"},
            )
        if len(metric_items) > 1:
            from marivo.analysis.intents.observe_multi import observe_multi

            return observe_multi(
                metric_items,
                time_scope=time_scope,
                grain=grain,
                dimensions=dimensions,
                slice_by=slice_by,
                time_dimension=time_dimension,
                expect_shape=expect_shape,
                analysis_purpose=analysis_purpose,
                session=session,
            )
        single_metric: MetricInput = metric_items[0]
    else:
        single_metric = metric
    if session is None:
        session = require_current_session()
    ensure_session_writable(session)
    catalog = session.catalog
    catalog._require_ready()
    metric_id = _normalize_metric_boundary(catalog, single_metric)
    model_name, metric_name = metric_id.split(".", 1)
    metric_details = _catalog_object(catalog, metric_id, SemanticKind.METRIC).details()
    assert isinstance(metric_details, (SimpleMetricDetails, DerivedMetricDetails))
    metric_ir = _planned_metric(metric_details)
    planner_scope = _metric_planner_scope(catalog, metric_ir)
    time_dimension_id = (
        _normalize_dimension_boundary(
            catalog,
            time_dimension,
            argument="time_dimension",
            scoped_entity_refs=planner_scope,
        )
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
    primary_datasource: str | None = None
    stored_where = normalize_slice_for_storage(where_by_id)
    metric_datasets = tuple(metric_ir.entities)
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
                details={
                    "intent": "observe",
                    "predicted_semantic_shape": predicted_shape,
                    "expect_shape": expect_shape,
                },
            )
    if metric_ir.metric_type == "derived":
        # Build adapters for all catalog entities so derived components can plan
        # across any entity they reference.
        all_entity_refs = {
            obj.ref.id
            for domain in catalog.list("domain")
            for obj in catalog.list("entity", scope=f"domain.{domain.ref.id}")
        }
        _, _, all_dataset_irs, all_dataset_fns = _entity_adapter_maps(
            catalog=catalog,
            resolver=resolver,
            entity_refs=all_entity_refs,
        )
        component_metric_irs = {
            component_id: _planned_metric(component_details)
            for component_id in metric_ir.composition.components.values()
            if isinstance(
                component_details := _catalog_object(
                    catalog, component_id, SemanticKind.METRIC
                ).details(),
                (SimpleMetricDetails, DerivedMetricDetails),
            )
        }

        session._connection_runtime.begin_query_capture()
        try:
            derived_plan = plan_observe(
                catalog=catalog,
                session=session,
                metric_ir=metric_ir,
                dataset_irs=all_dataset_irs,
                dataset_fns=all_dataset_fns,
                dimensions=dimension_refs,
                where=where_by_id,
                resolved_window=resolved_window,
                time_dimension=planner_time_dimension_id,
                component_metric_irs=component_metric_irs,
            )
            # plan_observe returns DerivedObservePlan for ratio/weighted/linear
            # derived metrics, or CumulativeObservePlan for cumulative metrics.
            assert isinstance(derived_plan, (DerivedObservePlan, CumulativeObservePlan))
            if isinstance(derived_plan, CumulativeObservePlan):
                # --- Cumulative observe execution ---
                # Resolve the real 'over' from the MetricIR in the registry,
                # since _MetricDetailsAdapter.composition.over defaults to None.
                cum_over = derived_plan.over
                if cum_over is None and catalog._reg is not None:
                    real_ir = catalog._reg.metrics.get(metric_id)
                    if real_ir is not None and real_ir.composition is not None:
                        cum_over = getattr(real_ir.composition, "over", None)
                cumulative_meta = {
                    "kind": "cumulative",
                    "base": derived_plan.base_metric_ir.semantic_id,
                    "over": cum_over,
                    "anchor": "all_history",
                    "components": None,
                }
                params_timescope_cum = None
                if resolved_window is not None:
                    params_timescope_cum = {
                        "original": original_timescope,
                        "resolved": dump_window(resolved_window),
                        "report_tz": session.report_tz_name,
                    }
                params = {
                    "metric": metric_id,
                    "timescope": params_timescope_cum,
                    "dimensions": _dump_dimensions(dimension_refs),
                    "where": stored_where,
                    "version_resolutions": derived_plan.base_plan.lineage_metadata.get(
                        "version_resolutions", []
                    ),
                    "warnings": derived_plan.warnings,
                    "lineage_metadata": derived_plan.base_plan.lineage_metadata,
                    "cumulative": {
                        "base": derived_plan.base_metric_ir.semantic_id,
                        "over": cum_over,
                        "anchor": "all_history",
                        "spine_synthesized": bool(resolved_window and resolved_window.grain),
                        "query_strategy": (
                            "baseline_plus_flow"
                            if resolved_window and resolved_window.grain
                            else "as_of_end"
                        ),
                    },
                }
                prospective_id = compute_prospective_artifact_id(
                    step_type="observe",
                    inputs=CommitInputs(input_refs=[]),
                    params=CommitParams(values=params),
                    semantic_anchors=CommitSemanticAnchors(
                        values={"metric_id": metric_id, "model": model_name}
                    ),
                )
                if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
                    session._connection_runtime.take_captured_queries()
                    return cast("MetricFrame", load_frame(prospective_id, session=session))

                cum_result, cum_axes, cum_kind = _execute_cumulative(
                    derived_plan,
                    catalog=catalog,
                    resolver=resolver,
                    session=session,
                    resolved_window=resolved_window,
                )
            else:
                # Build params and check cache before executing the backend query.
                params_timescope = None
                if resolved_window is not None:
                    params_timescope = {
                        "original": original_timescope,
                        "resolved": dump_window(resolved_window),
                        "report_tz": session.report_tz_name,
                    }
                params = {
                    "metric": metric_id,
                    "timescope": params_timescope,
                    "dimensions": _dump_dimensions(dimension_refs),
                    "where": stored_where,
                    "version_resolutions": [
                        vr
                        for cp in derived_plan.component_plans
                        for vr in cp.base_plan.lineage_metadata.get("version_resolutions", [])
                    ],
                    "warnings": derived_plan.warnings,
                    "lineage_metadata": derived_plan.lineage_metadata,
                }
                prospective_id = compute_prospective_artifact_id(
                    step_type="observe",
                    inputs=CommitInputs(input_refs=[]),
                    params=CommitParams(values=params),
                    semantic_anchors=CommitSemanticAnchors(
                        values={"metric_id": metric_id, "model": model_name}
                    ),
                )
                if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
                    session._connection_runtime.take_captured_queries()
                    return cast("MetricFrame", load_frame(prospective_id, session=session))

                result, component_df, derived_axes, derived_kind, derived_coverage_df = (
                    _execute_derived(
                        derived_plan,
                        metric_ir,
                        catalog=catalog,
                        resolver=resolver,
                        session=session,
                        resolved_window=resolved_window,
                    )
                )
        except BaseException:
            session._connection_runtime.take_captured_queries()
            raise
        _captured_queries = session._connection_runtime.take_captured_queries()
        finished_at = datetime.now(UTC)
        frame_ref = _gen_ref("frame")
        job_ref = _gen_ref("job")
        if isinstance(derived_plan, CumulativeObservePlan):
            meta = MetricFrameMeta(
                kind="metric_frame",
                ref=frame_ref,
                session_id=session.id,
                project_root=str(session.project_root),
                produced_by_job=job_ref,
                analysis_purpose=analysis_purpose,
                created_at=finished_at,
                row_count=cum_result.row_count,
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
                axes=cum_axes,
                measure={"name": metric_name},
                window=dump_window(resolved_window),
                where=stored_where,
                semantic_kind=cum_kind,
                semantic_model=model_name,
                unit=metric_ir.unit,
                reaggregatable=False,
                additivity="non_additive",
                cumulative=cumulative_meta,
            )
            frame = MetricFrame(_df=cum_result.df, meta=meta)
            _grain_token = (
                resolved_window.grain.to_token()
                if resolved_window is not None and resolved_window.grain is not None
                else None
            )
            frame = _commit_observe_metric_frame(
                session=session,
                frame=frame,
                params=params,
                metric_id=metric_id,
                model_name=model_name,
                stored_where=stored_where,
                semantic_kind=cum_kind,
                subject_grain=_grain_token,
            )
        else:
            # Determine fold metadata for derived metrics with folded components
            _any_folded = any(
                getattr(cp.component_metric_ir, "time_fold", None) is not None
                for cp in derived_plan.component_plans
            )
            _derived_fold: dict[str, Any] | None = None
            if _any_folded:
                _derived_fold = _build_derived_fold_meta(derived_plan, catalog)
            _derived_cumulative = _derived_cumulative_marker(derived_plan, catalog)
            meta = MetricFrameMeta(
                kind="metric_frame",
                ref=frame_ref,
                session_id=session.id,
                project_root=str(session.project_root),
                produced_by_job=job_ref,
                analysis_purpose=analysis_purpose,
                created_at=finished_at,
                row_count=result.row_count,
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
                axes=derived_axes,
                measure={"name": metric_name},
                window=dump_window(resolved_window),
                where=stored_where,
                semantic_kind=derived_kind,
                semantic_model=model_name,
                unit=metric_ir.unit,
                fold=_derived_fold,
                reaggregatable=not _any_folded and _derived_cumulative is None,
                additivity=_meta_additivity(metric_ir.additivity),
                cumulative=_derived_cumulative,
            )
            frame = MetricFrame(_df=result.df, meta=meta)
            frame = _commit_observe_metric_frame(
                session=session,
                frame=frame,
                params=params,
                metric_id=metric_id,
                model_name=model_name,
                stored_where=stored_where,
                semantic_kind=derived_kind,
            )
            if component_df is not None:
                component = _persist_metric_component_frame(
                    session=session,
                    df=component_df,
                    parent=frame,
                    metric_ir=metric_ir,
                    axes=derived_axes,
                    semantic_kind=derived_kind,
                    job_ref=job_ref,
                )
                frame = _attach_metric_component_ref(
                    session=session,
                    parent=frame,
                    component=component,
                    metric_ir=metric_ir,
                )
            # --- Persist coverage sidecar for derived metrics with folded components ---
            if derived_coverage_df is not None:
                frame = _persist_and_attach_coverage_sidecar(
                    session=session,
                    df=derived_coverage_df,
                    parent=frame,
                    job_ref=job_ref,
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
                "queries": [
                    {**qe.to_dict(), "output_ref": _output_ref} for qe in _captured_queries
                ],
            },
        )
        return frame

    # --- Base (non-derived) metric path: route through planner ---
    required_entity_refs = set(metric_datasets)
    for field_id in [*(dimension_refs or []), *where_by_id]:
        if "." in field_id:
            required_entity_refs.add(_field_details(catalog, field_id).entity.id)
    _entity_details_by_id, _dataset_tables, dataset_irs, dataset_fns = _entity_adapter_maps(
        catalog=catalog,
        resolver=resolver,
        entity_refs=required_entity_refs,
    )

    session._connection_runtime.begin_query_capture()
    try:
        plan = plan_base_observe(
            catalog=catalog,
            session=session,
            metric_ir=metric_ir,
            dataset_irs=dataset_irs,
            dataset_fns=dataset_fns,
            dimensions=dimension_refs,
            where=where_by_id,
            resolved_window=resolved_window,
            time_dimension=planner_time_dimension_id,
        )
        primary_datasource = plan.datasource_name

        if primary_datasource is None:
            raise MetricNotFoundError(message=f"metric '{metric_id}' references no datasets")

        # Build params and check cache before executing the backend query.
        params_timescope = None
        if resolved_window is not None:
            params_timescope = {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "report_tz": session.report_tz_name,
            }
        params = {
            "metric": metric_id,
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimension_refs),
            "where": stored_where,
            "relationships": plan.lineage_metadata.get("relationships") or [],
            "version_resolutions": plan.lineage_metadata.get("version_resolutions") or [],
            "fanout_policy": plan.lineage_metadata.get("fanout_policy"),
            "fanouts": plan.lineage_metadata.get("fanouts") or [],
            "warnings": plan.warnings,
        }
        prospective_id = compute_prospective_artifact_id(
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(
                values={"metric_id": metric_id, "model": model_name}
            ),
        )
        if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
            session._connection_runtime.take_captured_queries()
            return cast("MetricFrame", load_frame(prospective_id, session=session))

        result, axes, semantic_kind, coverage_df = _execute_base(
            plan,
            metric_ir,
            catalog=catalog,
            resolver=resolver,
            session=session,
            dimensions=dimension_refs,
            resolved_window=resolved_window,
        )
    except BaseException:
        session._connection_runtime.take_captured_queries()
        raise
    _captured_queries = session._connection_runtime.take_captured_queries()
    finished_at = datetime.now(UTC)

    # Resolve quantile capability for percentile-folded metrics
    _capability = None
    _time_fold = getattr(metric_ir, "time_fold", None)
    if _time_fold is not None and _time_fold.kind == "percentile":
        if primary_datasource is None:
            raise AnalysisError(
                message="percentile sampled fold requires a primary datasource to resolve backend type.",
                details={"metric": metric_ir.semantic_id},
            )
        _profile = datasource_engine_profile(session._connection_runtime, primary_datasource)
        _capability = quantile_capability(_profile)
    quantile_mode = _capability.mode if _capability is not None else None
    quantile_method = _capability.method if _capability is not None else None

    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=result.row_count,
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
        axes=axes,
        measure={"name": metric_name},
        window=dump_window(resolved_window),
        where=stored_where,
        semantic_kind=semantic_kind,
        semantic_model=model_name,
        unit=metric_ir.unit,
        fold=_build_fold_meta(metric_ir, catalog) if metric_ir.time_fold is not None else None,
        reaggregatable=metric_ir.time_fold is None,
        additivity=_meta_additivity(metric_ir.additivity),
        quantile_mode=quantile_mode,
        quantile_method=quantile_method,
    )
    frame = MetricFrame(_df=result.df, meta=meta)

    # --- Evidence pipeline: commit_result replaces write_frame_to_disk ---
    _grain_token = (
        resolved_window.grain.to_token()
        if resolved_window is not None and resolved_window.grain is not None
        else None
    )
    frame = _commit_observe_metric_frame(
        session=session,
        frame=frame,
        params=params,
        metric_id=metric_id,
        model_name=model_name,
        stored_where=stored_where,
        semantic_kind=semantic_kind,
        subject_grain=_grain_token,
    )

    # --- Persist coverage sidecar for sampled metrics ---
    if coverage_df is not None:
        frame = _persist_and_attach_coverage_sidecar(
            session=session,
            df=coverage_df,
            parent=frame,
            job_ref=job_ref,
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
            "queries": [{**qe.to_dict(), "output_ref": _output_ref} for qe in _captured_queries],
        },
    )
    return frame


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
) -> MetricFrame:
    """Commit a MetricFrame through the evidence pipeline (shared tail).

    Shared by observe and derive_metric_frame; both are metric_frame-family
    commits and must emit the same evidence side effects. When ``metric_ids``
    is provided (arity-N multi-metric path), the anchors carry the full metric
    list while the commit subject keeps ``metric=None`` — the extractor reads
    per-measure subjects from ``meta.measures``.
    """
    if metric_ids is not None:
        anchors: dict[str, Any] = {"metrics": metric_ids, "models": models or [model_name]}
    else:
        anchors = {"metric_id": metric_id, "model": model_name}
    result = cast(
        "MetricFrame",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type=step_type,
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(values=anchors),
            subject=Subject(
                metric=metric_id,
                slice=stored_where or {},
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


def _analysis_axis_for_kind(
    semantic_kind: str,
) -> Literal[
    "scalar",
    "time",
    "segment",
    "panel",
    "change",
    "decomposition",
    "correlation",
    "forecast",
    "anomaly",
]:
    """Map semantic_kind to the Subject.analysis_axis literal."""
    mapping: dict[
        str,
        Literal[
            "scalar",
            "time",
            "segment",
            "panel",
            "change",
            "decomposition",
            "correlation",
            "forecast",
            "anomaly",
        ],
    ] = {
        "scalar": "scalar",
        "time_series": "time",
        "segmented": "segment",
        "panel": "panel",
    }
    return mapping.get(semantic_kind, "scalar")
