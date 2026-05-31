"""Compare two MetricFrames into a DeltaFrame."""

from __future__ import annotations

import calendar as calendar_lib
import hashlib
import json
import secrets
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any, Literal, cast

import numpy as np
import pandas as pd

from marivo.analysis.calendar.align import _local_dates, align_calendar_frames
from marivo.analysis.calendar.model import CalendarPolicy
from marivo.analysis.errors import (
    AlignmentFailedError,
    AlignmentPolicyNotApplicableError,
    CalendarPolicyError,
    ComponentFrameMismatchError,
    ComponentFrameUnavailableError,
    CrossSessionFrameError,
    SegmentDimensionMismatchError,
    SemanticKindMismatchError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._validate import raise_first, validate_compare
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef
from marivo.analysis.session.attach import active as session_active
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.session.persistence import write_frame_to_disk, write_job_record

EXPECTED_METRIC_FRAME_KIND = "metric_frame"
PRESENCE_STATUS_COLUMN = "presence_status"


def _presence_status(*, has_current: bool, has_baseline: bool) -> str | float:
    if has_current and has_baseline:
        return "matched"
    if has_current:
        return "new"
    if has_baseline:
        return "churned"
    return np.nan


def _gen_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _display_kind(kind: str) -> str:
    return "".join(part.capitalize() for part in kind.split("_"))


def _frame_kind(frame: object) -> str | None:
    meta = getattr(frame, "meta", None)
    kind = getattr(meta, "kind", None)
    return kind if isinstance(kind, str) and kind else None


def _require_metric_frame(label: str, frame: object) -> MetricFrame:
    got_kind = _frame_kind(frame)
    if isinstance(frame, MetricFrame) and got_kind == EXPECTED_METRIC_FRAME_KIND:
        return frame
    if got_kind is None:
        got_kind = type(frame).__name__
    raise SemanticKindMismatchError(
        message=(
            f"compare(current, baseline) expected MetricFrame for `{label}`, got {_display_kind(got_kind)}."
        ),
        details={
            "parameter": label,
            "expected_kind": EXPECTED_METRIC_FRAME_KIND,
            "got_kind": got_kind,
        },
    )


# ---------------------------------------------------------------------------
# Component-aware compare helpers
# ---------------------------------------------------------------------------


def _component_decomposition_kind(frame: MetricFrame) -> str | None:
    """Return the decomposition kind if the frame is component-aware, else None."""
    decomp = frame.meta.decomposition
    if isinstance(decomp, dict) and decomp.get("kind"):
        return str(decomp["kind"])
    return None


def _load_component_for_compare(frame: MetricFrame, session: Session, label: str) -> ComponentFrame:
    """Load and validate the component frame for a compare input."""
    from marivo.analysis.session._load import load_frame

    if frame.meta.component_ref is None:
        raise ComponentFrameUnavailableError(
            message=(
                f"compare input '{label}' has decomposition metadata but no "
                "component_ref; component frame was not persisted by observe"
            ),
            details={"frame_ref": frame.ref, "label": label},
        )
    loaded = load_frame(frame.meta.component_ref, session=session)
    if not isinstance(loaded, ComponentFrame):
        raise ComponentFrameUnavailableError(
            message=(
                f"compare input '{label}' component_ref resolved to "
                f"{loaded.meta.kind!r}, expected component_frame"
            ),
            details={
                "frame_ref": frame.ref,
                "component_ref": frame.meta.component_ref,
                "loaded_kind": loaded.meta.kind,
            },
        )
    return loaded


def _require_compatible_components(
    current_comp: ComponentFrame,
    baseline_comp: ComponentFrame,
    current_parent: MetricFrame,
    baseline_parent: MetricFrame,
) -> None:
    """Validate that two component frames are compatible for delta computation."""
    if current_comp.meta.decomposition_kind != baseline_comp.meta.decomposition_kind:
        raise ComponentFrameMismatchError(
            message=(
                "compare inputs have incompatible decomposition kinds: "
                f"{current_comp.meta.decomposition_kind!r} vs "
                f"{baseline_comp.meta.decomposition_kind!r}"
            ),
            details={
                "current_kind": current_comp.meta.decomposition_kind,
                "baseline_kind": baseline_comp.meta.decomposition_kind,
            },
        )
    if current_comp.meta.components != baseline_comp.meta.components:
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component maps",
            details={
                "current_components": current_comp.meta.components,
                "baseline_components": baseline_comp.meta.components,
            },
        )
    if current_comp.meta.semantic_kind != baseline_comp.meta.semantic_kind:
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component semantic kinds",
            details={
                "current_semantic_kind": current_comp.meta.semantic_kind,
                "baseline_semantic_kind": baseline_comp.meta.semantic_kind,
            },
        )
    if current_comp.meta.axes != baseline_comp.meta.axes:
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component axes",
            details={
                "current_axes": current_comp.meta.axes,
                "baseline_axes": baseline_comp.meta.axes,
            },
        )
    if current_comp.meta.semantic_model != baseline_comp.meta.semantic_model:
        raise ComponentFrameMismatchError(
            message="compare inputs have incompatible component semantic models",
            details={
                "current_semantic_model": current_comp.meta.semantic_model,
                "baseline_semantic_model": baseline_comp.meta.semantic_model,
            },
        )


def _component_axis_columns(component: ComponentFrame) -> list[str]:
    """Extract time and dimension column names from a component frame's axes."""
    columns: list[str] = []
    for axis in component.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        role = axis.get("role")
        if role not in {"time", "dimension"}:
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            columns.append(column)
    return columns


def _component_role_columns(component: ComponentFrame) -> list[str]:
    axis_columns = set(_component_axis_columns(component))
    return [
        str(column) for column in component.to_pandas().columns if str(column) not in axis_columns
    ]


def _component_role_metric_frame(
    parent: MetricFrame,
    component: ComponentFrame,
    *,
    role_column: str,
) -> MetricFrame:
    axis_columns = _component_axis_columns(component)
    df = component.to_pandas()[[*axis_columns, role_column]].copy()
    meta = parent.meta.model_copy(
        update={
            "ref": f"{component.ref}_{role_column}",
            "axes": component.meta.axes,
            "measure": {"name": role_column},
            "semantic_kind": component.meta.semantic_kind,
            "component_ref": None,
            "decomposition": None,
        }
    )
    return MetricFrame(_df=df, meta=meta)


