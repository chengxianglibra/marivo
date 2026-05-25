"""Materialize a semantic_py metric into a MetricFrame."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast

from marivo.analysis_py.errors import (
    CrossBackendMetricError,
    MetricNotFoundError,
    MetricShapeUnsupportedError,
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


def observe(
    metric: str,
    *,
    window: WindowInput = None,
    slice: dict[str, Any] | None = None,
    session: Session | None = None,
) -> MetricFrame:
    if session is None:
        session = session_active()
    ensure_session_writable(session)
    if "." not in metric:
        raise MetricNotFoundError(message=f"metric '{metric}' is not '<model>.<metric>'")
    model_name, metric_name = metric.split(".", 1)
    window_in = normalize_window_input(window)
    resolved_window, original_window, as_of_resolved = _resolve_window(window_in, session=session)
    is_time_series = resolved_window is not None and resolved_window.grain is not None

    from marivo.semantic_py import reader
    from marivo.semantic_py.errors import PySemanticNotFound

    reader.ensure_loaded(project=session.semantic_project)
    try:
        metric_ir = reader.get_metric(model_name, metric_name, project=session.semantic_project)
    except PySemanticNotFound as exc:
        raise MetricNotFoundError(
            message=f"metric '{metric}' not found",
            hint="Check <project_root>/.marivo/semantic/.",
            details={"model": model_name, "metric": metric_name},
        ) from exc

    started_at = datetime.now(UTC)
    started = monotonic()
    dataset_tables: dict[str, Any] = {}
    dataset_irs: dict[str, Any] = {}
    primary_datasource: str | None = None
    stored_slice = normalize_slice_for_storage(slice)
    metric_datasets = tuple(metric_ir.references.datasets)
    if is_time_series and len(metric_datasets) > 1:
        raise MetricShapeUnsupportedError(
            message=(
                f"windowed time_series observe does not support multi-dataset metric '{metric}'"
            ),
            details={
                "kind": "WindowedTimeSeriesUnsupported",
                "metric": metric,
                "datasets": sorted(metric_datasets),
            },
        )

    for dataset_name in metric_datasets:
        dataset_ir = reader.get_dataset(model_name, dataset_name, project=session.semantic_project)
        datasource_name = dataset_ir.datasource_name
        if primary_datasource is None:
            primary_datasource = datasource_name
        elif primary_datasource != datasource_name:
            raise CrossBackendMetricError(
                message=f"metric '{metric}' spans multiple datasources; v1 does not support it",
            )
        backend = session.backend_cache.get_or_create(datasource_name)
        table = dataset_ir.fn(backend)
        table = apply_slice_to_dataset(table, slice, dataset_ir=dataset_ir)
        table = apply_window_to_dataset(
            table,
            resolved_window,
            dataset_ir=dataset_ir,
            session_tz=session.tz,
        )
        dataset_tables[dataset_name] = table
        dataset_irs[dataset_name] = dataset_ir
        session.known_datasources.add(datasource_name)
    _persist_known_datasources(session)

    if primary_datasource is None:
        raise MetricNotFoundError(message=f"metric '{metric}' references no datasets")

    axes: dict[str, Any] = {}
    semantic_kind: Literal["scalar", "time_series", "segmented", "panel"] = "scalar"
    if is_time_series and resolved_window is not None:
        dataset_name = metric_datasets[0]
        dataset_ir = dataset_irs[dataset_name]
        time_field_ir = resolve_window_time_field(dataset_ir, window=resolved_window)
        bucketed_table = apply_time_series_bucket(
            dataset_tables[dataset_name],
            field_ir=time_field_ir,
            window=resolved_window,
            session_tz=session.tz,
        )
        dataset_tables[dataset_name] = bucketed_table
        metric_expr = metric_ir.fn(**dataset_tables)
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
    else:
        metric_expr = metric_ir.fn(**dataset_tables)
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
    params = {"metric": metric, "window": params_window, "slice": stored_slice}
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
        metric_id=metric,
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
