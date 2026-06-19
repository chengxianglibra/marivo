"""Family-preserving MetricFrame / DeltaFrame transforms."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import copy
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any, Literal, TypeGuard, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel

from marivo.analysis.delta_math import compute_delta_columns
from marivo.analysis.errors import (
    CrossSessionFrameError,
    SemanticKindMismatchError,
    TransformArgError,
    TransformDimensionNotFoundError,
    TransformOpUnsupportedError,
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
    dump_window,
    make_absolute_window,
    normalize_timescope_input,
)
from marivo.semantic.catalog import SemanticKind, SemanticObject, SemanticRef

TransformOp = Literal["filter", "slice", "rollup", "topk", "bottomk", "rank", "normalize", "window"]
TransformFrame = MetricFrame | DeltaFrame
TopKDirection = Literal["increase", "decrease"]
RankMethod = Literal["ordinal", "dense", "min", "max"]
NormalizeKind = Literal["index", "share", "pct_change", "per_unit", "z_score"]

_SUPPORTED_OPS: tuple[TransformOp, ...] = (
    "filter",
    "slice",
    "rollup",
    "topk",
    "bottomk",
    "rank",
    "normalize",
    "window",
)


@dataclass(frozen=True)
class _TransformParams:
    op: TransformOp
    session: Session
    where: Any
    predicate: Any
    drop_axes: Any
    by: Any
    limit: int | None
    order: str | None
    method: str
    rank_column: str
    mode: str | None
    baseline: Any
    window: Any


_TransformHandlerResult = tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]
_TransformHandler = Callable[[TransformFrame, _TransformParams], _TransformHandlerResult]
_OP_DISPATCH: dict[TransformOp, _TransformHandler] = {}


def _is_supported_op(op: str) -> TypeGuard[TransformOp]:
    return op in _SUPPORTED_OPS


def _transform_dispatch(
    frame: object,
    *,
    op: TransformOp | str,
    session: Session | None = None,
    where: Any = None,
    predicate: Any = None,
    drop_axes: Any = None,
    by: Any = None,
    limit: int | None = None,
    order: str | None = None,
    method: str = "ordinal",
    rank_column: str = "rank",
    mode: str | None = None,
    baseline: Any = None,
    window: Any = None,
    _triggered_by: TriggeredByFollowup | None = None,
) -> MetricFrame | DeltaFrame:
    """Family-preserving reshape of a MetricFrame or DeltaFrame.

    When to use: reshape a frame without changing its family; prefer typed sub-methods (session.transform.topk, etc.).

    The operator preserves the frame family: MetricFrame → MetricFrame and
    DeltaFrame → DeltaFrame. Each ``op`` consumes a subset of the kwargs below;
    pass only those listed for the chosen op.

    Args:
        frame: A MetricFrame or DeltaFrame to reshape.
        op: One of:

            - ``filter``: row filter on a derived ``predicate``.
            - ``slice``: row filter on raw axis values; pass ``where``.
            - ``rollup``: aggregate to coarser segments; pass ``by`` and
              optional ``drop_axes``.
            - ``topk`` / ``bottomk``: keep best/worst N rows; pass ``limit``,
              optional ``order``.
            - ``rank``: add a rank column; pass ``method``, ``rank_column``.
            - ``normalize``: convert metric values to a share (MetricFrame only
              in v1); pass ``baseline``.
            - ``window``: re-bucket along time; pass ``window``.
        session: Defaults to the currently-attached session.
        where: ``op="slice"`` — mapping of axis → value or predicate dict.
        predicate: ``op="filter"`` — a derived boolean expression.
        drop_axes: ``op="rollup"`` — axes to drop after aggregation.
        by: ``op="rollup"`` — axes to retain.
        limit: ``op="topk"``/``"bottomk"`` — number of rows.
        order: ``op="topk"``/``"bottomk"`` — ``"increase"`` or ``"decrease"``.
        method: ``op="rank"`` — ``"ordinal"`` (default) or other supported method.
        rank_column: ``op="rank"`` — output column name.
        mode: ``op="normalize"`` — normalization mode.
        baseline: ``op="normalize"`` — baseline/reference for normalization.
        window: ``op="window"`` — new window spec.

    Raises:
        TransformOpUnsupportedError: ``frame`` is not a MetricFrame/DeltaFrame, or
            ``op`` is unknown / not implemented in v1.
        TransformArgError: Kwargs are missing or invalid for the chosen ``op``.
        TransformDimensionNotFoundError: A referenced axis is not in ``frame``.
        TransformShapeUnsupportedError: The frame shape does not support ``op``.
        WindowInvalidError: ``window`` argument is malformed.
        CrossSessionFrameError: ``frame`` belongs to a different session.

    Example:
        >>> top = session.transform.topk(delta, by="delta", limit=10, order="decrease")
        >>> top.summary()
    """

    if session is None:
        session = require_current_session()
    ensure_session_writable(session)

    if not isinstance(frame, (MetricFrame, DeltaFrame)):
        raise TransformOpUnsupportedError(
            message=(
                "transform v1 accepts only MetricFrame and DeltaFrame inputs, "
                f"got {type(frame).__name__}"
            ),
            details={"expected_families": ["MetricFrame", "DeltaFrame"]},
        )

    if frame.meta.session_id != session.id:
        raise CrossSessionFrameError(
            message=(
                f"transform input frame belongs to session {frame.meta.session_id!r}, "
                f"not {session.id!r}"
            ),
            details={
                "frame_session": frame.meta.session_id,
                "active_session": session.id,
                "frame_ref": frame.ref,
            },
        )

    if not _is_supported_op(op):
        raise TransformOpUnsupportedError(
            message=f"unknown transform op {op!r}",
            hint=f"Supported transform ops: {', '.join(_SUPPORTED_OPS)}.",
            details={"op": op, "supported_ops": list(_SUPPORTED_OPS)},
        )

    transform_op = op
    handler = _OP_DISPATCH.get(transform_op)
    if handler is None:
        raise TransformOpUnsupportedError(
            message=f"op '{transform_op}' is declared but not implemented in v1",
            details={"op": transform_op},
        )

    params = _TransformParams(
        op=transform_op,
        session=session,
        where=where,
        predicate=predicate,
        drop_axes=drop_axes,
        by=by,
        limit=limit,
        order=order,
        method=method,
        rank_column=rank_column,
        mode=mode,
        baseline=baseline,
        window=window,
    )
    started_at = datetime.now(UTC)
    started_monotonic = monotonic()
    new_df, meta_overrides, op_params = handler(frame, params)
    return _persist_transform_frame(
        session=session,
        parent=frame,
        df=new_df,
        params=op_params,
        started_at=started_at,
        started_monotonic=started_monotonic,
        axes=meta_overrides.get("axes"),
        semantic_kind=meta_overrides.get("semantic_kind"),
        where_scope=meta_overrides.get("where"),
        alignment=meta_overrides.get("alignment"),
        normalization=meta_overrides.get("normalization"),
        window=meta_overrides.get("window"),
        triggered_by_followup=_triggered_by,
    )


def _normalize_dimension_boundary(session: Session, value: DimensionInput, *, argument: str) -> str:
    try:
        return normalize_catalog_dimension_boundary(session.catalog, value, argument=argument)
    except SemanticKindMismatchError as exc:
        ref = exc.details.get("ref", type(value).__name__)
        raise TransformDimensionNotFoundError(
            message=f"transform {argument} dimension {ref!r} is not present",
            hint="Transform dimension refs must resolve to declared catalog dimensions.",
            details={
                "argument": argument,
                "dimension": ref,
                "available_ids": exc.details.get("available_ids", []),
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
                message="transform slice(where=...) requires catalog dimension refs",
                hint="Pass where={session.catalog.get('sales.orders.country').ref: 'US'}.",
                details={"expected_kind": "DimensionInput", "got_kind": "str"},
            )
    return {
        _normalize_dimension_boundary(session, key, argument="where"): value
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
                hint="Pass drop_axes=[session.catalog.get('sales.orders.country').ref].",
                details={"expected_kind": "DimensionInput", "got_kind": "str"},
            )
    return [
        _normalize_dimension_boundary(session, axis, argument="drop_axes") for axis in drop_axes
    ]


class TransformAPI:
    """Callable namespace for family-preserving MetricFrame / DeltaFrame transforms."""

    def filter(
        self,
        frame: object,
        *,
        predicate: Callable[[pd.DataFrame], pd.Series],
        session: Session | None = None,
    ) -> MetricFrame | DeltaFrame:
        return _transform_dispatch(frame, op="filter", predicate=predicate, session=session)

    def slice(
        self,
        frame: object,
        *,
        where: dict[DimensionInput, Any],
        session: Session | None = None,
    ) -> MetricFrame | DeltaFrame:
        resolved_session = session if session is not None else require_current_session()
        where_by_id = _normalize_where_boundary(resolved_session, where)
        return _transform_dispatch(frame, op="slice", where=where_by_id, session=resolved_session)

    def rollup(
        self,
        frame: object,
        *,
        drop_axes: list[DimensionInput],
        session: Session | None = None,
    ) -> MetricFrame | DeltaFrame:
        resolved_session = session if session is not None else require_current_session()
        drop_axis_ids = _normalize_drop_axes_boundary(resolved_session, drop_axes)
        return _transform_dispatch(
            frame,
            op="rollup",
            drop_axes=drop_axis_ids,
            session=resolved_session,
        )

    def topk(
        self,
        frame: object,
        *,
        by: str,
        limit: int,
        order: TopKDirection | None = None,
        session: Session | None = None,
    ) -> MetricFrame | DeltaFrame:
        return _transform_dispatch(
            frame,
            op="topk",
            by=by,
            limit=limit,
            order=order,
            session=session,
        )

    def bottomk(
        self,
        frame: object,
        *,
        by: str,
        limit: int,
        session: Session | None = None,
    ) -> MetricFrame | DeltaFrame:
        return _transform_dispatch(frame, op="bottomk", by=by, limit=limit, session=session)

    def rank(
        self,
        frame: object,
        *,
        by: str,
        method: RankMethod = "ordinal",
        rank_column: str = "rank",
        session: Session | None = None,
    ) -> MetricFrame | DeltaFrame:
        return _transform_dispatch(
            frame,
            op="rank",
            by=by,
            method=method,
            rank_column=rank_column,
            session=session,
        )

    def normalize(
        self,
        frame: MetricFrame,
        *,
        mode: NormalizeKind,
        baseline: Any = None,
        session: Session | None = None,
    ) -> MetricFrame:
        result = _transform_dispatch(
            frame,
            op="normalize",
            mode=mode,
            baseline=baseline,
            session=session,
        )
        return cast("MetricFrame", result)

    def window(
        self,
        frame: object,
        *,
        window: Any,
        session: Session | None = None,
    ) -> MetricFrame | DeltaFrame:
        return _transform_dispatch(frame, op="window", window=window, session=session)


transform = TransformAPI()


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _params_digest(params: dict[str, Any]) -> str:
    normalized = _normalize_param_value(params)
    body = json.dumps(normalized, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _normalize_param_value(value: Any) -> Any:
    if isinstance(value, SemanticObject):
        return {"ref": value.ref.id, "kind": str(value.kind)}
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
    if isinstance(value, SemanticObject):
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
    if isinstance(value, SemanticObject):
        if value.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
            raise TransformArgError(
                message="transform dimension input requires a dimension or time_dimension object",
                details={"actual_kind": str(value.kind), "ref": value.ref.id},
            )
        return value.ref.id
    if value.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        raise TransformArgError(
            message="transform dimension input requires a dimension or time_dimension ref",
            details={"actual_kind": str(value.kind), "ref": value.id},
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
            hint='Pass drop_axes=["time"] or drop_axes=[session.catalog.get("<dimension_id>").ref].',
            details={"op": "rollup", "argument": "drop_axes"},
        )

    axes = _frame_axes(frame)
    drop_ids: set[str] = set()
    for item in drop_axes:
        if isinstance(item, SemanticRef | SemanticObject):
            dimension_id = _dimension_input_id(item)
            axis_id = _resolve_axis_id(axes, dimension_id, role="dimension")
            if axis_id is None:
                raise TransformDimensionNotFoundError(
                    message=f"transform(op='rollup') dimension {dimension_id!r} is not present",
                    hint="Rollup catalog refs must reference existing dimension axes.",
                    details={"op": "rollup", "dimension": dimension_id, "axes": axes},
                )
            drop_ids.add(axis_id)
            continue
        if isinstance(item, str):
            axis_id = _resolve_axis_id(axes, item)
            if axis_id is None:
                raise TransformDimensionNotFoundError(
                    message=f"transform(op='rollup') axis {item!r} is not present",
                    hint="Rollup string targets must match existing axis ids such as 'time'.",
                    details={"op": "rollup", "axis": item, "axes": axes},
                )
            drop_ids.add(axis_id)
            continue
        raise TransformArgError(
            message="transform(op='rollup') drop_axes items must be catalog dimension refs or str",
            hint='Pass drop_axes=["time"] or drop_axes=[session.catalog.get("<dimension_id>").ref].',
            details={
                "op": "rollup",
                "argument": "drop_axes",
                "actual_item_type": type(item).__name__,
            },
        )

    if drop_ids == set(axes):
        raise TransformShapeUnsupportedError(
            message="transform(op='rollup') cannot drop every axis",
            hint="Keep at least one time or dimension axis in the rollup output.",
            details={"op": "rollup", "drop_axes": sorted(drop_ids), "axes": axes},
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
                    details={
                        "op": "normalize",
                        "measure_column": declared_column,
                        "columns": list(df.columns),
                    },
                )
            if declared_column in axis_columns:
                raise TransformArgError(
                    message="transform(op='normalize') metric measure column cannot be an axis",
                    hint="MetricFrameMeta.measure['column'] must name a non-axis measure column.",
                    details={
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
                details={
                    "op": "normalize",
                    "measure_name": declared_name,
                    "axis_columns": sorted(axis_columns),
                    "columns": list(df.columns),
                },
            )
        raise TransformArgError(
            message="transform(op='normalize') requires explicit metric measure metadata",
            hint="MetricFrameMeta.measure must include 'column' or a 'name' matching a df column.",
            details={
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
        details={
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
            details={"op": "normalize", "mode": mode, "argument": argument},
        )
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise TransformArgError(
            message=f"transform(op='normalize') {argument} must be numeric",
            hint="Use an int or float value for normalize baseline values.",
            details={
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
                details={"op": "normalize", "mode": mode, "argument": "baseline"},
            )
        if df.empty:
            raise TransformShapeUnsupportedError(
                message="transform(op='normalize', mode='index') requires at least one row",
                hint="Normalize index uses the first row as the default baseline.",
                details={"op": "normalize", "mode": mode},
            )
        return _coerce_normalize_number(df[column].iloc[0], argument="baseline", mode=mode)

    if not isinstance(baseline, dict) or not baseline:
        raise TransformArgError(
            message="transform(op='normalize') baseline must be a non-empty dict",
            hint="Pass baseline={'value': 100} or baseline={axis_column: axis_value}.",
            details={"op": "normalize", "mode": mode, "argument": "baseline"},
        )

    if set(baseline) == {"value"}:
        return _coerce_normalize_number(baseline["value"], argument="baseline.value", mode=mode)

    missing_columns = [key for key in baseline if key not in df.columns]
    if missing_columns:
        raise TransformArgError(
            message="transform(op='normalize') baseline selector references missing columns",
            hint="Baseline selector keys must match persisted frame columns.",
            details={
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
            details={"op": "normalize", "mode": mode, "argument": "baseline", "baseline": baseline},
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
            details={
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
            details={"op": "normalize", "mode": mode, "argument": "baseline"},
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
            details={
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
            details={
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
            details={"op": "normalize", "mode": mode, "argument": "baseline", "baseline": baseline},
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
            details={
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
            details={
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
            details={"op": "window", "argument": "window"},
        )
    window = make_absolute_window(timescope)
    if window is None:
        raise TransformArgError(
            message="transform(op='window') requires window",
            hint='Pass window={"start": "2026-07-01", "end": "2026-08-01"}.',
            details={"op": "window", "argument": "window"},
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
        details={"op": "window", "required_axis": "time", "axes": axes},
    )


def _coerce_window_bound(value: str, *, bound_name: str) -> pd.Timestamp:
    try:
        bound = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise WindowInvalidError(
            message=f"window.{bound_name}={value!r} is not a valid ISO-8601 date/datetime",
            details={"kind": "WindowBoundInvalid", "bound": bound_name, "value": value},
        ) from exc
    if pd.isna(bound):
        raise WindowInvalidError(
            message=f"window.{bound_name}={value!r} is not a valid ISO-8601 date/datetime",
            details={"kind": "WindowBoundInvalid", "bound": bound_name, "value": value},
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


def _op_window(frame: TransformFrame, params: _TransformParams) -> _TransformHandlerResult:
    unsupported_kwargs = [
        name
        for name in ("baseline", "by", "order", "drop_axes", "mode", "limit", "predicate", "where")
        if getattr(params, name) is not None
    ]
    if params.method != "ordinal":
        unsupported_kwargs.append("method")
    if params.rank_column != "rank":
        unsupported_kwargs.append("rank_column")
    unsupported_kwargs = sorted(unsupported_kwargs)
    if unsupported_kwargs:
        raise TransformArgError(
            message=(
                "transform(op='window') received unsupported kwargs: "
                f"{', '.join(unsupported_kwargs)}"
            ),
            hint="Use only window=... with window.",
            details={"op": "window", "unsupported_kwargs": unsupported_kwargs},
        )

    resolved_window = _resolve_transform_window(params.window, session=params.session)
    df = frame.to_pandas()
    time_axis = _window_time_axis(frame, df)
    time_column = time_axis.get("column")
    if not isinstance(time_column, str) or time_column not in df.columns:
        raise TransformDimensionNotFoundError(
            message="transform(op='window') time axis column is not present",
            hint="Window can only filter by a persisted time axis column.",
            details={"op": "window", "time_axis": time_axis, "columns": list(df.columns)},
        )

    start = _coerce_window_bound(resolved_window.start, bound_name="start")
    end = _coerce_window_bound(resolved_window.end, bound_name="end")
    series = pd.to_datetime(df[time_column], errors="raise")
    comparison_tz = _window_comparison_tz(resolved_window, session=params.session)
    start = _align_window_bound_to_series(start, series=series, comparison_tz=comparison_tz)
    end = _align_window_bound_to_series(end, series=series, comparison_tz=comparison_tz)
    if start >= end:
        raise TransformArgError(
            message="transform(op='window') requires window.start before window.end",
            hint="Pass explicit start/end bounds with start before end.",
            details={
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
            details={
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
            details={"op": "window", "argument": "window"},
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


_OP_DISPATCH["window"] = _op_window


def _op_normalize(frame: TransformFrame, params: _TransformParams) -> _TransformHandlerResult:
    unsupported_kwargs = [
        name
        for name in ("by", "order", "drop_axes", "limit", "predicate", "where", "window")
        if getattr(params, name) is not None
    ]
    if params.method != "ordinal":
        unsupported_kwargs.append("method")
    if params.rank_column != "rank":
        unsupported_kwargs.append("rank_column")
    unsupported_kwargs = sorted(unsupported_kwargs)
    if unsupported_kwargs:
        raise TransformArgError(
            message=(
                "transform(op='normalize') received unsupported kwargs: "
                f"{', '.join(unsupported_kwargs)}"
            ),
            hint="Use only mode=... and optional baseline=... with normalize.",
            details={"op": "normalize", "unsupported_kwargs": unsupported_kwargs},
        )

    mode = params.mode
    if not isinstance(mode, str):
        raise TransformArgError(
            message="transform(op='normalize') requires mode",
            hint="Pass mode='index', 'share', 'pct_change', 'per_unit', or 'z_score'.",
            details={"op": "normalize", "argument": "mode"},
        )

    if isinstance(frame, DeltaFrame):
        raise TransformArgError(
            message="transform(op='normalize') is not supported for DeltaFrame in v1",
            hint=(
                "Normalize MetricFrame inputs before compare, or use DeltaFrame transforms that "
                "preserve current, baseline, delta, and pct_change together."
            ),
            details={
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
            details={
                "op": "normalize",
                "mode": mode,
                "supported_modes": sorted(metric_modes),
                "frame_kind": frame.meta.kind,
            },
        )

    if mode in {"share", "pct_change", "z_score"} and params.baseline is not None:
        raise TransformArgError(
            message=f"transform(op='normalize', mode={mode!r}) does not accept baseline",
            hint="Use baseline only with mode='index' or mode='per_unit'.",
            details={"op": "normalize", "mode": mode, "unsupported_kwargs": ["baseline"]},
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
            details={"op": "normalize", "mode": mode, "column": column},
        )

    if mode == "index":
        base_values = _resolve_grouped_normalize_base(
            df,
            column=column,
            baseline=params.baseline,
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
                    details={"op": "normalize", "mode": mode, "column": column},
                )
            new_df[column] = new_df[column] / total
    elif mode == "pct_change":
        new_df[column] = _pct_change_series(frame, new_df, column)
    elif mode == "per_unit":
        base_value = _resolve_normalize_base(df, column=column, baseline=params.baseline, mode=mode)
        if not _finite_non_zero(base_value):
            raise TransformArgError(
                message=(
                    "transform(op='normalize', mode='per_unit') baseline must be finite and non-zero"
                ),
                hint="Choose a non-zero baseline row or pass baseline={'value': number}.",
                details={"op": "normalize", "mode": mode, "argument": "baseline"},
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
                    details={"op": "normalize", "mode": mode, "column": column},
                )
            new_df[column] = (new_df[column] - mean) / std

    normalized_baseline = (
        _normalize_param_value(params.baseline) if params.baseline is not None else None
    )
    normalization = {
        "mode": mode,
        "baseline": normalized_baseline,
        "columns_affected": [column],
    }
    return (
        new_df,
        {"normalization": normalization},
        {"op": "normalize", "mode": mode, "baseline": params.baseline, "column": column},
    )


_OP_DISPATCH["normalize"] = _op_normalize


def _resolve_slice_column(
    frame: TransformFrame, df: pd.DataFrame, key: Any
) -> tuple[str, str | None]:
    if isinstance(key, SemanticRef | SemanticObject):
        axes = _frame_axes(frame)
        dimension_id = _dimension_input_id(key)
        axis_id = _resolve_axis_id(axes, dimension_id, role="dimension")
        axis = axes.get(axis_id) if axis_id is not None else None
        if not isinstance(axis, dict) or axis.get("role") != "dimension":
            raise TransformDimensionNotFoundError(
                message=f"transform(op='slice') dimension {dimension_id!r} is not present",
                hint="Slice catalog ref keys must reference existing dimension axes.",
                details={"op": "slice", "dimension": dimension_id},
            )
        column = axis.get("column")
        if not isinstance(column, str) or column not in df.columns:
            raise TransformDimensionNotFoundError(
                message=f"transform(op='slice') dimension {dimension_id!r} column is not present",
                hint="Slice catalog ref keys must reference persisted frame columns.",
                details={"op": "slice", "dimension": dimension_id, "column": column},
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
                details={
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
                details={"op": "slice", "column": column, "axes": axes},
            )
        return key, dimension_key_id
    raise TransformArgError(
        message="transform(op='slice') where keys must be catalog dimension refs or str",
        hint=(
            'Use where={session.catalog.get("<dimension_id>").ref: "US"} '
            "or where={'value': (10, 20)}."
        ),
        details={"op": "slice", "actual_key_type": type(key).__name__},
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
    frame: TransformFrame, params: _TransformParams
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    unsupported_kwargs = [
        name
        for name in ("baseline", "by", "order", "drop_axes", "mode", "limit", "where", "window")
        if getattr(params, name) is not None
    ]
    if params.method != "ordinal":
        unsupported_kwargs.append("method")
    if params.rank_column != "rank":
        unsupported_kwargs.append("rank_column")
    unsupported_kwargs = sorted(unsupported_kwargs)
    if unsupported_kwargs:
        raise TransformArgError(
            message=(
                "transform(op='filter') received unsupported kwargs: "
                f"{', '.join(unsupported_kwargs)}"
            ),
            hint="Use predicate=... with filter; where belongs to op='slice'.",
            details={"op": "filter", "unsupported_kwargs": unsupported_kwargs},
        )

    predicate = params.predicate
    if not callable(predicate):
        raise TransformArgError(
            message="transform(op='filter') requires a callable predicate",
            hint="Pass predicate=lambda df: ... returning a boolean pandas Series.",
            details={"op": params.op, "argument": "predicate"},
        )

    df = frame.to_pandas()
    mask = predicate(df)
    if not isinstance(mask, pd.Series):
        raise TransformArgError(
            message="transform(op='filter') predicate must return a pandas Series",
            hint="Return a boolean Series with one value per input row.",
            details={
                "op": params.op,
                "argument": "predicate",
                "actual_type": type(mask).__name__,
            },
        )
    if not mask.index.equals(df.index):
        raise TransformArgError(
            message="transform(op='filter') predicate mask index alignment is invalid",
            hint="Return a boolean Series with the same index as the input DataFrame.",
            details={"op": "filter", "argument": "predicate"},
        )
    if len(mask) != len(df):
        raise TransformArgError(
            message="transform(op='filter') predicate returned a mask with the wrong length",
            hint="Return a boolean Series with one value per input row.",
            details={
                "op": params.op,
                "expected_length": len(df),
                "actual_length": len(mask),
            },
        )
    if not pd.api.types.is_bool_dtype(mask.dtype):
        raise TransformArgError(
            message="transform(op='filter') predicate mask must be boolean-like",
            hint="Return expressions such as df['column'] > value, not filtered data.",
            details={"op": params.op, "actual_dtype": str(mask.dtype)},
        )

    return df[mask].reset_index(drop=True), {}, {"op": params.op, "predicate": predicate}


_OP_DISPATCH["filter"] = _op_filter


def _ordered_take(
    frame: TransformFrame, params: _TransformParams, *, ascending: bool, op_name: str
) -> _TransformHandlerResult:
    unsupported_kwargs = [
        name
        for name in ("baseline", "drop_axes", "mode", "predicate", "where", "window")
        if getattr(params, name) is not None
    ]
    if params.method != "ordinal":
        unsupported_kwargs.append("method")
    if params.rank_column != "rank":
        unsupported_kwargs.append("rank_column")
    unsupported_kwargs = sorted(unsupported_kwargs)
    if unsupported_kwargs:
        raise TransformArgError(
            message=(
                f"transform(op='{op_name}') received unsupported kwargs: "
                f"{', '.join(unsupported_kwargs)}"
            ),
            hint=f"Use only by=... and limit=... with {op_name}.",
            details={"op": op_name, "unsupported_kwargs": unsupported_kwargs},
        )

    by = params.by
    if not isinstance(by, str):
        raise TransformArgError(
            message=f"transform(op='{op_name}') requires by to be a column name",
            hint=f"Pass by='value' or another persisted frame column for {op_name}.",
            details={
                "op": op_name,
                "argument": "by",
                "actual_type": type(by).__name__,
            },
        )

    limit = params.limit
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise TransformArgError(
            message=f"transform(op='{op_name}') requires a positive integer limit",
            hint=f"Pass limit=10 or another positive integer for {op_name}.",
            details={"op": op_name, "argument": "limit", "limit": limit},
        )

    df = frame.to_pandas()
    if by not in df.columns:
        raise TransformArgError(
            message=f"transform(op='{op_name}') by column {by!r} is not present",
            hint=f"Choose one of the persisted frame columns: {', '.join(map(str, df.columns))}.",
            details={"op": op_name, "argument": "by", "by": by, "columns": list(df.columns)},
        )

    sorted_df = (
        df.sort_values(by=by, ascending=ascending, na_position="last")
        .head(limit)
        .reset_index(drop=True)
    )
    return (
        sorted_df,
        {},
        {"op": op_name, "by": by, "limit": limit, "order": params.order},
    )


def _op_topk(frame: TransformFrame, params: _TransformParams) -> _TransformHandlerResult:
    if params.order not in (None, "decrease", "increase"):
        raise TransformArgError(
            message="transform(op='topk') order must be 'increase' or 'decrease'",
            hint=(
                "Omit order for descending topk, pass order='increase' "
                "for largest positive deltas, or order='decrease' for most negative deltas."
            ),
            details={"op": "topk", "argument": "order", "order": params.order},
        )
    return _ordered_take(
        frame,
        params,
        ascending=params.order == "decrease",
        op_name="topk",
    )


_OP_DISPATCH["topk"] = _op_topk


def _op_bottomk(frame: TransformFrame, params: _TransformParams) -> _TransformHandlerResult:
    if params.order is not None:
        raise TransformArgError(
            message="transform(op='bottomk') does not accept order",
            hint="Use topk(order='increase') if you need explicit increasing order semantics.",
            details={"op": "bottomk", "unsupported_kwargs": ["order"]},
        )
    return _ordered_take(frame, params, ascending=True, op_name="bottomk")


_OP_DISPATCH["bottomk"] = _op_bottomk


def _op_rank(frame: TransformFrame, params: _TransformParams) -> _TransformHandlerResult:
    unsupported_kwargs = [
        name
        for name in (
            "baseline",
            "order",
            "drop_axes",
            "mode",
            "limit",
            "predicate",
            "where",
            "window",
        )
        if getattr(params, name) is not None
    ]
    unsupported_kwargs = sorted(unsupported_kwargs)
    if unsupported_kwargs:
        raise TransformArgError(
            message=(
                f"transform(op='rank') received unsupported kwargs: {', '.join(unsupported_kwargs)}"
            ),
            hint="Use only by=..., method=..., and rank_column=... with rank.",
            details={"op": "rank", "unsupported_kwargs": unsupported_kwargs},
        )

    by = params.by
    if not isinstance(by, str):
        raise TransformArgError(
            message="transform(op='rank') requires by to be a column name",
            hint="Pass by='value' or another persisted frame column for rank.",
            details={"op": "rank", "argument": "by", "actual_type": type(by).__name__},
        )

    method_map: dict[str, Literal["dense", "first", "min", "max"]] = {
        "dense": "dense",
        "ordinal": "first",
        "min": "min",
        "max": "max",
    }
    method = params.method
    pandas_method = method_map.get(method)
    if pandas_method is None:
        raise TransformArgError(
            message=f"transform(op='rank') method {method!r} is not supported",
            hint="Use method='ordinal', 'dense', 'min', or 'max'.",
            details={
                "op": "rank",
                "argument": "method",
                "method": method,
                "supported_methods": sorted(method_map),
            },
        )

    rank_column = params.rank_column
    if not isinstance(rank_column, str) or not rank_column:
        raise TransformArgError(
            message="transform(op='rank') rank_column must be a non-empty string",
            hint="Pass rank_column='rank' or another new output column name.",
            details={
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
            details={"op": "rank", "argument": "by", "by": by, "columns": list(df.columns)},
        )
    if rank_column in df.columns:
        raise TransformArgError(
            message=f"transform(op='rank') rank_column {rank_column!r} already exists",
            hint="Choose a new rank_column name that does not overwrite an existing column.",
            details={"op": "rank", "argument": "rank_column", "rank_column": rank_column},
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
            details={
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


_OP_DISPATCH["rank"] = _op_rank


def _op_rollup(
    frame: TransformFrame, params: _TransformParams
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    unsupported_kwargs = [
        name
        for name in ("baseline", "by", "order", "mode", "limit", "predicate", "where", "window")
        if getattr(params, name) is not None
    ]
    if params.method != "ordinal":
        unsupported_kwargs.append("method")
    if params.rank_column != "rank":
        unsupported_kwargs.append("rank_column")
    unsupported_kwargs = sorted(unsupported_kwargs)
    if unsupported_kwargs:
        raise TransformArgError(
            message=(
                "transform(op='rollup') received unsupported kwargs: "
                f"{', '.join(unsupported_kwargs)}"
            ),
            hint="Use only drop_axes=... with rollup.",
            details={"op": "rollup", "unsupported_kwargs": unsupported_kwargs},
        )

    if getattr(frame.meta, "reaggregatable", True) is False:
        raise TransformShapeUnsupportedError(
            message="transform(op='rollup') cannot roll up non-reaggregatable metric values",
            hint="Re-run session.observe(...) at the target grain or target dimensions.",
            details={"op": "rollup", "reason": "non_reaggregatable", "frame_ref": frame.ref},
        )

    drop_ids = _normalize_rollup_drop_axes(frame, params.drop_axes)
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
            details={"op": "rollup", "missing_columns": missing_columns, "axes": axes},
        )

    all_axis_columns = set(axis_columns.values())
    measure_columns = _rollup_measure_columns(frame, df, all_axis_columns)
    if not measure_columns:
        raise TransformShapeUnsupportedError(
            message="transform(op='rollup') found no measure columns to aggregate",
            hint="Rollup requires at least one non-axis measure column.",
            details={"op": "rollup", "axes": axes},
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


_OP_DISPATCH["rollup"] = _op_rollup


def _op_slice(
    frame: TransformFrame, params: _TransformParams
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    unsupported_kwargs = [
        name
        for name in (
            "baseline",
            "by",
            "order",
            "drop_axes",
            "mode",
            "limit",
            "predicate",
            "window",
        )
        if getattr(params, name) is not None
    ]
    if params.method != "ordinal":
        unsupported_kwargs.append("method")
    if params.rank_column != "rank":
        unsupported_kwargs.append("rank_column")
    unsupported_kwargs = sorted(unsupported_kwargs)
    if unsupported_kwargs:
        raise TransformArgError(
            message=(
                "transform(op='slice') received unsupported kwargs: "
                f"{', '.join(unsupported_kwargs)}"
            ),
            hint="Use where=... with slice; predicate belongs to op='filter'.",
            details={"op": "slice", "unsupported_kwargs": unsupported_kwargs},
        )

    where = params.where
    if not isinstance(where, dict) or not where:
        raise TransformArgError(
            message="transform(op='slice') requires a non-empty where dict",
            hint=(
                'Pass where={session.catalog.get("<dimension_id>").ref: "US"} '
                "or where={'value': (10, 20)}."
            ),
            details={"op": "slice", "argument": "where"},
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
                    details={"op": "slice", "column": column},
                )
            if not _series_supports_range_slice(series):
                raise TransformArgError(
                    message=(
                        "transform(op='slice') range tuple values require a numeric "
                        "or date/time axis column"
                    ),
                    hint="Use a list for membership predicates on dimension columns.",
                    details={"op": "slice", "column": column},
                )
            lo, hi = value
            try:
                clause = series.between(lo, hi, inclusive="both")
            except (TypeError, ValueError) as exc:
                raise TransformArgError(
                    message="transform(op='slice') range tuple bounds are not comparable",
                    hint="Use bounds with the same numeric or date/time type as the axis column.",
                    details={"op": "slice", "column": column},
                ) from exc
        elif isinstance(value, list):
            clause = series.isin(list(value))
        elif isinstance(value, set):
            raise TransformArgError(
                message="transform(op='slice') does not accept set values",
                hint="Use a list for membership predicates so params persist deterministically.",
                details={"op": "slice", "column": column},
            )
        elif isinstance(value, dict):
            raise TransformArgError(
                message="transform(op='slice') where values must be scalar, list, or range tuple",
                hint="Use a scalar for equality, a list for membership, or a (lo, hi) tuple range.",
                details={
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

    return new_df, meta_overrides, {"op": params.op, "where": where}


_OP_DISPATCH["slice"] = _op_slice


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
            ),
        )
        register_frame_artifact(session, frame)

    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "transform",
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
