"""Forecast MetricFrames into ForecastFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import UTC, datetime
from math import sqrt
from time import monotonic
from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis.errors import (
    ForecastInputQualityError,
    ForecastInsufficientHistoryError,
    ForecastPolicyError,
    ForecastShapeUnsupportedError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject, TriggeredByFollowup
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    require_numeric_column,
    resolve_session,
)
from marivo.analysis.intents._validate import cumulative_issue, require_single_metric
from marivo.analysis.lineage import LineageStep
from marivo.analysis.session._runtime import persist_job_record, register_frame_artifact
from marivo.analysis.session.core import Session, ensure_session_writable

_FREQ = {"day": "D", "week": "W-MON", "month": "MS", "quarter": "QS"}
_DEFAULT_SEASONALITY = {"day": 7, "week": 52, "month": 12, "quarter": 4}


def forecast(
    history: MetricFrame,
    *,
    horizon: int,
    model: Literal["naive", "seasonal_naive", "drift"] = "seasonal_naive",
    seasonality_period: int | None = None,
    interval_level: float = 0.95,
    measure_column: str | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
    _triggered_by: TriggeredByFollowup | None = None,
) -> ForecastFrame:
    session = resolve_session(session)
    ensure_session_writable(session)
    if getattr(getattr(history, "meta", None), "kind", None) != "metric_frame":
        raise ForecastShapeUnsupportedError(
            message="forecast requires MetricFrame time_series or panel input"
        )
    semantic_kind = history.meta.semantic_kind
    if semantic_kind != "time_series" and semantic_kind != "panel":
        raise ForecastShapeUnsupportedError(
            message="forecast requires MetricFrame time_series or panel input"
        )
    require_single_metric(history, intent="forecast")
    # forecast operates on arity-1 metric frames; multi-metric frames are gated
    # out upstream. Narrow metric_id for downstream ForecastFrameMeta / Subject.
    assert history.meta.metric_id is not None
    ensure_frame_in_session(history, session=session, label="forecast history")
    cumulative_gate = cumulative_issue(history, intent="forecast")
    if cumulative_gate is not None:
        raise cumulative_gate
    if horizon < 1:
        raise ForecastPolicyError(message="horizon must be >= 1", context={"horizon": horizon})
    if not 0 < interval_level < 1:
        raise ForecastPolicyError(
            message="interval_level must be in (0, 1)",
            context={"interval_level": interval_level},
        )

    time_col, grain = _time_axis(history)
    if grain not in _FREQ:
        raise ForecastShapeUnsupportedError(
            message=f"forecast does not support grain {grain!r}",
            context={"grain": grain},
        )
    effective_seasonality = _resolve_seasonality(
        model=model,
        grain=grain,
        seasonality_period=seasonality_period,
    )

    df = history._dataframe_copy()
    value_col = require_numeric_column(df, measure_column, purpose="forecast history")
    if df[value_col].isna().any():
        raise ForecastInputQualityError(message="forecast history contains NaN values")
    _ensure_no_time_gap(df, time_col=time_col, grain=grain)

    started_at = datetime.now(UTC)
    started = monotonic()
    segment_dims = _segment_dimensions(history)
    future_times = _future_times(df[time_col], grain=grain, horizon=horizon)
    if semantic_kind == "panel":
        rows, counts = _forecast_panel(
            df,
            time_col=time_col,
            value_col=value_col,
            segment_dims=segment_dims,
            future_times=future_times,
            model=model,
            seasonality_period=effective_seasonality,
            interval_level=interval_level,
        )
    else:
        rows = _forecast_one(
            df.sort_values(time_col),
            time_col=time_col,
            value_col=value_col,
            future_times=future_times,
            model=model,
            seasonality_period=effective_seasonality,
            interval_level=interval_level,
            fail_open=False,
        )
        counts = {"__all__": len(df)}

    output = pd.DataFrame(rows)
    params = {
        "source_ref": history.ref,
        "measure_column": value_col,
        "horizon": horizon,
        "model": model,
        "seasonality_period": effective_seasonality,
        "interval_level": interval_level,
    }
    frame_ref = gen_ref("frame")
    job_ref = gen_ref("job")
    finished_at = datetime.now(UTC)
    meta = ForecastFrameMeta(
        kind="forecast_frame",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(output),
        byte_size=0,
        lineage=compose_lineage(
            [history],
            step=LineageStep(
                intent="forecast",
                job_ref=job_ref,
                inputs=[history.ref],
                params_digest=params_digest(params),
                analysis_purpose=analysis_purpose,
            ),
        ),
        source_refs=[history.ref],
        metric_id=history.meta.metric_id,
        semantic_model=history.meta.semantic_model,
        semantic_kind=semantic_kind,
        measure=history.meta.measure,
        axes=history.meta.axes,
        history_window=history.meta.window or {},
        forecast_window={
            "start": future_times[0].isoformat(),
            "end": (future_times[-1] + pd.tseries.frequencies.to_offset(_FREQ[grain])).isoformat(),
            "grain": grain,
            "time_dimension": time_col,
        },
        horizon=horizon,
        horizon_unit=cast("Literal['day', 'week', 'month', 'quarter']", grain),
        model=model,
        seasonality_period=effective_seasonality,
        interval_level=interval_level,
        interval_method="normal_residual",
        train_row_count_per_segment=counts,
        segment_dimensions=segment_dims,
    )
    frame = ForecastFrame(_df=output, meta=meta)
    frame = cast(
        "ForecastFrame",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type="forecast",
            inputs=CommitInputs(input_refs=[history.meta.artifact_id or history.ref]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(values={"metric_id": history.meta.metric_id}),
            subject=Subject(
                metric=history.meta.metric_id,
                grain=cast("Any", grain),
                analysis_axis="forecast",
            ),
            extractor_family="forecast_frame",
            triggered_by_followup=_triggered_by,
        ),
    )
    register_frame_artifact(session, frame)
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "forecast",
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": [history.ref],
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog._project.semantic_root),
            "semantic_model": history.meta.semantic_model,
        },
    )
    return frame


def _time_axis(frame: MetricFrame) -> tuple[str, str]:
    axis = frame.meta.axes.get("time", {})
    if not isinstance(axis, dict):
        raise ForecastShapeUnsupportedError(message="forecast requires a time axis")
    return str(axis.get("field") or axis.get("column") or "time"), str(axis.get("grain", "day"))


def _segment_dimensions(frame: MetricFrame) -> list[str]:
    dims = frame.meta.axes.get("dimensions", [])
    return [str(dim["field"]) for dim in dims if isinstance(dim, dict) and "field" in dim]


def _resolve_seasonality(*, model: str, grain: str, seasonality_period: int | None) -> int | None:
    if model == "seasonal_naive" and grain == "year":
        raise ForecastPolicyError(message="seasonal_naive is not supported for year grain")
    if seasonality_period is not None and seasonality_period <= 1:
        raise ForecastPolicyError(message="seasonality_period must be > 1")
    if model == "seasonal_naive":
        return seasonality_period or _DEFAULT_SEASONALITY[grain]
    return seasonality_period


def _ensure_no_time_gap(df: pd.DataFrame, *, time_col: str, grain: str) -> None:
    values = pd.to_datetime(df[time_col]).drop_duplicates().sort_values()
    expected = pd.date_range(values.iloc[0], values.iloc[-1], freq=_FREQ[grain])
    if values.nunique() != len(expected):
        raise ForecastInputQualityError(message="forecast history has missing time buckets")


def _future_times(series: pd.Series, *, grain: str, horizon: int) -> pd.DatetimeIndex:
    last = pd.to_datetime(series).max()
    return pd.date_range(last, periods=horizon + 1, freq=_FREQ[grain])[1:]


def _min_points(model: str, seasonality_period: int | None) -> int:
    if model == "naive":
        return 2
    if model == "drift":
        return 3
    return int(seasonality_period or 0) + 1


def _forecast_one(
    df: pd.DataFrame,
    *,
    time_col: str,
    value_col: str,
    future_times: pd.DatetimeIndex,
    model: str,
    seasonality_period: int | None,
    interval_level: float,
    fail_open: bool,
    prefix: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    prefix = prefix or {}
    y = df.sort_values(time_col)[value_col].astype(float).to_numpy()
    minimum = _min_points(model, seasonality_period)
    if len(y) < minimum:
        if not fail_open:
            raise ForecastInsufficientHistoryError(
                message="not enough history for selected forecast model",
                context={"row_count": len(y), "minimum": minimum},
            )
        return [
            {
                **prefix,
                "time": t,
                "predicted": float("nan"),
                "lower": float("nan"),
                "upper": float("nan"),
                "residual_stddev": float("nan"),
                "model": "insufficient",
                "horizon_index": i + 1,
                "reason_code": "insufficient_history",
            }
            for i, t in enumerate(future_times)
        ]

    preds, residual = _predict(
        y,
        model=model,
        seasonality_period=seasonality_period,
        horizon=len(future_times),
    )
    residual_stddev = float(pd.Series(residual).std(ddof=1)) if len(residual) > 1 else 0.0
    if pd.isna(residual_stddev):
        residual_stddev = 0.0
    # Lazy import: scipy is heavy (~0.6s) and only needed for the
    # prediction interval z-score. Importing here keeps `marivo.analysis`
    # import cheap for workers/tests that never forecast.
    from scipy import stats

    z = float(stats.norm.ppf((1 + interval_level) / 2))
    reason = "constant_history" if residual_stddev == 0 else "ok"
    rows = []
    for i, (time_value, predicted) in enumerate(zip(future_times, preds, strict=True), start=1):
        margin = z * residual_stddev * sqrt(i)
        rows.append(
            {
                **prefix,
                "time": time_value,
                "predicted": float(predicted),
                "lower": float(predicted - margin),
                "upper": float(predicted + margin),
                "residual_stddev": residual_stddev,
                "model": model,
                "horizon_index": i,
                "reason_code": reason,
            }
        )
    return rows


def _predict(
    y: Any,
    *,
    model: str,
    seasonality_period: int | None,
    horizon: int,
) -> tuple[list[float], list[float]]:
    if model == "naive":
        return [y[-1]] * horizon, [y[i] - y[i - 1] for i in range(1, len(y))]
    if model == "drift":
        slope = (y[-1] - y[0]) / (len(y) - 1)
        fitted = [y[0] + i * slope for i in range(len(y))]
        return [y[-1] + h * slope for h in range(1, horizon + 1)], [
            actual - fit for actual, fit in zip(y, fitted, strict=True)
        ]
    period = int(seasonality_period or 0)
    preds = [y[len(y) - period + ((h - 1) % period)] for h in range(1, horizon + 1)]
    residual = [y[i] - y[i - period] for i in range(period, len(y))]
    return preds, residual


def _forecast_panel(
    df: pd.DataFrame,
    *,
    time_col: str,
    value_col: str,
    segment_dims: list[str],
    future_times: pd.DatetimeIndex,
    model: str,
    seasonality_period: int | None,
    interval_level: float,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    rows: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    group_key: str | list[str] = segment_dims[0] if len(segment_dims) == 1 else segment_dims
    for segment_key, group in df.groupby(group_key, dropna=False):
        values = segment_key if isinstance(segment_key, tuple) else (segment_key,)
        prefix = dict(zip(segment_dims, values, strict=True))
        key = "|".join(str(value) for value in values)
        minimum = _min_points(model, seasonality_period)
        counts[key] = len(group) if len(group) >= minimum else 0
        rows.extend(
            _forecast_one(
                group,
                time_col=time_col,
                value_col=value_col,
                future_times=future_times,
                model=model,
                seasonality_period=seasonality_period,
                interval_level=interval_level,
                fail_open=True,
                prefix=prefix,
            )
        )
    return rows, counts
