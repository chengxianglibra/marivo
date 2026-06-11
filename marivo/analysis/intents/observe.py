"""Materialize a semantic metric into a MetricFrame."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

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
from marivo.analysis.executor.runner import (
    apply_time_series_bucket,
    bucket_start_expr,
    ensure_bucket_start_timestamp,
    execute,
    normalize_slice_for_storage,
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
    DerivedObservePlan,
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
from marivo.analysis.refs import DimensionRef, MetricRef
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

# ---------------------------------------------------------------------------
# v1.1 -> runner adapter types
# ---------------------------------------------------------------------------
# The runner.py functions expect old-style IR objects with attributes like
# ``fn``, ``fields``, ``datasource_name``, ``is_time``, ``time_meta``.
# The new v1.1 semantic stores callables in a sidecar map and uses
# different IR dataclass shapes.  These adapter classes bridge the gap
# without modifying runner.py.


class _TimeFieldMetaAdapter:
    """Adapter that mimics the old TimeFieldMeta for runner.py."""

    def __init__(
        self,
        data_type: str,
        granularity: str,
        format: str | None = None,
        required_prefix: str | None = None,
        timezone: str | None = None,
    ) -> None:
        self.data_type = data_type
        self.granularity = granularity
        self.format = format
        self.required_prefix = required_prefix
        self.timezone = timezone


class _DimensionIRAdapter:
    """Adapter that mimics the old DimensionIR for runner.py."""

    def __init__(
        self,
        semantic_id: str,
        name: str,
        dataset_name: str,
        fn: Callable[..., Any],
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
    """Adapter that mimics the old EntityIR for runner.py."""

    def __init__(
        self,
        name: str,
        fn: Callable[..., Any],
        datasource_name: str,
        fields: dict[str, _DimensionIRAdapter],
    ) -> None:
        self.name = name
        self.fn = fn
        self.datasource_name = datasource_name
        self.fields = fields


def _build_dataset_adapter(
    sp: Any,
    dataset_ir: Any,
) -> _EntityIRAdapter:
    """Build a _EntityIRAdapter from a v1.1 EntityIR + sidecar."""
    sidecar = sp._sidecar

    def _source_fn(backend: Any) -> Any:
        source = dataset_ir.source
        if source.kind == "table":
            if source.database is None:
                return backend.table(source.table)
            return backend.table(source.table, database=source.database)
        if source.kind == "file":
            reader_name = "read_parquet" if source.format == "parquet" else "read_csv"
            reader = getattr(backend, reader_name, None)
            if reader is None:
                raise RuntimeError(
                    f"Backend for dataset {dataset_ir.semantic_id!r} does not support "
                    f"{source.format} file sources."
                )
            return reader(source.path, **source.options)
        raise RuntimeError(f"Unsupported source kind for dataset {dataset_ir.semantic_id!r}")

    # Build field adapters for this dataset
    field_adapters: dict[str, _DimensionIRAdapter] = {}
    for field_ir in sp.list_dimensions(entity=dataset_ir.semantic_id):
        field_fn = sidecar.get(field_ir.semantic_id) if sidecar else None
        _captured_field_sid = field_ir.semantic_id

        def _default_field_fn(table: Any, *, _sid: str = _captured_field_sid) -> Any:
            raise RuntimeError(f"No sidecar callable for field {_sid!r}")

        adapter = _DimensionIRAdapter(
            semantic_id=field_ir.semantic_id,
            name=field_ir.name,
            dataset_name=dataset_ir.name,
            fn=field_fn if field_fn is not None else _default_field_fn,
            is_time=False,
        )
        field_adapters[field_ir.name] = adapter

    # Add time fields
    for tf_ir in sp.list_time_dimensions(entity=dataset_ir.semantic_id):
        tf_fn = sidecar.get(tf_ir.semantic_id) if sidecar else None
        _captured_tf_sid = tf_ir.semantic_id

        def _default_tf_fn(table: Any, *, _sid: str = _captured_tf_sid) -> Any:
            raise RuntimeError(f"No sidecar callable for time_dimension {_sid!r}")

        time_meta = _TimeFieldMetaAdapter(
            data_type=tf_ir.data_type or "date",
            granularity=tf_ir.granularity or "day",
            format=tf_ir.format,
            required_prefix=tf_ir.required_prefix,
            timezone=tf_ir.timezone,
        )
        adapter = _DimensionIRAdapter(
            semantic_id=tf_ir.semantic_id,
            name=tf_ir.name,
            dataset_name=dataset_ir.name,
            fn=tf_fn if tf_fn is not None else _default_tf_fn,
            is_time=True,
            is_default=getattr(tf_ir, "is_default", False),
            time_meta=time_meta,
            sample_interval=getattr(tf_ir, "sample_interval", None),
        )
        field_adapters[tf_ir.name] = adapter

    return _EntityIRAdapter(
        name=dataset_ir.name,
        fn=_source_fn,
        datasource_name=dataset_ir.datasource,
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
# Component-aware decomposition helpers
# ---------------------------------------------------------------------------

_COMPONENT_AWARE_DECOMPOSITIONS = {"ratio", "weighted_average"}


def _is_component_aware_decomposition(metric_ir: Any) -> bool:
    decomposition = getattr(metric_ir, "decomposition", None)
    kind = getattr(decomposition, "kind", None)
    return isinstance(kind, str) and kind in _COMPONENT_AWARE_DECOMPOSITIONS


def _decomposition_payload(metric_ir: Any) -> dict[str, Any] | None:
    if not _is_component_aware_decomposition(metric_ir):
        return None
    return {
        "kind": metric_ir.decomposition.kind,
        "components": dict(metric_ir.decomposition.components),
    }


def _role_to_column_name(metric_ir: Any, role: str) -> str:
    return resolve_role_column_name(metric_ir.decomposition.components, role)


def _component_parent_columns(metric_ir: Any) -> list[str]:
    return resolve_role_columns(metric_ir.decomposition.components)


def _component_frame_df(
    *,
    raw_df: Any,
    metric_ir: Any,
    axes_columns: list[str],
    metric_value_column: str,
) -> Any:
    role_columns = _component_parent_columns(metric_ir)
    for role, col in zip(metric_ir.decomposition.components, role_columns, strict=True):
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
    and fold_time_dimension columns.
    """
    pandas = __import__("pandas")
    role_columns = _component_parent_columns(metric_ir)
    # Build a mapping from role column name to component plan metadata
    role_to_meta: dict[str, dict[str, Any]] = {}
    for cp in component_plans:
        role = cp.role
        col_name = resolve_role_column_name(metric_ir.decomposition.components, role)
        role_to_meta[col_name] = {
            "component_metric_id": cp.component_metric_ir.semantic_id.rsplit(".", 1)[-1],
            "time_fold": (
                cp.component_metric_ir.time_fold.label()
                if getattr(cp.component_metric_ir, "time_fold", None)
                else None
            ),
            "fold_time_dimension": getattr(cp.component_metric_ir, "fold_time_dimension", None),
        }
    # Melt the wide-format df into long format
    long_frames: list[Any] = []
    for col_name in role_columns:
        meta = role_to_meta.get(
            col_name,
            {
                "component_metric_id": col_name,
                "time_fold": None,
                "fold_time_dimension": None,
            },
        )
        subset = df[[*merge_keys, col_name, metric_name]].copy()
        subset = subset.rename(columns={col_name: "value"})
        subset["component_metric_id"] = meta["component_metric_id"]
        subset["time_fold"] = meta["time_fold"]
        subset["fold_time_dimension"] = meta["fold_time_dimension"]
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
            decomposition_kind=metric_ir.decomposition.kind,
            components=dict(metric_ir.decomposition.components),
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
            "decomposition": _decomposition_payload(metric_ir),
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
    existing_quality = parent.meta.quality
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
            "quality": updated_quality,
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


