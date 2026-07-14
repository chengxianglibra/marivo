"""Family-preserving MetricFrame / DeltaFrame transforms."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import copy
import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any, Literal, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel

from marivo.analysis.delta_math import compute_delta_columns
from marivo.analysis.errors import (
    CrossSessionFrameError,
    SemanticKindMismatchError,
    TransformArgError,
    TransformDimensionNotFoundError,
    TransformShapeUnsupportedError,
    WindowInvalidError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject, TriggeredByFollowup
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._types import SliceValue
from marivo.analysis.intents._validate import require_single_metric
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.semantic_inputs import DimensionInput
from marivo.analysis.semantic_inputs import (
    normalize_dimension_boundary as normalize_catalog_dimension_boundary,
)
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.windows import (
    AbsoluteWindow,
    TimeScopeInput,
    dump_window,
    make_absolute_window,
    normalize_timescope_input,
)
from marivo.semantic.catalog import CatalogObject, SemanticKind, SemanticRef

TransformFrame = MetricFrame | DeltaFrame
RankMethod = Literal["ordinal", "dense", "min", "max"]
NormalizeKind = Literal["index", "share", "pct_change", "per_unit", "z_score"]
NormalizeBaseline = dict[str, str | int | float | bool | None]

_SUPPORTED_OPS: tuple[str, ...] = (
    "filter",
    "slice",
    "rollup",
    "topk",
    "bottomk",
    "rank",
    "normalize",
    "window",
)

_TransformHandlerResult = tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]


def _prepare_transform[TTransformFrame: TransformFrame](
    frame: TTransformFrame,
) -> tuple[Session, TTransformFrame]:
    session = require_current_session()
    ensure_session_writable(session)
    if isinstance(frame, MetricFrame):
        require_single_metric(frame, intent="transform")
    if frame.meta.session_id != session.id:
        raise CrossSessionFrameError(
            message=(
                f"transform input frame belongs to session {frame.meta.session_id!r}, "
                f"not {session.id!r}"
            ),
            context={
                "frame_session": frame.meta.session_id,
                "active_session": session.id,
                "frame_ref": frame.ref,
            },
        )
    return session, frame


def _finish_transform[TTransformFrame: TransformFrame](
    *,
    session: Session,
    parent: TTransformFrame,
    df: pd.DataFrame,
    meta_overrides: dict[str, Any],
    op_params: dict[str, Any],
    started_at: datetime,
    started_monotonic: float,
    analysis_purpose: str | None,
    triggered_by_followup: TriggeredByFollowup | None = None,
) -> TTransformFrame:
    result = cast(
        "TTransformFrame",
        _persist_transform_frame(
            session=session,
            parent=parent,
            df=df,
            params=op_params,
            started_at=started_at,
            started_monotonic=started_monotonic,
            axes=meta_overrides.get("axes"),
            semantic_kind=meta_overrides.get("semantic_kind"),
            where_scope=meta_overrides.get("where"),
            alignment=meta_overrides.get("alignment"),
            normalization=meta_overrides.get("normalization"),
            window=meta_overrides.get("window"),
            analysis_purpose=analysis_purpose,
            triggered_by_followup=triggered_by_followup,
        ),
    )
    coverage_df = meta_overrides.get("coverage_df")
    if coverage_df is not None and isinstance(result, MetricFrame):
        from marivo.analysis.intents.observe import (
            _persist_and_attach_coverage_sidecar,
        )

        job_ref = result.meta.produced_by_job or result.ref
        result = cast(
            "TTransformFrame",
            _persist_and_attach_coverage_sidecar(
                session=session, df=coverage_df, parent=result, job_ref=job_ref
            ),
        )
    return result


def _normalize_dimension_boundary(session: Session, value: DimensionInput, *, argument: str) -> str:
    try:
        return normalize_catalog_dimension_boundary(session.catalog, value, argument=argument)
    except SemanticKindMismatchError as exc:
        ref = exc._context.get("ref", type(value).__name__)
        raise TransformDimensionNotFoundError(
            message=f"transform {argument} dimension {ref!r} is not present",
            hint="Transform dimension refs must resolve to declared catalog dimensions.",
            context={
                "argument": argument,
                "dimension": ref,
                "available_ids": exc._context.get("available_ids", []),
            },
        ) from exc


def _normalize_where_boundary(
    session: Session,
    where: dict[DimensionInput, Any] | None,
) -> dict[str, Any]:
    if where is None:
        return {}
    for key in where:
        if isinstance(key, str):
            raise TransformArgError(
                message="transform slice(slice_by=...) requires catalog dimension refs",
                hint="Pass slice_by={session.catalog.get('dimension.sales.orders.country').ref: 'US'}.",
                context={"expected_kind": "DimensionInput", "got_kind": "str"},
            )
    return {
        _normalize_dimension_boundary(session, key, argument="slice_by"): value
        for key, value in where.items()
    }


def _normalize_drop_axes_boundary(
    session: Session,
    drop_axes: list[DimensionInput] | None,
) -> list[str]:
    if drop_axes is None:
        return []
    for axis in drop_axes:
        if isinstance(axis, str):
            raise TransformArgError(
                message="transform rollup(drop_axes=...) requires catalog dimension refs",
                hint="Pass drop_axes=[session.catalog.get('dimension.sales.orders.country').ref].",
                context={"expected_kind": "DimensionInput", "got_kind": "str"},
            )
    return [
        _normalize_dimension_boundary(session, axis, argument="drop_axes") for axis in drop_axes
    ]


def transform_filter[TTransformFrame: TransformFrame](
    frame: TTransformFrame,
    *,
    predicate: Callable[[pd.DataFrame], pd.Series],
    analysis_purpose: str | None = None,
) -> TTransformFrame:
    session, prepared = _prepare_transform(frame)
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = _op_filter(prepared, predicate=predicate)
    return _finish_transform(
        session=session,
        parent=prepared,
        df=new_df,
        meta_overrides=meta_overrides,
        op_params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        analysis_purpose=analysis_purpose,
    )


def transform_slice[TTransformFrame: TransformFrame](
    frame: TTransformFrame,
    *,
    slice_by: dict[DimensionInput, SliceValue],
    analysis_purpose: str | None = None,
) -> TTransformFrame:
    session, prepared = _prepare_transform(frame)
    where_by_id = _normalize_where_boundary(session, slice_by)
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = _op_slice(prepared, where=where_by_id)
    return _finish_transform(
        session=session,
        parent=prepared,
        df=new_df,
        meta_overrides=meta_overrides,
        op_params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        analysis_purpose=analysis_purpose,
    )


def transform_rollup[TTransformFrame: TransformFrame](
    frame: TTransformFrame,
    *,
    drop_axes: list[DimensionInput] | None = None,
    grain: str | None = None,
    analysis_purpose: str | None = None,
) -> TTransformFrame:
    if drop_axes is None and grain is None:
        raise TransformArgError(
            message="transform(op='rollup') requires at least one of drop_axes= or grain=",
            hint="Pass drop_axes=[...] to drop dimensions, or grain='month' to re-bucket the time axis.",
            context={"op": "rollup", "argument": "drop_axes_or_grain"},
        )
    session, prepared = _prepare_transform(frame)
    drop_axis_ids = (
        _normalize_drop_axes_boundary(session, drop_axes) if drop_axes is not None else None
    )
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = _op_rollup(prepared, drop_axes=drop_axis_ids, grain=grain)
    return _finish_transform(
        session=session,
        parent=prepared,
        df=new_df,
        meta_overrides=meta_overrides,
        op_params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        analysis_purpose=analysis_purpose,
    )


def transform_topk[TTransformFrame: TransformFrame](
    frame: TTransformFrame,
    *,
    by: str,
    limit: int,
    analysis_purpose: str | None = None,
) -> TTransformFrame:
    session, prepared = _prepare_transform(frame)
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = _op_topk(prepared, by=by, limit=limit)
    return _finish_transform(
        session=session,
        parent=prepared,
        df=new_df,
        meta_overrides=meta_overrides,
        op_params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        analysis_purpose=analysis_purpose,
    )


def transform_bottomk[TTransformFrame: TransformFrame](
    frame: TTransformFrame,
    *,
    by: str,
    limit: int,
    analysis_purpose: str | None = None,
) -> TTransformFrame:
    session, prepared = _prepare_transform(frame)
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = _op_bottomk(prepared, by=by, limit=limit)
    return _finish_transform(
        session=session,
        parent=prepared,
        df=new_df,
        meta_overrides=meta_overrides,
        op_params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        analysis_purpose=analysis_purpose,
    )


def transform_rank[TTransformFrame: TransformFrame](
    frame: TTransformFrame,
    *,
    by: str,
    method: RankMethod = "ordinal",
    rank_column: str = "rank",
    analysis_purpose: str | None = None,
) -> TTransformFrame:
    session, prepared = _prepare_transform(frame)
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = _op_rank(
        prepared,
        by=by,
        method=method,
        rank_column=rank_column,
    )
    return _finish_transform(
        session=session,
        parent=prepared,
        df=new_df,
        meta_overrides=meta_overrides,
        op_params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        analysis_purpose=analysis_purpose,
    )


def transform_window[TTransformFrame: TransformFrame](
    frame: TTransformFrame,
    *,
    window: TimeScopeInput,
    analysis_purpose: str | None = None,
) -> TTransformFrame:
    session, prepared = _prepare_transform(frame)
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = _op_window(prepared, window=window, session=session)
    return _finish_transform(
        session=session,
        parent=prepared,
        df=new_df,
        meta_overrides=meta_overrides,
        op_params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        analysis_purpose=analysis_purpose,
    )


def transform_normalize(
    frame: MetricFrame,
    *,
    mode: NormalizeKind,
    baseline: NormalizeBaseline | None = None,
    analysis_purpose: str | None = None,
) -> MetricFrame:
    session, prepared = _prepare_transform(frame)
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = _op_normalize(prepared, mode=mode, baseline=baseline)
    return _finish_transform(
        session=session,
        parent=prepared,
        df=new_df,
        meta_overrides=meta_overrides,
        op_params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        analysis_purpose=analysis_purpose,
    )


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _params_digest(params: dict[str, Any]) -> str:
    normalized = _normalize_param_value(params)
    body = json.dumps(normalized, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _normalize_param_value(value: Any) -> Any:
    if isinstance(value, CatalogObject):
        return {"ref": value.ref.id, "kind": str(value.ref.kind)}
    if isinstance(value, SemanticRef):
        return {"ref": value.id, "kind": str(value.kind)}
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, np.generic):
        return _normalize_param_value(value.item())
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if callable(value):
        module = getattr(value, "__module__", type(value).__module__)
        qualname = getattr(value, "__qualname__", type(value).__qualname__)
        return {"type": "callable", "name": f"{module}.{qualname}"}
    if isinstance(value, dict):
        return {
            str(key): _normalize_param_value(item)
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_param_value(item) for item in value]
    return value


def _axis_names(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, CatalogObject):
        return {value.ref.id}
    if isinstance(value, SemanticRef):
        return {value.id}
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        names: set[str] = set()
        for item in value:
            names.update(_axis_names(item))
        return names
    return set()


def _dimension_input_id(value: DimensionInput) -> str:
    if isinstance(value, CatalogObject):
        if value.ref.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            raise TransformArgError(
                message="transform dimension input requires a dimension or time_dimension object",
                context={"actual_kind": str(value.ref.kind), "ref": value.ref.id},
            )
        return value.ref.id
    if value.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        raise TransformArgError(
            message="transform dimension input requires a dimension or time_dimension ref",
            context={"actual_kind": str(value.kind), "ref": value.id},
        )
    return value.id


def _axis_matches(axis_id: str, axis_meta: Any, requested: str) -> bool:
    candidates = {axis_id, axis_id.rsplit(".", 1)[-1]}
    if isinstance(axis_meta, dict):
        column = axis_meta.get("column")
        ref = axis_meta.get("ref")
        if isinstance(column, str):
            candidates.add(column)
        if isinstance(ref, str):
            candidates.add(ref)
            candidates.add(ref.rsplit(".", 1)[-1])
    return requested in candidates or requested.rsplit(".", 1)[-1] in candidates


def _resolve_axis_id(
    axes: dict[str, Any], requested: str, *, role: str | None = None
) -> str | None:
    for axis_id, axis_meta in axes.items():
        if role is not None and (not isinstance(axis_meta, dict) or axis_meta.get("role") != role):
            continue
        if _axis_matches(str(axis_id), axis_meta, requested):
            return str(axis_id)
    return None


def _recompute_axes(frame: TransformFrame, *, drop_axes: Any = None) -> dict[str, Any]:
    if isinstance(frame, MetricFrame):
        axes = copy.deepcopy(frame.meta.axes)
    else:
        alignment = frame.meta.alignment
        axes = copy.deepcopy(alignment.get("axes", {})) if isinstance(alignment, dict) else {}

    for axis_name in _axis_names(drop_axes):
        axes.pop(axis_name, None)
        for key, axis in list(axes.items()):
            if _axis_matches(str(key), axis, axis_name):
                axes.pop(key, None)
    return axes


def _frame_axes(frame: TransformFrame) -> dict[str, Any]:
    if isinstance(frame, MetricFrame):
        return frame.meta.axes
    alignment = frame.meta.alignment
    axes = alignment.get("axes", {}) if isinstance(alignment, dict) else {}
    return cast("dict[str, Any]", axes) if isinstance(axes, dict) else {}


def _semantic_kind_from_axes(
    axes: dict[str, Any],
) -> Literal["scalar", "time_series", "segmented", "panel"]:
    has_time = False
    has_dimension = False
    for axis in axes.values():
        if not isinstance(axis, dict):
            continue
        role = axis.get("role")
        if role == "time":
            has_time = True
        elif role == "dimension":
            has_dimension = True
    if has_time and has_dimension:
        return "panel"
    if has_time:
        return "time_series"
    if has_dimension:
        return "segmented"
    return "scalar"


def _axis_columns_by_id(axes: dict[str, Any]) -> dict[str, str]:
    columns: dict[str, str] = {}
    for axis_id, axis in axes.items():
        if not isinstance(axis, dict):
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            columns[axis_id] = column
    return columns


def _axis_columns_by_role(frame: TransformFrame, df: pd.DataFrame, role: str) -> list[str]:
    columns: list[str] = []
    for axis in _frame_axes(frame).values():
        if not isinstance(axis, dict) or axis.get("role") != role:
            continue
        column = axis.get("column")
        if isinstance(column, str) and column in df.columns and column not in columns:
            columns.append(column)
    return columns


def _normalize_rollup_drop_axes(frame: TransformFrame, drop_axes: Any) -> set[str]:
    if not isinstance(drop_axes, list) or not drop_axes:
        raise TransformArgError(
            message="transform(op='rollup') requires a non-empty drop_axes list",
            hint='Pass drop_axes=["time"] or drop_axes=[session.catalog.get("dimension.<dimension_id>").ref].',
            context={"op": "rollup", "argument": "drop_axes"},
        )

    axes = _frame_axes(frame)
    drop_ids: set[str] = set()
    for item in drop_axes:
        if isinstance(item, SemanticRef | CatalogObject):
            dimension_id = _dimension_input_id(item)
            axis_id = _resolve_axis_id(axes, dimension_id, role="dimension")
            if axis_id is None:
                raise TransformDimensionNotFoundError(
                    message=f"transform(op='rollup') dimension {dimension_id!r} is not present",
                    hint="Rollup catalog refs must reference existing dimension axes.",
                    context={"op": "rollup", "dimension": dimension_id, "axes": axes},
                )
            drop_ids.add(axis_id)
            continue
        if isinstance(item, str):
            axis_id = _resolve_axis_id(axes, item)
            if axis_id is None:
                raise TransformDimensionNotFoundError(
                    message=f"transform(op='rollup') axis {item!r} is not present",
                    hint="Rollup string targets must match existing axis ids such as 'time'.",
                    context={"op": "rollup", "axis": item, "axes": axes},
                )
            drop_ids.add(axis_id)
            continue
        raise TransformArgError(
            message="transform(op='rollup') drop_axes items must be catalog dimension refs or str",
            hint='Pass drop_axes=["time"] or drop_axes=[session.catalog.get("dimension.<dimension_id>").ref].',
            context={
                "op": "rollup",
                "argument": "drop_axes",
                "actual_item_type": type(item).__name__,
            },
        )

    if drop_ids == set(axes):
        raise TransformShapeUnsupportedError(
            message="transform(op='rollup') cannot drop every axis",
            hint="Keep at least one time or dimension axis in the rollup output.",
            context={"op": "rollup", "drop_axes": sorted(drop_ids), "axes": axes},
        )
    return drop_ids


def _rollup_measure_columns(
    frame: TransformFrame, df: pd.DataFrame, axis_columns: set[str]
) -> list[str]:
    measure_columns = [column for column in df.columns if column not in axis_columns]
    if not isinstance(frame, DeltaFrame):
        return measure_columns
    return [
        column
        for column in measure_columns
        if column != "pct_change" and pd.api.types.is_numeric_dtype(df[column])
    ]


def _recompute_delta_pct_change(df: pd.DataFrame) -> None:
    if "current" not in df.columns or "baseline" not in df.columns:
        return
    compute_delta_columns(df)


def _primary_normalize_column(frame: TransformFrame, df: pd.DataFrame) -> str:
    axis_columns = set(_axis_columns_by_id(_frame_axes(frame)).values())
    measure_columns = [
        str(column)
        for column in df.columns
        if isinstance(column, str) and column not in axis_columns
    ]
    if isinstance(frame, MetricFrame):
        # Canonical "value" column takes priority.
        if "value" in df.columns and "value" not in axis_columns:
            return "value"
        declared_column = frame.meta.measure.get("column")
        if declared_column is not None:
            if not isinstance(declared_column, str) or declared_column not in df.columns:
                raise TransformArgError(
                    message="transform(op='normalize') metric measure metadata is invalid",
                    hint="MetricFrameMeta.measure['column'] must name a persisted measure column.",
                    context={
                        "op": "normalize",
                        "measure_column": declared_column,
                        "columns": list(df.columns),
                    },
                )
            if declared_column in axis_columns:
                raise TransformArgError(
                    message="transform(op='normalize') metric measure column cannot be an axis",
                    hint="MetricFrameMeta.measure['column'] must name a non-axis measure column.",
                    context={
                        "op": "normalize",
                        "measure_column": declared_column,
                        "axis_columns": sorted(axis_columns),
                    },
                )
            return declared_column
        declared_name = frame.meta.measure.get("name")
        if declared_name is not None:
            if (
                isinstance(declared_name, str)
                and declared_name in df.columns
                and declared_name not in axis_columns
            ):
                return declared_name
            raise TransformArgError(
                message="transform(op='normalize') metric measure metadata is invalid",
                hint=(
                    "MetricFrameMeta.measure['name'] must match a persisted non-axis measure "
                    "column when measure['column'] is absent."
                ),
                context={
                    "op": "normalize",
                    "measure_name": declared_name,
                    "axis_columns": sorted(axis_columns),
                    "columns": list(df.columns),
                },
            )
        raise TransformArgError(
            message="transform(op='normalize') requires explicit metric measure metadata",
            hint="MetricFrameMeta.measure must include 'column' or a 'name' matching a df column.",
            context={
                "op": "normalize",
                "measure": frame.meta.measure,
                "axis_columns": sorted(axis_columns),
                "columns": list(df.columns),
            },
        )
    if isinstance(frame, DeltaFrame) and "delta" in measure_columns:
        return "delta"
    for column in measure_columns:
        if pd.api.types.is_numeric_dtype(df[column]):
            return column
    raise TransformShapeUnsupportedError(
        message="transform(op='normalize') found no numeric measure column",
        hint="Normalize requires a numeric non-axis measure column.",
        context={
            "op": "normalize",
            "axis_columns": sorted(axis_columns),
            "columns": list(df.columns),
        },
    )


def _finite_non_zero(value: float) -> bool:
    return bool(np.isfinite(value) and value != 0)


def _coerce_normalize_number(value: Any, *, argument: str, mode: str) -> float:
    if isinstance(value, bool):
        raise TransformArgError(
            message=f"transform(op='normalize') {argument} must be numeric",
            hint="Use an int or float value for normalize baseline values.",
            context={"op": "normalize", "mode": mode, "argument": argument},
        )
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise TransformArgError(
            message=f"transform(op='normalize') {argument} must be numeric",
            hint="Use an int or float value for normalize baseline values.",
            context={
                "op": "normalize",
                "mode": mode,
                "argument": argument,
                "actual_type": type(value).__name__,
            },
        ) from exc
    return numeric


def _resolve_normalize_base(
    df: pd.DataFrame,
    *,
    column: str,
    baseline: Any,
    mode: str,
) -> float:
    if baseline is None:
        if mode == "per_unit":
            raise TransformArgError(
                message="transform(op='normalize', mode='per_unit') requires baseline",
                hint="Pass baseline={'value': 100} or baseline={axis_column: axis_value}.",
                context={"op": "normalize", "mode": mode, "argument": "baseline"},
            )
        if df.empty:
            raise TransformShapeUnsupportedError(
                message="transform(op='normalize', mode='index') requires at least one row",
                hint="Normalize index uses the first row as the default baseline.",
                context={"op": "normalize", "mode": mode},
            )
        return _coerce_normalize_number(df[column].iloc[0], argument="baseline", mode=mode)

    if not isinstance(baseline, dict) or not baseline:
        raise TransformArgError(
            message="transform(op='normalize') baseline must be a non-empty dict",
            hint="Pass baseline={'value': 100} or baseline={axis_column: axis_value}.",
            context={"op": "normalize", "mode": mode, "argument": "baseline"},
        )

    if set(baseline) == {"value"}:
        return _coerce_normalize_number(baseline["value"], argument="baseline.value", mode=mode)

    missing_columns = [key for key in baseline if key not in df.columns]
    if missing_columns:
        raise TransformArgError(
            message="transform(op='normalize') baseline selector references missing columns",
            hint="Baseline selector keys must match persisted frame columns.",
            context={
                "op": "normalize",
                "mode": mode,
                "argument": "baseline",
                "missing_columns": missing_columns,
            },
        )

    mask = pd.Series(True, index=df.index)
    for key, value in baseline.items():
        mask &= df[key] == value
    matches = df[mask]
    if matches.empty:
        raise TransformArgError(
            message="transform(op='normalize') baseline selector matched no rows",
            hint="Choose baseline selector values that identify at least one persisted frame row.",
            context={"op": "normalize", "mode": mode, "argument": "baseline", "baseline": baseline},
        )
    return _coerce_normalize_number(matches[column].iloc[0], argument="baseline", mode=mode)


def _sort_for_series_order(
    df: pd.DataFrame,
    *,
    group_columns: list[str],
    time_columns: list[str],
) -> pd.DataFrame:
    sort_columns = [*group_columns, *time_columns]
    if not sort_columns:
        return df
    return df.sort_values(sort_columns, kind="mergesort")


def _reject_invalid_normalize_denominator(
    values: pd.Series,
    *,
    mask: pd.Series,
    mode: str,
    column: str,
    message: str,
    hint: str,
) -> None:
    denominator_values = pd.to_numeric(values, errors="coerce")
    finite_denominator = pd.Series(
        np.isfinite(denominator_values.to_numpy(dtype=float, na_value=np.nan)),
        index=values.index,
    )
    invalid_denominator = mask & (
        denominator_values.isna() | (denominator_values == 0) | ~finite_denominator
    )
    if bool(invalid_denominator.any()):
        raise TransformArgError(
            message=message,
            hint=hint,
            context={
                "op": "normalize",
                "mode": mode,
                "column": column,
                "invalid_row_count": int(invalid_denominator.sum()),
            },
        )


def _resolve_grouped_normalize_base(
    df: pd.DataFrame,
    *,
    column: str,
    baseline: Any,
    mode: str,
    group_columns: list[str],
    time_columns: list[str],
) -> pd.Series:
    if not group_columns:
        base_value = _resolve_normalize_base(df, column=column, baseline=baseline, mode=mode)
        return pd.Series(base_value, index=df.index)

    if baseline is None:
        ordered = _sort_for_series_order(df, group_columns=group_columns, time_columns=time_columns)
        base_values = ordered.groupby(group_columns, dropna=False)[column].transform("first")
        return base_values.reindex(df.index)

    if not isinstance(baseline, dict) or not baseline:
        raise TransformArgError(
            message="transform(op='normalize') baseline must be a non-empty dict",
            hint="Pass baseline={'value': 100} or baseline={axis_column: axis_value}.",
            context={"op": "normalize", "mode": mode, "argument": "baseline"},
        )

    if set(baseline) == {"value"}:
        base_value = _coerce_normalize_number(
            baseline["value"], argument="baseline.value", mode=mode
        )
        return pd.Series(base_value, index=df.index)

    missing_columns = [key for key in baseline if key not in df.columns]
    if missing_columns:
        raise TransformArgError(
            message="transform(op='normalize') baseline selector references missing columns",
            hint="Baseline selector keys must match persisted frame columns.",
            context={
                "op": "normalize",
                "mode": mode,
                "argument": "baseline",
                "missing_columns": missing_columns,
            },
        )

    grouped_selector_columns = [key for key in baseline if key in group_columns]
    if grouped_selector_columns:
        raise TransformArgError(
            message="transform(op='normalize') grouped baseline selector must not include group columns",
            hint="Select the baseline row within each series, for example with a time column.",
            context={
                "op": "normalize",
                "mode": mode,
                "argument": "baseline",
                "group_columns": grouped_selector_columns,
            },
        )

    mask = pd.Series(True, index=df.index)
    for key, value in baseline.items():
        mask &= df[key] == value
    matches = df[mask]
    if matches.empty:
        raise TransformArgError(
            message="transform(op='normalize') baseline selector matched no rows",
            hint="Choose baseline selector values that identify at least one persisted frame row.",
            context={"op": "normalize", "mode": mode, "argument": "baseline", "baseline": baseline},
        )

    grouped_matches = matches.groupby(group_columns, dropna=False)[column].first().reset_index()
    grouped_matches = grouped_matches.rename(columns={column: "__normalize_baseline"})
    merged = df[group_columns].merge(grouped_matches, on=group_columns, how="left")
    base_values = pd.Series(merged["__normalize_baseline"].to_numpy(), index=df.index)
    missing_groups = base_values.isna()
    if bool(missing_groups.any()):
        raise TransformArgError(
            message="transform(op='normalize') baseline selector matched no rows for some groups",
            hint="Choose selector values that identify a baseline row in every dimension group.",
            context={
                "op": "normalize",
                "mode": mode,
                "argument": "baseline",
                "missing_group_count": int(missing_groups.sum()),
            },
        )
    return base_values


def _pct_change_series(frame: TransformFrame, df: pd.DataFrame, column: str) -> pd.Series:
    group_columns = _axis_columns_by_role(frame, df, "dimension")
    time_columns = _axis_columns_by_role(frame, df, "time")
    if not time_columns:
        raise TransformShapeUnsupportedError(
            message="transform(op='normalize', mode='pct_change') requires a time axis",
            hint="Use pct_change only on time_series or panel frames with a persisted time axis.",
            context={
                "op": "normalize",
                "mode": "pct_change",
                "required_axis": "time",
                "axes": _frame_axes(frame),
            },
        )
    ordered = _sort_for_series_order(df, group_columns=group_columns, time_columns=time_columns)
    if group_columns:
        grouped = ordered.groupby(group_columns, dropna=False)[column]
        denominator = grouped.shift(1)
        computed_rows = ordered.groupby(group_columns, dropna=False).cumcount() > 0
    else:
        denominator = ordered[column].shift(1)
        computed_rows = pd.Series(range(len(ordered)), index=ordered.index) > 0

    _reject_invalid_normalize_denominator(
        denominator,
        mask=computed_rows,
        mode="pct_change",
        column=column,
        message=(
            "transform(op='normalize', mode='pct_change') denominator values "
            "must be finite and non-zero"
        ),
        hint="Remove or impute zero, NaN, inf, and -inf previous values before pct_change.",
    )

    pct_change = (ordered[column] - denominator) / denominator
    return pct_change.mask(~computed_rows, np.nan).reindex(df.index)


def _resolve_transform_window(raw_window: Any, *, session: Session) -> AbsoluteWindow:
    timescope = normalize_timescope_input(raw_window)
    if timescope is None:
        raise TransformArgError(
            message="transform(op='window') requires window",
            hint='Pass window={"start": "2026-07-01", "end": "2026-08-01"}.',
            context={"op": "window", "argument": "window"},
        )
    window = make_absolute_window(timescope)
    if window is None:
        raise TransformArgError(
            message="transform(op='window') requires window",
            hint='Pass window={"start": "2026-07-01", "end": "2026-08-01"}.',
            context={"op": "window", "argument": "window"},
        )
    return window


def _window_time_axis(frame: TransformFrame, df: pd.DataFrame) -> dict[str, Any]:
    if isinstance(frame, MetricFrame):
        axes = frame.meta.axes
    else:
        alignment = frame.meta.alignment
        axes = alignment.get("axes", {}) if isinstance(alignment, dict) else {}
    if isinstance(axes, dict):
        for axis in axes.values():
            if not isinstance(axis, dict) or axis.get("role") != "time":
                continue
            column = axis.get("column")
            if isinstance(column, str) and column:
                return axis

    if (
        isinstance(frame, DeltaFrame)
        and frame.meta.semantic_kind == "time_series"
        and "bucket_start" in df.columns
    ):
        return {"role": "time", "column": "bucket_start"}

    raise TransformShapeUnsupportedError(
        message="transform(op='window') requires a time axis",
        hint="Use window only on time_series or panel frames with a persisted time axis.",
        context={"op": "window", "required_axis": "time", "axes": axes},
    )


def _coerce_window_bound(value: str, *, bound_name: str) -> pd.Timestamp:
    try:
        bound = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise WindowInvalidError(
            message=f"window.{bound_name}={value!r} is not a valid ISO-8601 date/datetime",
            context={"kind": "WindowBoundInvalid", "bound": bound_name, "value": value},
        ) from exc
    if pd.isna(bound):
        raise WindowInvalidError(
            message=f"window.{bound_name}={value!r} is not a valid ISO-8601 date/datetime",
            context={"kind": "WindowBoundInvalid", "bound": bound_name, "value": value},
        )
    return bound


def _window_comparison_tz(window: AbsoluteWindow, *, session: Session) -> Any:
    return session.report_tz


def _align_window_bound_to_series(
    bound: pd.Timestamp, *, series: pd.Series, comparison_tz: Any
) -> pd.Timestamp:
    series_tz = series.dt.tz
    if series_tz is not None:
        if bound.tzinfo is None:
            return bound.tz_localize(comparison_tz).tz_convert(series_tz)
        return bound.tz_convert(series_tz)
    if bound.tzinfo is not None:
        return bound.tz_convert(comparison_tz).tz_localize(None)
    return bound


def _op_window(
    frame: TransformFrame,
    *,
    window: TimeScopeInput,
    session: Session,
) -> _TransformHandlerResult:
    resolved_window = _resolve_transform_window(window, session=session)
    df = frame.to_pandas()
    time_axis = _window_time_axis(frame, df)
    time_column = time_axis.get("column")
    if not isinstance(time_column, str) or time_column not in df.columns:
        raise TransformDimensionNotFoundError(
            message="transform(op='window') time axis column is not present",
            hint="Window can only filter by a persisted time axis column.",
            context={"op": "window", "time_axis": time_axis, "columns": list(df.columns)},
        )

    start = _coerce_window_bound(resolved_window.start, bound_name="start")
    end = _coerce_window_bound(resolved_window.end, bound_name="end")
    series = pd.to_datetime(df[time_column], errors="raise")
    comparison_tz = _window_comparison_tz(resolved_window, session=session)
    start = _align_window_bound_to_series(start, series=series, comparison_tz=comparison_tz)
    end = _align_window_bound_to_series(end, series=series, comparison_tz=comparison_tz)
    if start >= end:
        raise TransformArgError(
            message="transform(op='window') requires window.start before window.end",
            hint="Pass explicit start/end bounds with start before end.",
            context={
                "op": "window",
                "kind": "WindowEmptyRange",
                "start": resolved_window.start,
                "end": resolved_window.end,
            },
        )
    try:
        mask = (series >= start) & (series < end)
    except (TypeError, ValueError) as exc:
        raise TransformArgError(
            message="transform(op='window') time axis comparison is invalid",
            hint="Use window bounds with timezone awareness compatible with the time axis column.",
            context={
                "op": "window",
                "kind": "WindowTimeComparisonInvalid",
                "time_column": time_column,
                "window": dump_window(resolved_window),
            },
        ) from exc
    new_window_dump = dump_window(resolved_window)
    if new_window_dump is None:
        raise TransformArgError(
            message="transform(op='window') requires window",
            hint='Pass window={"start": "2026-07-01", "end": "2026-08-01"}.',
            context={"op": "window", "argument": "window"},
        )

    if isinstance(frame, MetricFrame):
        new_window_dump["chained_from"] = copy.deepcopy(frame.meta.window)
        meta_overrides = {"window": new_window_dump}
    else:
        alignment = copy.deepcopy(frame.meta.alignment)
        new_window_dump["chained_from"] = copy.deepcopy(alignment.get("window"))
        alignment["window"] = new_window_dump
        meta_overrides = {"alignment": alignment}

    return (
        df[mask].reset_index(drop=True),
        meta_overrides,
        {"op": "window", "window": new_window_dump},
    )


def _op_normalize(
    frame: MetricFrame,
    *,
    mode: NormalizeKind,
    baseline: NormalizeBaseline | None = None,
) -> _TransformHandlerResult:
    if not isinstance(mode, str):
        raise TransformArgError(
            message="transform(op='normalize') requires mode",
            hint="Pass mode='index', 'share', 'pct_change', 'per_unit', or 'z_score'.",
            context={"op": "normalize", "argument": "mode"},
        )

    if isinstance(frame, DeltaFrame):
        raise TransformArgError(
            message="transform(op='normalize') is not supported for DeltaFrame in v1",
            hint=(
                "Normalize MetricFrame inputs before compare, or use DeltaFrame transforms that "
                "preserve current, baseline, delta, and pct_change together."
            ),
            context={
                "op": "normalize",
                "mode": mode,
                "supported_modes": [],
                "frame_kind": frame.meta.kind,
            },
        )

    metric_modes = {"index", "share", "pct_change", "per_unit", "z_score"}
    if mode not in metric_modes:
        raise TransformArgError(
            message=f"transform(op='normalize') mode {mode!r} is not supported for this frame",
            hint=(
                "MetricFrame normalize supports index, share, pct_change, per_unit, and z_score. "
                "DeltaFrame normalize is rejected in v1."
            ),
            context={
                "op": "normalize",
                "mode": mode,
                "supported_modes": sorted(metric_modes),
                "frame_kind": frame.meta.kind,
            },
        )

    if mode in {"share", "pct_change", "z_score"} and baseline is not None:
        raise TransformArgError(
            message=f"transform(op='normalize', mode={mode!r}) does not accept baseline",
            hint="Use baseline only with mode='index' or mode='per_unit'.",
            context={"op": "normalize", "mode": mode, "unsupported_kwargs": ["baseline"]},
        )

    df = frame.to_pandas()
    column = _primary_normalize_column(frame, df)
    new_df = df.copy()
    dimension_group_columns = _axis_columns_by_role(frame, new_df, "dimension")
    time_columns = _axis_columns_by_role(frame, new_df, "time")
    if not pd.api.types.is_numeric_dtype(new_df[column]):
        raise TransformShapeUnsupportedError(
            message=f"transform(op='normalize') column {column!r} is not numeric",
            hint="Normalize requires a numeric non-axis measure column.",
            context={"op": "normalize", "mode": mode, "column": column},
        )

    if mode == "index":
        base_values = _resolve_grouped_normalize_base(
            df,
            column=column,
            baseline=baseline,
            mode=mode,
            group_columns=dimension_group_columns,
            time_columns=time_columns,
        )
        _reject_invalid_normalize_denominator(
            base_values,
            mask=pd.Series(True, index=new_df.index),
            mode=mode,
            column=column,
            message="transform(op='normalize', mode='index') baseline must be finite and non-zero",
            hint="Choose a non-zero baseline row or pass baseline={'value': number}.",
        )
        new_df[column] = new_df[column] / base_values * 100
    elif mode == "share":
        if time_columns and dimension_group_columns:
            totals = new_df.groupby(time_columns, dropna=False)[column].transform("sum")
            _reject_invalid_normalize_denominator(
                totals,
                mask=pd.Series(True, index=new_df.index),
                mode=mode,
                column=column,
                message=(
                    "transform(op='normalize', mode='share') time bucket sums must be finite "
                    "and non-zero"
                ),
                hint="Normalize panel share requires a non-zero total measure value per time bucket.",
            )
            new_df[column] = new_df[column] / totals
        else:
            total = _coerce_normalize_number(new_df[column].sum(), argument="sum", mode=mode)
            if not _finite_non_zero(total):
                raise TransformArgError(
                    message="transform(op='normalize', mode='share') sum must be finite and non-zero",
                    hint="Normalize share requires a non-zero total measure value.",
                    context={"op": "normalize", "mode": mode, "column": column},
                )
            new_df[column] = new_df[column] / total
    elif mode == "pct_change":
        new_df[column] = _pct_change_series(frame, new_df, column)
    elif mode == "per_unit":
        base_value = _resolve_normalize_base(df, column=column, baseline=baseline, mode=mode)
        if not _finite_non_zero(base_value):
            raise TransformArgError(
                message=(
                    "transform(op='normalize', mode='per_unit') baseline must be finite and non-zero"
                ),
                hint="Choose a non-zero baseline row or pass baseline={'value': number}.",
                context={"op": "normalize", "mode": mode, "argument": "baseline"},
            )
        new_df[column] = new_df[column] / base_value
    else:
        if dimension_group_columns:
            means = new_df.groupby(dimension_group_columns, dropna=False)[column].transform("mean")
            stds = new_df.groupby(dimension_group_columns, dropna=False)[column].transform(
                lambda values: values.std(ddof=0)
            )
            _reject_invalid_normalize_denominator(
                stds,
                mask=pd.Series(True, index=new_df.index),
                mode=mode,
                column=column,
                message=(
                    "transform(op='normalize', mode='z_score') group std must be finite "
                    "and non-zero"
                ),
                hint=(
                    "Normalize grouped z_score requires at least two non-identical measure "
                    "values per dimension group."
                ),
            )
            new_df[column] = (new_df[column] - means) / stds
        else:
            mean = _coerce_normalize_number(new_df[column].mean(), argument="mean", mode=mode)
            std = _coerce_normalize_number(new_df[column].std(ddof=0), argument="std", mode=mode)
            if not _finite_non_zero(std):
                raise TransformArgError(
                    message=(
                        "transform(op='normalize', mode='z_score') std must be finite and non-zero"
                    ),
                    hint="Normalize z_score requires at least two non-identical measure values.",
                    context={"op": "normalize", "mode": mode, "column": column},
                )
            new_df[column] = (new_df[column] - mean) / std

    normalized_baseline = _normalize_param_value(baseline) if baseline is not None else None
    normalization = {
        "mode": mode,
        "baseline": normalized_baseline,
        "columns_affected": [column],
    }
    return (
        new_df,
        {"normalization": normalization},
        {"op": "normalize", "mode": mode, "baseline": baseline, "column": column},
    )


def _resolve_slice_column(
    frame: TransformFrame, df: pd.DataFrame, key: Any
) -> tuple[str, str | None]:
    if isinstance(key, SemanticRef | CatalogObject):
        axes = _frame_axes(frame)
        dimension_id = _dimension_input_id(key)
        axis_id = _resolve_axis_id(axes, dimension_id, role="dimension")
        axis = axes.get(axis_id) if axis_id is not None else None
        if not isinstance(axis, dict) or axis.get("role") != "dimension":
            raise TransformDimensionNotFoundError(
                message=f"transform(op='slice') dimension {dimension_id!r} is not present",
                hint="Slice catalog ref keys must reference existing dimension axes.",
                context={"op": "slice", "dimension": dimension_id},
            )
        column = axis.get("column")
        if not isinstance(column, str) or column not in df.columns:
            raise TransformDimensionNotFoundError(
                message=f"transform(op='slice') dimension {dimension_id!r} column is not present",
                hint="Slice catalog ref keys must reference persisted frame columns.",
                context={"op": "slice", "dimension": dimension_id, "column": column},
            )
        return column, dimension_id
    if isinstance(key, str):
        axes = _frame_axes(frame)
        dimension_key_id: str | None = None
        matched_axis_id = _resolve_axis_id(axes, key)
        axis_columns = {
            column
            for axis in axes.values()
            if isinstance(axis, dict)
            for column in [axis.get("column")]
            if isinstance(column, str) and column
        }
        for axis_name, axis in axes.items():
            if not isinstance(axis, dict) or str(axis_name) != matched_axis_id:
                continue
            if axis.get("role") == "dimension":
                dimension_key_id = key if "." in key else str(axis_name)
            break
        if matched_axis_id is None:
            raise TransformDimensionNotFoundError(
                message=f"transform(op='slice') column {key!r} is not an axis column",
                hint="Slice string keys must match an existing time or dimension axis column.",
                context={
                    "op": "slice",
                    "column": key,
                    "axis_columns": sorted(axis_columns),
                    "axes": axes,
                },
            )
        axis = axes[matched_axis_id]
        column = axis.get("column") if isinstance(axis, dict) else None
        if not isinstance(column, str):
            column = key
        if key not in df.columns:
            if column in df.columns:
                return column, dimension_key_id
            raise TransformDimensionNotFoundError(
                message=f"transform(op='slice') axis column {column!r} is not present",
                hint="Slice string keys must reference persisted frame axis columns.",
                context={"op": "slice", "column": column, "axes": axes},
            )
        return key, dimension_key_id
    raise TransformArgError(
        message="transform(op='slice') slice_by keys must be catalog dimension refs or str",
        hint=(
            'Use slice_by={session.catalog.get("dimension.<dimension_id>").ref: "US"} '
            "or slice_by={'value': (10, 20)}."
        ),
        context={"op": "slice", "actual_key_type": type(key).__name__},
    )


def _series_supports_range_slice(series: pd.Series) -> bool:
    if pd.api.types.is_bool_dtype(series.dtype):
        return False
    if pd.api.types.is_numeric_dtype(series.dtype):
        return True
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        return True
    if pd.api.types.is_timedelta64_dtype(series.dtype):
        return True
    non_null = series.dropna()
    if non_null.empty:
        return False
    return all(isinstance(value, (date, datetime, pd.Timestamp)) for value in non_null)


def _op_filter(
    frame: TransformFrame,
    *,
    predicate: Callable[[pd.DataFrame], pd.Series],
) -> _TransformHandlerResult:
    if not callable(predicate):
        raise TransformArgError(
            message="transform(op='filter') requires a callable predicate",
            hint="Pass predicate=lambda df: ... returning a boolean pandas Series.",
            context={"op": "filter", "argument": "predicate"},
        )

    df = frame.to_pandas()
    mask = predicate(df)
    if not isinstance(mask, pd.Series):
        raise TransformArgError(
            message="transform(op='filter') predicate must return a pandas Series",
            hint="Return a boolean Series with one value per input row.",
            context={
                "op": "filter",
                "argument": "predicate",
                "actual_type": type(mask).__name__,
            },
        )
    if not mask.index.equals(df.index):
        raise TransformArgError(
            message="transform(op='filter') predicate mask index alignment is invalid",
            hint="Return a boolean Series with the same index as the input DataFrame.",
            context={"op": "filter", "argument": "predicate"},
        )
    if len(mask) != len(df):
        raise TransformArgError(
            message="transform(op='filter') predicate returned a mask with the wrong length",
            hint="Return a boolean Series with one value per input row.",
            context={
                "op": "filter",
                "expected_length": len(df),
                "actual_length": len(mask),
            },
        )
    if not pd.api.types.is_bool_dtype(mask.dtype):
        raise TransformArgError(
            message="transform(op='filter') predicate mask must be boolean-like",
            hint="Return expressions such as df['column'] > value, not filtered data.",
            context={"op": "filter", "actual_dtype": str(mask.dtype)},
        )

    return df[mask].reset_index(drop=True), {}, {"op": "filter", "predicate": predicate}


def _ordered_take(
    frame: TransformFrame,
    *,
    by: str,
    limit: int,
    ascending: bool,
    op_name: str,
) -> _TransformHandlerResult:
    if not isinstance(by, str):
        raise TransformArgError(
            message=f"transform(op='{op_name}') requires by to be a column name",
            hint=f"Pass by='value' or another persisted frame column for {op_name}.",
            context={
                "op": op_name,
                "argument": "by",
                "actual_type": type(by).__name__,
            },
        )

    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise TransformArgError(
            message=f"transform(op='{op_name}') requires a positive integer limit",
            hint=f"Pass limit=10 or another positive integer for {op_name}.",
            context={"op": op_name, "argument": "limit", "limit": limit},
        )

    df = frame.to_pandas()
    if by not in df.columns:
        raise TransformArgError(
            message=f"transform(op='{op_name}') by column {by!r} is not present",
            hint=f"Choose one of the persisted frame columns: {', '.join(map(str, df.columns))}.",
            context={"op": op_name, "argument": "by", "by": by, "columns": list(df.columns)},
        )

    sorted_df = (
        df.sort_values(by=by, ascending=ascending, na_position="last")
        .head(limit)
        .reset_index(drop=True)
    )
    return (
        sorted_df,
        {},
        {"op": op_name, "by": by, "limit": limit},
    )


def _op_topk(frame: TransformFrame, *, by: str, limit: int) -> _TransformHandlerResult:
    return _ordered_take(frame, by=by, limit=limit, ascending=False, op_name="topk")


def _op_bottomk(frame: TransformFrame, *, by: str, limit: int) -> _TransformHandlerResult:
    return _ordered_take(frame, by=by, limit=limit, ascending=True, op_name="bottomk")


def _op_rank(
    frame: TransformFrame,
    *,
    by: str,
    method: RankMethod = "ordinal",
    rank_column: str = "rank",
) -> _TransformHandlerResult:
    if not isinstance(by, str):
        raise TransformArgError(
            message="transform(op='rank') requires by to be a column name",
            hint="Pass by='value' or another persisted frame column for rank.",
            context={"op": "rank", "argument": "by", "actual_type": type(by).__name__},
        )

    method_map: dict[str, Literal["dense", "first", "min", "max"]] = {
        "dense": "dense",
        "ordinal": "first",
        "min": "min",
        "max": "max",
    }
    pandas_method = method_map.get(method)
    if pandas_method is None:
        raise TransformArgError(
            message=f"transform(op='rank') method {method!r} is not supported",
            hint="Use method='ordinal', 'dense', 'min', or 'max'.",
            context={
                "op": "rank",
                "argument": "method",
                "method": method,
                "supported_methods": sorted(method_map),
            },
        )

    if not isinstance(rank_column, str) or not rank_column:
        raise TransformArgError(
            message="transform(op='rank') rank_column must be a non-empty string",
            hint="Pass rank_column='rank' or another new output column name.",
            context={
                "op": "rank",
                "argument": "rank_column",
                "actual_type": type(rank_column).__name__,
            },
        )

    df = frame.to_pandas()
    if by not in df.columns:
        raise TransformArgError(
            message=f"transform(op='rank') by column {by!r} is not present",
            hint=f"Choose one of the persisted frame columns: {', '.join(map(str, df.columns))}.",
            context={"op": "rank", "argument": "by", "by": by, "columns": list(df.columns)},
        )
    if rank_column in df.columns:
        raise TransformArgError(
            message=f"transform(op='rank') rank_column {rank_column!r} already exists",
            hint="Choose a new rank_column name that does not overwrite an existing column.",
            context={"op": "rank", "argument": "rank_column", "rank_column": rank_column},
        )

    by_values = df[by]
    null_count = int(by_values.isna().sum())
    non_finite_count = 0
    if pd.api.types.is_numeric_dtype(by_values):
        finite_mask = np.isfinite(by_values.to_numpy(dtype=float, na_value=np.nan))
        non_finite_count = int((~finite_mask).sum()) - null_count
    if null_count > 0 or non_finite_count > 0:
        raise TransformArgError(
            message=(f"transform(op='rank') by column {by!r} contains null or non-finite values"),
            hint="Remove or impute null, NaN, inf, and -inf values before ranking.",
            context={
                "op": "rank",
                "argument": "by",
                "by": by,
                "null_count": null_count,
                "non_finite_count": non_finite_count,
            },
        )

    ranked = df.copy()
    ranked[rank_column] = ranked[by].rank(method=pandas_method, ascending=False).astype(int)
    return (
        ranked,
        {},
        {"op": "rank", "by": by, "method": method, "rank_column": rank_column},
    )


def _op_rollup(
    frame: TransformFrame,
    *,
    drop_axes: list[str] | None,
    grain: str | None,
) -> _TransformHandlerResult:
    reaggregatable = getattr(frame.meta, "reaggregatable", True)
    rollup_fold = getattr(frame.meta, "rollup_fold", None)
    if reaggregatable is False and rollup_fold is None:
        # v1 rejection verbatim for non-reaggregatable frames without rollup_fold.
        raise TransformShapeUnsupportedError(
            message="transform(op='rollup') cannot roll up non-reaggregatable metric values",
            hint="Re-run session.observe(...) at the target grain or target dimensions.",
            context={"op": "rollup", "reason": "non_reaggregatable", "frame_ref": frame.ref},
        )

    if grain is not None:
        return _op_rollup_grain(frame, grain=grain, rollup_fold=rollup_fold)

    if not drop_axes:
        raise TransformArgError(
            message="transform(op='rollup') requires a non-empty drop_axes list",
            hint='Pass drop_axes=[session.catalog.get("dimension.<dimension_id>").ref].',
            context={"op": "rollup", "argument": "drop_axes"},
        )
    drop_ids = _normalize_rollup_drop_axes(frame, drop_axes)
    axes = copy.deepcopy(_frame_axes(frame))
    new_axes = {axis_id: axis for axis_id, axis in axes.items() if axis_id not in drop_ids}
    axis_columns = _axis_columns_by_id(axes)
    remaining_axis_columns = [
        column for axis_id, column in axis_columns.items() if axis_id not in drop_ids
    ]

    df = frame.to_pandas()
    missing_columns = [column for column in remaining_axis_columns if column not in df.columns]
    if missing_columns:
        raise TransformDimensionNotFoundError(
            message="transform(op='rollup') remaining axis columns are not present",
            hint="Rollup can only group by persisted frame axis columns.",
            context={"op": "rollup", "missing_columns": missing_columns, "axes": axes},
        )

    all_axis_columns = set(axis_columns.values())
    measure_columns = _rollup_measure_columns(frame, df, all_axis_columns)
    if not measure_columns:
        raise TransformShapeUnsupportedError(
            message="transform(op='rollup') found no measure columns to aggregate",
            hint="Rollup requires at least one non-axis measure column.",
            context={"op": "rollup", "axes": axes},
        )

    new_df = (
        df.groupby(remaining_axis_columns, as_index=False, dropna=False)[measure_columns]
        .sum(min_count=1)
        .reset_index(drop=True)
    )
    if isinstance(frame, DeltaFrame):
        _recompute_delta_pct_change(new_df)
    new_kind = _semantic_kind_from_axes(new_axes)
    return (
        new_df,
        {"axes": new_axes, "semantic_kind": new_kind},
        {"op": "rollup", "drop_axes": sorted(drop_ids)},
    )


# Supported target grains for rollup re-bucketing, ordered finest-to-coarsest.
_ROLLUP_GRAIN_RANK: dict[str, int] = {
    "hour": 0,
    "day": 1,
    "week": 2,
    "month": 3,
    "quarter": 4,
    "year": 5,
}


def _require_target_grain_compatible(frame: TransformFrame, target_grain: str) -> None:
    """Validate that ``target_grain`` is a legal re-bucketing target for ``frame``.

    The target must be a supported rollup grain AND strictly coarser than the
    frame's current time-axis grain. For cumulative frames anchored to a
    ``grain_to_date`` reset, the grain-compatibility rule applies: a week target
    under a month/quarter/year reset is illegal because week buckets straddle
    those reset-period boundaries.
    """
    if target_grain not in _ROLLUP_GRAIN_RANK:
        raise TransformArgError(
            message=(f"transform(op='rollup') unsupported grain {target_grain!r}"),
            hint=("Supported rollup grains: hour, day, week, month, quarter, year."),
            context={"op": "rollup", "argument": "grain", "grain": target_grain},
        )
    time_axis = _window_time_axis(frame, frame.to_pandas())
    current_grain = time_axis.get("grain") if isinstance(time_axis, dict) else None
    if current_grain not in _ROLLUP_GRAIN_RANK:
        raise TransformShapeUnsupportedError(
            message=(
                "transform(op='rollup') grain= requires a time axis with a "
                f"supported grain; got {current_grain!r}"
            ),
            hint="Rollup grain re-bucketing needs an hour/day/week/month/quarter/year time axis.",
            context={"op": "rollup", "time_axis_grain": current_grain},
        )
    if _ROLLUP_GRAIN_RANK[target_grain] <= _ROLLUP_GRAIN_RANK[current_grain]:
        raise TransformArgError(
            message=(
                f"transform(op='rollup') target grain {target_grain!r} must be "
                f"coarser than the current time-axis grain {current_grain!r}"
            ),
            hint="Pick a target grain strictly coarser than the frame's existing time grain.",
            context={
                "op": "rollup",
                "argument": "grain",
                "target_grain": target_grain,
                "current_grain": current_grain,
            },
        )
    # Grain-compatibility rule for cumulative grain_to_date frames: a week
    # target under a month/quarter/year reset is illegal (week buckets straddle
    # those boundaries), mirroring observe's _require_grain_to_date_compat.
    if target_grain == "week":
        cumulative = getattr(frame.meta, "cumulative", None)
        if isinstance(cumulative, dict):
            anchor = cumulative.get("anchor")
            # anchor may be a tuple ("grain_to_date", grain) or a list form
            # after model_dump round-trips; accept both.
            if isinstance(anchor, (tuple, list)) and len(anchor) == 2:
                anchor_kind, reset_grain = anchor[0], anchor[1]
                if anchor_kind == "grain_to_date" and reset_grain in ("month", "quarter", "year"):
                    raise TransformShapeUnsupportedError(
                        message=(
                            f"transform(op='rollup') grain={target_grain!r} is "
                            f"incompatible with grain_to_date(reset={reset_grain!r}): "
                            f"week buckets straddle {reset_grain} boundaries."
                        ),
                        hint="Use day or hour target grain, or grain_to_date(grain='week') for a week reset.",
                        context={
                            "op": "rollup",
                            "reason": "grain_incompatible",
                            "target_grain": target_grain,
                            "reset_grain": reset_grain,
                        },
                    )


def _trunc_to_grain_tz(values: pd.Series, grain: str) -> pd.Series:
    """Truncate a timestamp Series to the start of the target grain period.

    Mirrors observe's ``_trunc_series_to_grain`` but returns timezone-naive
    period-start timestamps suitable for the rollup group key.
    """
    ts = pd.to_datetime(pd.Series(values))
    if grain == "hour":
        return ts.dt.floor("h")
    if grain == "day":
        return ts.dt.floor("D")
    if grain == "week":
        return ts.dt.to_period("W").dt.start_time
    if grain == "month":
        return pd.Series(
            ts.values.astype(np.dtype("datetime64[M]")).astype(np.dtype("datetime64[s]")),
            index=ts.index,
            name=ts.name,
        )
    if grain == "quarter":
        month = ts.dt.month
        quarter_start_month = ((month - 1) // 3) * 3 + 1
        return pd.to_datetime(
            pd.DataFrame({"year": ts.dt.year, "month": quarter_start_month, "day": 1})
        )
    if grain == "year":
        return pd.to_datetime(pd.DataFrame({"year": ts.dt.year, "month": 1, "day": 1}))
    raise ValueError(f"unsupported rollup grain for truncation: {grain!r}")


def _rollup_grain_coverage_df(
    *,
    new_df: pd.DataFrame,
    time_col: str,
    parent_df: pd.DataFrame,
    target_grain: str,
    frame: TransformFrame,
) -> pd.DataFrame | None:
    """Build a time_slot coverage sidecar for a grain rollup.

    Each rolled period is ``complete`` except the final one, which is ``partial``
    when the parent display window ends before that period's end boundary. When
    the parent frame has no window end (e.g. scalar), returns ``None``.
    """
    window = getattr(frame.meta, "window", None)
    end_value: Any = None
    if isinstance(window, dict):
        end_value = window.get("end")
    if end_value is None:
        return None
    try:
        window_end = pd.Timestamp(end_value)
    except (TypeError, ValueError):
        return None

    period_starts = sorted(new_df[time_col].dropna().unique().tolist())
    if not period_starts:
        return None
    rows = []
    for idx, start in enumerate(period_starts):
        is_last = idx == len(period_starts) - 1
        period_end = _grain_period_end(pd.Timestamp(start), target_grain)
        status = "complete"
        if is_last and window_end < period_end:
            status = "partial"
        rows.append({time_col: pd.Timestamp(start), "coverage_status": status})
    return pd.DataFrame(rows)


def _grain_period_end(start: pd.Timestamp, grain: str) -> pd.Timestamp:
    """Return the exclusive end boundary of the grain period beginning at *start*."""
    if grain == "hour":
        return start + pd.Timedelta(hours=1)
    if grain == "day":
        return start + pd.Timedelta(days=1)
    if grain == "week":
        return start + pd.Timedelta(weeks=1)
    if grain == "month":
        return (start + pd.DateOffset(months=1)).normalize()
    if grain == "quarter":
        return (start + pd.DateOffset(months=3)).normalize()
    if grain == "year":
        return (start + pd.DateOffset(years=1)).normalize()
    raise ValueError(f"unsupported rollup grain for period end: {grain!r}")


def _op_rollup_grain(
    frame: TransformFrame,
    *,
    grain: str,
    rollup_fold: str | None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Re-bucket the time axis to ``grain`` and aggregate per fold.

    - ``rollup_fold == "last"`` (cumulative): take the last bucket per period
      per dims (period-end running total), preserving the cumulative marker
      and ``rollup_fold`` so chains keep the fold.
    - otherwise (reaggregatable additive): sum measures per period.
    """
    _require_target_grain_compatible(frame, grain)

    df = frame.to_pandas()
    time_axis = _window_time_axis(frame, df)
    time_col = time_axis["column"]
    dims = [
        axis["column"]
        for axis in _frame_axes(frame).values()
        if isinstance(axis, dict)
        and axis.get("role") == "dimension"
        and isinstance(axis.get("column"), str)
    ]
    dims = [c for c in dims if c in df.columns]

    df = df.copy()
    df["_target_period"] = _trunc_to_grain_tz(df[time_col], grain)
    group_keys = [*dims, "_target_period"]

    if rollup_fold == "last":
        new_df = (
            df.sort_values([*dims, time_col])
            .groupby(group_keys, as_index=False, dropna=False)
            .last()
            .drop(columns=["_target_period"])
        )
        # Restore the time column name as the period-start bucket. The .last()
        # above keeps the original time_col values; replace with the period
        # start so the rolled frame's time axis is the target grain.
        new_df[time_col] = _trunc_to_grain_tz(new_df[time_col], grain).values
        fold_meta: dict[str, Any] = {"rollup_fold": "last"}
    else:
        axis_columns = set(_axis_columns_by_id(_frame_axes(frame)).values())
        measure_columns = _rollup_measure_columns(frame, df, axis_columns | {"_target_period"})
        new_df = (
            df.groupby(group_keys, as_index=False, dropna=False)[measure_columns]
            .sum(min_count=1)
            .rename(columns={"_target_period": time_col})
        )
        if isinstance(frame, DeltaFrame):
            _recompute_delta_pct_change(new_df)
        fold_meta = {}

    new_df = new_df.reset_index(drop=True)

    # Re-bucketed axes: update the time axis grain to the target.
    new_axes = copy.deepcopy(_frame_axes(frame))
    for axis in new_axes.values():
        if isinstance(axis, dict) and axis.get("role") == "time":
            axis["grain"] = grain
            axis["column"] = time_col
    new_kind = _semantic_kind_from_axes(new_axes)

    op_params: dict[str, Any] = {"op": "rollup", "grain": grain}
    op_params.update(fold_meta)
    meta_overrides: dict[str, Any] = {"axes": new_axes, "semantic_kind": new_kind}
    coverage_df = _rollup_grain_coverage_df(
        new_df=new_df,
        time_col=time_col,
        parent_df=df,
        target_grain=grain,
        frame=frame,
    )
    if coverage_df is not None and not coverage_df.empty:
        meta_overrides["coverage_df"] = coverage_df
    return new_df, meta_overrides, op_params