def _align_component_role(
    current_role: MetricFrame,
    baseline_role: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    session: Session,
) -> pd.DataFrame:
    if current_role.meta.semantic_kind == "segmented":
        aligned, _segment_info = _align_segmented(current_role, baseline_role)
        return aligned
    if current_role.meta.semantic_kind == "panel":
        aligned, _segment_info, _calendar_info, _window_info = _align_panel(
            current_role,
            baseline_role,
            alignment=alignment,
            session=session,
        )
        return aligned
    if current_role.meta.semantic_kind == "time_series":
        if alignment.kind == "window_bucket":
            aligned, _window_info = _align_time_series_window_bucket(current_role, baseline_role)
            return aligned
        calendar_ref = alignment.calendar
        if not isinstance(calendar_ref, CalendarRef):
            raise CalendarPolicyError(
                message="calendar-backed alignment requires CalendarRef",
                details={
                    "kind": "CalendarRefMissing",
                    "alignment": alignment.model_dump(mode="json"),
                },
            )
        loaded_calendar = session.calendars.get(calendar_ref.id)
        session_tz = str(session.tz)
        policy = CalendarPolicy(
            mode=alignment.kind,
            align_period=alignment.period,
            fallback=alignment.fallback,
        )
        current_df = current_role.to_pandas()
        baseline_df = baseline_role.to_pandas()
        time_column = _time_axis_column(current_role)
        value_column = _value_column(current_role, current_df, time_column=time_column)
        baseline_value_column = _value_column(
            baseline_role,
            baseline_df,
            time_column=time_column,
        )
        aligned, _info = align_calendar_frames(
            current_df[[time_column, value_column]],
            baseline_df[[time_column, baseline_value_column]].rename(
                columns={baseline_value_column: value_column}
            ),
            time_column=time_column,
            value_column=value_column,
            calendar=loaded_calendar,
            policy=policy,
            session_tz=session_tz,
        )
        return aligned
    return _align_and_compute(current_role.to_pandas(), baseline_role.to_pandas())


def _aligned_key_columns(aligned: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in aligned.columns
        if str(column) not in {PRESENCE_STATUS_COLUMN, "current", "baseline", "delta", "pct_change"}
    ]


def _align_component_frames(
    current_comp: ComponentFrame,
    baseline_comp: ComponentFrame,
    current_parent: MetricFrame,
    baseline_parent: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    session: Session,
) -> pd.DataFrame:
    """Merge current/baseline component data with delta columns using parent alignment logic."""
    role_columns = _component_role_columns(current_comp)
    result: pd.DataFrame | None = None
    key_columns: list[str] | None = None

    for role_column in role_columns:
        current_role = _component_role_metric_frame(
            current_parent,
            current_comp,
            role_column=role_column,
        )
        baseline_role = _component_role_metric_frame(
            baseline_parent,
            baseline_comp,
            role_column=role_column,
        )
        aligned = _align_component_role(
            current_role,
            baseline_role,
            alignment=alignment,
            session=session,
        )
        role_keys = _aligned_key_columns(aligned)
        renamed = aligned[[*role_keys, "current", "baseline", "delta"]].rename(
            columns={
                "current": f"current_{role_column}",
                "baseline": f"baseline_{role_column}",
                "delta": f"delta_{role_column}",
            }
        )
        if result is None:
            result = renamed
            key_columns = role_keys
            continue
        if role_keys != key_columns:
            raise ComponentFrameMismatchError(
                message="component role alignment produced incompatible key columns",
                details={
                    "role_column": role_column,
                    "expected_key_columns": key_columns,
                    "got_key_columns": role_keys,
                },
            )
        if not role_keys:
            # Scalar (no-axis) component frames: merge by position instead of
            # by key columns, since there are no axis columns to join on.
            result = pd.concat(
                [result.reset_index(drop=True), renamed.reset_index(drop=True)], axis=1
            )
        else:
            result = pd.merge(result, renamed, on=role_keys, how="outer")

    if result is None:
        raise ComponentFrameMismatchError(
            message="component frame has no role columns to align",
            details={"component_ref": current_comp.ref},
        )
    return result


def _persist_delta_component_frame(
    session: Session,
    df: pd.DataFrame,
    parent_ref: str,
    source_component: ComponentFrame,
    job_ref: str,
) -> ComponentFrame:
    """Persist the delta component frame and return it."""
    comp_ref = _gen_ref("comp")
    meta = ComponentFrameMeta(
        ref=comp_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(),
        parent_ref=parent_ref,
        parent_kind="delta_frame",
        metric_id=source_component.meta.metric_id,
        decomposition_kind=source_component.meta.decomposition_kind,
        components=source_component.meta.components,
        axes=source_component.meta.axes,
        semantic_kind=source_component.meta.semantic_kind,
        semantic_model=source_component.meta.semantic_model,
    )
    comp_frame = ComponentFrame(_df=df, meta=meta)
    comp_frame.meta = cast("ComponentFrameMeta", write_frame_to_disk(session.layout, comp_frame))
    return comp_frame