def _validate_dimension_refs(dimensions: list[Any] | None) -> list[DimensionRef]:
    if dimensions is None:
        return []
    if len(dimensions) == 0:
        raise SemanticKindMismatchError(
            message=(
                "For time-series observations, omit dimensions or pass None; "
                "segmented observations require at least one DimensionRef."
            ),
            details={
                "expected_kind": "list[DimensionRef] | None",
                "got_kind": "list[]",
            },
        )

    validated: list[DimensionRef] = []
    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for dimension in dimensions:
        if not isinstance(dimension, DimensionRef):
            raise SemanticKindMismatchError(
                message="observe dimensions requires DimensionRef entries",
                details={
                    "expected_kind": "DimensionRef",
                    "got_kind": type(dimension).__name__,
                },
            )
        if dimension.semantic_id in seen:
            duplicate_ids.add(dimension.semantic_id)
        seen.add(dimension.semantic_id)
        validated.append(dimension)
    if duplicate_ids:
        raise SemanticKindMismatchError(
            message="observe dimensions must not contain duplicate DimensionRef ids",
            details={
                "expected_kind": "unique DimensionRef ids",
                "got_kind": "duplicate DimensionRef ids",
                "duplicate_dimensions": sorted(duplicate_ids),
            },
        )
    return validated


def _normalize_time_dimension_ref(time_dimension: DimensionRef | None) -> str | None:
    if time_dimension is None:
        return None
    if not isinstance(time_dimension, DimensionRef):
        raise SemanticKindMismatchError(
            message="observe requires time_dimension=DimensionRef(...)",
            details={
                "expected_kind": "DimensionRef",
                "got_kind": type(time_dimension).__name__,
            },
        )
    return time_dimension.semantic_id


