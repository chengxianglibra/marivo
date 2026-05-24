"""Materialize a semantic_py metric into a MetricFrame."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from time import monotonic
from typing import Any, cast

from marivo.analysis_py.errors import (
    CrossBackendMetricError,
    MetricNotFoundError,
)
from marivo.analysis_py.executor.runner import (
    apply_slice_to_dataset,
    apply_window_to_dataset,
    execute,
    normalize_slice_for_storage,
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


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _params_digest(params: dict[str, Any]) -> str:
    body = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def observe(
    metric: str,
    *,
    window: dict[str, Any] | None = None,
    slice: dict[str, Any] | None = None,
    session: Session | None = None,
) -> MetricFrame:
    if session is None:
        session = session_active()
    ensure_session_writable(session)
    if "." not in metric:
        raise MetricNotFoundError(message=f"metric '{metric}' is not '<model>.<metric>'")
    model_name, metric_name = metric.split(".", 1)

    from marivo.semantic_py import reader

    try:
        reader.ensure_loaded(project=session.semantic_project)
        metric_ir = reader.get_metric(model_name, metric_name, project=session.semantic_project)
    except Exception as exc:
        raise MetricNotFoundError(
            message=f"metric '{metric}' not found",
            hint="Check <project_root>/.marivo/semantic/.",
            details={"model": model_name, "metric": metric_name},
        ) from exc

    started_at = datetime.now(UTC)
    started = monotonic()
    dataset_tables: dict[str, Any] = {}
    primary_datasource: str | None = None
    stored_slice = normalize_slice_for_storage(slice)

    for dataset_name in metric_ir.references.datasets:
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
        table = apply_window_to_dataset(table, window, dataset_ir=dataset_ir)
        dataset_tables[dataset_name] = table
        session.known_datasources.add(datasource_name)
    _persist_known_datasources(session)

    if primary_datasource is None:
        raise MetricNotFoundError(message=f"metric '{metric}' references no datasets")

    expr = metric_ir.fn(**dataset_tables)
    result = execute(expr, datasource_name=primary_datasource, cache=session.backend_cache)
    finished_at = datetime.now(UTC)

    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    params = {"metric": metric, "window": window, "slice": stored_slice}
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
        axes={},
        measure={"name": metric_name},
        window=dict(window) if window else None,
        slice=stored_slice,
        semantic_kind="time_series" if window else "scalar",
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