def _op_slice(
    frame: TransformFrame,
    *,
    where: dict[str, Any],
) -> _TransformHandlerResult:
    if not where:
        raise TransformArgError(
            message="transform(op='slice') requires a non-empty slice_by dict",
            hint='Pass slice_by={session.catalog.get("dimension.<dimension_id>").ref: "US"}.',
            context={"op": "slice", "argument": "slice_by"},
        )

    df = frame.to_pandas()
    mask = pd.Series(True, index=df.index)
    locked_single_value_dims: list[tuple[str, str, Any]] = []

    for key, value in where.items():
        column, dimension_id = _resolve_slice_column(frame, df, key)
        series = df[column]
        if isinstance(value, tuple):
            if len(value) != 2:
                raise TransformArgError(
                    message=(
                        "transform(op='slice') tuple values must be length-2 inclusive ranges"
                    ),
                    hint="Use a list for membership or a (lo, hi) tuple for range slicing.",
                    context={"op": "slice", "column": column},
                )
            if not _series_supports_range_slice(series):
                raise TransformArgError(
                    message=(
                        "transform(op='slice') range tuple values require a numeric "
                        "or date/time axis column"
                    ),
                    hint="Use a list for membership predicates on dimension columns.",
                    context={"op": "slice", "column": column},
                )
            lo, hi = value
            try:
                clause = series.between(lo, hi, inclusive="both")
            except (TypeError, ValueError) as exc:
                raise TransformArgError(
                    message="transform(op='slice') range tuple bounds are not comparable",
                    hint="Use bounds with the same numeric or date/time type as the axis column.",
                    context={"op": "slice", "column": column},
                ) from exc
        elif isinstance(value, list):
            clause = series.isin(list(value))
        elif isinstance(value, set):
            raise TransformArgError(
                message="transform(op='slice') does not accept set values",
                hint="Use a list for membership predicates so params persist deterministically.",
                context={"op": "slice", "column": column},
            )
        elif isinstance(value, dict):
            raise TransformArgError(
                message="transform(op='slice') slice_by values must be scalar, list, or range tuple",
                hint="Use a scalar for equality, a list for membership, or a (lo, hi) tuple range.",
                context={
                    "op": "slice",
                    "column": column,
                    "actual_value_type": type(value).__name__,
                },
            )
        else:
            clause = series == value
            if dimension_id is not None:
                selector_value = _normalize_param_value(value)
                locked_single_value_dims.append((dimension_id, column, selector_value))
        mask &= clause

    new_df = df[mask].reset_index(drop=True)
    meta_overrides: dict[str, Any] = {}
    if locked_single_value_dims:
        drop_axes = [dimension for dimension, _, _ in locked_single_value_dims]
        new_axes = _recompute_axes(frame, drop_axes=drop_axes)
        drop_columns = [
            column for _, column, _ in locked_single_value_dims if column in new_df.columns
        ]
        if drop_columns:
            new_df = new_df.drop(columns=drop_columns)
        selector = {dimension: value for dimension, _, value in locked_single_value_dims}
        meta_overrides = {
            "axes": new_axes,
            "semantic_kind": _semantic_kind_from_axes(new_axes),
        }
        if isinstance(frame, MetricFrame):
            meta_overrides["where"] = {
                **copy.deepcopy(frame.meta.where),
                **selector,
            }
        else:
            alignment = copy.deepcopy(frame.meta.alignment)
            existing_where = alignment.get("where", {})
            if not isinstance(existing_where, dict):
                existing_where = {}
            alignment["where"] = {**existing_where, **selector}
            meta_overrides["alignment"] = alignment

    return new_df, meta_overrides, {"op": "slice", "where": where}