def compare(
    current: MetricFrame,
    baseline: MetricFrame,
    *,
    alignment: AlignmentPolicy | None = None,
    session: Session | None = None,
) -> DeltaFrame:
    """Compute the typed delta between two MetricFrames (current minus baseline).

    When to use: quantify change between two periods; produces a DeltaFrame for decompose or discover.

    The two frames must share ``metric_id`` and ``semantic_kind``. ``segmented``
    frames must share segment columns; ``panel`` frames must share grain.

    Args:
        current: Current-period MetricFrame.
        baseline: Baseline-period MetricFrame.
        alignment: Defaults to ``AlignmentPolicy(kind="window_bucket")``. For
            ``segmented`` frames, only ``window_bucket`` is supported in v1.
        session: Defaults to the currently-attached session. Both frames must
            belong to it.

    Raises:
        SemanticKindMismatchError: Different ``metric_id``, ``semantic_kind``, or
            ``current``/``baseline`` is not a MetricFrame.
        SegmentDimensionMismatchError: ``segmented`` frames disagree on segment columns.
        PanelGrainMismatchError: ``panel`` frames disagree on time grain.
        AlignmentPolicyNotApplicableError: Alignment kind incompatible with the frame shape.
        CrossSessionFrameError: A frame belongs to a different session.

    Example:
        >>> cur  = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2026-07-01", "end": "2026-09-30"})
        >>> base = session.observe(mv.MetricRef("sales.revenue"), timescope={"start": "2025-07-01", "end": "2025-09-30"})
        >>> delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))
    """
    if session is None:
        session = session_active()
    ensure_session_writable(session)
    if alignment is None:
        alignment = AlignmentPolicy(kind="window_bucket")
    if not isinstance(alignment, AlignmentPolicy):
        raise SemanticKindMismatchError(
            message="compare requires alignment=AlignmentPolicy(...)",
            details={
                "expected_kind": "AlignmentPolicy",
                "got_kind": type(alignment).__name__,
            },
        )
    current = _require_metric_frame("current", current)
    baseline = _require_metric_frame("baseline", baseline)
    for label, source_frame in (("current", current), ("baseline", baseline)):
        if source_frame.meta.session_id != session.id:
            raise CrossSessionFrameError(
                message=(
                    f"compare argument '{label}' belongs to session "
                    f"{source_frame.meta.session_id!r}, not {session.id!r}"
                ),
            )
    raise_first(validate_compare(current, baseline, alignment=alignment))

    # --- Component-aware validation ---
    current_decomp_kind = _component_decomposition_kind(current)
    baseline_decomp_kind = _component_decomposition_kind(baseline)
    current_component: ComponentFrame | None = None
    baseline_component: ComponentFrame | None = None
    if current_decomp_kind is not None or baseline_decomp_kind is not None:
        # At least one side declares decomposition; both must have component_ref
        current_component = _load_component_for_compare(current, session, "current")
        baseline_component = _load_component_for_compare(baseline, session, "baseline")
        _require_compatible_components(current_component, baseline_component, current, baseline)

    started_at = datetime.now(UTC)
    started = monotonic()
    calendar_info: dict[str, Any] | None = None
    segment_info: dict[str, Any] | None = None
    window_info: dict[str, Any] | None = None
    if current.meta.semantic_kind == "segmented":
        df, segment_info = _align_segmented(current, baseline)
    elif current.meta.semantic_kind == "panel":
        df, segment_info, calendar_info, window_info = _align_panel(
            current, baseline, alignment=alignment, session=session
        )
    elif alignment.kind == "window_bucket":
        if current.meta.semantic_kind == "time_series":
            _require_matching_time_series_bucket_grain(current, baseline)
            df, window_info = _align_time_series_window_bucket(current, baseline)
        else:
            df = _align_and_compute(current.to_pandas(), baseline.to_pandas())
    else:
        calendar_ref = alignment.calendar
        if not isinstance(calendar_ref, CalendarRef):
            raise CalendarPolicyError(
                message="calendar-backed alignment requires CalendarRef",
                details={
                    "kind": "CalendarRefMissing",
                    "alignment": alignment.model_dump(mode="json"),
                },
            )
        loaded_calendar = session.calendars.get(calendar_ref.id)
        session_tz = str(session.tz)
        policy = CalendarPolicy(
            mode=alignment.kind,
            align_period=alignment.period,
            fallback=alignment.fallback,
        )
        current_df = current.to_pandas()
        baseline_df = baseline.to_pandas()
        time_column = _time_axis_column(current)
        baseline_time_column = _time_axis_column(baseline)
        if baseline_time_column != time_column:
            raise AlignmentFailedError(
                message="calendar-backed compare alignment requires matching time axis columns",
                details={
                    "kind": "CalendarAlignTimeAxisMismatch",
                    "source_time_column": time_column,
                    "baseline_time_column": baseline_time_column,
                },
            )
        value_column = _value_column(current, current_df, time_column=time_column)
        _require_calendar_columns(
            current_df, frame_label="current", columns=(time_column, value_column)
        )
        _require_calendar_columns(
            baseline_df, frame_label="baseline", columns=(time_column, value_column)
        )
        df, info = align_calendar_frames(
            current_df,
            baseline_df,
            time_column=time_column,
            value_column=value_column,
            calendar=loaded_calendar,
            policy=policy,
            session_tz=session_tz,
        )
        calendar_info = info.model_dump(mode="json")
    if df.empty:
        raise AlignmentFailedError(message=f"alignment '{alignment.kind}' produced no rows")
    finished_at = datetime.now(UTC)

    frame_ref = _gen_ref("frame")
    job_ref = _gen_ref("job")
    alignment_dump = alignment.model_dump(mode="json")
    if alignment.kind == "window_bucket" and "bucket_start_b" in df.columns:
        alignment_dump["mode"] = "ordinal_bucket"
        alignment_dump["baseline_bucket_column"] = "bucket_start_b"
    if calendar_info is not None:
        alignment_dump["calendar_info"] = calendar_info
    if window_info is not None:
        alignment_dump["coverage"] = window_info
    if segment_info is not None:
        alignment_dump["segment_info"] = segment_info
    if current.meta.semantic_kind in {"segmented", "panel"}:
        alignment_dump["axes"] = current.meta.axes
    params = {
        "source_current_ref": current.ref,
        "source_baseline_ref": baseline.ref,
        "alignment": alignment_dump,
    }
    digest = f"sha256:{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()}"
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        created_at=finished_at,
        row_count=len(df),
        byte_size=0,
        lineage=Lineage.compose(
            current.lineage,
            baseline.lineage,
            new_step=LineageStep(
                intent="compare",
                job_ref=job_ref,
                inputs=[current.ref, baseline.ref],
                params_digest=digest,
            ),
        ),
        metric_id=current.meta.metric_id,
        source_current_ref=current.ref,
        source_baseline_ref=baseline.ref,
        alignment=alignment_dump,
        semantic_kind=current.meta.semantic_kind,
        semantic_model=current.meta.semantic_model,
        decomposition=current.meta.decomposition if current_component is not None else None,
    )
    output_frame = DeltaFrame(_df=df, meta=meta)

    # --- Evidence pipeline: commit_result replaces write_frame_to_disk ---
    subject = Subject(
        metric=current.meta.metric_id,
        slice=getattr(current.meta, "slice", None) or {},
        grain=_grain_from_axes(current),
        analysis_axis="change",
    )
    comparison_window_dict = _scope_for_window(current)
    commit_result(
        store=session.evidence_store(),
        frames_dir=session.layout.frames_dir,
        frame=output_frame,
        step_type="compare",
        inputs=CommitInputs(input_refs=[current.ref, baseline.ref]),
        params=CommitParams(values=params),
        semantic_anchors=CommitSemanticAnchors(
            values={"metric_id": current.meta.metric_id, "model": current.meta.semantic_model}
        ),
        subject=subject,
        extractor_family="delta_frame",
        comparison_window=comparison_window_dict,
        comparison_basis="left_vs_right",
    )

    # --- Persist delta component frame if both inputs are component-aware ---
    if current_component is not None and baseline_component is not None:
        comp_df = _align_component_frames(
            current_component,
            baseline_component,
            current,
            baseline,
            alignment=alignment,
            session=session,
        )
        delta_comp = _persist_delta_component_frame(
            session,
            comp_df,
            parent_ref=output_frame.ref,
            source_component=current_component,
            job_ref=job_ref,
        )
        output_frame.meta = output_frame.meta.model_copy(update={"component_ref": delta_comp.ref})
        # Re-persist the output frame meta with the component_ref
        write_frame_to_disk(session.layout, output_frame)

    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "compare",
            "params": params,
            "input_frame_refs": [current.ref, baseline.ref],
            "output_frame_ref": output_frame.meta.artifact_id or output_frame.ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": current.meta.semantic_model,
        },
    )
    return output_frame


