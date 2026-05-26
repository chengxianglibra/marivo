"""Materialize a semantic_py metric into a MetricFrame."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast

from marivo.analysis_py.errors import (
    AmbiguousDimensionError,
    CrossBackendMetricError,
    DimensionAcrossDatasetsError,
    DimensionFieldNotFoundError,
    MetricNotFoundError,
    MetricShapeUnsupportedError,
    SemanticKindMismatchError,
)
from marivo.analysis_py.executor.runner import (
    apply_slice_to_dataset,
    apply_time_series_bucket,
    apply_window_to_dataset,
    execute,
    normalize_slice_for_storage,
    resolve_window_time_field,
)
from marivo.analysis_py.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis_py.lineage import Lineage, LineageStep
from marivo.analysis_py.refs import DimensionRef, MetricRef
from marivo.analysis_py.session.attach import active as session_active
from marivo.analysis_py.session.core import Session, ensure_session_writable
from marivo.analysis_py.session.persistence import (
    read_session_meta,
    write_frame_to_disk,
    write_job_record,
    write_session_meta,
)
from marivo.analysis_py.windows.resolver import (
    coerce_as_of,
    resolve_to_absolute,
    zoneinfo_from_name,
)
from marivo.analysis_py.windows.spec import (
    AbsoluteWindow,
    RelativeWindow,
    WindowInput,
    dump_window,
    normalize_window_input,
)

# ---------------------------------------------------------------------------
# v1.1 -> runner adapter types
# ---------------------------------------------------------------------------
# The runner.py functions expect old-style IR objects with attributes like
# ``fn``, ``fields``, ``datasource_name``, ``is_time``, ``time_meta``.
# The new v1.1 semantic_py stores callables in a sidecar map and uses
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
    ) -> None:
        self.data_type = data_type
        self.granularity = granularity
        self.format = format
        self.required_prefix = required_prefix


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
    dataset_fn = sidecar.get(dataset_ir.semantic_id) if sidecar else None

    def _default_fn(backend: Any) -> Any:
        raise RuntimeError(f"No sidecar callable for dataset {dataset_ir.semantic_id!r}")

    fn = dataset_fn if dataset_fn is not None else _default_fn

    # Build field adapters for this dataset
    field_adapters: dict[str, _FieldIRAdapter] = {}
    for field_ir in sp.list_fields(dataset=dataset_ir.semantic_id):
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
    for tf_ir in sp.list_time_fields(dataset=dataset_ir.semantic_id):
        tf_fn = sidecar.get(tf_ir.semantic_id) if sidecar else None
        _captured_tf_sid = tf_ir.semantic_id

        def _default_tf_fn(table: Any, *, _sid: str = _captured_tf_sid) -> Any:
            raise RuntimeError(f"No sidecar callable for time_field {_sid!r}")

        time_meta = _TimeFieldMetaAdapter(
            data_type=tf_ir.data_type or "date",
            granularity=tf_ir.granularity or "day",
            format=None,
            required_prefix=tf_ir.required_prefix,
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
        fn=fn,
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


def _resolve_window(
    window_in: AbsoluteWindow | RelativeWindow | None, *, session: Session
) -> tuple[AbsoluteWindow | None, RelativeWindow | None, str | None]:
    if window_in is None:
        return None, None, None
    if isinstance(window_in, AbsoluteWindow):
        return window_in, None, None
    effective_tz = session.tz
    if window_in.tz is not None:
        effective_tz = zoneinfo_from_name(window_in.tz)
    as_of_dt = coerce_as_of(window_in.as_of, tz=effective_tz)
    resolved = resolve_to_absolute(window_in, as_of=as_of_dt, tz=effective_tz)
    return resolved, window_in, as_of_dt.isoformat()


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


def _resolve_dimensions(
    dimensions: list[Any] | None, *, dataset_irs: dict[str, Any]
) -> list[tuple[str, Any]]:
    dimension_refs = _validate_dimension_refs(dimensions)
    resolved: list[tuple[str, Any]] = []
    for dimension in dimension_refs:
        matches = [
            (dataset_name, field_ir)
            for dataset_name, dataset_ir in dataset_irs.items()
            for field_ir in dataset_ir.fields.values()
            if dimension.id in {field_ir.name, field_ir.semantic_id}
        ]
        if not matches:
            raise DimensionFieldNotFoundError(
                message=f"dimension '{dimension.id}' not found",
                details={
                    "dimension_id": dimension.id,
                    "searched_datasets": sorted(dataset_irs),
                },
            )
        if len(matches) > 1:
            raise AmbiguousDimensionError(
                message=f"dimension '{dimension.id}' is ambiguous",
                details={
                    "dimension_id": dimension.id,
                    "candidates": sorted(
                        f"{dataset_name}.{field_ir.name}" for dataset_name, field_ir in matches
                    ),
                },
            )
        resolved.append(matches[0])

    dimensions_by_dataset: dict[str, list[str]] = {}
    for dataset_name, field_ir in resolved:
        dimensions_by_dataset.setdefault(dataset_name, []).append(field_ir.name)
    if len(dimensions_by_dataset) > 1:
        raise DimensionAcrossDatasetsError(
            message="observe dimensions must resolve to one dataset",
            details={"dimensions_by_dataset": dimensions_by_dataset},
        )
    return resolved


def _dump_dimensions(dimensions: list[DimensionRef] | None) -> list[dict[str, Any]] | None:
    if dimensions is None:
        return None
    return [dimension.model_dump(mode="json") for dimension in dimensions]


def _datasource_cache_key(datasource_semantic_id: str) -> str:
    return (
        datasource_semantic_id.rsplit(".", 1)[-1]
        if "." in datasource_semantic_id
        else datasource_semantic_id
    )


def _backend_for_datasource(session: Session, datasource_semantic_id: str) -> tuple[str, Any]:
    try:
        return datasource_semantic_id, session.backend_cache.get_or_create(datasource_semantic_id)
    except KeyError:
        short_name = _datasource_cache_key(datasource_semantic_id)
        return short_name, session.backend_cache.get_or_create(short_name)


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
    window: WindowInput = None,
    dimensions: list[DimensionRef] | None = None,
    slice: dict[str, Any] | None = None,
    session: Session | None = None,
) -> MetricFrame:
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
    window_in = normalize_window_input(window)
    resolved_window, original_window, as_of_resolved = _resolve_window(window_in, session=session)
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    # Access semantic layer through session.semantic_project (SemanticProject instance)
    sp = session.semantic_project
    if not sp.is_ready():
        sp.load()
    metric_semantic_id = f"{model_name}.{metric_name}"
    metric_ir = sp.get_metric(metric_semantic_id)
    if metric_ir is None:
        raise MetricNotFoundError(
            message=f"metric '{metric_id}' not found",
            hint="Check <project_root>/.marivo/semantic/.",
            details={"model": model_name, "metric": metric_name},
        )

    # Get the metric callable from the sidecar
    sidecar = sp.sidecar()
    metric_fn = sidecar.get(metric_semantic_id) if sidecar else None
    if metric_fn is None:
        raise MetricNotFoundError(
            message=f"metric callable for '{metric_id}' not found",
            details={"model": model_name, "metric": metric_name},
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    dataset_tables: dict[str, Any] = {}
    dataset_irs: dict[str, _DatasetIRAdapter] = {}
    primary_datasource: str | None = None
    stored_slice = normalize_slice_for_storage(slice)
    metric_datasets = tuple(metric_ir.datasets)
    dimension_refs = _validate_dimension_refs(dimensions)
    if is_time_series and not dimension_refs and len(metric_datasets) > 1:
        raise MetricShapeUnsupportedError(
            message=(
                f"windowed time_series observe does not support multi-dataset metric '{metric_id}'"
            ),
            details={
                "kind": "WindowedTimeSeriesUnsupported",
                "metric": metric_id,
                "datasets": sorted(metric_datasets),
            },
        )

    for dataset_name in metric_datasets:
        dataset_ir = sp.get_dataset(dataset_name)
        if dataset_ir is None:
            raise MetricNotFoundError(
                message=f"dataset '{dataset_name}' not found for metric '{metric_id}'",
                details={"dataset": dataset_name},
            )
        dataset_irs[dataset_name] = _build_dataset_adapter(sp, dataset_ir)

    resolved_dimensions = _resolve_dimensions(dimensions, dataset_irs=dataset_irs)
    if resolved_dimensions and len(metric_datasets) > 1:
        raise MetricShapeUnsupportedError(
            message=(f"segmented observe does not support multi-dataset metric '{metric_id}'"),
            details={
                "kind": "SegmentedMultiDatasetUnsupported",
                "metric": metric_id,
                "datasets": sorted(metric_datasets),
                "dimensions": _dump_dimensions(dimensions),
            },
        )

    for dataset_name in metric_datasets:
        ds_adapter = dataset_irs[dataset_name]
        datasource_name, backend = _backend_for_datasource(session, ds_adapter.datasource_name)
        if primary_datasource is None:
            primary_datasource = datasource_name
        elif primary_datasource != datasource_name:
            raise CrossBackendMetricError(
                message=f"metric '{metric_id}' spans multiple datasources; v1 does not support it",
            )
        table = ds_adapter.fn(backend)
        table = apply_slice_to_dataset(table, slice, dataset_ir=ds_adapter)
        table = apply_window_to_dataset(
            table,
            resolved_window,
            dataset_ir=ds_adapter,
            session_tz=session.tz,
        )
        dataset_tables[dataset_name] = table
        session.known_datasources.add(datasource_name)
    _persist_known_datasources(session)

    if primary_datasource is None:
        raise MetricNotFoundError(message=f"metric '{metric_id}' references no datasets")

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"
    if is_time_series and resolved_window is not None and resolved_dimensions:
        dataset_name = metric_datasets[0]
        ds_adapter = dataset_irs[dataset_name]
        time_field_ir = resolve_window_time_field(ds_adapter, window=resolved_window)
        bucketed_table = apply_time_series_bucket(
            dataset_tables[dataset_name],
            field_ir=time_field_ir,
            window=resolved_window,
            session_tz=session.tz,
        )
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: field_ir.fn(bucketed_table).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        bucketed_table = bucketed_table.mutate(**dimension_exprs)
        dataset_tables[dataset_name] = bucketed_table
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
            grouped_expr, datasource_name=primary_datasource, cache=session.backend_cache
        )
        if resolved_window.grain == "day" and "bucket_start" in result.df:
            with suppress(AttributeError):
                result.df["bucket_start"] = result.df["bucket_start"].dt.date
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain,
                "time_field": time_field_ir.name,
            },
            **{
                field_ir.name: {"role": "dimension", "column": field_ir.name}
                for _, field_ir in resolved_dimensions
            },
        }
        semantic_kind = "panel"
    elif is_time_series and resolved_window is not None:
        dataset_name = metric_datasets[0]
        ds_adapter = dataset_irs[dataset_name]
        time_field_ir = resolve_window_time_field(ds_adapter, window=resolved_window)
        bucketed_table = apply_time_series_bucket(
            dataset_tables[dataset_name],
            field_ir=time_field_ir,
            window=resolved_window,
            session_tz=session.tz,
        )
        dataset_tables[dataset_name] = bucketed_table
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
            grouped_expr, datasource_name=primary_datasource, cache=session.backend_cache
        )
        axes = {
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": resolved_window.grain,
                "time_field": time_field_ir.name,
            }
        }
        semantic_kind = "time_series"
    elif resolved_dimensions:
        dataset_name = resolved_dimensions[0][0]
        table = dataset_tables[dataset_name]
        dimension_names = [field_ir.name for _, field_ir in resolved_dimensions]
        dimension_exprs = {
            field_ir.name: field_ir.fn(table).name(field_ir.name)
            for _, field_ir in resolved_dimensions
        }
        table = table.mutate(**dimension_exprs)
        dataset_tables[dataset_name] = table
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
            grouped_expr, datasource_name=primary_datasource, cache=session.backend_cache
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
            metric_expr, datasource_name=primary_datasource, cache=session.backend_cache
        )
    finished_at = datetime.now(UTC)

    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    params_window = None
    if resolved_window is not None:
        params_window = {
            "original": dump_window(original_window),
            "resolved": dump_window(resolved_window),
            "as_of_resolved": as_of_resolved,
            "session_tz": str(session.tz),
        }
    params = {
        "metric": metric_id,
        "window": params_window,
        "dimensions": _dump_dimensions(dimensions),
        "slice": stored_slice,
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
                )
            ]
        ),
        metric_id=metric_id,
        axes=axes,
        measure={"name": metric_name},
        window=dump_window(resolved_window),
        slice=stored_slice,
        semantic_kind=semantic_kind,
        semantic_model=model_name,
    )
    frame = MetricFrame(_df=result.df, meta=meta)
    frame.meta = cast("MetricFrameMeta", write_frame_to_disk(session.layout, frame))
    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "observe",
            "params": params,
            "input_frame_refs": [],
            "output_frame_ref": frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": model_name,
        },
    )
    return frame


def _persist_known_datasources(session: Session) -> None:
    meta = read_session_meta(session.layout)
    meta["known_datasources"] = sorted(session.known_datasources)
    meta["updated_at"] = datetime.now(UTC).isoformat()
    write_session_meta(session.layout, meta)