def _persist_transform_frame(
    *,
    session: Session,
    parent: TransformFrame,
    df: pd.DataFrame,
    params: dict[str, Any],
    started_at: datetime,
    started_monotonic: float,
    axes: dict[str, Any] | None = None,
    semantic_kind: str | None = None,
    where_scope: dict[str, Any] | None = None,
    alignment: dict[str, Any] | None = None,
    normalization: dict[str, Any] | None = None,
    window: dict[str, Any] | None = None,
    analysis_purpose: str | None = None,
    triggered_by_followup: TriggeredByFollowup | None = None,
) -> MetricFrame | DeltaFrame:
    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    finished_at = datetime.now(UTC)
    source_refs = [parent.ref]
    normalized_params = _normalize_param_value(params)
    lineage = Lineage(
        steps=[
            *parent.lineage.steps,
            LineageStep(
                intent="transform",
                job_ref=job_ref,
                inputs=source_refs,
                params_digest=_params_digest(normalized_params),
                analysis_purpose=analysis_purpose,
            ),
        ],
        external_inputs=list(parent.lineage.external_inputs),
    )
    meta_payload = parent.meta.model_dump()
    meta_payload.update(
        {
            "ref": frame_ref,
            "session_id": session.id,
            "project_root": str(session.project_root),
            "produced_by_job": job_ref,
            "analysis_purpose": analysis_purpose,
            "created_at": finished_at,
            "row_count": len(df),
            "byte_size": 0,
            "lineage": lineage,
        }
    )
    # Transforms change data shape; component links from the parent no longer apply.
    meta_payload["component_ref"] = None
    meta_payload["composition"] = None
    if semantic_kind is not None:
        meta_payload["semantic_kind"] = semantic_kind
    if where_scope is not None and isinstance(parent, MetricFrame):
        meta_payload["where"] = where_scope
    if alignment is not None and isinstance(parent, DeltaFrame):
        meta_payload["alignment"] = alignment
    if normalization is not None:
        meta_payload["normalization"] = normalization
    if window is not None and isinstance(parent, MetricFrame):
        meta_payload["window"] = window
    if axes is not None and isinstance(parent, MetricFrame):
        meta_payload["axes"] = axes
    elif axes is not None and isinstance(parent, DeltaFrame):
        alignment_payload = copy.deepcopy(meta_payload["alignment"])
        alignment_payload["axes"] = axes
        meta_payload["alignment"] = alignment_payload

    if isinstance(parent, MetricFrame):
        metric_meta = MetricFrameMeta(**meta_payload)
        frame: MetricFrame | DeltaFrame = MetricFrame(_df=df.copy(), meta=metric_meta)
        grain = None
        time_axis = metric_meta.axes.get("time")
        if isinstance(time_axis, dict):
            axis_grain = time_axis.get("grain")
            grain = axis_grain if axis_grain in ("hour", "day", "week", "month") else None
        frame = cast(
            "MetricFrame",
            commit_result(
                store=session._evidence_store(),
                frames_dir=session._layout.frames_dir,
                frame=frame,
                step_type="transform",
                inputs=CommitInputs(input_refs=[parent.meta.artifact_id or parent.ref]),
                params=CommitParams(values=normalized_params),
                semantic_anchors=CommitSemanticAnchors(values={"metric_id": metric_meta.metric_id}),
                subject=Subject(
                    metric=metric_meta.metric_id,
                    slice=metric_meta.where,
                    grain=grain,
                    analysis_axis=_analysis_axis_for_metric_kind(metric_meta.semantic_kind),
                ),
                extractor_family="metric_frame",
                triggered_by_followup=triggered_by_followup,
                emit_evidence=False,
            ),
        )
        register_frame_artifact(session, frame)
    else:
        delta_meta = DeltaFrameMeta(**meta_payload)
        frame = DeltaFrame(_df=df.copy(), meta=delta_meta)
        frame = cast(
            "DeltaFrame",
            commit_result(
                store=session._evidence_store(),
                frames_dir=session._layout.frames_dir,
                frame=frame,
                step_type="transform",
                inputs=CommitInputs(input_refs=[parent.meta.artifact_id or parent.ref]),
                params=CommitParams(values=normalized_params),
                semantic_anchors=CommitSemanticAnchors(values={"metric_id": delta_meta.metric_id}),
                subject=Subject(metric=delta_meta.metric_id, analysis_axis="change"),
                extractor_family="delta_frame",
                triggered_by_followup=triggered_by_followup,
                emit_evidence=False,
            ),
        )
        register_frame_artifact(session, frame)

    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "transform",
            "analysis_purpose": analysis_purpose,
            "params": normalized_params,
            "input_frame_refs": source_refs,
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started_monotonic) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog.semantic_root),
            "semantic_model": parent.meta.semantic_model,
        },
    )
    return frame


def _analysis_axis_for_metric_kind(
    semantic_kind: str,
) -> Literal["scalar", "time", "segment", "panel"]:
    if semantic_kind == "time_series":
        return "time"
    if semantic_kind == "segmented":
        return "segment"
    if semantic_kind == "panel":
        return "panel"
    return "scalar"
