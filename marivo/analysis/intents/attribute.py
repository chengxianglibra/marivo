"""Public deterministic attribution composite operator."""

from __future__ import annotations

from marivo.analysis.errors import (
    AttributionMaterializationError,
    CumulativeFrameUnsupportedError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._attribution_mode import AttributionMode, validate_attribution_mode
from marivo.analysis.intents._derived import (
    ensure_frame_in_session,
    resolve_session,
)
from marivo.analysis.intents._replay import (
    _dimension_ref,
    recover_alignment_policy,
    recover_observe_replay,
)
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.decompose import (
    _effective_component_axis_column,
    _normalize_axis_boundary,
    _validate_attribution_semantics,
    decompose,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.refs import FieldKind, Ref


def _normalize_attribute_axes(session: Session, axes: list[Ref[FieldKind]]) -> list[str]:
    if not axes:
        raise SemanticKindMismatchError(
            message="attribute requires at least one axis",
            context={"argument": "axes"},
        )
    axis_ids = [_normalize_axis_boundary(session, axis) for axis in axes]
    if len(set(axis_ids)) != len(axis_ids):
        raise SemanticKindMismatchError(
            message="attribute axes must be distinct",
            context={"argument": "axes", "reason": "duplicate_axes", "axes": axis_ids},
        )
    return axis_ids


def _missing_axis_ids(frame: DeltaFrame, axis_ids: list[str]) -> list[str]:
    columns = [str(column) for column in frame._dataframe_copy().columns]
    return [
        axis_id
        for axis_id in axis_ids
        if _effective_component_axis_column(frame, axis_id, columns) is None
    ]


def _load_metric_source(
    session: Session,
    ref: str,
    *,
    label: str,
    delta: DeltaFrame,
    missing_axes: list[str],
) -> MetricFrame:
    try:
        frame = session.get_frame(ref)
    except Exception as exc:
        raise AttributionMaterializationError(
            message=f"attribute could not load {label} source frame",
            context={
                "recoverability_status": "source_frame_missing",
                "delta_ref": delta.ref,
                "missing_axes": missing_axes,
                "source_refs": {
                    "current": delta.meta.source_current_ref,
                    "baseline": delta.meta.source_baseline_ref,
                },
            },
        ) from exc
    if not isinstance(frame, MetricFrame):
        raise AttributionMaterializationError(
            message=f"attribute {label} source is not a MetricFrame",
            context={
                "recoverability_status": "source_frame_not_metric",
                "delta_ref": delta.ref,
                "missing_axes": missing_axes,
                "source_ref": ref,
                "source_kind": getattr(getattr(frame, "meta", None), "kind", type(frame).__name__),
            },
        )
    return frame


def attribute(
    frame: DeltaFrame,
    *,
    axes: list[Ref[FieldKind]],
    mode: AttributionMode | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> AttributionFrame:
    """Attribute a DeltaFrame's movement over explicit deterministic axes."""
    resolved_session = resolve_session(session)
    ensure_session_writable(resolved_session)
    if not isinstance(frame, DeltaFrame):
        raise SemanticKindMismatchError(message="attribute requires a DeltaFrame input")
    if frame.meta.cumulative is not None:
        raise CumulativeFrameUnsupportedError(
            intent="attribute",
            frame_ref=frame.ref,
            metric_id=frame.meta.metric_id,
            cumulative=frame.meta.cumulative,
        )
    ensure_frame_in_session(frame, session=resolved_session, label="attribute frame")
    axis_ids = _normalize_attribute_axes(resolved_session, axes)
    validated_mode = validate_attribution_mode(axis_ids, mode, intent="attribute")
    missing_axes = _missing_axis_ids(frame, axis_ids)
    if not missing_axes:
        return decompose(
            frame,
            axes=axes,
            mode=validated_mode,
            session=resolved_session,
            _intent="attribute",
            _analysis_purpose=analysis_purpose,
            _params_extra={
                "materialization_status": "not_required",
                "original_delta_ref": frame.ref,
            },
        )

    _validate_attribution_semantics(frame, axes=axis_ids, session=resolved_session)
    current = _load_metric_source(
        resolved_session,
        frame.meta.source_current_ref,
        label="current",
        delta=frame,
        missing_axes=missing_axes,
    )
    baseline = _load_metric_source(
        resolved_session,
        frame.meta.source_baseline_ref,
        label="baseline",
        delta=frame,
        missing_axes=missing_axes,
    )
    missing_axis_refs = [_dimension_ref(resolved_session, axis) for axis in missing_axes]
    current_replay = recover_observe_replay(current, session=resolved_session).with_dimensions(
        missing_axis_refs
    )
    baseline_replay = recover_observe_replay(baseline, session=resolved_session).with_dimensions(
        missing_axis_refs
    )
    alignment = recover_alignment_policy(frame)

    expanded_current = current_replay.call_observe(resolved_session)
    expanded_baseline = baseline_replay.call_observe(resolved_session)
    expanded_delta = compare(
        expanded_current,
        expanded_baseline,
        alignment=alignment,
        session=resolved_session,
    )
    return decompose(
        expanded_delta,
        axes=axes,
        mode=validated_mode,
        session=resolved_session,
        _intent="attribute",
        _analysis_purpose=analysis_purpose,
        _params_extra={
            "materialization_status": "expanded",
            "original_delta_ref": frame.ref,
            "missing_axes": missing_axes,
            "expanded_current_ref": expanded_current.ref,
            "expanded_baseline_ref": expanded_baseline.ref,
            "expanded_delta_ref": expanded_delta.ref,
            "alignment_policy": alignment.model_dump(mode="json"),
        },
    )