def _dimension_columns(frame: MetricFrame) -> list[str]:
    columns: list[str] = []
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        if axis.get("role") != "dimension":
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            columns.append(column)
    return sorted(columns)


def _time_axis_column(frame: MetricFrame) -> str:
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        if axis.get("role") != "time":
            continue
        column = axis.get("column")
        if isinstance(column, str) and column:
            return column
    raise AlignmentFailedError(
        message="time axis column is required for calendar-backed alignment",
        details={"kind": "NoTimeAxis"},
    )


def _time_column_for_frame(frame: MetricFrame) -> str:
    return _time_axis_column(frame)


def _require_matching_time_series_bucket_grain(a: MetricFrame, b: MetricFrame) -> None:
    a_time_column = _time_axis_column(a)
    b_time_column = _time_axis_column(b)
    if a_time_column != b_time_column:
        raise AlignmentFailedError(
            message="window_bucket time_series alignment requires matching time axis columns",
            details={
                "kind": "WindowBucketTimeAxisMismatch",
                "current_time_column": a_time_column,
                "baseline_time_column": b_time_column,
            },
        )
    a_grain, b_grain = _panel_grains(a, b)
    if a_grain != b_grain:
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires same-grain time_series windows",
            details={
                "kind": "WindowBucketGrainMismatch",
                "current_grain": a_grain,
                "baseline_grain": b_grain,
            },
        )


def _panel_grain(frame: MetricFrame) -> str | None:
    for axis in frame.meta.axes.values():
        if not isinstance(axis, dict):
            continue
        if axis.get("role") != "time":
            continue
        grain = axis.get("grain")
        if isinstance(grain, str) and grain:
            return grain
    return None


def _panel_grains(a: MetricFrame, b: MetricFrame) -> tuple[str | None, str | None]:
    return _panel_grain(a), _panel_grain(b)


def _is_date_only(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return len(value) == 10


def _parse_window_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise AlignmentFailedError(
            message=f"window_bucket alignment requires window.{field}",
            details={"kind": "WindowBucketWindowMissing", "field": field},
        )
    if _is_date_only(value):
        return datetime.combine(date.fromisoformat(value), time.min)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise AlignmentFailedError(
            message=f"window_bucket alignment requires valid ISO window.{field}",
            details={"kind": "WindowBucketWindowInvalid", "field": field, "value": value},
        ) from exc


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    year = index // 12
    month = index % 12 + 1
    day = min(value.day, calendar_lib.monthrange(year, month)[1])
    return date(year, month, day)


def _truncate_bucket_date(value: date, *, grain: str) -> date:
    if grain == "day":
        return value
    if grain == "week":
        return value - timedelta(days=value.weekday())
    if grain == "month":
        return value.replace(day=1)
    if grain == "quarter":
        month = ((value.month - 1) // 3) * 3 + 1
        return value.replace(month=month, day=1)
    if grain == "year":
        return value.replace(month=1, day=1)
    raise AlignmentFailedError(
        message=f"window_bucket alignment does not support grain {grain!r}",
        details={"kind": "WindowBucketUnsupportedGrain", "grain": grain},
    )


def _advance_bucket_date(value: date, *, grain: str) -> date:
    if grain == "day":
        return value + timedelta(days=1)
    if grain == "week":
        return value + timedelta(weeks=1)
    if grain == "month":
        return _add_months(value, 1)
    if grain == "quarter":
        return _add_months(value, 3)
    if grain == "year":
        return value.replace(year=value.year + 1)
    raise AlignmentFailedError(
        message=f"window_bucket alignment does not support grain {grain!r}",
        details={"kind": "WindowBucketUnsupportedGrain", "grain": grain},
    )


def _bucket_key(value: object, *, grain: str) -> str:
    if value is None or pd.isna(cast("Any", value)):
        return ""
    timestamp = pd.Timestamp(cast("Any", value))
    if grain == "hour":
        return timestamp.floor("h").strftime("%Y-%m-%dT%H:00:00")
    bucket_date = _truncate_bucket_date(timestamp.date(), grain=grain)
    return bucket_date.isoformat()


def _window_bucket_values(frame: MetricFrame) -> list[object]:
    grain = _panel_grain(frame)
    window = frame.meta.window
    if not isinstance(window, dict) or not isinstance(window.get("start"), str):
        raise AlignmentFailedError(
            message=(
                "window_bucket ordinal alignment requires metric frame window metadata "
                "when bucket_start values do not overlap"
            ),
            details={"kind": "WindowBucketWindowMissing", "frame_ref": frame.ref},
        )
    if not isinstance(window.get("end"), str):
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires window.end metadata",
            details={"kind": "WindowBucketWindowMissing", "frame_ref": frame.ref},
        )
    if not isinstance(grain, str) or grain not in {
        "hour",
        "day",
        "week",
        "month",
        "quarter",
        "year",
    }:
        raise AlignmentFailedError(
            message=(
                "window_bucket ordinal alignment requires hour/day/week/month/quarter/year grain"
            ),
            details={"kind": "WindowBucketGrainMissing", "frame_ref": frame.ref, "grain": grain},
        )

    start_raw = window["start"]
    end_raw = window["end"]
    if grain == "hour":
        current = _parse_window_datetime(start_raw, field="start").replace(
            minute=0, second=0, microsecond=0
        )
        if _is_date_only(end_raw):
            stop_exclusive = datetime.combine(date.fromisoformat(end_raw), time.min) + timedelta(
                days=1
            )
            values: list[object] = []
            while current < stop_exclusive:
                values.append(pd.Timestamp(current))
                current += timedelta(hours=1)
            return values
        stop = _parse_window_datetime(end_raw, field="end").replace(
            minute=0, second=0, microsecond=0
        )
        values = []
        while current <= stop:
            values.append(pd.Timestamp(current))
            current += timedelta(hours=1)
        return values

    current_date = _truncate_bucket_date(
        _parse_window_datetime(start_raw, field="start").date(), grain=grain
    )
    stop_date = _truncate_bucket_date(
        _parse_window_datetime(end_raw, field="end").date(), grain=grain
    )
    values = []
    while current_date <= stop_date:
        values.append(current_date)
        current_date = _advance_bucket_date(current_date, grain=grain)
    return values


def _prepared_value_map(
    df: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
    grain: str,
) -> dict[str, tuple[object, object]]:
    if df.empty:
        return {}
    keys = df[time_column].map(lambda value: _bucket_key(value, grain=grain))
    if keys.duplicated().any():
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires unique bucket_start values",
            details={"kind": "WindowBucketDuplicateBuckets"},
        )
    return {
        str(key): (row[time_column], row[value_column])
        for key, (_, row) in zip(keys, df.iterrows(), strict=True)
        if key
    }


