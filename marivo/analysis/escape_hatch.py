"""Controlled pandas/Ibis escape hatch for analysis."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import copy
import hashlib
import json
import math
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, ParamSpec, TypeVar, cast

import pandas as pd
from pandas.api.types import is_numeric_dtype, is_object_dtype

from marivo.analysis.errors import CrossSessionFrameError, PromotionFailedError
from marivo.analysis.executor.runner import execute
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.delta import (
    DeltaFrame,
    DeltaFrameMeta,
    _compatible_metric_semantics,
)
from marivo.analysis.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._derived import compose_lineage
from marivo.analysis.intents._observe_persist import _meta_additivity, _meta_aggregation
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy, PromotionPolicy
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.semantic_inputs import DimensionInput, MetricInput
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import persist_frame, require_current_session
from marivo.analysis.session.core import ensure_session_writable
from marivo.analysis.windows import (
    AbsoluteWindow,
    dump_window,
    normalize_absolute_window_input,
)
from marivo.datasource.authoring import DatasourceRef, _require_datasource_ref
from marivo.semantic.catalog import CatalogObject, SemanticRef
from marivo.semantic.catalog import SemanticKind as CatalogSemanticKind

DimensionAnchorInput = DimensionInput

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

SemanticKind = Literal["scalar", "time_series", "segmented", "panel"]
_P = ParamSpec("_P")
_R = TypeVar("_R")


def _instrument_escape_hatch(intent: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    event_name = f"marivo.analysis.escape_hatch.{intent}"

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            kwargs_dict = cast("dict[str, Any]", kwargs)
            resolved_session = _resolve_session(cast("Session | None", kwargs_dict.get("session")))
            kwargs_dict["session"] = resolved_session
            from marivo.telemetry import track_operation

            with track_operation(
                event_name,
                family="escape_hatch",
                intent=intent,
                session=resolved_session,
            ):
                return func(*args, **kwargs_dict)

        return wrapper

    return decorator


def _resolve_session(session: Session | None) -> Session:
    return session if session is not None else require_current_session()


def _new_frame_ref() -> str:
    return f"frame_{secrets.token_hex(4)}"


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _digest_json(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return _digest_text(payload)


def _policy_or_default(policy: PromotionPolicy | None) -> PromotionPolicy:
    return policy if policy is not None else PromotionPolicy()


def _isolate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    isolated = df.copy(deep=True)
    for column in [col for col in isolated.columns if is_object_dtype(isolated[col].dtype)]:
        isolated[column] = isolated[column].map(copy.deepcopy)
    return isolated


def _compile_query(expr: Any) -> str | None:
    compile_fn = getattr(expr, "compile", None)
    if not callable(compile_fn):
        return None
    try:
        compiled = compile_fn()
    except Exception:
        return None
    return str(compiled)


def _source_to_scratch(
    source: ExplorationResult | pd.DataFrame,
    *,
    session: Session,
) -> ExplorationResult:
    if isinstance(source, ExplorationResult):
        if source.meta.session_id != session.id:
            raise CrossSessionFrameError(
                message=(
                    f"scratch result '{source.ref}' belongs to session "
                    f"{source.meta.session_id!r} but was promoted through session {session.id!r}"
                ),
            )
        return source
    return from_pandas(source, session=session)


def _raise_promotion_failed(
    *,
    target_kind: str,
    missing: list[str],
    ambiguous: list[str],
    available_columns: list[str],
    source_refs: list[str],
) -> None:
    raise PromotionFailedError(
        message=f"cannot promote scratch result to {target_kind}",
        context={
            "target_kind": target_kind,
            "missing": missing,
            "ambiguous": ambiguous,
            "available_columns": available_columns,
            "source_refs": source_refs,
        },
    )


def _validate_columns(
    df: pd.DataFrame,
    *,
    target_kind: str,
    required: list[str],
    numeric: list[str],
    source_refs: list[str],
) -> None:
    missing = [column for column in required if column not in df.columns]
    non_numeric = [
        column for column in numeric if column in df.columns and not is_numeric_dtype(df[column])
    ]
    if missing or non_numeric:
        _raise_promotion_failed(
            target_kind=target_kind,
            missing=missing,
            ambiguous=[f"non_numeric:{column}" for column in non_numeric],
            available_columns=list(map(str, df.columns)),
            source_refs=source_refs,
        )


def _validate_metric_in_catalog(
    metric_id: str,
    *,
    session: Session,
    target_kind: str,
    available_columns: list[str],
    source_refs: list[str],
) -> None:
    available_metric_ids = sorted(_catalog_metric_ids(session.catalog))
    # An empty workspace loads as "ready" with zero metrics; promotion in
    # catalog-less sessions stays unvalidated.
    if not available_metric_ids:
        return
    if metric_id in available_metric_ids:
        return
    raise PromotionFailedError(
        message=f"cannot promote scratch result to {target_kind}",
        context={
            "target_kind": target_kind,
            "missing": [],
            "ambiguous": [f"metric_not_in_catalog:{metric_id}"],
            "available_columns": available_columns,
            "source_refs": source_refs,
            "available_metric_ids": available_metric_ids,
        },
    )


def _catalog_metric_ids(catalog: Any) -> set[str]:
    try:
        return set(catalog._require_index().semantic_ids(CatalogSemanticKind.METRIC))
    except Exception:
        return set()


def _catalog_metric_details(metric_id: str, *, session: Session) -> Any | None:
    """Return semantic metric details only when the catalog can prove the id."""
    if metric_id not in _catalog_metric_ids(session.catalog):
        return None
    return session.catalog.get(f"metric.{metric_id}").details()


def _load_metric_ref(ref: ArtifactRef | None, *, session: Session) -> MetricFrame | None:
    if ref is None:
        return None
    loaded = load_frame(ref.id, session=session)
    if not isinstance(loaded, MetricFrame):
        _raise_promotion_failed(
            target_kind="delta_frame",
            missing=[],
            ambiguous=[f"not_metric_frame:{ref.id}"],
            available_columns=loaded.columns,
            source_refs=[ref.id],
        )
    return cast("MetricFrame", loaded)


def _load_delta_ref(ref: ArtifactRef | None, *, session: Session) -> DeltaFrame | None:
    if ref is None:
        return None
    loaded = load_frame(ref.id, session=session)
    if not isinstance(loaded, DeltaFrame):
        _raise_promotion_failed(
            target_kind="attribution_frame",
            missing=[],
            ambiguous=[f"not_delta_frame:{ref.id}"],
            available_columns=loaded.columns,
            source_refs=[ref.id],
        )
    return cast("DeltaFrame", loaded)


def _validate_delta_formula(
    df: pd.DataFrame,
    *,
    current_column: str | None,
    baseline_column: str | None,
    delta_column: str,
    source_refs: list[str],
) -> None:
    missing = []
    if current_column is None:
        missing.append("current_column")
    if baseline_column is None:
        missing.append("baseline_column")
    if missing:
        _raise_promotion_failed(
            target_kind="delta_frame",
            missing=missing,
            ambiguous=[],
            available_columns=list(map(str, df.columns)),
            source_refs=source_refs,
        )
    assert current_column is not None
    assert baseline_column is not None
    _validate_columns(
        df,
        target_kind="delta_frame",
        required=[current_column, baseline_column, delta_column],
        numeric=[current_column, baseline_column, delta_column],
        source_refs=source_refs,
    )
    formula_columns = [current_column, baseline_column, delta_column]
    if bool(df[formula_columns].isna().any().any()):
        _raise_promotion_failed(
            target_kind="delta_frame",
            missing=[],
            ambiguous=["delta_formula_null"],
            available_columns=list(map(str, df.columns)),
            source_refs=source_refs,
        )
    try:
        expected = df[current_column] - df[baseline_column]
        mismatches = [
            idx
            for idx, pair in enumerate(zip(expected, df[delta_column], strict=True))
            if not math.isclose(float(pair[0]), float(pair[1]), rel_tol=1e-9, abs_tol=1e-9)
        ]
    except (TypeError, ValueError):
        _raise_promotion_failed(
            target_kind="delta_frame",
            missing=[],
            ambiguous=["delta_formula"],
            available_columns=list(map(str, df.columns)),
            source_refs=source_refs,
        )
    if mismatches:
        _raise_promotion_failed(
            target_kind="delta_frame",
            missing=[],
            ambiguous=["delta_formula"],
            available_columns=list(map(str, df.columns)),
            source_refs=source_refs,
        )


def _dump_delta_alignment(
    alignment: AlignmentPolicy,
    *,
    axes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if alignment.kind == "window_bucket":
        dumped: dict[str, Any] = {"kind": alignment.kind}
    else:
        dumped = alignment.model_dump(mode="json")
    if axes:
        dumped["axes"] = copy.deepcopy(axes)
    return dumped


def _raise_delta_metadata_mismatch(
    *,
    mismatch: str,
    available_columns: list[str],
    source_refs: list[str],
) -> None:
    _raise_promotion_failed(
        target_kind="delta_frame",
        missing=[],
        ambiguous=[mismatch],
        available_columns=available_columns,
        source_refs=source_refs,
    )


def _metric_anchor_id(ref: MetricInput) -> str:
    if isinstance(ref, CatalogObject):
        return ref.ref.id
    return ref.id


def _dimension_anchor_id(ref: DimensionAnchorInput) -> str:
    if isinstance(ref, CatalogObject):
        return ref.ref.id
    return ref.id


def _dimension_anchor_kind(ref: DimensionAnchorInput) -> CatalogSemanticKind | None:
    if isinstance(ref, CatalogObject):
        return ref.ref.kind
    return ref.kind


def _validate_dimension_anchors(
    *,
    axes: dict[str, DimensionAnchorInput] | None,
    time_axis: str | DimensionAnchorInput | None,
    available_columns: list[str],
    source_refs: list[str],
) -> None:
    ambiguous: list[str] = []
    for column, ref in (axes or {}).items():
        kind = _dimension_anchor_kind(ref)
        if kind not in {CatalogSemanticKind.DIMENSION, CatalogSemanticKind.TIME_DIMENSION}:
            ambiguous.append(f"axis_ref_kind:{column}:{_dimension_anchor_id(ref)}:{kind}")
    if isinstance(time_axis, SemanticRef | CatalogObject):
        kind = _dimension_anchor_kind(time_axis)
        if kind not in {CatalogSemanticKind.DIMENSION, CatalogSemanticKind.TIME_DIMENSION}:
            ambiguous.append(f"time_axis_ref_kind:{_dimension_anchor_id(time_axis)}:{kind}")
    if ambiguous:
        _raise_promotion_failed(
            target_kind="metric_frame",
            missing=[],
            ambiguous=ambiguous,
            available_columns=available_columns,
            source_refs=source_refs,
        )


def _axis_meta(axes: dict[str, DimensionAnchorInput] | None) -> dict[str, dict[str, str]]:
    return {
        column: {"role": "dimension", "column": column, "ref": _dimension_anchor_id(ref)}
        for column, ref in (axes or {}).items()
    }


def _time_axis_column_and_ref(
    time_axis: str | DimensionAnchorInput | None,
) -> tuple[str, str] | None:
    if time_axis is None:
        return None
    if isinstance(time_axis, SemanticRef | CatalogObject):
        axis_id = _dimension_anchor_id(time_axis)
        return axis_id, axis_id
    return time_axis, time_axis


def _axis_identifiers(
    *,
    axes: dict[str, DimensionAnchorInput] | None,
    time_axis_meta: tuple[str, str] | None,
) -> set[str]:
    identifiers: set[str] = set()
    for column, ref in (axes or {}).items():
        identifiers.add(column)
        identifiers.add(_dimension_anchor_id(ref))
    if time_axis_meta is not None:
        time_column, time_ref = time_axis_meta
        identifiers.update({"time", time_column, time_ref})
    return identifiers


def _validate_metric_shape_columns(
    df: pd.DataFrame,
    *,
    semantic_kind: SemanticKind,
    measure_column: str,
    axes: dict[str, DimensionAnchorInput] | None,
    time_axis_meta: tuple[str, str] | None,
    source_refs: list[str],
) -> None:
    ambiguous: list[str] = []
    if measure_column in _axis_identifiers(axes=axes, time_axis_meta=time_axis_meta):
        ambiguous.append(f"measure_axis_collision:{measure_column}")
    if semantic_kind == "scalar":
        extra_columns = sorted(
            str(column) for column in df.columns if str(column) != measure_column
        )
        ambiguous.extend(f"scalar_extra_columns:{column}" for column in extra_columns)
    if ambiguous:
        _raise_promotion_failed(
            target_kind="metric_frame",
            missing=[],
            ambiguous=ambiguous,
            available_columns=list(map(str, df.columns)),
            source_refs=source_refs,
        )


def _window_grain(window: dict[str, Any] | None) -> str | None:
    if not isinstance(window, dict):
        return None
    grain = window.get("grain")
    return grain if isinstance(grain, str) and grain else None


def _axis_columns_from_meta(axes: dict[str, Any] | None) -> list[str]:
    if not isinstance(axes, dict):
        return []
    columns: list[str] = []
    for axis in axes.values():
        if not isinstance(axis, dict):
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            columns.append(column)
    return sorted(set(columns))


def _validate_delta_source_compatibility(
    *,
    current_frame: MetricFrame | None,
    baseline_frame: MetricFrame | None,
    available_columns: list[str],
) -> None:
    if current_frame is None or baseline_frame is None:
        return
    source_refs = [current_frame.ref, baseline_frame.ref]
    if (
        current_frame.meta.semantic_kind in {"time_series", "segmented", "panel"}
        and current_frame.meta.axes != baseline_frame.meta.axes
    ):
        _raise_delta_metadata_mismatch(
            mismatch="axes_mismatch",
            available_columns=available_columns,
            source_refs=source_refs,
        )
    current_grain = _window_grain(current_frame.meta.window)
    baseline_grain = _window_grain(baseline_frame.meta.window)
    if current_grain != baseline_grain:
        _raise_delta_metadata_mismatch(
            mismatch="window_grain_mismatch",
            available_columns=available_columns,
            source_refs=source_refs,
        )


def _resolve_metric_window(
    window: object | None,
    *,
    session: Session,
) -> AbsoluteWindow | None:
    return normalize_absolute_window_input(window)


def _validate_semantic_shape(
    *,
    semantic_kind: SemanticKind,
    axes: dict[str, DimensionAnchorInput] | None,
    time_axis_meta: tuple[str, str] | None,
    available_columns: list[str],
    source_refs: list[str],
) -> None:
    has_axes = bool(axes)
    has_time_axis = time_axis_meta is not None
    missing: list[str] = []
    ambiguous: list[str] = []
    if semantic_kind == "scalar":
        if has_axes:
            ambiguous.append("unexpected_axes")
        if has_time_axis:
            ambiguous.append("unexpected_time_axis")
    elif semantic_kind == "time_series":
        if not has_time_axis:
            missing.append("time_axis")
        if has_axes:
            ambiguous.append("unexpected_axes")
    elif semantic_kind == "segmented":
        if not has_axes:
            missing.append("axes")
        if has_time_axis:
            ambiguous.append("unexpected_time_axis")
    elif semantic_kind == "panel":
        if not has_axes:
            missing.append("axes")
        if not has_time_axis:
            missing.append("time_axis")
    if missing or ambiguous:
        _raise_promotion_failed(
            target_kind="metric_frame",
            missing=missing,
            ambiguous=ambiguous,
            available_columns=available_columns,
            source_refs=source_refs,
        )


def _validate_axis_collisions(
    *,
    axes: dict[str, DimensionAnchorInput] | None,
    time_axis_meta: tuple[str, str] | None,
    available_columns: list[str],
    source_refs: list[str],
) -> None:
    if time_axis_meta is None:
        return
    time_column, time_ref = time_axis_meta
    reserved_axis_ids = {"time", time_column, time_ref}
    collisions: list[str] = []
    seen: set[str] = set()
    for column, ref in (axes or {}).items():
        if column in seen:
            collisions.append(column)
        seen.add(column)
        if column in reserved_axis_ids:
            collisions.append(column)
        ref_id = _dimension_anchor_id(ref)
        if ref_id in reserved_axis_ids:
            collisions.append(ref_id)
    if collisions:
        _raise_promotion_failed(
            target_kind="metric_frame",
            missing=[],
            ambiguous=[f"axis_collision:{column}" for column in sorted(set(collisions))],
            available_columns=available_columns,
            source_refs=source_refs,
        )


@_instrument_escape_hatch("from_pandas")
def from_pandas(
    df: pd.DataFrame,
    *,
    session: Session | None = None,
    description: str | None = None,
    sources: list[ArtifactRef] | None = None,
) -> ExplorationResult:
    resolved_session = _resolve_session(session)
    ensure_session_writable(resolved_session)
    source_refs = [ref.id for ref in sources or []]
    frame_ref = _new_frame_ref()
    copied = _isolate_dataframe(df)
    meta = ExplorationResultMeta(
        kind="exploration_result",
        ref=frame_ref,
        session_id=resolved_session.id,
        project_root=str(resolved_session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(copied),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="from_pandas",
                    job_ref=None,
                    inputs=source_refs,
                    params_digest=_digest_text(description or "from_pandas"),
                )
            ],
            external_inputs=[frame_ref],
        ),
        source_kind="pandas",
        description=description,
        source_query=None,
        source_datasource=None,
        source_artifact_refs=source_refs,
        promotion_refs=[],
    )
    scratch = ExplorationResult(_df=copied, meta=meta)
    scratch.meta = cast(
        "ExplorationResultMeta",
        persist_frame(resolved_session, scratch),
    )
    return scratch


@_instrument_escape_hatch("explore_ibis")
def explore_ibis(
    query_builder: Callable[[Any], Any],
    *,
    datasource: DatasourceRef,
    session: Session | None = None,
    description: str | None = None,
    sources: list[ArtifactRef] | None = None,
) -> ExplorationResult:
    resolved_session = _resolve_session(session)
    ensure_session_writable(resolved_session)
    datasource_id = _require_datasource_ref(
        datasource, argument="mv.explore_ibis(datasource=...)"
    ).id
    backend = resolved_session._connection_runtime.session_backend(datasource_id)
    try:
        expr = query_builder(backend)
    except NameError as exc:
        raise NameError(
            f"explore_ibis: {exc}. The query_builder callable must import its own "
            f"dependencies. Add 'import ibis' in your module or capture ibis "
            f"in a closure. Example: explore_ibis("
            f"lambda con: con.table('orders').order_by(ibis.desc('amount')), "
            f"datasource=...)"
        ) from exc
    if not callable(getattr(expr, "compile", None)) and not callable(
        getattr(expr, "to_pandas", None)
    ):
        raise TypeError("explore_ibis query_builder must return an Ibis expression")
    result = execute(
        expr,
        datasource_name=datasource_id,
        cache=resolved_session._connection_runtime,
        session_id=resolved_session.id,
    )
    source_refs = [ref.id for ref in sources or []]
    frame_ref = _new_frame_ref()
    query = result.query.sql if result.query is not None else _compile_query(expr)
    copied = _isolate_dataframe(result.df)
    meta = ExplorationResultMeta(
        kind="exploration_result",
        ref=frame_ref,
        session_id=resolved_session.id,
        project_root=str(resolved_session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(copied),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="explore_ibis",
                    job_ref=None,
                    inputs=source_refs,
                    params_digest=_digest_text(f"{datasource_id}:{query or description or ''}"),
                )
            ],
            external_inputs=[frame_ref],
        ),
        source_kind="ibis",
        description=description,
        source_query=query,
        source_datasource=datasource_id,
        source_artifact_refs=source_refs,
        promotion_refs=[],
    )
    scratch = ExplorationResult(_df=copied, meta=meta)
    scratch.meta = cast(
        "ExplorationResultMeta",
        persist_frame(resolved_session, scratch),
    )
    return scratch


@_instrument_escape_hatch("promote_metric_frame")
def promote_metric_frame(
    source: ExplorationResult | pd.DataFrame,
    *,
    policy: PromotionPolicy | None = None,
    session: Session | None = None,
    metric: MetricInput | None = None,
    semantic_kind: SemanticKind | None = None,
    measure_column: str | None = None,
    axes: dict[str, DimensionAnchorInput] | None = None,
    time_axis: str | DimensionAnchorInput | None = None,
    semantic_model: str | None = None,
    window: object | None = None,
    slice_by: dict[str, Any] | None = None,
) -> MetricFrame:
    resolved_session = _resolve_session(session)
    ensure_session_writable(resolved_session)
    resolved_policy = _policy_or_default(policy)
    scratch = _source_to_scratch(source, session=resolved_session)
    df = scratch.to_pandas()
    metric_id = (
        _metric_anchor_id(metric) if metric is not None else resolved_policy.semantic_anchors.metric
    )
    time_axis_ref = time_axis or resolved_policy.semantic_anchors.time_axis
    missing: list[str] = []
    if metric_id is None:
        missing.append("metric")
    if semantic_kind is None:
        missing.append("semantic_kind")
    if measure_column is None:
        missing.append("measure_column")
    if semantic_model is None:
        missing.append("semantic_model")
    for required in resolved_policy.required_fields:
        if required == "measure_column" and measure_column is None:
            missing.append(required)
        if required == "semantic_model" and semantic_model is None:
            missing.append(required)
    if missing:
        _raise_promotion_failed(
            target_kind="metric_frame",
            missing=sorted(set(missing)),
            ambiguous=[],
            available_columns=list(map(str, df.columns)),
            source_refs=[scratch.ref],
        )
    assert metric_id is not None
    assert semantic_kind is not None
    assert measure_column is not None
    assert semantic_model is not None
    available_columns = list(map(str, df.columns))
    source_refs = [scratch.ref]
    _validate_dimension_anchors(
        axes=axes,
        time_axis=time_axis_ref,
        available_columns=available_columns,
        source_refs=source_refs,
    )
    time_axis_meta = _time_axis_column_and_ref(time_axis_ref)
    _validate_metric_in_catalog(
        metric_id,
        session=resolved_session,
        target_kind="metric_frame",
        available_columns=available_columns,
        source_refs=source_refs,
    )
    metric_details = _catalog_metric_details(metric_id, session=resolved_session)
    metric_additivity = _meta_additivity(
        getattr(metric_details, "additivity", None) if metric_details is not None else None
    )
    metric_aggregation = _meta_aggregation(
        getattr(metric_details, "aggregation", None) if metric_details is not None else None
    )
    metric_status_time_dimension = (
        getattr(metric_details, "status_time_dimension", None)
        if metric_details is not None
        else None
    )
    _validate_semantic_shape(
        semantic_kind=semantic_kind,
        axes=axes,
        time_axis_meta=time_axis_meta,
        available_columns=available_columns,
        source_refs=source_refs,
    )
    _validate_axis_collisions(
        axes=axes,
        time_axis_meta=time_axis_meta,
        available_columns=available_columns,
        source_refs=source_refs,
    )
    _validate_metric_shape_columns(
        df,
        semantic_kind=semantic_kind,
        measure_column=measure_column,
        axes=axes,
        time_axis_meta=time_axis_meta,
        source_refs=source_refs,
    )
    required_columns = [measure_column, *list((axes or {}).keys())]
    if time_axis_meta is not None:
        required_columns.append(time_axis_meta[0])
    _validate_columns(
        df,
        target_kind="metric_frame",
        required=required_columns,
        numeric=[measure_column],
        source_refs=source_refs,
    )
    resolved_window = _resolve_metric_window(window, session=resolved_session)
    resolved_axes = _axis_meta(axes)
    if time_axis_meta is not None:
        time_column, time_ref = time_axis_meta
        time_meta = {"role": "time", "column": time_column, "ref": time_ref}
        if isinstance(resolved_window, AbsoluteWindow):
            if resolved_window.grain is not None:
                time_meta["grain"] = resolved_window.grain.to_token()
            if resolved_window.time_dimension is not None:
                time_meta["time_dimension"] = resolved_window.time_dimension
        resolved_axes = {"time": time_meta, **resolved_axes}
    promotion_params = {
        "metric_id": metric_id,
        "semantic_kind": semantic_kind,
        "semantic_model": semantic_model,
        "measure_column": measure_column,
        "axes": resolved_axes,
        "time_axis": {
            "column": time_axis_meta[0],
            "ref": time_axis_meta[1],
        }
        if time_axis_meta is not None
        else None,
        "window": dump_window(resolved_window),
        "where": slice_by or {},
        "additivity": metric_additivity,
        "aggregation": metric_aggregation,
        "status_time_dimension": metric_status_time_dimension,
    }
    meta = MetricFrameMeta(
        kind="metric_frame",
        ref=_new_frame_ref(),
        session_id=resolved_session.id,
        project_root=str(resolved_session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                *scratch.lineage.steps,
                LineageStep(
                    intent="promote_metric_frame",
                    job_ref=None,
                    inputs=[scratch.ref],
                    params_digest=_digest_json(promotion_params),
                ),
            ],
            external_inputs=sorted({*scratch.lineage.external_inputs, scratch.ref}),
        ),
        metric_id=metric_id,
        axes=resolved_axes,
        measure={"name": measure_column},
        window=dump_window(resolved_window),
        where=slice_by or {},
        semantic_kind=semantic_kind,
        semantic_model=semantic_model,
        additivity=metric_additivity,
        aggregation=metric_aggregation,
        status_time_dimension=metric_status_time_dimension,
    )
    frame = MetricFrame(_df=df, meta=meta)
    frame.meta = cast(
        "MetricFrameMeta",
        persist_frame(resolved_session, frame),
    )
    return frame


@_instrument_escape_hatch("promote_delta_frame")
def promote_delta_frame(
    source: ExplorationResult | pd.DataFrame,
    *,
    policy: PromotionPolicy | None = None,
    session: Session | None = None,
    current: ArtifactRef | None = None,
    baseline: ArtifactRef | None = None,
    metric: MetricInput | None = None,
    semantic_kind: SemanticKind | None = None,
    semantic_model: str | None = None,
    delta_column: str | None = None,
    current_column: str | None = None,
    baseline_column: str | None = None,
    alignment: AlignmentPolicy | None = None,
) -> DeltaFrame:
    resolved_session = _resolve_session(session)
    ensure_session_writable(resolved_session)
    resolved_policy = _policy_or_default(policy)
    current_ref = current or resolved_policy.semantic_anchors.current
    baseline_ref = baseline or resolved_policy.semantic_anchors.baseline
    scratch = _source_to_scratch(source, session=resolved_session)
    df = scratch.to_pandas()
    current_frame = _load_metric_ref(current_ref, session=resolved_session)
    baseline_frame = _load_metric_ref(baseline_ref, session=resolved_session)
    inherited_metric = current_frame.meta.metric_id if current_frame is not None else None
    inherited_kind = current_frame.meta.semantic_kind if current_frame is not None else None
    inherited_model = current_frame.meta.semantic_model if current_frame is not None else None
    metric_id = _metric_anchor_id(metric) if metric is not None else inherited_metric
    final_kind = semantic_kind or inherited_kind
    final_model = semantic_model or inherited_model
    missing: list[str] = []
    if current_ref is None:
        missing.append("current")
    if baseline_ref is None:
        missing.append("baseline")
    if metric_id is None:
        missing.append("metric")
    if final_kind is None:
        missing.append("semantic_kind")
    if final_model is None:
        missing.append("semantic_model")
    if delta_column is None:
        missing.append("delta_column")
    if (
        current_frame is not None
        and baseline_frame is not None
        and current_frame.meta.metric_id != baseline_frame.meta.metric_id
    ):
        _raise_delta_metadata_mismatch(
            mismatch="metric_mismatch",
            available_columns=list(map(str, df.columns)),
            source_refs=[current_frame.ref, baseline_frame.ref],
        )
    if (
        current_frame is not None
        and baseline_frame is not None
        and current_frame.meta.semantic_kind != baseline_frame.meta.semantic_kind
    ):
        _raise_delta_metadata_mismatch(
            mismatch="semantic_kind_mismatch",
            available_columns=list(map(str, df.columns)),
            source_refs=[current_frame.ref, baseline_frame.ref],
        )
    if (
        current_frame is not None
        and baseline_frame is not None
        and current_frame.meta.semantic_model != baseline_frame.meta.semantic_model
    ):
        _raise_delta_metadata_mismatch(
            mismatch="semantic_model_mismatch",
            available_columns=list(map(str, df.columns)),
            source_refs=[current_frame.ref, baseline_frame.ref],
        )
    _validate_delta_source_compatibility(
        current_frame=current_frame,
        baseline_frame=baseline_frame,
        available_columns=list(map(str, df.columns)),
    )
    override_mismatches = [
        frame.ref
        for frame in [current_frame, baseline_frame]
        if frame is not None
        and metric is not None
        and frame.meta.metric_id != _metric_anchor_id(metric)
    ]
    if override_mismatches:
        _raise_delta_metadata_mismatch(
            mismatch="metric_override_mismatch",
            available_columns=list(map(str, df.columns)),
            source_refs=override_mismatches,
        )
    kind_override_mismatches = [
        frame.ref
        for frame in [current_frame, baseline_frame]
        if frame is not None
        and semantic_kind is not None
        and frame.meta.semantic_kind != semantic_kind
    ]
    if kind_override_mismatches:
        _raise_delta_metadata_mismatch(
            mismatch="semantic_kind_override_mismatch",
            available_columns=list(map(str, df.columns)),
            source_refs=kind_override_mismatches,
        )
    model_override_mismatches = [
        frame.ref
        for frame in [current_frame, baseline_frame]
        if frame is not None
        and semantic_model is not None
        and frame.meta.semantic_model != semantic_model
    ]
    if model_override_mismatches:
        _raise_delta_metadata_mismatch(
            mismatch="semantic_model_override_mismatch",
            available_columns=list(map(str, df.columns)),
            source_refs=model_override_mismatches,
        )
    if missing:
        source_refs_for_error = [
            ref
            for ref in [
                current_ref.id if current_ref is not None else None,
                baseline_ref.id if baseline_ref is not None else None,
            ]
            if ref is not None
        ]
        _raise_promotion_failed(
            target_kind="delta_frame",
            missing=missing,
            ambiguous=[],
            available_columns=list(map(str, df.columns)),
            source_refs=source_refs_for_error,
        )
    assert metric_id is not None
    assert final_kind is not None
    assert final_model is not None
    assert delta_column is not None
    source_refs = [current_ref.id, baseline_ref.id] if current_ref and baseline_ref else []
    _validate_metric_in_catalog(
        metric_id,
        session=resolved_session,
        target_kind="delta_frame",
        available_columns=list(map(str, df.columns)),
        source_refs=source_refs,
    )
    alignment_axes = (
        current_frame.meta.axes
        if current_frame is not None and final_kind in {"time_series", "segmented", "panel"}
        else None
    )
    required_columns = [delta_column, *_axis_columns_from_meta(alignment_axes)]
    _validate_columns(
        df,
        target_kind="delta_frame",
        required=required_columns,
        numeric=[delta_column],
        source_refs=source_refs,
    )
    _validate_delta_formula(
        df,
        current_column=current_column,
        baseline_column=baseline_column,
        delta_column=delta_column,
        source_refs=source_refs,
    )
    final_alignment = alignment or AlignmentPolicy(kind="window_bucket")
    alignment_dump = _dump_delta_alignment(final_alignment, axes=alignment_axes)
    additivity, aggregation, status_time_dimension = _compatible_metric_semantics(
        current_frame.meta if current_frame is not None else None,
        baseline_frame.meta if baseline_frame is not None else None,
    )
    promotion_params = {
        "source_current_ref": current_ref.id if current_ref else None,
        "source_baseline_ref": baseline_ref.id if baseline_ref else None,
        "metric_id": metric_id,
        "semantic_kind": final_kind,
        "semantic_model": final_model,
        "delta_column": delta_column,
        "current_column": current_column,
        "baseline_column": baseline_column,
        "alignment": alignment_dump,
        "additivity": additivity,
        "aggregation": aggregation,
        "status_time_dimension": status_time_dimension,
    }
    source_lineage_steps = [
        *scratch.lineage.steps,
        *(current_frame.lineage.steps if current_frame is not None else []),
        *(baseline_frame.lineage.steps if baseline_frame is not None else []),
    ]
    source_external_inputs = sorted(
        {
            *scratch.lineage.external_inputs,
            scratch.ref,
            *(current_frame.lineage.external_inputs if current_frame is not None else []),
            *(baseline_frame.lineage.external_inputs if baseline_frame is not None else []),
        }
    )
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref=_new_frame_ref(),
        session_id=resolved_session.id,
        project_root=str(resolved_session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                *source_lineage_steps,
                LineageStep(
                    intent="promote_delta_frame",
                    job_ref=None,
                    inputs=[scratch.ref, *source_refs],
                    params_digest=_digest_json(promotion_params),
                ),
            ],
            external_inputs=source_external_inputs,
        ),
        metric_id=metric_id,
        source_current_ref=current_ref.id if current_ref else "",
        source_baseline_ref=baseline_ref.id if baseline_ref else "",
        alignment=alignment_dump,
        semantic_kind=final_kind,
        semantic_model=final_model,
        additivity=additivity,
        aggregation=aggregation,
        status_time_dimension=status_time_dimension,
    )
    frame = DeltaFrame(_df=df, meta=meta)
    frame.meta = cast(
        "DeltaFrameMeta",
        persist_frame(resolved_session, frame),
    )
    return frame


@_instrument_escape_hatch("promote_attribution_frame")
def promote_attribution_frame(
    source: ExplorationResult | pd.DataFrame,
    *,
    policy: PromotionPolicy | None = None,
    session: Session | None = None,
    source_delta: ArtifactRef | None = None,
    driver_field: str | None = None,
    contribution_column: str | None = None,
    value_column: str | None = None,
    method: str = "promotion",
    method_params: dict[str, Any] | None = None,
) -> AttributionFrame:
    resolved_session = _resolve_session(session)
    ensure_session_writable(resolved_session)
    resolved_policy = _policy_or_default(policy)
    delta_ref = source_delta or resolved_policy.semantic_anchors.source_delta
    scratch = _source_to_scratch(source, session=resolved_session)
    df = scratch.to_pandas()
    source_delta_frame = _load_delta_ref(delta_ref, session=resolved_session)
    missing: list[str] = []
    if delta_ref is None:
        missing.append("source_delta")
    if driver_field is None:
        missing.append("driver_field")
    if contribution_column is None:
        missing.append("contribution_column")
    if source_delta_frame is None and delta_ref is not None:
        missing.append("source_delta")
    if missing:
        _raise_promotion_failed(
            target_kind="attribution_frame",
            missing=missing,
            ambiguous=[],
            available_columns=list(map(str, df.columns)),
            source_refs=[delta_ref.id] if delta_ref else [],
        )
    assert delta_ref is not None
    assert source_delta_frame is not None
    assert driver_field is not None
    assert contribution_column is not None
    required = [driver_field, contribution_column]
    if value_column is not None:
        required.append(value_column)
    _validate_columns(
        df,
        target_kind="attribution_frame",
        required=required,
        numeric=[contribution_column],
        source_refs=[delta_ref.id],
    )
    if bool(df[contribution_column].isna().any()):
        _raise_promotion_failed(
            target_kind="attribution_frame",
            missing=[],
            ambiguous=["contribution_null"],
            available_columns=list(map(str, df.columns)),
            source_refs=[delta_ref.id],
        )
    resolved_params = method_params or {}
    meta = AttributionFrameMeta(
        kind="attribution_frame",
        ref=_new_frame_ref(),
        session_id=resolved_session.id,
        project_root=str(resolved_session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=compose_lineage(
            [scratch, source_delta_frame],
            step=LineageStep(
                intent="promote_attribution_frame",
                job_ref=None,
                inputs=[scratch.ref, delta_ref.id],
                params_digest=_digest_json(
                    {
                        "source_delta": delta_ref.id,
                        "driver_field": driver_field,
                        "contribution_column": contribution_column,
                        "value_column": value_column,
                        "method": method,
                        "method_params": resolved_params,
                    }
                ),
            ),
        ),
        metric_ids=[source_delta_frame.meta.metric_id],
        source_refs=[delta_ref.id],
        attribution_kind="decomposition",
        driver_field=driver_field,
        value_column=value_column,
        contribution_column=contribution_column,
        method=method,
        params=resolved_params,
        semantic_kind=source_delta_frame.meta.semantic_kind,
        semantic_model=source_delta_frame.meta.semantic_model,
    )
    frame = AttributionFrame(_df=df, meta=meta)
    frame.meta = cast(
        "AttributionFrameMeta",
        persist_frame(resolved_session, frame),
    )
    return frame
