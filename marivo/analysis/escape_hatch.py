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
from typing import TYPE_CHECKING, Any, Literal, cast

import pandas as pd
from pandas.api.types import is_numeric_dtype, is_object_dtype

from marivo.analysis.errors import CrossSessionFrameError, PromotionFailedError
from marivo.analysis.executor.runner import execute
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._derived import compose_lineage
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy, PromotionPolicy
from marivo.analysis.refs import ArtifactRef, DimensionRef, MetricRef
from marivo.analysis.session._load import load_frame
from marivo.analysis.session.attach import active as active_session
from marivo.analysis.session.core import ensure_session_writable
from marivo.analysis.session.persistence import write_frame_to_disk
from marivo.analysis.windows import (
    AbsoluteWindow,
    dump_window,
    normalize_absolute_window_input,
)

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

SemanticKind = Literal["scalar", "time_series", "segmented", "panel"]


def _resolve_session(session: Session | None) -> Session:
    return session if session is not None else active_session()


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
        details={
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


def _axis_meta(axes: dict[str, DimensionRef] | None) -> dict[str, dict[str, str]]:
    return {
        column: {"role": "dimension", "column": column, "ref": ref.id}
        for column, ref in (axes or {}).items()
    }


def _time_axis_column_and_ref(time_axis: str | DimensionRef | None) -> tuple[str, str] | None:
    if time_axis is None:
        return None
    if isinstance(time_axis, DimensionRef):
        return time_axis.id, time_axis.id
    return time_axis, time_axis


def _axis_identifiers(
    *,
    axes: dict[str, DimensionRef] | None,
    time_axis_meta: tuple[str, str] | None,
) -> set[str]:
    identifiers: set[str] = set()
    for column, ref in (axes or {}).items():
        identifiers.add(column)
        identifiers.add(ref.id)
    if time_axis_meta is not None:
        time_column, time_ref = time_axis_meta
        identifiers.update({"time", time_column, time_ref})
    return identifiers


def _validate_metric_shape_columns(
    df: pd.DataFrame,
    *,
    semantic_kind: SemanticKind,
    measure_column: str,
    axes: dict[str, DimensionRef] | None,
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
    axes: dict[str, DimensionRef] | None,
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
    axes: dict[str, DimensionRef] | None,
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
        if ref.id in reserved_axis_ids:
            collisions.append(ref.id)
    if collisions:
        _raise_promotion_failed(
            target_kind="metric_frame",
            missing=[],
            ambiguous=[f"axis_collision:{column}" for column in sorted(set(collisions))],
            available_columns=available_columns,
            source_refs=source_refs,
        )


def from_pandas(
    df: pd.DataFrame,
    *,
    session: Session | None = None,
    description: str | None = None,
    sources: list[ArtifactRef] | None = None,
) -> ExplorationResult:
    """Import a pandas DataFrame into the session as an ExplorationResult.

    Use this when you have data from an external source (CSV, API response,
    manual construction) that you want to bring into the Marivo analysis
    pipeline. The returned ExplorationResult is an untyped scratch frame;
    promote it with ``promote_metric_frame`` or similar before passing to
    typed intents like ``compare`` or ``decompose``.

    Args:
        df: Source DataFrame. A defensive copy is made internally.
        session: Target session. Uses the ambient session when None.
        description: Human-readable note stored in frame metadata.
        sources: Optional lineage references to upstream artifacts that
            produced this data.

    Returns:
        An ExplorationResult persisted to the session's frame store.

    Raises:
        SessionNotWritableError: If the resolved session is read-only.

    Example:
        >>> result = from_pandas(my_df, description="daily sales extract")
        >>> mf = promote_metric_frame(result, metric=metric_ref, ...)
    """
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
        write_frame_to_disk(resolved_session.layout, scratch),
    )
    return scratch


def explore_ibis(
    query_builder: Callable[[Any], Any],
    *,
    datasource: str,
    session: Session | None = None,
    description: str | None = None,
    sources: list[ArtifactRef] | None = None,
) -> ExplorationResult:
    """Run an ibis query against a datasource and return an ExplorationResult.

    Use this when you need to query a registered datasource with custom ibis
    logic that goes beyond what the semantic model exposes. The query is
    executed immediately and the result is persisted as an untyped scratch
    frame. Promote the result before passing to typed intents.

    Args:
        query_builder: A callable that receives an ibis backend connection
            and returns an ibis expression (table or column expression).
        datasource: Name of the datasource registered in the session's
            backend cache.
        session: Target session. Uses the ambient session when None.
        description: Human-readable note stored in frame metadata.
        sources: Optional lineage references to upstream artifacts.

    Returns:
        An ExplorationResult containing the query result, persisted to disk.

    Raises:
        TypeError: If ``query_builder`` does not return a valid ibis expression.
        SessionNotWritableError: If the resolved session is read-only.

    Example:
        >>> result = explore_ibis(
        ...     lambda con: con.table("orders").filter(_.status == "shipped"),
        ...     datasource="warehouse",
        ... )
    """
    resolved_session = _resolve_session(session)
    ensure_session_writable(resolved_session)
    backend = resolved_session.backend_cache.get_or_create(datasource)
    expr = query_builder(backend)
    if not callable(getattr(expr, "compile", None)) and not callable(
        getattr(expr, "to_pandas", None)
    ):
        raise TypeError("explore_ibis query_builder must return an Ibis expression")
    result = execute(
        expr,
        datasource_name=datasource,
        cache=resolved_session.backend_cache,
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
                    params_digest=_digest_text(f"{datasource}:{query or description or ''}"),
                )
            ],
            external_inputs=[frame_ref],
        ),
        source_kind="ibis",
        description=description,
        source_query=query,
        source_datasource=datasource,
        source_artifact_refs=source_refs,
        promotion_refs=[],
    )
    scratch = ExplorationResult(_df=copied, meta=meta)
    scratch.meta = cast(
        "ExplorationResultMeta",
        write_frame_to_disk(resolved_session.layout, scratch),
    )
    return scratch


