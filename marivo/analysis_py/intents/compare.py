"""Compare two MetricFrames into a DeltaFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
import secrets
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal

import numpy as np
import pandas as pd

from marivo.analysis_py.calendar.align import _local_dates, align_calendar_frames
from marivo.analysis_py.calendar.model import CalendarPolicy
from marivo.analysis_py.errors import (
    AlignmentFailedError,
    AlignmentPolicyNotApplicableError,
    CalendarPolicyError,
    CrossSessionFrameError,
    PanelGrainMismatchError,
    SegmentDimensionMismatchError,
    SemanticKindMismatchError,
)
from marivo.analysis_py.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis_py.evidence.types import Subject
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.lineage import Lineage, LineageStep
from marivo.analysis_py.policies import AlignmentPolicy
from marivo.analysis_py.refs import CalendarRef
from marivo.analysis_py.session.attach import active as session_active
from marivo.analysis_py.session.core import Session, ensure_session_writable
from marivo.analysis_py.session.persistence import write_job_record

EXPECTED_METRIC_FRAME_KIND = "metric_frame"


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
            f"compare(a, b) expected MetricFrame for `{label}`, got {_display_kind(got_kind)}."
        ),
        details={
            "parameter": label,
            "expected_kind": EXPECTED_METRIC_FRAME_KIND,
            "got_kind": got_kind,
        },
    )


def compare(
    a: MetricFrame,
    b: MetricFrame,
    *,
    alignment: AlignmentPolicy | None = None,
    session: Session | None = None,
) -> DeltaFrame:
    if session is None:
        session = session_active()
    ensure_session_writable(session)
    if alignment is None:
        alignment = AlignmentPolicy(kind="calendar_bucket")
    if not isinstance(alignment, AlignmentPolicy):
        raise SemanticKindMismatchError(
            message="compare requires alignment=AlignmentPolicy(...)",
            details={
                "expected_kind": "AlignmentPolicy",
                "got_kind": type(alignment).__name__,
            },
        )
    a = _require_metric_frame("a", a)
    b = _require_metric_frame("b", b)
    for label, source_frame in (("a", a), ("b", b)):
        if source_frame.meta.session_id != session.id:
            raise CrossSessionFrameError(
                message=(
                    f"compare argument '{label}' belongs to session "
                    f"{source_frame.meta.session_id!r}, not {session.id!r}"
                ),
            )
    if a.meta.metric_id != b.meta.metric_id:
        raise SemanticKindMismatchError(
            message=f"compare requires the same metric, got {a.meta.metric_id!r} and {b.meta.metric_id!r}",
        )
    if a.meta.semantic_kind != b.meta.semantic_kind:
        raise SemanticKindMismatchError(
            message=(
                "compare requires matching semantic_kind, got "
                f"{a.meta.semantic_kind!r} and {b.meta.semantic_kind!r}"
            ),
        )
    if a.meta.semantic_kind in {"segmented", "panel"}:
        a_dimensions = _dimension_columns(a)
        b_dimensions = _dimension_columns(b)
        if a_dimensions != b_dimensions:
            raise SegmentDimensionMismatchError(
                message="compare requires matching segment dimension columns",
                details={
                    "kind": "SegmentDimensionMismatch",
                    "current_dimensions": a_dimensions,
                    "baseline_dimensions": b_dimensions,
                },
            )
    if a.meta.semantic_kind == "panel":
        a_grain, b_grain = _panel_grains(a, b)
        if a_grain != b_grain:
            raise PanelGrainMismatchError(
                message="panel compare requires matching time grain",
                details={
                    "kind": "PanelGrainMismatch",
                    "current_grain": a_grain,
                    "baseline_grain": b_grain,
                },
            )

    started_at = datetime.now(UTC)
    started = monotonic()
    calendar_info: dict[str, Any] | None = None
    segment_info: dict[str, Any] | None = None
    if a.meta.semantic_kind == "segmented":
        if alignment.kind != "calendar_bucket":
            raise AlignmentPolicyNotApplicableError(
                message="segmented compare supports only calendar_bucket alignment",
                details={
                    "kind": "AlignmentPolicyNotApplicable",
                    "semantic_kind": "segmented",
                    "alignment_kind": alignment.kind,
                },
            )
        df, segment_info = _align_segmented(a, b)
    elif a.meta.semantic_kind == "panel":
        df, segment_info, calendar_info = _align_panel(a, b, alignment=alignment, session=session)
    elif alignment.kind == "calendar_bucket":
        if a.meta.semantic_kind == "time_series":
            _require_matching_time_series_bucket_grain(a, b)
        df = _align_and_compute(a.to_pandas(), b.to_pandas())
    else:
        if a.meta.semantic_kind != "time_series" or b.meta.semantic_kind != "time_series":
            raise SemanticKindMismatchError(
                message="calendar-backed compare alignment requires time_series MetricFrames",
                details={
                    "kind": "CalendarAlignRequiresTimeSeries",
                    "expected_kind": "time_series",
                    "got_kind": {
                        "a": a.meta.semantic_kind,
                        "b": b.meta.semantic_kind,
                    },
                },
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
        if loaded_calendar.timezone != session_tz:
            raise CalendarPolicyError(
                message="calendar timezone must match session timezone",
                details={
                    "kind": "CalendarTimezoneMismatch",
                    "calendar_name": calendar_ref.id,
                    "calendar_timezone": loaded_calendar.timezone,
                    "session_timezone": session_tz,
                },
            )
        policy = CalendarPolicy(
            mode=alignment.kind,
            align_period=alignment.period,
            fallback=alignment.fallback,
        )
        a_df = a.to_pandas()
        b_df = b.to_pandas()
        time_column = _time_axis_column(a)
        b_time_column = _time_axis_column(b)
        if b_time_column != time_column:
            raise AlignmentFailedError(
                message="calendar-backed compare alignment requires matching time axis columns",
                details={
                    "kind": "CalendarAlignTimeAxisMismatch",
                    "source_time_column": time_column,
                    "baseline_time_column": b_time_column,
                },
            )
        value_column = _value_column(a, a_df, time_column=time_column)
        _require_calendar_columns(a_df, frame_label="a", columns=(time_column, value_column))
        _require_calendar_columns(b_df, frame_label="b", columns=(time_column, value_column))
        df, info = align_calendar_frames(
            a_df,
            b_df,
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
    if alignment.kind == "calendar_bucket" and "bucket_start_b" in df.columns:
        alignment_dump["mode"] = "ordinal_bucket"
        alignment_dump["baseline_bucket_column"] = "bucket_start_b"
    if calendar_info is not None:
        alignment_dump["calendar_info"] = calendar_info
    if segment_info is not None:
        alignment_dump["segment_info"] = segment_info
    if a.meta.semantic_kind in {"segmented", "panel"}:
        alignment_dump["axes"] = a.meta.axes
    params = {
        "source_a_ref": a.ref,
        "source_b_ref": b.ref,
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
            a.lineage,
            b.lineage,
            new_step=LineageStep(
                intent="compare",
                job_ref=job_ref,
                inputs=[a.ref, b.ref],
                params_digest=digest,
            ),
        ),
        metric_id=a.meta.metric_id,
        source_a_ref=a.ref,
        source_b_ref=b.ref,
        alignment=alignment_dump,
        semantic_kind=a.meta.semantic_kind,
        semantic_model=a.meta.semantic_model,
    )
    output_frame = DeltaFrame(_df=df, meta=meta)

    # --- Evidence pipeline: commit_result replaces write_frame_to_disk ---
    subject = Subject(
        metric=a.meta.metric_id,
        slice=getattr(a.meta, "slice", None) or {},
        grain=_grain_from_axes(a),
        analysis_axis="change",
    )
    comparison_window_dict = _scope_for_window(a)
    commit_result(
        store=session.evidence_store(),
        frames_dir=session.layout.frames_dir,
        frame=output_frame,
        step_type="compare",
        inputs=CommitInputs(input_refs=[a.ref, b.ref]),
        params=CommitParams(values=params),
        semantic_anchors=CommitSemanticAnchors(
            values={"metric_id": a.meta.metric_id, "model": a.meta.semantic_model}
        ),
        subject=subject,
        extractor_family="delta_frame",
        comparison_window=comparison_window_dict,
        comparison_basis="left_vs_right",
    )

    write_job_record(
        session.layout,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "compare",
            "params": params,
            "input_frame_refs": [a.ref, b.ref],
            "output_frame_ref": output_frame.meta.artifact_id or output_frame.ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": a.meta.semantic_model,
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
            message="calendar_bucket time_series alignment requires matching time axis columns",
            details={
                "kind": "CalendarBucketTimeAxisMismatch",
                "current_time_column": a_time_column,
                "baseline_time_column": b_time_column,
            },
        )
    a_grain, b_grain = _panel_grains(a, b)
    if a_grain != b_grain:
        raise AlignmentFailedError(
            message="calendar_bucket ordinal alignment requires same-grain time_series windows",
            details={
                "kind": "CalendarBucketGrainMismatch",
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
    merged["delta"] = merged["current"] - merged["baseline"]
    baseline = merged["baseline"]
    delta = merged["delta"]
    merged["pct_change"] = np.where(
        baseline.notna() & (baseline != 0),
        delta / baseline,
        np.nan,
    )
    result_columns = [*dim_columns, "current", "baseline", "delta", "pct_change"]
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
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any] | None]:
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
    _require_calendar_columns(a_df, frame_label="a", columns=(*dim_columns, time_column, a_value))
    _require_calendar_columns(b_df, frame_label="b", columns=(*dim_columns, time_column, b_value))

    a_groups = _panel_groups(a_df, dim_columns=dim_columns)
    b_groups = _panel_groups(b_df, dim_columns=dim_columns)
    segment_keys = sorted(
        set(a_groups) | set(b_groups),
        key=lambda key: tuple("" if item is None else str(item) for item in key),
    )
    pieces: list[pd.DataFrame] = []
    calendar_infos: list[dict[str, Any]] = []
    calendar_context = (
        _calendar_context(alignment, session=session)
        if alignment.kind != "calendar_bucket"
        else None
    )

    for key in segment_keys:
        a_part = a_groups.get(key)
        b_part = b_groups.get(key)
        if a_part is None and b_part is None:
            continue
        if a_part is None:
            assert b_part is not None
            if alignment.kind == "calendar_bucket":
                delta = _one_sided_panel_delta(
                    b_part,
                    time_column=time_column,
                    value_column=b_value,
                    side="baseline",
                )
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
            if alignment.kind == "calendar_bucket":
                delta = _one_sided_panel_delta(
                    a_part,
                    time_column=time_column,
                    value_column=a_value,
                    side="current",
                )
            else:
                assert calendar_context is not None
                delta = _one_sided_panel_calendar_delta(
                    a_part,
                    time_column=time_column,
                    value_column=a_value,
                    side="current",
                    session_tz=calendar_context[2],
                )
        elif alignment.kind == "calendar_bucket":
            delta = _align_panel_calendar_bucket(
                a_part,
                b_part,
                time_column=time_column,
                a_value_column=a_value,
                b_value_column=b_value,
            )
        else:
            assert calendar_context is not None
            loaded_calendar, policy, session_tz = calendar_context
            delta, info = align_calendar_frames(
                a_part[[time_column, a_value]],
                b_part[[time_column, b_value]].rename(columns={b_value: a_value}),
                time_column=time_column,
                value_column=a_value,
                calendar=loaded_calendar,
                policy=policy,
                session_tz=session_tz,
            )
            calendar_infos.append(info.model_dump(mode="json"))

        for column, value in zip(dim_columns, key, strict=True):
            delta[column] = value
        pieces.append(delta)

    if pieces:
        result = pd.concat(pieces, ignore_index=True)
    else:
        result = pd.DataFrame(
            columns=[time_column, *dim_columns, "current", "baseline", "delta", "pct_change"]
        )

    if alignment.kind == "calendar_bucket":
        time_columns = [time_column]
        baseline_time_column = f"{time_column}_b"
        if baseline_time_column in result.columns:
            time_columns.append(baseline_time_column)
        result = result[[*time_columns, *dim_columns, "current", "baseline", "delta", "pct_change"]]
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

    segment_info = {
        "segment_count": len(segment_keys),
        "a_only_segments_count": sum(
            1 for key in segment_keys if key in a_groups and key not in b_groups
        ),
        "b_only_segments_count": sum(
            1 for key in segment_keys if key in b_groups and key not in a_groups
        ),
    }
    return result, segment_info, _aggregate_calendar_info(calendar_infos)


def _panel_groups(
    df: pd.DataFrame,
    *,
    dim_columns: list[str],
) -> dict[tuple[object, ...], pd.DataFrame]:
    groups: dict[tuple[object, ...], pd.DataFrame] = {}
    grouped = df.groupby(dim_columns, dropna=False, sort=False)
    for raw_key, group in grouped:
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        groups[tuple(None if pd.isna(value) else value for value in key)] = group.copy()
    return groups


def _one_sided_panel_delta(
    df: pd.DataFrame,
    *,
    time_column: str,
    value_column: str,
    side: str,
) -> pd.DataFrame:
    prepared = df[[time_column, value_column]].sort_values(time_column).reset_index(drop=True)
    values = pd.to_numeric(prepared[value_column], errors="coerce")
    result = pd.DataFrame({time_column: prepared[time_column]})
    if side == "current":
        result["current"] = values
        result["baseline"] = np.nan
    else:
        result["current"] = np.nan
        result["baseline"] = values
    result["delta"] = result["current"] - result["baseline"]
    result["pct_change"] = np.nan
    return result


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
            "align_key": np.nan,
            "align_quality": "unmatched",
            "bucket_start_a": bucket_starts if side == "current" else np.nan,
            "bucket_start_b": bucket_starts if side == "baseline" else np.nan,
        }
    )
    if side == "current":
        result["current"] = values
        result["baseline"] = np.nan
    else:
        result["current"] = np.nan
        result["baseline"] = values
    result["delta"] = result["current"] - result["baseline"]
    result["pct_change"] = np.nan
    return result


def _align_panel_calendar_bucket(
    a_df: pd.DataFrame,
    b_df: pd.DataFrame,
    *,
    time_column: str,
    a_value_column: str,
    b_value_column: str,
) -> pd.DataFrame:
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
    merged = pd.merge(a_prepared, b_prepared, how="outer", on=time_column)
    if (merged["current"].notna() & merged["baseline"].notna()).any():
        merged = merged.sort_values(time_column).reset_index(drop=True)
        merged["delta"] = merged["current"] - merged["baseline"]
        baseline = merged["baseline"]
        merged["pct_change"] = np.where(
            baseline.notna() & (baseline != 0),
            merged["delta"] / baseline,
            np.nan,
        )
        return merged[[time_column, "current", "baseline", "delta", "pct_change"]]

    if len(a_prepared) != len(b_prepared):
        raise AlignmentFailedError(
            message=(
                "calendar_bucket alignment requires shared bucket_start values or "
                "equal-length same-grain windows for ordinal bucket alignment"
            ),
            details={
                "kind": "CalendarBucketNoComparableBuckets",
                "current_rows": len(a_prepared),
                "baseline_rows": len(b_prepared),
            },
        )
    if a_prepared[time_column].duplicated().any() or b_prepared[time_column].duplicated().any():
        raise AlignmentFailedError(
            message="calendar_bucket ordinal alignment requires unique bucket_start values",
            details={"kind": "CalendarBucketDuplicateBuckets"},
        )
    merged = pd.DataFrame(
        {
            time_column: a_prepared[time_column],
            f"{time_column}_b": b_prepared[time_column],
            "current": a_prepared["current"],
            "baseline": b_prepared["baseline"],
        }
    )
    merged = merged.sort_values(time_column).reset_index(drop=True)
    merged["delta"] = merged["current"] - merged["baseline"]
    baseline = merged["baseline"]
    merged["pct_change"] = np.where(
        baseline.notna() & (baseline != 0),
        merged["delta"] / baseline,
        np.nan,
    )
    return merged[[time_column, f"{time_column}_b", "current", "baseline", "delta", "pct_change"]]


def _calendar_context(
    alignment: AlignmentPolicy, *, session: Session
) -> tuple[Any, CalendarPolicy, str]:
    if alignment.kind == "calendar_bucket":
        raise AlignmentPolicyNotApplicableError(
            message="calendar_bucket alignment does not require calendar context",
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
    if loaded_calendar.timezone != session_tz:
        raise CalendarPolicyError(
            message="calendar timezone must match session timezone",
            details={
                "kind": "CalendarTimezoneMismatch",
                "calendar_name": calendar_ref.id,
                "calendar_timezone": loaded_calendar.timezone,
                "session_timezone": session_tz,
            },
        )
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
            message="calendar_bucket alignment could not find paired value columns"
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
            message=(
                "calendar_bucket alignment requires shared bucket_start values or "
                "equal-length same-grain windows for ordinal bucket alignment"
            ),
            details={
                "kind": "CalendarBucketNoComparableBuckets",
                "current_rows": len(a_df),
                "baseline_rows": len(b_df),
            },
        )
    if a_df[key].duplicated().any() or b_df[key].duplicated().any():
        raise AlignmentFailedError(
            message="calendar_bucket ordinal alignment requires unique bucket_start values",
            details={"kind": "CalendarBucketDuplicateBuckets"},
        )
    value_cols_a = [column for column in a_df.columns if column != key]
    value_cols_b = [column for column in b_df.columns if column != key]
    if len(value_cols_a) != 1 or len(value_cols_b) != 1:
        raise AlignmentFailedError(
            message="calendar_bucket ordinal alignment requires exactly one value column per frame",
            details={
                "kind": "CalendarBucketValueColumnAmbiguous",
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