def _normalize_where_refs(
    where: dict[DimensionRef, SliceValue] | None,
) -> dict[str, SliceValue] | None:
    if where is None:
        return None
    normalized: dict[str, SliceValue] = {}
    for key, value in where.items():
        if not isinstance(key, DimensionRef):
            raise SemanticKindMismatchError(
                message="observe where keys must be DimensionRef(...)",
                details={
                    "expected_kind": "DimensionRef",
                    "got_kind": type(key).__name__,
                },
            )
        normalized[key.semantic_id] = value
    return normalized


def _field_fn(sp: Any, field_id: str) -> Callable[..., Any]:
    sidecar = sp._sidecar
    fn = sidecar.get(field_id) if sidecar else None
    if fn is None:
        raise MetricNotFoundError(
            message=f"field callable for '{field_id}' not found",
            details={"field": field_id},
        )
    return cast("Callable[..., Any]", fn)


def _evaluate_decomposition_on_frame(metric_ir: Any, frame: Any) -> Any:
    kind = metric_ir.decomposition.kind
    if kind == "ratio":
        num_col = _role_to_column_name(metric_ir, "numerator")
        den_col = _role_to_column_name(metric_ir, "denominator")
        return frame[num_col] / frame[den_col]
    if kind == "weighted_average":
        num_col = _role_to_column_name(metric_ir, "numerator")
        weight_col = _role_to_column_name(metric_ir, "weight")
        return frame[num_col] / frame[weight_col]
    raise MetricShapeUnsupportedError(
        message=f"unsupported derived metric decomposition kind {kind!r}",
        details={
            "kind": "DerivedMetricDecompositionUnsupported",
            "metric": metric_ir.semantic_id,
            "decomposition_kind": kind,
        },
    )


def _require_component_role_column(
    metric_ir: Any,
    role: str,
    column_name: str,
    frame: Any,
) -> Any:
    component_id = metric_ir.decomposition.components.get(role)
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
    sp: Any,
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
    sidecar = sp._sidecar
    metric_fn = sidecar.get(metric_ir.semantic_id) if sidecar else None
    if metric_fn is None:
        raise MetricNotFoundError(
            message=f"metric callable for '{metric_ir.semantic_id}' not found",
            details={"metric": metric_ir.semantic_id},
        )
    assert metric_ir.fold_time_dimension is not None
    time_dimension_ir = _resolve_fold_time_field(sp, metric_ir.fold_time_dimension)
    root_ds_ir = sp.get_entity(plan.root_entity)
    root_adapter = _build_dataset_adapter(sp, root_ds_ir)
    root_time_adapter = root_adapter.fields.get(
        time_dimension_ir.name if hasattr(time_dimension_ir, "name") else time_dimension_ir
    )
    if root_time_adapter is None:
        # Try by iterating time dimensions
        for _fname, fadapter in root_adapter.fields.items():
            if fadapter.is_time and fadapter.semantic_id == metric_ir.fold_time_dimension:
                root_time_adapter = fadapter
                break
    if root_time_adapter is None:
        raise MetricNotFoundError(
            message=f"time field adapter for '{metric_ir.fold_time_dimension}' not found",
            details={"fold_time_dimension": metric_ir.fold_time_dimension},
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
    table = sample_point_table(
        plan.table,
        time_field_ir=root_time_adapter,
        sample_grain=sample_grain,
        session_tz=cast("ZoneInfo", session.tz),
        window=resolved_window,
    )
    dimension_names = [dimension.column for dimension in plan.dimensions]
    metric_datasets = tuple(metric_ir.entities)
    dataset_tables = dict.fromkeys(metric_datasets, table)
    metric_expr = _call_metric(
        metric_fn,
        metric_datasets=metric_datasets,
        dataset_tables=dataset_tables,
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
            .aggregate(**{metric_ir.name: folded_value})
            .order_by(group_names)
            .select(*group_names, metric_ir.name)
        )
    else:
        grouped_expr = phase_b_source.aggregate(**{metric_ir.name: folded_value}).select(
            metric_ir.name
        )
    result = execute(
        grouped_expr,
        datasource_name=plan.datasource_name,
        cache=session._backend_cache,
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
            cache=session._backend_cache,
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
                session_tz=cast("ZoneInfo", session.tz),
            )
            if resolved_window.grain.is_day:
                with suppress(AttributeError):
                    coverage_df["bucket_start"] = coverage_df["bucket_start"].dt.date
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
            session_tz=cast("ZoneInfo", session.tz),
        )
    if (
        resolved_window is not None
        and resolved_window.grain is not None
        and resolved_window.grain.is_day
        and "bucket_start" in result.df
    ):
        with suppress(AttributeError):
            result.df["bucket_start"] = result.df["bucket_start"].dt.date
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


def _resolve_fold_time_field(sp: Any, fold_time_dimension_id: str) -> Any:
    """Resolve a fold_time_dimension ID to a DimensionSummary."""
    for tf in sp.list_time_dimensions():
        if tf.semantic_id == fold_time_dimension_id:
            return tf
    raise MetricNotFoundError(
        message=f"fold time dimension '{fold_time_dimension_id}' not found",
        details={"fold_time_dimension": fold_time_dimension_id},
    )