def _compute_delta_columns(df: pd.DataFrame) -> pd.DataFrame:
    df["current"] = pd.to_numeric(df["current"], errors="coerce")
    df["baseline"] = pd.to_numeric(df["baseline"], errors="coerce")
    current_for_delta = df["current"]
    baseline_for_delta = df["baseline"]
    if PRESENCE_STATUS_COLUMN in df.columns:
        current_for_delta = current_for_delta.mask(
            df[PRESENCE_STATUS_COLUMN] == "churned",
            0.0,
        )
        baseline_for_delta = baseline_for_delta.mask(
            df[PRESENCE_STATUS_COLUMN] == "new",
            0.0,
        )
        df["current"] = df["current"].mask(df[PRESENCE_STATUS_COLUMN] == "churned", 0.0)
        df["baseline"] = df["baseline"].mask(df[PRESENCE_STATUS_COLUMN] == "new", 0.0)
    df["delta"] = current_for_delta - baseline_for_delta
    baseline = df["baseline"]
    df["pct_change"] = np.where(
        baseline.notna() & (baseline != 0),
        df["delta"] / baseline,
        np.nan,
    )
    return df


def _align_prepared_window_bucket(
    a_prepared: pd.DataFrame,
    b_prepared: pd.DataFrame,
    *,
    time_column: str,
    a_value_column: str,
    b_value_column: str,
    current_frame: MetricFrame,
    baseline_frame: MetricFrame,
    track_presence_status: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    grain = _panel_grain(current_frame)
    if grain != _panel_grain(baseline_frame) or not isinstance(grain, str):
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires same-grain windows",
            details={
                "kind": "WindowBucketGrainMismatch",
                "current_grain": grain,
                "baseline_grain": _panel_grain(baseline_frame),
            },
        )
    a_values = _prepared_value_map(
        a_prepared,
        time_column=time_column,
        value_column=a_value_column,
        grain=grain,
    )
    b_values = _prepared_value_map(
        b_prepared,
        time_column=time_column,
        value_column=b_value_column,
        grain=grain,
    )
    shared_keys = set(a_values) & set(b_values)
    if shared_keys:
        rows: list[dict[str, object]] = []
        for key in sorted(set(a_values) | set(b_values)):
            has_current = key in a_values
            has_baseline = key in b_values
            a_time, current_value = a_values.get(key, (None, np.nan))
            b_time, baseline_value = b_values.get(key, (None, np.nan))
            row = {
                time_column: a_time if a_time is not None else b_time,
                "current": current_value,
                "baseline": baseline_value,
            }
            if track_presence_status:
                row[PRESENCE_STATUS_COLUMN] = _presence_status(
                    has_current=has_current,
                    has_baseline=has_baseline,
                )
            rows.append(row)
        result = _compute_delta_columns(pd.DataFrame(rows))
        result_columns = [time_column, "current", "baseline", "delta", "pct_change"]
        if track_presence_status:
            result_columns.insert(1, PRESENCE_STATUS_COLUMN)
        return result[result_columns], None

    current_buckets = _window_bucket_values(current_frame)
    baseline_buckets = _window_bucket_values(baseline_frame)
    if len(current_buckets) != len(baseline_buckets):
        raise AlignmentFailedError(
            message=(
                "window_bucket ordinal alignment requires equal expected bucket counts; "
                f"current window has {len(current_buckets)} buckets, baseline window has "
                f"{len(baseline_buckets)} buckets"
            ),
            details={
                "kind": "WindowBucketExpectedCountMismatch",
                "current_expected_buckets": len(current_buckets),
                "baseline_expected_buckets": len(baseline_buckets),
            },
        )

    rows = []
    current_present = 0
    baseline_present = 0
    for current_bucket, baseline_bucket in zip(current_buckets, baseline_buckets, strict=True):
        current_key = _bucket_key(current_bucket, grain=grain)
        baseline_key = _bucket_key(baseline_bucket, grain=grain)
        current_value = a_values.get(current_key, (None, np.nan))[1]
        baseline_value = b_values.get(baseline_key, (None, np.nan))[1]
        has_current = current_key in a_values
        has_baseline = baseline_key in b_values
        if not pd.isna(cast("Any", current_value)):
            current_present += 1
        if not pd.isna(cast("Any", baseline_value)):
            baseline_present += 1
        row = {
            time_column: current_bucket,
            f"{time_column}_b": baseline_bucket,
            "current": current_value,
            "baseline": baseline_value,
        }
        if track_presence_status:
            row[PRESENCE_STATUS_COLUMN] = _presence_status(
                has_current=has_current,
                has_baseline=has_baseline,
            )
        rows.append(row)
    result = _compute_delta_columns(pd.DataFrame(rows))
    coverage = {
        "current": {
            "expected_buckets": len(current_buckets),
            "present_buckets": current_present,
            "missing_buckets": len(current_buckets) - current_present,
        },
        "baseline": {
            "expected_buckets": len(baseline_buckets),
            "present_buckets": baseline_present,
            "missing_buckets": len(baseline_buckets) - baseline_present,
        },
    }
    result_columns = [time_column, f"{time_column}_b", "current", "baseline", "delta", "pct_change"]
    if track_presence_status:
        result_columns.insert(2, PRESENCE_STATUS_COLUMN)
    return result[result_columns], coverage


