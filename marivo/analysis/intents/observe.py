"""Materialize a semantic metric into a MetricFrame."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from marivo.analysis.errors import (
    MetricNotFoundError,
    MetricShapeUnsupportedError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.executor.runner import (
    apply_time_series_bucket,
    execute,
    normalize_slice_for_storage,
    resolve_window_time_field,
)
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._shape import SemanticShape, observe_output_shape
from marivo.analysis.intents._types import SliceValue
from marivo.analysis.intents.observe_planner import (
    BaseObservePlan,
    DerivedObservePlan,
    plan_base_observe,
    plan_observe,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.refs import DimensionRef, MetricRef
from marivo.analysis.session.attach import active as session_active
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.session.persistence import (
    read_session_meta,
    write_frame_to_disk,
    write_job_record,
    write_session_meta,
)
from marivo.analysis.windows.spec import (
    AbsoluteWindow,
    GrainInput,
    TimeScopeInput,
    dump_window,
    ensure_grain_supported,
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


class _FieldIRAdapter:
    """Adapter that mimics the old FieldIR for runner.py."""

    def __init__(
        self,
        semantic_id: str,
        name: str,
        dataset_name: str,
        fn: Callable[..., Any],
        *,
        is_time: bool = False,
        time_meta: _TimeFieldMetaAdapter | None = None,
    ) -> None:
        self.semantic_id = semantic_id
        self.name = name
        self.dataset_name = dataset_name
        self.fn = fn
        self.is_time = is_time
        self.time_meta = time_meta


class _DatasetIRAdapter:
    """Adapter that mimics the old DatasetIR for runner.py."""

    def __init__(
        self,
        name: str,
        fn: Callable[..., Any],
        datasource_name: str,
        fields: dict[str, _FieldIRAdapter],
    ) -> None:
        self.name = name
        self.fn = fn
        self.datasource_name = datasource_name
        self.fields = fields


def _build_dataset_adapter(
    sp: Any,
    dataset_ir: Any,
) -> _DatasetIRAdapter:
    """Build a _DatasetIRAdapter from a v1.1 DatasetIR + sidecar."""
    sidecar = sp.sidecar()

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
    field_adapters: dict[str, _FieldIRAdapter] = {}
    for field_ir in sp.list_dimensions(dataset=dataset_ir.semantic_id, display=False):
        field_fn = sidecar.get(field_ir.semantic_id) if sidecar else None
        _captured_field_sid = field_ir.semantic_id

        def _default_field_fn(table: Any, *, _sid: str = _captured_field_sid) -> Any:
            raise RuntimeError(f"No sidecar callable for field {_sid!r}")

        adapter = _FieldIRAdapter(
            semantic_id=field_ir.semantic_id,
            name=field_ir.name,
            dataset_name=dataset_ir.name,
            fn=field_fn if field_fn is not None else _default_field_fn,
            is_time=False,
        )
        field_adapters[field_ir.name] = adapter

    # Add time fields
    for tf_ir in sp.list_time_fields(dataset=dataset_ir.semantic_id, display=False):
        tf_fn = sidecar.get(tf_ir.semantic_id) if sidecar else None
        _captured_tf_sid = tf_ir.semantic_id

        def _default_tf_fn(table: Any, *, _sid: str = _captured_tf_sid) -> Any:
            raise RuntimeError(f"No sidecar callable for time_field {_sid!r}")

        time_meta = _TimeFieldMetaAdapter(
            data_type=tf_ir.data_type or "date",
            granularity=tf_ir.granularity or "day",
            format=tf_ir.format,
            required_prefix=tf_ir.required_prefix,
            timezone=tf_ir.timezone,
        )
        adapter = _FieldIRAdapter(
            semantic_id=tf_ir.semantic_id,
            name=tf_ir.name,
            dataset_name=dataset_ir.name,
            fn=tf_fn if tf_fn is not None else _default_tf_fn,
            is_time=True,
            time_meta=time_meta,
        )
        field_adapters[tf_ir.name] = adapter

    return _DatasetIRAdapter(
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


def _component_parent_columns(metric_ir: Any) -> list[str]:
    kind = metric_ir.decomposition.kind
    if kind == "ratio":
        return ["numerator", "denominator"]
    if kind == "weighted_average":
        return ["numerator", "weight"]
    return []


def _component_frame_df(
    *,
    raw_df: Any,
    metric_ir: Any,
    axes_columns: list[str],
    metric_value_column: str,
) -> Any:
    role_columns = _component_parent_columns(metric_ir)
    for role in role_columns:
        _require_component_role_column(metric_ir, role, raw_df)
    selected = [*axes_columns, *role_columns, metric_value_column]
    component_df = raw_df[selected].rename(columns={metric_value_column: "metric_value"})
    return component_df[[*axes_columns, *role_columns, "metric_value"]]


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
    frame_ref = _gen_ref("frame")
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
    component.meta = cast("ComponentFrameMeta", write_frame_to_disk(session.layout, component))
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
    parent.meta = cast("MetricFrameMeta", write_frame_to_disk(session.layout, parent))
    return parent


def _resolve_timescope(
    timescope: TimeScopeInput,
    *,
    grain: GrainInput,
    time_field: str | None,
) -> tuple[AbsoluteWindow | None, dict[str, Any] | None]:
    timescope_in = normalize_timescope_input(timescope)
    resolved = make_absolute_window(timescope_in, grain=grain, time_field=time_field)
    original = timescope_in.model_dump(mode="json") if timescope_in is not None else None
    return resolved, original


def _validate_dimension_refs(dimensions: list[Any] | None) -> list[DimensionRef]:
    if dimensions is None:
        return []
    if len(dimensions) == 0:
        raise SemanticKindMismatchError(
            message="observe dimensions must be omitted or contain at least one DimensionRef",
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
        if dimension.id in seen:
            duplicate_ids.add(dimension.id)
        seen.add(dimension.id)
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


def _field_fn(sp: Any, field_id: str) -> Callable[..., Any]:
    sidecar = sp.sidecar()
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
        numerator = _require_component_role_column(metric_ir, "numerator", frame)
        denominator = _require_component_role_column(metric_ir, "denominator", frame)
        return numerator / denominator
    if kind == "weighted_average":
        numerator = _require_component_role_column(metric_ir, "numerator", frame)
        weight = _require_component_role_column(metric_ir, "weight", frame)
        return numerator / weight
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
    if role not in frame:
        raise MetricShapeUnsupportedError(
            message=(
                f"derived metric {metric_ir.semantic_id!r} component role column "
                f"{role!r} is missing"
            ),
            details={
                "kind": "DerivedMetricComponentColumnMissing",
                "metric": metric_ir.semantic_id,
                "role": role,
                "component_metric": component_id,
                "column": role,
            },
        )
    return frame[role]


class _Result:
    """Minimal result holder used by _execute_base and _execute_derived."""

    def __init__(self, df: Any) -> None:
        self.df = df
        self.row_count = len(df)


def _execute_base(
    plan: BaseObservePlan,
    metric_ir: Any,
    *,
    sp: Any,
    session: Session,
    dimensions: list[Any] | None,
    resolved_window: AbsoluteWindow | None,
) -> tuple[Any, dict[str, Any], Literal["scalar", "time_series", "segmented", "panel"]]:
    """Execute a BaseObservePlan and return (result, axes, semantic_kind)."""
    sidecar = sp.sidecar()
    metric_fn = sidecar.get(metric_ir.semantic_id) if sidecar else None
    if metric_fn is None:
        raise MetricNotFoundError(
            message=f"metric callable for '{metric_ir.semantic_id}' not found",
            details={"metric": metric_ir.semantic_id},
        )
    metric_name = metric_ir.name
    metric_datasets = tuple(metric_ir.datasets)
    primary_datasource = plan.datasource_name
    dataset_tables = plan.dataset_tables
    resolved_dimensions = [
        (dimension.field.dataset, dimension.field) for dimension in plan.dimensions
    ]
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"

    if is_time_series and resolved_window is not None and resolved_dimensions:
        root_ds_ir = sp.get_dataset(plan.root_dataset)
        root_adapter = _build_dataset_adapter(sp, root_ds_ir)
        time_field_ir = resolve_window_time_field(root_adapter, window=resolved_window)
        if resolved_window.grain is not None:
            base = (
                time_field_ir.time_meta.granularity if time_field_ir.time_meta else None
            ) or "day"
            ensure_grain_supported(resolved_window.grain, base)
        bucketed_table = apply_time_series_bucket(
            plan.table,
            field_ir=time_field_ir,
            window=resolved_window,
            session_tz=cast("ZoneInfo", session.tz),
        )
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: _field_fn(sp, field_ir.semantic_id)(bucketed_table).name(field_ir.name)
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
            cache=session.backend_cache,
            session_id=session.id,
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
                "time_field": time_field_ir.name,
            },
            **{
                field_ir.name: {"role": "dimension", "column": field_ir.name}
                for _, field_ir in resolved_dimensions
            },
        }
        semantic_kind = "panel"
    elif is_time_series and resolved_window is not None:
        root_ds_ir = sp.get_dataset(plan.root_dataset)
        root_adapter = _build_dataset_adapter(sp, root_ds_ir)
        time_field_ir = resolve_window_time_field(root_adapter, window=resolved_window)
        if resolved_window.grain is not None:
            base = (
                time_field_ir.time_meta.granularity if time_field_ir.time_meta else None
            ) or "day"
            ensure_grain_supported(resolved_window.grain, base)
        bucketed_table = apply_time_series_bucket(
            plan.table,
            field_ir=time_field_ir,
            window=resolved_window,
            session_tz=cast("ZoneInfo", session.tz),
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
            cache=session.backend_cache,
            session_id=session.id,
        )
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain.to_token()
                if resolved_window.grain is not None
                else None,
                "time_field": time_field_ir.name,
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
            cache=session.backend_cache,
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
        result = execute(
            metric_expr,
            datasource_name=primary_datasource,
            cache=session.backend_cache,
            session_id=session.id,
        )
    return result, axes, semantic_kind


def _execute_derived(
    plan: DerivedObservePlan,
    metric_ir: Any,
    *,
    sp: Any,
    session: Session,
    resolved_window: AbsoluteWindow | None,
) -> tuple[Any, Any | None, dict[str, Any], Literal["scalar", "time_series", "segmented", "panel"]]:
    """Execute a DerivedObservePlan and return (result, component_df, axes, semantic_kind)."""
    pandas = __import__("pandas")
    sidecar = sp.sidecar()
    metric_name = metric_ir.name
    component_frames: list[Any] = []
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
        component_name = cp.role
        component_datasets = tuple(cp.component_metric_ir.datasets)
        table = cp.base_plan.table
        if has_time:
            assert resolved_window is not None  # narrowing: has_time implies resolved_window is set
            root_ds_ir = sp.get_dataset(cp.base_plan.root_dataset)
            root_adapter = _build_dataset_adapter(sp, root_ds_ir)
            time_field_ir = resolve_window_time_field(root_adapter, window=resolved_window)
            if resolved_window.grain is not None:
                base = (
                    time_field_ir.time_meta.granularity if time_field_ir.time_meta else None
                ) or "day"
                ensure_grain_supported(resolved_window.grain, base)
            table = apply_time_series_bucket(
                table,
                field_ir=time_field_ir,
                window=resolved_window,
                session_tz=cast("ZoneInfo", session.tz),
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
            cache=session.backend_cache,
            session_id=session.id,
        ).df
        session.known_datasources.add(cp.base_plan.datasource_name)
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
        first_cp = plan.component_plans[0]
        root_ds_ir = sp.get_dataset(first_cp.base_plan.root_dataset)
        root_adapter = _build_dataset_adapter(sp, root_ds_ir)
        time_field_ir = resolve_window_time_field(root_adapter, window=resolved_window)
        axes["time"] = {
            "role": "time",
            "column": "bucket_start",
            "grain": resolved_window.grain.to_token()
            if resolved_window.grain is not None
            else None,
            "time_field": time_field_ir.name,
        }
    for col in dim_columns:
        axes[col] = {"role": "dimension", "column": col}

    return _Result(result_df), component_df, axes, semantic_kind


def _dump_dimensions(dimensions: list[DimensionRef] | None) -> list[dict[str, Any]] | None:
    if dimensions is None:
        return None
    return [dimension.model_dump(mode="json") for dimension in dimensions]


def _backend_for_datasource(session: Session, datasource_name: str) -> tuple[str, Any]:
    return datasource_name, session.backend_cache.get_or_create(datasource_name)


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
    where: dict[str, SliceValue] | None = None,
    time_field: str | None = None,
    expect_shape: SemanticShape | None = None,
    session: Session | None = None,
) -> MetricFrame:
    """Materialize a metric into a typed MetricFrame.

    When to use: starting point for any metric analysis workflow.

    Resolves ``metric`` against the active semantic project, applies the
    optional ``timescope`` / ``grain`` / ``dimensions`` / ``where`` filters, executes against
    the session's backend, and persists the result as a MetricFrame on disk.

    Args:
        metric: Wrap the registered metric id with ``mv.MetricRef("<model>.<metric>")``.
            Bare strings are rejected.
        timescope: Absolute time range as ``{"start": ..., "end": ...}``.
        grain: Optional time bucket grain. When present, observe returns a time
            series or panel depending on ``dimensions``.
        dimensions: Segment axes. In v1 all dimensions must resolve to the same
            dataset as ``metric``.
        where: Pre-aggregation row filter. Values are either a scalar (``==``),
            a list (``in``), or ``{"op": "<op>", "value": ...}`` where op is
            one of ``==, !=, in, >, >=, <, <=, between``.
        expect_shape: Optional guard. If set, observe predicts the output shape
            from ``grain``/``dimensions`` and raises ``SemanticKindMismatchError``
            before any backend work when the prediction differs.
        session: Defaults to the currently-attached session.

    Raises:
        MetricNotFoundError: The metric id is unknown or not ``<model>.<metric>``.
        SemanticKindMismatchError: ``metric`` is not a ``MetricRef``.
        ObservePlanningError: Planning failed (e.g. cross-datasource plan, missing
            path, ambiguous dimension). Check ``details["code"]`` for the specific
            error code.

    Example:
        >>> frame = session.observe(
        ...     mv.MetricRef("sales.revenue"),
        ...     timescope={"start": "2026-07-01", "end": "2026-09-30"},
        ...     grain="day",
        ...     dimensions=[mv.DimensionRef("country")],
        ... )
        >>> frame.summary()
    """
    if session is None:
        session = session_active()
    ensure_session_writable(session)
    if not isinstance(metric, MetricRef):
        raise SemanticKindMismatchError(
            message="observe requires metric=MetricRef(...)",
            details={
                "expected_kind": "MetricRef",
                "got_kind": type(metric).__name__,
            },
        )
    metric_id = metric.id
    if "." not in metric_id:
        raise MetricNotFoundError(message=f"metric '{metric_id}' is not '<model>.<metric>'")
    model_name, metric_name = metric_id.split(".", 1)
    resolved_window, original_timescope = _resolve_timescope(
        timescope,
        grain=grain,
        time_field=time_field,
    )
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    # Access semantic layer through session.semantic_project (SemanticProject instance)
    sp = session.semantic_project
    if not sp.is_ready():
        sp.load()
    metric_semantic_id = f"{model_name}.{metric_name}"
    metric_ir = sp.get_metric(metric_semantic_id)
    if metric_ir is None:
        available_ids = sorted(m.semantic_id for m in sp.list_metrics(display=False))
        raise MetricNotFoundError(
            message=f"metric '{metric_id}' not found",
            hint="Check <project_root>/.marivo/semantic/.",
            details={
                "model": model_name,
                "metric": metric_name,
                "available_ids": available_ids,
            },
        )

    # Get the metric callable from the sidecar
    sidecar = sp.sidecar()
    metric_fn = sidecar.get(metric_semantic_id) if sidecar else None
    if metric_fn is None and not metric_ir.is_derived:
        raise MetricNotFoundError(
            message=f"metric callable for '{metric_id}' not found",
            details={"model": model_name, "metric": metric_name},
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    session.backend_cache.begin_query_capture()
    dataset_irs: dict[str, _DatasetIRAdapter] = {}
    primary_datasource: str | None = None
    stored_where = normalize_slice_for_storage(where)
    metric_datasets = tuple(metric_ir.datasets)
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
        # Build dataset adapters for all datasets in the project so the planner
        # can resolve component metrics that span different datasets.
        all_dataset_irs: dict[str, _DatasetIRAdapter] = {}
        for ds_summary in sp.list_datasets(display=False):
            ds_ir = sp.get_dataset(ds_summary.semantic_id)
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
            where=where,
            resolved_window=resolved_window,
            time_field=time_field,
        )
        # plan_observe always returns DerivedObservePlan for derived metrics
        assert isinstance(derived_plan, DerivedObservePlan)
        result, component_df, derived_axes, derived_kind = _execute_derived(
            derived_plan,
            metric_ir,
            sp=sp,
            session=session,
            resolved_window=resolved_window,
        )
        _persist_known_datasources(session)
        finished_at = datetime.now(UTC)
        frame_ref = _gen_ref("frame")
        job_ref = _gen_ref("job")
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
        _captured_queries = session.backend_cache.take_captured_queries()
        _output_ref = frame.meta.artifact_id or frame.ref
        write_job_record(
            session.layout,
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
                "semantic_project_root": session.semantic_project.root,
                "semantic_model": model_name,
                "queries": [
                    {**qe.to_dict(), "output_ref": _output_ref} for qe in _captured_queries
                ],
            },
        )
        return frame

    # --- Base (non-derived) metric path: route through planner ---
    # Build dataset adapters for all metric datasets
    for dataset_name in metric_datasets:
        dataset_ir = sp.get_dataset(dataset_name)
        if dataset_ir is None:
            raise MetricNotFoundError(
                message=f"dataset '{dataset_name}' not found for metric '{metric_id}'",
                details={"dataset": dataset_name},
            )
        dataset_irs[dataset_name] = _build_dataset_adapter(sp, dataset_ir)

    # Add datasets required by explicit dimensions/where
    for field_ir in [*sp.list_dimensions(display=False), *sp.list_time_fields(display=False)]:
        if (
            dimensions
            and any(dim.id == field_ir.semantic_id for dim in dimension_refs)
            and field_ir.dataset not in dataset_irs
        ):
            ds_ir = sp.get_dataset(field_ir.dataset)
            if ds_ir is not None:
                dataset_irs[field_ir.dataset] = _build_dataset_adapter(sp, ds_ir)
        for raw_key in where or {}:
            if raw_key == field_ir.semantic_id and field_ir.dataset not in dataset_irs:
                ds_ir = sp.get_dataset(field_ir.dataset)
                if ds_ir is not None:
                    dataset_irs[field_ir.dataset] = _build_dataset_adapter(sp, ds_ir)

    dataset_fns = {dataset_id: adapter.fn for dataset_id, adapter in dataset_irs.items()}

    plan = plan_base_observe(
        project=sp,
        session=session,
        metric_ir=metric_ir,
        dataset_irs=dataset_irs,
        dataset_fns=dataset_fns,
        dimensions=dimensions,
        where=where,
        resolved_window=resolved_window,
        time_field=time_field,
    )
    primary_datasource = plan.datasource_name
    session.known_datasources.add(primary_datasource)
    _persist_known_datasources(session)

    if primary_datasource is None:
        raise MetricNotFoundError(message=f"metric '{metric_id}' references no datasets")

    result, axes, semantic_kind = _execute_base(
        plan,
        metric_ir,
        sp=sp,
        session=session,
        dimensions=dimensions,
        resolved_window=resolved_window,
    )
    finished_at = datetime.now(UTC)

    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
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

    _captured_queries = session.backend_cache.take_captured_queries()
    _output_ref = frame.meta.artifact_id or frame.ref
    write_job_record(
        session.layout,
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
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": model_name,
            "queries": [{**qe.to_dict(), "output_ref": _output_ref} for qe in _captured_queries],
        },
    )
    return frame


def _persist_known_datasources(session: Session) -> None:
    meta = read_session_meta(session.layout)
    meta["known_datasources"] = sorted(session.known_datasources)
    meta["updated_at"] = datetime.now(UTC).isoformat()
    write_session_meta(session.layout, meta)


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
    return cast(
        "MetricFrame",
        commit_result(
            store=session.evidence_store(),
            frames_dir=session.layout.frames_dir,
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