def promote_metric_frame(
    source: ExplorationResult | pd.DataFrame,
    *,
    policy: PromotionPolicy | None = None,
    session: Session | None = None,
    metric: MetricRef | None = None,
    semantic_kind: SemanticKind | None = None,
    measure_column: str | None = None,
    axes: dict[str, DimensionRef] | None = None,
    time_axis: str | DimensionRef | None = None,
    semantic_model: str | None = None,
    window: object | None = None,
    where: dict[str, Any] | None = None,
) -> MetricFrame:
    """Upgrade an ExplorationResult or DataFrame into a typed MetricFrame.

    Use this when you have raw tabular data that represents a metric
    observation and you need to feed it into typed intents (``compare``,
    ``decompose``, etc.) that require a MetricFrame.

    Required parameters (must be supplied explicitly; not auto-inferred):
        metric, semantic_kind, measure_column, semantic_model.

    Auto-inferred parameters (when ``policy.auto_infer=True``):
        axes, time_axis, window.

    Args:
        source: An ExplorationResult or raw pandas DataFrame to promote.
        policy: Controls auto-inference behavior. Defaults to auto_infer=True.
        session: Target session. Uses the ambient session when None.
        metric: Reference to the semantic metric this frame measures.
        semantic_kind: One of "scalar", "time_series", "segmented", "panel".
        measure_column: Column name holding the numeric measure values.
        axes: Mapping of column name to DimensionRef for dimension axes.
        time_axis: Column name or DimensionRef for the time dimension.
        semantic_model: Name of the semantic model this metric belongs to.
        window: Absolute time window specification.
        where: Filter predicates applied to the observation.

    Returns:
        A MetricFrame persisted to the session's frame store.

    Raises:
        PromotionError: If required fields are missing or columns are invalid.
        SessionNotWritableError: If the resolved session is read-only.

    Example:
        >>> mf = promote_metric_frame(
        ...     exploration_result,
        ...     metric=MetricRef("revenue"),
        ...     semantic_kind="time_series",
        ...     measure_column="total_revenue",
        ...     semantic_model="sales",
        ...     time_axis="order_date",
        ... )
    """
    resolved_session = _resolve_session(session)
    ensure_session_writable(resolved_session)
    resolved_policy = _policy_or_default(policy)
    scratch = _source_to_scratch(source, session=resolved_session)
    df = scratch.to_pandas()
    metric_ref = metric or resolved_policy.semantic_anchors.metric
    time_axis_ref = time_axis or resolved_policy.semantic_anchors.time_axis
    missing: list[str] = []
    if metric_ref is None:
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
    assert metric_ref is not None
    assert semantic_kind is not None
    assert measure_column is not None
    assert semantic_model is not None
    time_axis_meta = _time_axis_column_and_ref(time_axis_ref)
    available_columns = list(map(str, df.columns))
    source_refs = [scratch.ref]
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
        "metric_id": metric_ref.id,
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
        "where": where or {},
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
        metric_id=metric_ref.id,
        axes=resolved_axes,
        measure={"name": measure_column},
        window=dump_window(resolved_window),
        where=where or {},
        semantic_kind=semantic_kind,
        semantic_model=semantic_model,
    )
    frame = MetricFrame(_df=df, meta=meta)
    frame.meta = cast(
        "MetricFrameMeta",
        write_frame_to_disk(resolved_session.layout, frame),
    )
    return frame