def _aggregate_window_info(infos: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not infos:
        return None
    result: dict[str, Any] = {}
    for side in ("current", "baseline"):
        result[side] = {
            field: sum(int(info.get(side, {}).get(field, 0)) for info in infos)
            for field in ("expected_buckets", "present_buckets", "missing_buckets")
        }
    return result


def _value_column(frame: MetricFrame, df: pd.DataFrame, *, time_column: str) -> str:
    non_time_columns = [str(column) for column in df.columns if str(column) != time_column]
    measure_name = frame.meta.measure.get("name")
    if (
        isinstance(measure_name, str)
        and measure_name
        and measure_name != time_column
        and measure_name in df.columns
    ):
        return measure_name
    if len(non_time_columns) == 1:
        return non_time_columns[0]
    if not non_time_columns:
        raise AlignmentFailedError(
            message="calendar-backed compare alignment requires at least one value column",
            details={"kind": "CalendarAlignValueColumnMissing", "time_column": time_column},
        )
    raise AlignmentFailedError(
        message="calendar-backed compare alignment requires exactly one value column",
        details={
            "kind": "CalendarAlignValueColumnAmbiguous",
            "time_column": time_column,
            "value_candidates": non_time_columns,
            "measure_name": measure_name if isinstance(measure_name, str) else None,
        },
    )


def _value_column_segmented(frame: MetricFrame, df: pd.DataFrame, *, dim_columns: list[str]) -> str:
    missing_dimensions = [column for column in dim_columns if column not in df.columns]
    if missing_dimensions:
        raise AlignmentFailedError(
            message="segmented compare alignment frame is missing dimension columns",
            details={
                "kind": "SegmentDimensionColumnMissing",
                "missing_columns": missing_dimensions,
                "available_columns": [str(column) for column in df.columns],
            },
        )
    non_dimension_columns = [str(column) for column in df.columns if str(column) not in dim_columns]
    measure_name = frame.meta.measure.get("name")
    if (
        isinstance(measure_name, str)
        and measure_name
        and measure_name not in dim_columns
        and measure_name in df.columns
    ):
        return measure_name
    if len(non_dimension_columns) == 1:
        return non_dimension_columns[0]
    if not non_dimension_columns:
        raise AlignmentFailedError(
            message="segmented compare alignment requires at least one value column",
            details={"kind": "SegmentValueColumnMissing", "dimension_columns": dim_columns},
        )
    raise AlignmentFailedError(
        message="segmented compare alignment requires exactly one value column",
        details={
            "kind": "SegmentValueColumnAmbiguous",
            "dimension_columns": dim_columns,
            "value_candidates": non_dimension_columns,
            "measure_name": measure_name if isinstance(measure_name, str) else None,
        },
    )


def _align_segmented(a: MetricFrame, b: MetricFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    dim_columns = _dimension_columns(a)
    b_dim_columns = _dimension_columns(b)
    if dim_columns != b_dim_columns:
        raise SegmentDimensionMismatchError(
            message="compare requires matching segment dimension columns",
            details={
                "kind": "SegmentDimensionMismatch",
                "current_dimensions": dim_columns,
                "baseline_dimensions": b_dim_columns,
            },
        )
    if not dim_columns:
        raise AlignmentFailedError(
            message="segmented compare requires at least one dimension axis",
            details={"kind": "SegmentDimensionMissing"},
        )
    a_df = a.to_pandas()
    b_df = b.to_pandas()
    a_value = _value_column_segmented(a, a_df, dim_columns=dim_columns)
    b_value = _value_column_segmented(b, b_df, dim_columns=dim_columns)
    a_prepared = a_df[[*dim_columns, a_value]].rename(columns={a_value: "current"})
    b_prepared = b_df[[*dim_columns, b_value]].rename(columns={b_value: "baseline"})
    merged = pd.merge(
        a_prepared,
        b_prepared,
        how="outer",
        on=dim_columns,
        indicator="_segment_presence",
    )
    merged = merged.sort_values(dim_columns).reset_index(drop=True)
    merged[PRESENCE_STATUS_COLUMN] = merged["_segment_presence"].map(
        {"both": "matched", "left_only": "new", "right_only": "churned"}
    )
    merged = _compute_delta_columns(merged)
    result_columns = [
        *dim_columns,
        PRESENCE_STATUS_COLUMN,
        "current",
        "baseline",
        "delta",
        "pct_change",
    ]
    result = merged[result_columns]
    segment_info = {
        "segment_count": len(result),
        "a_only_segments_count": int((merged["_segment_presence"] == "left_only").sum()),
        "b_only_segments_count": int((merged["_segment_presence"] == "right_only").sum()),
    }
    return result, segment_info


def _align_panel(
    a: MetricFrame,
    b: MetricFrame,
    *,
    alignment: AlignmentPolicy,
    session: Session,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    dim_columns = _dimension_columns(a)
    if not dim_columns:
        raise AlignmentFailedError(
            message="panel compare requires at least one dimension axis",
            details={"kind": "PanelDimensionMissing"},
        )
    time_column = _time_column_for_frame(a)
    b_time_column = _time_column_for_frame(b)
    if b_time_column != time_column:
        raise AlignmentFailedError(
            message="panel compare requires matching time axis columns",
            details={
                "kind": "PanelTimeAxisMismatch",
                "source_time_column": time_column,
                "baseline_time_column": b_time_column,
            },
        )

    a_df = a.to_pandas()
    b_df = b.to_pandas()
    a_value = _value_column_segmented(a, a_df, dim_columns=[*dim_columns, time_column])
    b_value = _value_column_segmented(b, b_df, dim_columns=[*dim_columns, time_column])
    _require_calendar_columns(
        a_df, frame_label="current", columns=(*dim_columns, time_column, a_value)
    )
    _require_calendar_columns(
        b_df, frame_label="baseline", columns=(*dim_columns, time_column, b_value)
    )

    a_groups = _panel_groups(a_df, dim_columns=dim_columns)
    b_groups = _panel_groups(b_df, dim_columns=dim_columns)
    segment_keys = sorted(
        set(a_groups) | set(b_groups),
        key=lambda key: tuple("" if item is None else str(item) for item in key),
    )
    pieces: list[pd.DataFrame] = []
    calendar_infos: list[dict[str, Any]] = []
    window_infos: list[dict[str, Any]] = []
    calendar_context = (
        _calendar_context(alignment, session=session) if alignment.kind != "window_bucket" else None
    )

    for key in segment_keys:
        a_part = a_groups.get(key)
        b_part = b_groups.get(key)
        if a_part is None and b_part is None:
            continue
        if a_part is None:
            assert b_part is not None
            if alignment.kind == "window_bucket":
                delta, window_info_piece = _align_panel_window_bucket(
                    pd.DataFrame(columns=[time_column, a_value]),
                    b_part,
                    time_column=time_column,
                    a_value_column=a_value,
                    b_value_column=b_value,
                    current_frame=a,
                    baseline_frame=b,
                )
                if window_info_piece is not None:
                    window_infos.append(window_info_piece)
            else:
                assert calendar_context is not None
                delta = _one_sided_panel_calendar_delta(
                    b_part,
                    time_column=time_column,
                    value_column=b_value,
                    side="baseline",
                    session_tz=calendar_context[2],
                )
        elif b_part is None:
            if alignment.kind == "window_bucket":
                delta, window_info_piece = _align_panel_window_bucket(
                    a_part,
                    pd.DataFrame(columns=[time_column, b_value]),
                    time_column=time_column,
                    a_value_column=a_value,
                    b_value_column=b_value,
                    current_frame=a,
                    baseline_frame=b,
                )
                if window_info_piece is not None:
                    window_infos.append(window_info_piece)
            else:
                assert calendar_context is not None
                delta = _one_sided_panel_calendar_delta(
                    a_part,
                    time_column=time_column,
                    value_column=a_value,
                    side="current",
                    session_tz=calendar_context[2],
                )
        elif alignment.kind == "window_bucket":
            delta, window_info_piece = _align_panel_window_bucket(
                a_part,
                b_part,
                time_column=time_column,
                a_value_column=a_value,
                b_value_column=b_value,
                current_frame=a,
                baseline_frame=b,
            )
            if window_info_piece is not None:
                window_infos.append(window_info_piece)
        else:
            assert calendar_context is not None
            loaded_calendar, policy, session_tz = calendar_context
            delta, calendar_alignment_info = align_calendar_frames(
                a_part[[time_column, a_value]],
                b_part[[time_column, b_value]].rename(columns={b_value: a_value}),
                time_column=time_column,
                value_column=a_value,
                calendar=loaded_calendar,
                policy=policy,
                session_tz=session_tz,
            )
            calendar_infos.append(calendar_alignment_info.model_dump(mode="json"))

        for column, value in zip(dim_columns, key, strict=True):
            delta[column] = cast("Any", value)
        pieces.append(delta)

    if pieces:
        result = pd.concat(pieces, ignore_index=True)
    else:
        result = pd.DataFrame(
            columns=[
                time_column,
                *dim_columns,
                PRESENCE_STATUS_COLUMN,
                "current",
                "baseline",
                "delta",
                "pct_change",
            ]
        )

    if alignment.kind == "window_bucket":
        time_columns = [time_column]
        baseline_time_column = f"{time_column}_b"
        if baseline_time_column in result.columns:
            time_columns.append(baseline_time_column)
        result = result[
            [
                *time_columns,
                *dim_columns,
                PRESENCE_STATUS_COLUMN,
                "current",
                "baseline",
                "delta",
                "pct_change",
            ]
        ]
        sort_columns = [*dim_columns, time_column]
    else:
        leading_columns = [*dim_columns]
        result = result[
            [*leading_columns, *[c for c in result.columns if c not in leading_columns]]
        ]
        sort_columns = [*dim_columns]
        if "bucket_start_a" in result.columns:
            sort_columns.append("bucket_start_a")
    result = result.sort_values(sort_columns, na_position="last").reset_index(drop=True)

    segment_info: dict[str, Any] = {
        "segment_count": len(segment_keys),
        "a_only_segments_count": sum(
            1 for key in segment_keys if key in a_groups and key not in b_groups
        ),
        "b_only_segments_count": sum(
            1 for key in segment_keys if key in b_groups and key not in a_groups
        ),
    }
    window_info = _aggregate_window_info(window_infos)
    if window_info is not None:
        segment_info["coverage"] = window_info
    return result, segment_info, _aggregate_calendar_info(calendar_infos), window_info


def _panel_groups(
    df: pd.DataFrame,
    *,
    dim_columns: list[str],
) -> dict[tuple[object, ...], pd.DataFrame]:
    groups: dict[tuple[object, ...], pd.DataFrame] = {}
    grouped = df.groupby(dim_columns, dropna=False, sort=False)
    for raw_key, group in grouped:
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        groups[tuple(None if pd.isna(cast("Any", value)) else value for value in key)] = (
            group.copy()
        )
    return groups


def _one_sided_panel_calendar_delta(
    df: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
    side: str,
    session_tz: str,
) -> pd.DataFrame:
    prepared = df[[time_column, value_column]].sort_values(time_column).reset_index(drop=True)
    bucket_starts = _local_dates(prepared[time_column], session_tz=session_tz).map(
        lambda value: value.isoformat()
    )
    values = pd.to_numeric(prepared[value_column], errors="coerce")
    result = pd.DataFrame(
        {
            PRESENCE_STATUS_COLUMN: "new" if side == "current" else "churned",
            "align_key": np.nan,
            "align_quality": "unmatched",
            "bucket_start_a": bucket_starts if side == "current" else np.nan,
            "bucket_start_b": bucket_starts if side == "baseline" else np.nan,
        }
    )
    if side == "current":
        result["current"] = values
        result["baseline"] = 0.0
    else:
        result["current"] = 0.0
        result["baseline"] = values
    return _compute_delta_columns(result)


def _align_panel_window_bucket(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    time_column: str,
    a_value_column: str,
    b_value_column: str,
    current_frame: MetricFrame,
    baseline_frame: MetricFrame,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    a_prepared = (
        a_df[[time_column, a_value_column]]
        .sort_values(time_column)
        .rename(columns={a_value_column: "current"})
        .reset_index(drop=True)
    )
    b_prepared = (
        b_df[[time_column, b_value_column]]
        .sort_values(time_column)
        .rename(columns={b_value_column: "baseline"})
        .reset_index(drop=True)
    )
    return _align_prepared_window_bucket(
        a_prepared,
        b_prepared,
        time_column=time_column,
        a_value_column="current",
        b_value_column="baseline",
        current_frame=current_frame,
        baseline_frame=baseline_frame,
        track_presence_status=True,
    )


def _calendar_context(
    alignment: AlignmentPolicy, *, session: Session
) -> tuple[Any, CalendarPolicy, str]:
    if alignment.kind == "window_bucket":
        raise AlignmentPolicyNotApplicableError(
            message="window_bucket alignment does not require calendar context",
            details={"kind": "AlignmentPolicyNotApplicable", "alignment_kind": alignment.kind},
        )
    calendar_ref = alignment.calendar
    if not isinstance(calendar_ref, CalendarRef):
        raise CalendarPolicyError(
            message="calendar-backed alignment requires CalendarRef",
            details={
                "kind": "CalendarRefMissing",
                "alignment": alignment.model_dump(mode="json"),
            },
        )
    loaded_calendar = session.calendars.get(calendar_ref.id)
    session_tz = str(session.tz)
    policy = CalendarPolicy(
        mode=alignment.kind,
        align_period=alignment.period,
        fallback=alignment.fallback,
    )
    return loaded_calendar, policy, session_tz


def _aggregate_calendar_info(infos: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not infos:
        return None
    aggregated = dict(infos[0])
    for field in ("matched_rows", "fallback_rows", "dropped_rows_a", "dropped_rows_b"):
        aggregated[field] = sum(int(info.get(field, 0)) for info in infos)
    return aggregated


def _require_calendar_columns(
    df: pd.DataFrame, *, frame_label: str, columns: tuple[str, ...]
) -> None:
    missing_columns = [column for column in columns if column not in df.columns]
    if not missing_columns:
        return
    raise AlignmentFailedError(
        message=(
            f"calendar-backed compare alignment frame '{frame_label}' is missing required columns"
        ),
        details={
            "kind": "CalendarAlignColumnMissing",
            "frame": frame_label,
            "missing_columns": missing_columns,
            "available_columns": [str(column) for column in df.columns],
        },
    )


def _align_time_series_window_bucket(
    a: MetricFrame,
    b: MetricFrame,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    time_column = _time_axis_column(a)
    b_time_column = _time_axis_column(b)
    if b_time_column != time_column:
        raise AlignmentFailedError(
            message="window_bucket time_series alignment requires matching time axis columns",
            details={
                "kind": "WindowBucketTimeAxisMismatch",
                "current_time_column": time_column,
                "baseline_time_column": b_time_column,
            },
        )
    a_df = a.to_pandas()
    b_df = b.to_pandas()
    a_value = _value_column(a, a_df, time_column=time_column)
    b_value = _value_column(b, b_df, time_column=time_column)
    a_prepared = (
        a_df[[time_column, a_value]]
        .rename(columns={a_value: "current"})
        .sort_values(time_column)
        .reset_index(drop=True)
    )
    b_prepared = (
        b_df[[time_column, b_value]]
        .rename(columns={b_value: "baseline"})
        .sort_values(time_column)
        .reset_index(drop=True)
    )
    return _align_prepared_window_bucket(
        a_prepared,
        b_prepared,
        time_column=time_column,
        a_value_column="current",
        b_value_column="baseline",
        current_frame=a,
        baseline_frame=b,
    )


def _align_and_compute(a_df: pd.DataFrame, b_df: pd.DataFrame) -> pd.DataFrame:
    if len(a_df.columns) == 1 and len(b_df.columns) == 1:
        return _sample_align(a_df, b_df)
    key = a_df.columns[0]
    merged = pd.merge(a_df, b_df, on=key, suffixes=("_a", "_b"))
    if merged.empty:
        return _ordinal_bucket_align(a_df, b_df, key=key)
    value_cols_a = [col for col in merged.columns if col.endswith("_a")]
    value_cols_b = [col for col in merged.columns if col.endswith("_b")]
    if not value_cols_a or not value_cols_b:
        raise AlignmentFailedError(
            message="window_bucket alignment could not find paired value columns"
        )
    current = merged[value_cols_a[0]].to_numpy()
    baseline = merged[value_cols_b[0]].to_numpy()
    return pd.DataFrame(
        {
            key: merged[key],
            "current": current,
            "baseline": baseline,
            "delta": current - baseline,
            "pct_change": np.where(baseline != 0, (current - baseline) / baseline, np.nan),
        }
    )


def _ordinal_bucket_align(a_df: pd.DataFrame, b_df: pd.DataFrame, *, key: str) -> pd.DataFrame:
    if len(a_df) != len(b_df):
        raise AlignmentFailedError(
            message=_window_bucket_unequal_length_message(
                current_rows=len(a_df),
                baseline_rows=len(b_df),
            ),
            details={
                "kind": "WindowBucketNoComparableBuckets",
                "current_rows": len(a_df),
                "baseline_rows": len(b_df),
            },
        )
    if a_df[key].duplicated().any() or b_df[key].duplicated().any():
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires unique bucket_start values",
            details={"kind": "WindowBucketDuplicateBuckets"},
        )
    value_cols_a = [column for column in a_df.columns if column != key]
    value_cols_b = [column for column in b_df.columns if column != key]
    if len(value_cols_a) != 1 or len(value_cols_b) != 1:
        raise AlignmentFailedError(
            message="window_bucket ordinal alignment requires exactly one value column per frame",
            details={
                "kind": "WindowBucketValueColumnAmbiguous",
                "current_value_columns": [str(column) for column in value_cols_a],
                "baseline_value_columns": [str(column) for column in value_cols_b],
            },
        )
    a_sorted = a_df.sort_values(key).reset_index(drop=True)
    b_sorted = b_df.sort_values(key).reset_index(drop=True)
    current = pd.to_numeric(a_sorted[value_cols_a[0]], errors="coerce")
    baseline = pd.to_numeric(b_sorted[value_cols_b[0]], errors="coerce")
    delta = current - baseline
    return pd.DataFrame(
        {
            key: a_sorted[key],
            f"{key}_b": b_sorted[key],
            "current": current,
            "baseline": baseline,
            "delta": delta,
            "pct_change": np.where(baseline != 0, delta / baseline, np.nan),
        }
    )


def _window_bucket_unequal_length_message(*, current_rows: int, baseline_rows: int) -> str:
    return (
        "window_bucket alignment requires shared bucket_start values or "
        "equal-length same-grain windows for ordinal bucket alignment; "
        f"current has {current_rows} rows, baseline has {baseline_rows} rows; "
        "equal-length windows are required for ordinal bucket alignment"
    )


def _sample_align(a_df: pd.DataFrame, b_df: pd.DataFrame) -> pd.DataFrame:
    n = min(len(a_df), len(b_df))
    current = a_df.reset_index(drop=True).iloc[:n, 0].to_numpy()
    baseline = b_df.reset_index(drop=True).iloc[:n, 0].to_numpy()
    return pd.DataFrame(
        {
            "current": current,
            "baseline": baseline,
            "delta": current - baseline,
            "pct_change": np.where(baseline != 0, (current - baseline) / baseline, np.nan),
        }
    )


def _scope_for_window(frame: MetricFrame) -> dict[str, Any] | None:
    """Extract comparison_window dict from a MetricFrame's window metadata."""
    window = getattr(frame.meta, "window", None)
    if window is None:
        return None
    if isinstance(window, dict):
        return window
    return None


def _grain_from_axes(frame: MetricFrame) -> Literal["hour", "day", "week", "month"] | None:
    """Extract grain from a MetricFrame's axes metadata."""
    axes = getattr(frame.meta, "axes", {})
    for axis in axes.values():
        if isinstance(axis, dict) and axis.get("role") == "time":
            grain = axis.get("grain")
            if isinstance(grain, str) and grain in ("hour", "day", "week", "month"):
                return grain  # type: ignore[return-value]
    return None