def _execute_base(
    plan: BaseObservePlan,
    metric_ir: Any,
    *,
    sp: Any,
    session: Session,
    dimensions: list[Any] | None,
    resolved_window: AbsoluteWindow | None,
) -> tuple[Any, dict[str, Any], Literal["scalar", "time_series", "segmented", "panel"], Any | None]:
    """Execute a BaseObservePlan and return (result, axes, semantic_kind, coverage_df_or_None)."""
    if getattr(metric_ir, "time_fold", None) is not None:
        return _execute_sampled_base(
            plan,
            metric_ir,
            sp=sp,
            session=session,
            resolved_window=resolved_window,
        )
    sidecar = sp._sidecar
    metric_fn = sidecar.get(metric_ir.semantic_id) if sidecar else None
    if metric_fn is None:
        raise MetricNotFoundError(
            message=f"metric callable for '{metric_ir.semantic_id}' not found",
            details={"metric": metric_ir.semantic_id},
        )
    metric_name = metric_ir.name
    metric_datasets = tuple(metric_ir.entities)
    primary_datasource = plan.datasource_name
    dataset_tables = plan.dataset_tables
    resolved_dimensions = [
        (dimension.field.entity, dimension.field) for dimension in plan.dimensions
    ]
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"

    if is_time_series and resolved_window is not None and resolved_dimensions:
        root_ds_ir = sp.get_entity(plan.root_entity)
        root_adapter = _build_dataset_adapter(sp, root_ds_ir)
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
            session_tz=cast("ZoneInfo", session.tz),
            dataset_ir=root_adapter,
        )
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: _validate_field_expr(
                _field_fn(sp, field_ir.semantic_id)(bucketed_table),
                field_id=field_ir.semantic_id,
            ).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        bucketed_table = bucketed_table.mutate(**dimension_exprs)
        dataset_tables = dict.fromkeys(metric_datasets, bucketed_table)
        metric_expr = _call_metric(
            metric_fn,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        group_names = ["bucket_start", *dimension_names]
        grouped_expr = (
            bucketed_table.group_by(group_names)
            .aggregate(**{metric_name: metric_expr})
            .order_by(group_names)
            .select(*group_names, metric_name)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._backend_cache,
            session_id=session.id,
        )
        if "bucket_start" in result.df:
            result.df["bucket_start"] = ensure_bucket_start_timestamp(
                result.df["bucket_start"],
                time_meta=time_dimension_ir.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                session_tz=cast("ZoneInfo", session.tz),
            )
        if (
            resolved_window.grain is not None
            and resolved_window.grain.is_day
            and "bucket_start" in result.df
        ):
            with suppress(AttributeError):
                result.df["bucket_start"] = result.df["bucket_start"].dt.date
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
        root_ds_ir = sp.get_entity(plan.root_entity)
        root_adapter = _build_dataset_adapter(sp, root_ds_ir)
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
            session_tz=cast("ZoneInfo", session.tz),
            dataset_ir=root_adapter,
        )
        dataset_tables = dict.fromkeys(metric_datasets, bucketed_table)
        metric_expr = _call_metric(
            metric_fn,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        grouped_expr = (
            bucketed_table.group_by("bucket_start")
            .aggregate(**{metric_name: metric_expr})
            .order_by("bucket_start")
            .select("bucket_start", metric_name)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._backend_cache,
            session_id=session.id,
        )
        if "bucket_start" in result.df:
            result.df["bucket_start"] = ensure_bucket_start_timestamp(
                result.df["bucket_start"],
                time_meta=time_dimension_ir.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                session_tz=cast("ZoneInfo", session.tz),
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
        metric_expr = _call_metric(
            metric_fn,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        grouped_expr = (
            table.group_by(dimension_names)
            .aggregate(**{metric_name: metric_expr})
            .order_by(dimension_names)
            .select(*dimension_names, metric_name)
        )
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._backend_cache,
            session_id=session.id,
        )
        axes = {
            field_ir.name: {"role": "dimension", "column": field_ir.name}
            for _, field_ir in resolved_dimensions
        }
        semantic_kind = "segmented"
    else:
        metric_expr = _call_metric(
            metric_fn,
            metric_datasets=metric_datasets,
            dataset_tables=dataset_tables,
        )
        grouped_expr = plan.table.aggregate(**{metric_name: metric_expr})
        result = execute(
            grouped_expr,
            datasource_name=primary_datasource,
            cache=session._backend_cache,
            session_id=session.id,
        )
    return result, axes, semantic_kind, None


def _execute_folded_component(
    cp: ComponentPlan,
    component_fn: Any,
    component_name: str,
    component_datasets: tuple[str, ...],
    dim_columns: list[str],
    *,
    sp: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[Any, Any | None]:
    """Execute a single folded component through the two-phase sampled path.

    Returns (component_df, coverage_df_or_None).
    """
    component_metric_ir = cp.component_metric_ir
    assert component_metric_ir.fold_time_dimension is not None
    time_dimension_ir = _resolve_fold_time_field(sp, component_metric_ir.fold_time_dimension)
    root_ds_ir = sp.get_entity(cp.base_plan.root_entity)
    root_adapter = _build_dataset_adapter(sp, root_ds_ir)
    root_time_adapter = root_adapter.fields.get(
        time_dimension_ir.name if hasattr(time_dimension_ir, "name") else time_dimension_ir
    )
    if root_time_adapter is None:
        for _fname, fadapter in root_adapter.fields.items():
            if fadapter.is_time and fadapter.semantic_id == component_metric_ir.fold_time_dimension:
                root_time_adapter = fadapter
                break
    if root_time_adapter is None:
        raise MetricNotFoundError(
            message=f"time field adapter for '{component_metric_ir.fold_time_dimension}' not found",
            details={"fold_time_dimension": component_metric_ir.fold_time_dimension},
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
    table = sample_point_table(
        cp.base_plan.table,
        time_field_ir=root_time_adapter,
        sample_grain=sample_grain,
        session_tz=cast("ZoneInfo", session.tz),
        window=resolved_window,
    )
    dimension_names = [dimension.column for dimension in cp.base_plan.dimensions]
    dataset_tables = dict.fromkeys(component_datasets, table)
    metric_expr = _call_metric(
        component_fn,
        metric_datasets=component_datasets,
        dataset_tables=dataset_tables,
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
        cache=session._backend_cache,
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
            session_tz=cast("ZoneInfo", session.tz),
        )
        if resolved_window.grain.is_day:
            with suppress(AttributeError):
                df["bucket_start"] = df["bucket_start"].dt.date
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
            cache=session._backend_cache,
            session_id=session.id,
        )
        coverage_df = coverage_result.df
        if "bucket_start" in coverage_df.columns:
            coverage_df["bucket_start"] = ensure_bucket_start_timestamp(
                coverage_df["bucket_start"],
                time_meta=root_time_adapter.time_meta,
                dataset_ir=root_adapter,
                grain=resolved_window.grain,
                session_tz=cast("ZoneInfo", session.tz),
            )
            if resolved_window.grain.is_day:
                with suppress(AttributeError):
                    coverage_df["bucket_start"] = coverage_df["bucket_start"].dt.date
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
    sp: Any,
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
    sidecar = sp._sidecar
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
        component_fn = sidecar.get(cp.component_metric_ir.semantic_id) if sidecar else None
        if component_fn is None:
            raise MetricNotFoundError(
                message=f"metric callable for {cp.component_metric_ir.semantic_id!r} not found",
                details={"metric": cp.component_metric_ir.semantic_id},
            )
        component_name = _role_to_column_name(metric_ir, cp.role)
        component_datasets = tuple(cp.component_metric_ir.entities)
        component_time_fold = getattr(cp.component_metric_ir, "time_fold", None)

        if component_time_fold is not None and has_time:
            # Folded component: execute through two-phase sampled path
            df, coverage_df = _execute_folded_component(
                cp=cp,
                component_fn=component_fn,
                component_name=component_name,
                component_datasets=component_datasets,
                dim_columns=dim_columns,
                sp=sp,
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
                root_ds_ir = sp.get_entity(cp.base_plan.root_entity)
                root_adapter = _build_dataset_adapter(sp, root_ds_ir)
                time_dimension_ir = resolve_window_time_field(root_adapter, window=resolved_window)
                if resolved_window.grain is not None:
                    base = (
                        time_dimension_ir.time_meta.granularity
                        if time_dimension_ir.time_meta
                        else None
                    ) or "day"
                    ensure_grain_supported(resolved_window.grain, base)
                table = apply_time_series_bucket(
                    table,
                    field_ir=time_dimension_ir,
                    window=resolved_window,
                    session_tz=cast("ZoneInfo", session.tz),
                    dataset_ir=root_adapter,
                )
                group_names = ["bucket_start", *dim_columns]
            else:
                group_names = list(dim_columns)
            # Use _call_metric to handle multi-dataset component metrics correctly.
            # The planner already widened all component datasets into a single table,
            # so we map every component dataset to the same (possibly bucketed) table.
            component_dataset_tables = dict.fromkeys(component_datasets, table)
            metric_expr = _call_metric(
                component_fn,
                metric_datasets=component_datasets,
                dataset_tables=component_dataset_tables,
            )
            if group_names:
                grouped_expr = (
                    table.group_by(group_names)
                    .aggregate(**{component_name: metric_expr})
                    .order_by(group_names)
                    .select(*group_names, component_name)
                )
            else:
                grouped_expr = table.aggregate(**{component_name: metric_expr}).select(component_name)
            df = execute(
                grouped_expr,
                datasource_name=cp.base_plan.datasource_name,
                cache=session._backend_cache,
                session_id=session.id,
            ).df
            if has_time and "bucket_start" in df:
                df["bucket_start"] = ensure_bucket_start_timestamp(
                    df["bucket_start"],
                    time_meta=time_dimension_ir.time_meta,
                    dataset_ir=root_adapter,
                    grain=resolved_window.grain if resolved_window else None,
                    session_tz=cast("ZoneInfo", session.tz),
                )
            if (
                has_time
                and resolved_window
                and resolved_window.grain is not None
                and resolved_window.grain.is_day
                and "bucket_start" in df
            ):
                with suppress(AttributeError):
                    df["bucket_start"] = df["bucket_start"].dt.date
        component_frames.append(df)

    if not component_frames:
        merged = pandas.DataFrame(columns=[*merge_keys, metric_name])
    else:
        merged = component_frames[0]
        for frame in component_frames[1:]:
            if merge_keys:
                merged = pandas.merge(merged, frame, on=merge_keys, how="outer")
            else:
                merged = pandas.concat([merged, frame], axis=1)
    merged[metric_name] = _evaluate_decomposition_on_frame(metric_ir, merged)
    if merge_keys:
        result_df = (
            merged[[*merge_keys, metric_name]].sort_values(merge_keys).reset_index(drop=True)
        )
    else:
        result_df = merged[[metric_name]]

    component_df: Any | None = None
    if _is_component_aware_decomposition(metric_ir):
        component_df = _component_frame_df(
            raw_df=merged,
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
        # Prefer the fold_time_dimension from any folded component, otherwise
        # fall back to the standard time dimension resolution.
        fold_time_dim_name: str | None = None
        for cp in plan.component_plans:
            cp_fold = getattr(cp.component_metric_ir, "fold_time_dimension", None)
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
            root_ds_ir = sp.get_entity(first_cp.base_plan.root_entity)
            root_adapter = _build_dataset_adapter(sp, root_ds_ir)
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


def _dump_dimensions(dimensions: list[DimensionRef] | None) -> list[dict[str, Any]] | None:
    if dimensions is None:
        return None
    return [{"semantic_id": str(dimension)} for dimension in dimensions]


def _backend_for_datasource(session: Session, datasource_name: str) -> tuple[str, Any]:
    return datasource_name, session._backend_cache.get_or_create(datasource_name)


def _resolve_backend_type(datasource_name: str, project_root: str) -> str | None:
    """Resolve the backend_type for a named datasource from the project store."""
    from marivo.analysis.datasources import store as _ds_store

    ds_ir = _ds_store.load_one(datasource_name, project_root=Path(project_root))
    if ds_ir is not None:
        return ds_ir.backend_type
    return None


def _build_fold_meta(metric_ir: Any, sp: Any) -> dict[str, Any]:
    """Build fold metadata dict for a folded metric's MetricFrameMeta."""
    sample_interval_token_val: str | None = None
    if metric_ir.fold_time_dimension is not None:
        for tf in sp.list_time_dimensions():
            if tf.semantic_id == metric_ir.fold_time_dimension:
                si = getattr(tf, "sample_interval", None)
                if si is not None:
                    sample_interval_token_val = sample_interval_token(si)
                break
    return {
        "time_fold": metric_ir.time_fold.label(),
        "fold_time_dimension": metric_ir.fold_time_dimension,
        "sample_interval": sample_interval_token_val,
    }


def _build_derived_fold_meta(derived_plan: DerivedObservePlan, sp: Any) -> dict[str, Any]:
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
            "fold_time_dimension": cp_ir.fold_time_dimension,
        }
        component_folds.append(fold_entry)
        # Capture sample_interval from the first folded component
        if sample_interval_token_val is None and cp_ir.fold_time_dimension is not None:
            for tf in sp.list_time_dimensions():
                if tf.semantic_id == cp_ir.fold_time_dimension:
                    si = getattr(tf, "sample_interval", None)
                    if si is not None:
                        sample_interval_token_val = sample_interval_token(si)
                    break
    return {
        "time_fold": "derived",
        "component_folds": component_folds,
        "sample_interval": sample_interval_token_val,
    }


def _call_metric(
    metric_fn: Callable[..., Any],
    *,
    metric_datasets: tuple[str, ...],
    dataset_tables: dict[str, Any],
) -> Any:
    return metric_fn(*(dataset_tables[dataset_name] for dataset_name in metric_datasets))


def observe(
    metric: MetricRef,
    *,
    timescope: TimeScopeInput = None,
    grain: GrainInput = None,
    dimensions: list[DimensionRef] | None = None,
    where: dict[DimensionRef, SliceValue] | None = None,
    time_dimension: DimensionRef | None = None,
    expect_shape: SemanticShape | None = None,
    session: Session | None = None,
) -> MetricFrame:
    if session is None:
        session = require_current_session()
    ensure_session_writable(session)
    if not isinstance(metric, MetricRef):
        raise SemanticKindMismatchError(
            message="observe requires metric=MetricRef(...)",
            details={
                "expected_kind": "MetricRef",
                "got_kind": type(metric).__name__,
            },
        )
    metric_id = metric.semantic_id
    if "." not in metric_id:
        raise MetricNotFoundError(message=f"metric '{metric_id}' is not '<model>.<metric>'")
    model_name, metric_name = metric_id.split(".", 1)
    time_dimension_id = _normalize_time_dimension_ref(time_dimension)
    where_by_id = _normalize_where_refs(where)
    resolved_window, original_timescope = _resolve_timescope(
        timescope,
        grain=grain,
        time_dimension=time_dimension_id,
    )
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    # Access semantic layer through session._semantic_project (SemanticProject instance)
    sp = session._semantic_project
    if not sp.is_ready():
        sp.load()
    metric_semantic_id = f"{model_name}.{metric_name}"
    metric_ir = sp.get_metric(metric_semantic_id)
    if metric_ir is None:
        available_ids = sorted(m.semantic_id for m in sp.list_metrics())
        raise MetricNotFoundError(
            message=f"metric '{metric_id}' not found",
            hint="Check <project_root>/.marivo/semantic/.",
            details={
                "model": model_name,
                "metric": metric_name,
                "available_ids": available_ids,
            },
        )

    # For folded metrics, inject fold_time_dimension into the window if not
    # already specified so that downstream resolution picks the correct time axis.
    if (
        getattr(metric_ir, "time_fold", None) is not None
        and metric_ir.fold_time_dimension is not None
        and time_dimension_id is None
        and resolved_window is not None
        and resolved_window.time_dimension is None
    ):
        resolved_window, original_timescope = _resolve_timescope(
            timescope,
            grain=grain,
            time_dimension=metric_ir.fold_time_dimension,
        )

    # For derived metrics with folded components, inject the first component's
    # fold_time_dimension into the window so the planner resolves the correct
    # time axis when entities have multiple time dimensions.
    if (
        metric_ir.is_derived
        and time_dimension_id is None
        and resolved_window is not None
        and resolved_window.time_dimension is None
    ):
        for _role, _comp_id in metric_ir.decomposition.components.items():
            _comp_ir = sp.get_metric(_comp_id)
            if (
                _comp_ir is not None
                and getattr(_comp_ir, "time_fold", None) is not None
                and getattr(_comp_ir, "fold_time_dimension", None) is not None
            ):
                resolved_window, original_timescope = _resolve_timescope(
                    timescope,
                    grain=grain,
                    time_dimension=_comp_ir.fold_time_dimension,
                )
                break

    # Get the metric callable from the sidecar
    sidecar = sp._sidecar
    metric_fn = sidecar.get(metric_semantic_id) if sidecar else None
    if metric_fn is None and not metric_ir.is_derived:
        raise MetricNotFoundError(
            message=f"metric callable for '{metric_id}' not found",
            details={"model": model_name, "metric": metric_name},
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    session._backend_cache.begin_query_capture()
    dataset_irs: dict[str, _EntityIRAdapter] = {}
    primary_datasource: str | None = None
    stored_where = normalize_slice_for_storage(where_by_id)
    metric_datasets = tuple(metric_ir.entities)
    dimension_refs = _validate_dimension_refs(dimensions)
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
    if metric_ir.is_derived:
        # Build dataset adapters for all entities in the project so the planner
        # can resolve component metrics that span different entities.
        all_dataset_irs: dict[str, _EntityIRAdapter] = {}
        for ds_summary in sp.list_entities():
            ds_ir = sp.get_entity(ds_summary.semantic_id)
            if ds_ir is None:
                continue
            all_dataset_irs[ds_ir.semantic_id] = _build_dataset_adapter(sp, ds_ir)
        all_dataset_fns = {ds_id: adapter.fn for ds_id, adapter in all_dataset_irs.items()}

        derived_plan = plan_observe(
            project=sp,
            session=session,
            metric_ir=metric_ir,
            dataset_irs=all_dataset_irs,
            dataset_fns=all_dataset_fns,
            dimensions=dimensions,
            where=where_by_id,
            resolved_window=resolved_window,
            time_dimension=time_dimension_id,
        )
        # plan_observe always returns DerivedObservePlan for derived metrics
        assert isinstance(derived_plan, DerivedObservePlan)

        # Build params and check cache before executing the backend query.
        params_timescope = None
        if resolved_window is not None:
            params_timescope = {
                "original": original_timescope,
                "resolved": dump_window(resolved_window),
                "session_tz": str(session.tz),
            }
        params = {
            "metric": metric_id,
            "timescope": params_timescope,
            "dimensions": _dump_dimensions(dimensions),
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
            return cast("MetricFrame", load_frame(prospective_id, session=session))

        result, component_df, derived_axes, derived_kind, derived_coverage_df = _execute_derived(
            derived_plan,
            metric_ir,
            sp=sp,
            session=session,
            resolved_window=resolved_window,
        )
        finished_at = datetime.now(UTC)
        frame_ref = _gen_ref("frame")
        job_ref = _gen_ref("job")
        # Determine fold metadata for derived metrics with folded components
        _any_folded = any(
            getattr(cp.component_metric_ir, "time_fold", None) is not None
            for cp in derived_plan.component_plans
        )
        _derived_fold: dict[str, Any] | None = None
        if _any_folded:
            _derived_fold = _build_derived_fold_meta(derived_plan, sp)
        meta = MetricFrameMeta(
            kind="metric_frame",
            ref=frame_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
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
            reaggregatable=not _any_folded,
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
        _captured_queries = session._backend_cache.take_captured_queries()
        _output_ref = frame.meta.artifact_id or frame.ref
        persist_job_record(
            session,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "observe",
                "params": params,
                "input_frame_refs": [],
                "output_frame_ref": _output_ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": str(session._semantic_project.semantic_root),
                "semantic_model": model_name,
                "queries": [
                    {**qe.to_dict(), "output_ref": _output_ref} for qe in _captured_queries
                ],
            },
        )
        return frame

    # --- Base (non-derived) metric path: route through planner ---
    # Build dataset adapters for all metric entities
    for entity_name in metric_datasets:
        entity_ir = sp.get_entity(entity_name)
        if entity_ir is None:
            raise MetricNotFoundError(
                message=f"entity '{entity_name}' not found for metric '{metric_id}'",
                details={"entity": entity_name},
            )
        dataset_irs[entity_name] = _build_dataset_adapter(sp, entity_ir)

    # Add entities required by explicit dimensions/where
    for dim_ir in [*sp.list_dimensions(), *sp.list_time_dimensions()]:
        if (
            dimensions
            and any(dim.semantic_id == dim_ir.semantic_id for dim in dimension_refs)
            and dim_ir.entity not in dataset_irs
        ):
            ds_ir = sp.get_entity(dim_ir.entity)
            if ds_ir is not None:
                dataset_irs[dim_ir.entity] = _build_dataset_adapter(sp, ds_ir)
        for raw_key in where_by_id or {}:
            if raw_key == dim_ir.semantic_id and dim_ir.entity not in dataset_irs:
                ds_ir = sp.get_entity(dim_ir.entity)
                if ds_ir is not None:
                    dataset_irs[dim_ir.entity] = _build_dataset_adapter(sp, ds_ir)

    dataset_fns = {dataset_id: adapter.fn for dataset_id, adapter in dataset_irs.items()}

    plan = plan_base_observe(
        project=sp,
        session=session,
        metric_ir=metric_ir,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where_by_id,
        resolved_window=resolved_window,
        time_dimension=time_dimension_id,
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
            "session_tz": str(session.tz),
        }
    params = {
        "metric": metric_id,
        "timescope": params_timescope,
        "dimensions": _dump_dimensions(dimensions),
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
        return cast("MetricFrame", load_frame(prospective_id, session=session))

    result, axes, semantic_kind, coverage_df = _execute_base(
        plan,
        metric_ir,
        sp=sp,
        session=session,
        dimensions=dimensions,
        resolved_window=resolved_window,
    )
    finished_at = datetime.now(UTC)

    # Resolve quantile capability for quantile-folded metrics
    _capability = None
    _time_fold = getattr(metric_ir, "time_fold", None)
    if _time_fold is not None and _time_fold.kind == "quantile":
        if primary_datasource is None:
            raise AnalysisError(
                message="quantile sampled fold requires a primary datasource to resolve backend type.",
                details={"metric": metric_ir.semantic_id},
            )
        backend_type = _resolve_backend_type(primary_datasource, str(session.project_root))
        if backend_type is None:
            raise AnalysisError(
                message="quantile sampled fold could not resolve backend_type for the primary datasource.",
                details={"metric": metric_ir.semantic_id, "datasource": primary_datasource},
            )
        _capability = quantile_capability(backend_type)
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
        fold=_build_fold_meta(metric_ir, sp) if metric_ir.time_fold is not None else None,
        reaggregatable=metric_ir.time_fold is None,
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

    _captured_queries = session._backend_cache.take_captured_queries()
    _output_ref = frame.meta.artifact_id or frame.ref
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "observe",
            "params": params,
            "input_frame_refs": [],
            "output_frame_ref": _output_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session._semantic_project.semantic_root),
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
    metric_id: str,
    model_name: str,
    stored_where: dict[str, Any],
    semantic_kind: str,
    subject_grain: str | None = None,
) -> MetricFrame:
    """Commit an observe MetricFrame through the evidence pipeline (shared tail)."""
    result = cast(
        "MetricFrame",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type="observe",
            inputs=CommitInputs(input_refs=[]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(
                values={"metric_id": metric_id, "model": model_name}
            ),
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