def promote_delta_frame(
    source: ExplorationResult | pd.DataFrame,
    *,
    policy: PromotionPolicy | None = None,
    session: Session | None = None,
    current: ArtifactRef | None = None,
    baseline: ArtifactRef | None = None,
    metric: MetricRef | None = None,
    semantic_kind: SemanticKind | None = None,
    semantic_model: str | None = None,
    delta_column: str | None = None,
    current_column: str | None = None,
    baseline_column: str | None = None,
    alignment: AlignmentPolicy | None = None,
) -> DeltaFrame:
    """Upgrade an ExplorationResult or DataFrame into a typed DeltaFrame.

    Use this when you have pre-computed difference data between two metric
    observations and need a typed frame for downstream intents like
    ``decompose`` (attribution analysis).

    Required parameters (must be supplied explicitly):
        current, baseline, delta_column. Additionally metric, semantic_kind,
        and semantic_model are required but can be inherited from the
        referenced current/baseline MetricFrames.

    Auto-inherited from source MetricFrames:
        metric, semantic_kind, semantic_model (from the ``current`` frame).

    Args:
        source: An ExplorationResult or raw DataFrame containing delta data.
        policy: Controls auto-inference behavior. Defaults to auto_infer=True.
        session: Target session. Uses the ambient session when None.
        current: ArtifactRef pointing to the "current" MetricFrame.
        baseline: ArtifactRef pointing to the "baseline" MetricFrame.
        metric: Override metric reference (must match source frames if given).
        semantic_kind: Override semantic kind (must match source frames).
        semantic_model: Override semantic model name (must match source frames).
        delta_column: Column name holding the numeric delta values.
        current_column: Column with current-period raw values (optional).
        baseline_column: Column with baseline-period raw values (optional).
        alignment: How current and baseline periods are aligned.
            Defaults to window_bucket alignment.

    Returns:
        A DeltaFrame persisted to the session's frame store.

    Raises:
        PromotionError: If required fields are missing, columns are invalid,
            or current/baseline metadata is inconsistent.
        SessionNotWritableError: If the resolved session is read-only.

    Example:
        >>> df = promote_delta_frame(
        ...     delta_exploration,
        ...     current=ArtifactRef(current_mf.ref),
        ...     baseline=ArtifactRef(baseline_mf.ref),
        ...     delta_column="revenue_change",
        ... )
    """
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
    metric_id = metric.id if metric is not None else inherited_metric
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
        if frame is not None and metric is not None and frame.meta.metric_id != metric.id
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
    )
    frame = DeltaFrame(_df=df, meta=meta)
    frame.meta = cast(
        "DeltaFrameMeta",
        write_frame_to_disk(resolved_session.layout, frame),
    )
    return frame


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
    """Upgrade an ExplorationResult or DataFrame into a typed AttributionFrame.

    Use this when you have pre-computed attribution/decomposition data that
    explains which drivers contributed to a delta, and you need a typed frame
    that the analysis pipeline can consume as evidence.

    Required parameters (must be supplied explicitly):
        source_delta, driver_field, contribution_column.

    Auto-inherited from the source DeltaFrame:
        metric, semantic_kind, semantic_model.

    Args:
        source: An ExplorationResult or raw DataFrame with attribution rows.
        policy: Controls auto-inference behavior. Defaults to auto_infer=True.
        session: Target session. Uses the ambient session when None.
        source_delta: ArtifactRef pointing to the DeltaFrame being explained.
        driver_field: Column name identifying the dimension driver
            (e.g., "region", "product_category").
        contribution_column: Numeric column holding each driver's contribution
            to the total delta. Must not contain NaN values.
        value_column: Optional column with the absolute value per driver.
        method: Attribution method label. Defaults to "promotion".
        method_params: Additional parameters for the attribution method.

    Returns:
        An AttributionFrame persisted to the session's frame store.

    Raises:
        PromotionError: If required fields are missing, columns are invalid,
            or contribution_column contains null values.
        SessionNotWritableError: If the resolved session is read-only.

    Example:
        >>> af = promote_attribution_frame(
        ...     attribution_data,
        ...     source_delta=ArtifactRef(delta_frame.ref),
        ...     driver_field="region",
        ...     contribution_column="contribution",
        ... )
    """
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
        write_frame_to_disk(resolved_session.layout, frame),
    )
    return frame
