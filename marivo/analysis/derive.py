"""Governed MetricFrame derivation from custom Ibis queries."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, cast

import pandas as pd
from pandas.api.types import is_numeric_dtype

from marivo.analysis.errors import PromotionFailedError
from marivo.analysis.executor.runner import execute
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import (
    persist_frame,
    persist_job_record,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.windows import (
    AbsoluteWindow,
    dump_window,
    make_absolute_window,
    normalize_timescope_input,
)
from marivo.analysis.windows.grain import _TRUNCATE_CODE, parse_grain_token
from marivo.analysis.windows.spec import GrainInput, TimeScopeInput
from marivo.datasource.authoring import DatasourceRef, _require_datasource_ref
from marivo.refs import SemanticRef
from marivo.semantic.catalog import SemanticObject

if TYPE_CHECKING:
    from marivo.analysis.semantic_inputs import MetricInput

SemanticKind = Literal["scalar", "time_series", "segmented", "panel"]


@dataclass(frozen=True)
class DeriveContext:
    metric_id: str
    timescope: dict[str, Any]
    grain: str | None
    label: str | None

    def bucket(self, time_expr: Any) -> Any:
        if self.grain is None:
            return time_expr
        grain = parse_grain_token(self.grain)
        if grain.count == 1:
            if grain.is_day:
                return time_expr.cast("date")
            return time_expr.truncate(_TRUNCATE_CODE[grain.unit])
        width = grain.width_seconds()
        day_start = time_expr.truncate("D")
        offset = ((time_expr.epoch_seconds() - day_start.epoch_seconds()) // width) * width
        return day_start + offset.as_interval("s")


@dataclass(frozen=True)
class IbisQuerySpec:
    datasource: DatasourceRef
    build: Callable[[Any, DeriveContext], Any]

    def __post_init__(self) -> None:
        _require_datasource_ref(self.datasource, argument="mv.ibis_query(datasource=...)")


@dataclass(frozen=True)
class MetricColumnBinding:
    column: str
    ref: SemanticRef | SemanticObject
    role: str


@dataclass(frozen=True)
class MetricColumns:
    value: str
    time: MetricColumnBinding | None = None
    dimensions: tuple[MetricColumnBinding, ...] = ()


def ibis_query(
    *, datasource: DatasourceRef, build: Callable[[Any, DeriveContext], Any]
) -> IbisQuerySpec:
    return IbisQuerySpec(datasource=datasource, build=build)


def time_column(*, column: str, ref: SemanticRef | SemanticObject) -> MetricColumnBinding:
    return MetricColumnBinding(column=column, ref=ref, role="time")


def dimension_column(*, column: str, ref: SemanticRef | SemanticObject) -> MetricColumnBinding:
    return MetricColumnBinding(column=column, ref=ref, role="dimension")


def metric_columns(
    *,
    value: str,
    time: MetricColumnBinding | None = None,
    dimensions: Sequence[MetricColumnBinding] | None = None,
) -> MetricColumns:
    return MetricColumns(value=value, time=time, dimensions=tuple(dimensions or ()))


def _new_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _digest_json(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _semantic_model_from_metric(metric_id: str) -> str:
    return metric_id.split(".", 1)[0]


def _semantic_kind(*, has_time: bool, dimension_count: int) -> SemanticKind:
    if has_time and dimension_count:
        return "panel"
    if has_time:
        return "time_series"
    if dimension_count:
        return "segmented"
    return "scalar"


def _validate_columns(df: pd.DataFrame, *, columns: MetricColumns) -> None:
    required = [columns.value]
    if columns.time is not None:
        required.append(columns.time.column)
    required.extend(binding.column for binding in columns.dimensions)
    missing = [column for column in required if column not in df.columns]
    non_numeric: list[str] = []
    if columns.value in df.columns and not is_numeric_dtype(df[columns.value]):
        non_numeric.append(columns.value)
    if missing or non_numeric:
        raise PromotionFailedError(
            message="cannot derive MetricFrame from query output",
            details={
                "target_kind": "metric_frame",
                "missing": missing,
                "ambiguous": [f"non_numeric:{column}" for column in non_numeric],
                "available_columns": list(map(str, df.columns)),
                "source_refs": [],
            },
        )


def _axis_metadata(
    *,
    session: Session,
    columns: MetricColumns,
    resolved_window: AbsoluteWindow | None,
) -> dict[str, Any]:
    from marivo.analysis.semantic_inputs import normalize_dimension_input

    axes: dict[str, Any] = {}
    if columns.time is not None:
        time_ref = normalize_dimension_input(session.catalog, columns.time.ref, argument="time")
        axes["time"] = {
            "role": "time",
            "column": columns.time.column,
            "ref": time_ref,
        }
        if resolved_window is not None and resolved_window.grain is not None:
            axes["time"]["grain"] = resolved_window.grain.to_token()
        axes["time"]["time_dimension"] = time_ref
    for binding in columns.dimensions:
        dim_ref = normalize_dimension_input(session.catalog, binding.ref, argument="dimension")
        axes[binding.column] = {
            "role": "dimension",
            "column": binding.column,
            "ref": dim_ref,
        }
    return axes


def derive_metric_frame(
    *,
    metric: MetricInput,
    query: IbisQuerySpec,
    columns: MetricColumns,
    time_scope: TimeScopeInput = None,
    grain: GrainInput = None,
    label: str | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> MetricFrame:
    from marivo.analysis.semantic_inputs import normalize_dimension_input, normalize_metric_input

    resolved_session = session if session is not None else require_current_session()
    ensure_session_writable(resolved_session)
    metric_id = normalize_metric_input(resolved_session.catalog, metric)
    scope = normalize_timescope_input(time_scope)
    resolved_window = make_absolute_window(
        scope,
        grain=grain,
        time_dimension=(
            normalize_dimension_input(resolved_session.catalog, columns.time.ref, argument="time")
            if columns.time is not None
            else None
        ),
    )
    context = DeriveContext(
        metric_id=metric_id,
        timescope={"start": scope.start, "end": scope.end} if scope is not None else {},
        grain=resolved_window.grain.to_token()
        if resolved_window and resolved_window.grain
        else None,
        label=label,
    )
    datasource_id = query.datasource.id
    backend = resolved_session._connection_runtime.session_backend(datasource_id)
    expr = query.build(backend, context)
    if not callable(getattr(expr, "compile", None)) and not callable(
        getattr(expr, "to_pandas", None)
    ):
        raise TypeError("derive_metric_frame query build must return an Ibis expression")
    started_at = datetime.now(UTC)
    started = monotonic()
    result = execute(
        expr,
        datasource_name=datasource_id,
        cache=resolved_session._connection_runtime,
        session_id=resolved_session.id,
    )
    df = result.df.copy()
    _validate_columns(df, columns=columns)
    axes = _axis_metadata(
        session=resolved_session, columns=columns, resolved_window=resolved_window
    )
    semantic_kind = _semantic_kind(
        has_time=columns.time is not None,
        dimension_count=len(columns.dimensions),
    )
    params: dict[str, Any] = {
        "metric": metric_id,
        "query": {
            "datasource": datasource_id,
            "sql": result.query.sql if result.query is not None else None,
        },
        "columns": {
            "value": columns.value,
            "time": (
                {
                    "column": columns.time.column,
                    "ref": normalize_dimension_input(
                        resolved_session.catalog, columns.time.ref, argument="time"
                    ),
                }
                if columns.time is not None
                else None
            ),
            "dimensions": [
                {
                    "column": binding.column,
                    "ref": normalize_dimension_input(
                        resolved_session.catalog, binding.ref, argument="dimension"
                    ),
                }
                for binding in columns.dimensions
            ],
        },
        "timescope": {"start": scope.start, "end": scope.end} if scope is not None else None,
        "grain": resolved_window.grain.to_token()
        if resolved_window and resolved_window.grain
        else None,
        "label": label,
    }
    frame_ref = _new_ref("frame")
    job_ref = _new_ref("job")
    finished_at = datetime.now(UTC)
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=frame_ref,
        session_id=resolved_session.id,
        project_root=str(resolved_session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="derive_metric_frame",
                    job_ref=job_ref,
                    inputs=[],
                    params_digest=_digest_json(params),
                    analysis_purpose=analysis_purpose,
                    params=params,
                )
            ]
        ),
        metric_id=metric_id,
        axes=axes,
        measure={"name": columns.value},
        window=dump_window(resolved_window),
        where={},
        semantic_kind=semantic_kind,
        semantic_model=_semantic_model_from_metric(metric_id),
    )
    frame = MetricFrame(_df=df, meta=meta)
    frame.meta = cast(
        "MetricFrameMeta",
        persist_frame(resolved_session, frame),
    )
    persist_job_record(
        resolved_session,
        {
            "id": job_ref,
            "session_id": resolved_session.id,
            "intent": "derive_metric_frame",
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": [],
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(resolved_session.catalog._project.semantic_root),
            "semantic_model": _semantic_model_from_metric(metric_id),
            "queries": [],
        },
    )
    return frame
