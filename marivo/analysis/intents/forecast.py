"""Forecast MetricFrames into ForecastFrames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from math import sqrt
from numbers import Integral
from time import monotonic
from typing import Any, Literal, cast

import numpy as np
import pandas as pd

from marivo._compat import UTC
from marivo._temporal import (
    BuiltinPeriodBindingV1,
    FrameTemporalContractV1,
    GregorianIsoResolver,
    PeriodRecord,
    SemanticPeriodBindingV1,
    TemporalResolver,
    TemporalSnapshotStore,
    TimeScopeContractV1,
)
from marivo.analysis._semantic_persistence import job_semantics_from_frames
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
from marivo.analysis.evidence.types import Subject
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents._derived import (
    compose_candidate_origins,
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    resolve_metric_value_column,
    resolve_session,
)
from marivo.analysis.intents._metric_axes import (
    metric_dimension_columns,
    metric_time_axis,
)
from marivo.analysis.intents._validate import cumulative_issue, require_single_metric
from marivo.analysis.lineage import LineageStep
from marivo.analysis.session._runtime import persist_job_record, register_frame_artifact
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.refs import ref as ref_factory

_FREQ = {"day": "D", "week": "W-MON", "month": "MS", "quarter": "QS"}
_DEFAULT_SEASONALITY = {"day": 7, "week": 52, "month": 12, "quarter": 4}
_FORECAST_MODELS = frozenset({"naive", "seasonal_naive", "drift"})


@dataclass(frozen=True, slots=True)
class _ForecastBucket:
    """One exact future bucket, optionally backed by a semantic period."""

    start: date | datetime | pd.Timestamp
    end: date | datetime | pd.Timestamp
    key: str | int | float | bool
    ordinal: int | None = None
    semantic: bool = False


@dataclass(frozen=True, slots=True)
class _ForecastPlan:
    """Validated authority and future buckets for one forecast invocation."""

    binding: BuiltinPeriodBindingV1 | SemanticPeriodBindingV1
    horizon_unit: str
    display_timezone: str
    future: tuple[_ForecastBucket, ...]
    semantic: bool
    sort_column: str
    temporal_contract: FrameTemporalContractV1
    resolver: TemporalResolver | None = None


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
) -> ForecastFrame:
    session = resolve_session(session)
    ensure_session_can_execute(session)
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

    df = history._dataframe_copy()
    time_col, grain = _time_axis(history)
    segment_dims = metric_dimension_columns(history)
    if semantic_kind == "panel" and not segment_dims:
        raise ForecastShapeUnsupportedError(
            message="forecast panel input requires at least one dimension axis",
            context={"semantic_kind": semantic_kind},
        )
    binding, semantic, display_timezone = _resolve_forecast_authority(
        history,
        grain=grain,
        session=session,
    )
    effective_seasonality = _resolve_seasonality(
        model=model,
        grain=(binding.level_name if semantic else grain),
        seasonality_period=seasonality_period,
        semantic=semantic,
        binding=binding,
    )

    value_column = resolve_metric_value_column(
        history,
        df,
        measure_column,
        parameter="measure_column",
        purpose="forecast history",
    )
    value_col = value_column.internal_name
    if df[value_col].isna().any():
        raise ForecastInputQualityError(message="forecast history contains NaN values")
    resolver: TemporalResolver | None = None
    if semantic:
        resolver = _semantic_resolver(binding, session=session, model=model)
        history_records = _validate_semantic_history(
            df,
            resolver=resolver,
            binding=cast("SemanticPeriodBindingV1", binding),
            segment_dims=segment_dims if semantic_kind == "panel" else (),
            model=model,
            seasonality_period=effective_seasonality,
        )
        future = _semantic_future_buckets(
            resolver,
            level=binding.level_name,
            last_ordinal=history_records[-1].global_ordinal,
            horizon=horizon,
            binding=cast("SemanticPeriodBindingV1", binding),
            display_timezone=display_timezone,
            model=model,
        )
    else:
        _ensure_no_time_gap(
            df,
            time_col=time_col,
            grain=grain,
            segment_dims=segment_dims if semantic_kind == "panel" else (),
        )
        future = _builtin_future_buckets(
            df[time_col],
            grain=grain,
            horizon=horizon,
            binding=cast("BuiltinPeriodBindingV1", binding),
            display_timezone=display_timezone,
        )
    plan = _ForecastPlan(
        binding=binding,
        horizon_unit=binding.level_name,
        display_timezone=display_timezone,
        future=future,
        semantic=semantic,
        sort_column="period_ordinal" if semantic else time_col,
        temporal_contract=_forecast_temporal_contract(
            binding=binding,
            future=future,
            display_timezone=display_timezone,
        ),
        resolver=resolver,
    )

    started_at = datetime.now(UTC)
    started = monotonic()
    if semantic_kind == "panel":
        rows, counts = _forecast_panel(
            df,
            sort_column=plan.sort_column,
            value_col=value_col,
            segment_dims=segment_dims,
            future_buckets=plan.future,
            model=model,
            seasonality_period=effective_seasonality,
            interval_level=interval_level,
            fail_open=not plan.semantic,
        )
    else:
        rows = _forecast_one(
            df,
            sort_column=plan.sort_column,
            value_col=value_col,
            future_buckets=plan.future,
            model=model,
            seasonality_period=effective_seasonality,
            interval_level=interval_level,
            fail_open=False,
        )
        counts = {"__all__": len(df)}

    output = pd.DataFrame(rows)
    params = {
        "source_ref": history.ref,
        "measure_column": value_column.public_name,
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
        candidate_origins=compose_candidate_origins((history,)),
        source_refs=[history.ref],
        metric_id=history.meta.metric_id,
        semantic_model=history.meta.semantic_model,
        semantic_kind=semantic_kind,
        measure=history.meta.measure,
        axes=history.meta.axes,
        history_window=history.meta.window or {},
        forecast_window={
            "start": _isoformat(plan.future[0].start),
            "end": _isoformat(plan.future[-1].end),
            "grain": plan.horizon_unit,
            "time_dimension": time_col,
        },
        horizon=horizon,
        horizon_unit=plan.horizon_unit,
        model=model,
        seasonality_period=effective_seasonality,
        interval_level=interval_level,
        interval_method="normal_residual",
        train_row_count_per_segment=counts,
        segment_dimensions=segment_dims,
        temporal_contract=plan.temporal_contract,
    )
    frame = ForecastFrame(_df=output, meta=meta)
    frame = cast(
        "ForecastFrame",
        commit_result(
            session=session,
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=frame,
            step_type="forecast",
            inputs=CommitInputs(input_refs=[history.meta.artifact_id or history.ref]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors.from_frame(history),
            subject=Subject(
                grain=cast("Any", grain),
                analysis_axis="forecast",
            ),
            extractor_family="forecast_frame",
        ),
    )
    register_frame_artifact(session, frame)
    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "forecast",
            **job_semantics_from_frames(history),
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
        },
    )
    return frame


def _resolve_forecast_authority(
    history: MetricFrame,
    *,
    grain: str,
    session: Session,
) -> tuple[BuiltinPeriodBindingV1 | SemanticPeriodBindingV1, bool, str]:
    """Resolve the exact period authority already carried by ``history``."""

    contract = getattr(history.meta, "temporal_contract", None)
    observation = contract.observation_period if contract is not None else None
    if observation is None:
        timezone = history.meta.report_tz or session.report_tz_name
        binding = BuiltinPeriodBindingV1(level_name=grain, boundary_timezone=timezone)
        return binding, False, timezone
    if isinstance(observation, SemanticPeriodBindingV1):
        expected_tokens = {
            observation.level_name,
            f"{observation.calendar_ref}::{observation.level_name}",
        }
        if grain not in expected_tokens:
            raise ForecastShapeUnsupportedError(
                message="forecast history axis does not match its semantic period binding",
                context={
                    "case": "period_binding_axis_mismatch",
                    "grain": grain,
                    "period_binding": observation.model_dump(mode="json"),
                },
            )
        display_timezone = (
            contract.display_timezone if contract is not None else session.report_tz_name
        )
        return observation, True, display_timezone
    if isinstance(observation, BuiltinPeriodBindingV1):
        if grain != observation.level_name:
            raise ForecastShapeUnsupportedError(
                message="forecast history axis does not match its builtin period binding",
                context={
                    "case": "period_binding_axis_mismatch",
                    "grain": grain,
                    "period_binding": observation.model_dump(mode="json"),
                },
            )
        display_timezone = (
            contract.display_timezone if contract is not None else observation.boundary_timezone
        )
        return observation, False, display_timezone
    raise ForecastShapeUnsupportedError(
        message="forecast history is missing a supported period binding",
        context={
            "case": "period_binding_missing",
            "received": repr(observation),
            "expected": "BuiltinPeriodBindingV1 or SemanticPeriodBindingV1",
        },
    )


def _semantic_resolver(
    binding: BuiltinPeriodBindingV1 | SemanticPeriodBindingV1,
    *,
    session: Session,
    model: str,
) -> TemporalResolver:
    """Load the exact certified semantic snapshot named by one frame."""

    if not isinstance(binding, SemanticPeriodBindingV1):
        raise TypeError("semantic resolver requires a SemanticPeriodBindingV1")
    try:
        snapshot = TemporalSnapshotStore(session.project_root).load_exact(
            ref_factory.period_calendar(binding.calendar_ref),
            snapshot_digest=binding.snapshot_digest,
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ForecastShapeUnsupportedError(
            message="forecast history period authority snapshot is unavailable",
            context={
                "case": "period_snapshot_unavailable",
                "model": model,
                "period_binding": binding.model_dump(mode="json"),
                "required_coverage": None,
                "repair": "re-observe history after certifying the exact period calendar snapshot",
            },
        ) from exc
    if binding.level_name not in snapshot.levels or not any(
        period.level_name == binding.level_name for period in snapshot.periods
    ):
        raise ForecastShapeUnsupportedError(
            message="forecast history period binding level is not certified in its snapshot",
            context={
                "case": "period_level_unavailable",
                "model": model,
                "period_binding": binding.model_dump(mode="json"),
                "certified_levels": list(snapshot.levels),
                "required_coverage": "a certified snapshot containing the bound level",
                "repair": "re-certify the period calendar and re-observe the semantic grain",
            },
        )
    return TemporalResolver(snapshot)


def _validate_semantic_history(
    df: pd.DataFrame,
    *,
    resolver: TemporalResolver,
    binding: SemanticPeriodBindingV1,
    segment_dims: Sequence[str],
    model: str,
    seasonality_period: int | None,
) -> tuple[PeriodRecord, ...]:
    """Validate one complete, ordered certified period sequence per axis."""

    required_coverage = "complete consecutive certified periods"
    required = {"period_key", "period_start", "period_end", "period_ordinal", "is_complete"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ForecastShapeUnsupportedError(
            message="semantic forecast history is missing certified period columns",
            context={
                "case": "period_columns_missing",
                "model": model,
                "period_binding": binding.model_dump(mode="json"),
                "missing_columns": missing,
                "required_coverage": required_coverage,
                "repair": "re-observe a scope containing complete certified periods",
            },
        )
    if df.empty:
        raise ForecastShapeUnsupportedError(
            message="semantic forecast history must contain at least one certified period",
            context={
                "case": "period_history_empty",
                "model": model,
                "period_binding": binding.model_dump(mode="json"),
                "required_coverage": required_coverage,
                "repair": "re-observe a scope containing complete certified periods",
            },
        )

    groups: list[tuple[object, pd.DataFrame]]
    if segment_dims:
        group_key: str | list[str] = (
            segment_dims[0] if len(segment_dims) == 1 else list(segment_dims)
        )
        groups = list(df.groupby(group_key, dropna=False, sort=False))
    else:
        groups = [(None, df)]

    sequences: list[tuple[PeriodRecord, ...]] = []
    for segment_key, group in groups:
        records: list[PeriodRecord] = []
        for _, row in group.iterrows():
            raw_ordinal = row["period_ordinal"]
            if isinstance(raw_ordinal, bool) or not isinstance(raw_ordinal, Integral):
                raise ForecastShapeUnsupportedError(
                    message="semantic forecast period ordinal must be an integer",
                    context={
                        "case": "period_ordinal_invalid",
                        "model": model,
                        "period_binding": binding.model_dump(mode="json"),
                        "segment": repr(segment_key),
                        "received": repr(raw_ordinal),
                        "required_coverage": required_coverage,
                        "repair": "re-observe a scope containing certified period ordinals",
                    },
                )
            ordinal = int(raw_ordinal)
            complete_value = row["is_complete"]
            if not isinstance(complete_value, (bool, np.bool_)):
                raise ForecastShapeUnsupportedError(
                    message="semantic forecast history contains an invalid completeness flag",
                    context={
                        "case": "period_completeness_invalid",
                        "model": model,
                        "period_binding": binding.model_dump(mode="json"),
                        "segment": repr(segment_key),
                        "received": repr(complete_value),
                        "required_coverage": required_coverage,
                        "repair": "re-observe a scope containing complete certified periods",
                    },
                )
            if not complete_value:
                raise ForecastShapeUnsupportedError(
                    message="semantic forecast history contains a partial certified period",
                    context={
                        "case": "period_partial",
                        "model": model,
                        "period_binding": binding.model_dump(mode="json"),
                        "segment": repr(segment_key),
                        "period_key": row["period_key"],
                        "required_coverage": required_coverage,
                        "repair": "re-observe a scope containing complete certified periods",
                    },
                )
            period_key = cast("str | int | float | bool", _native_json_scalar(row["period_key"]))
            try:
                record = resolver.period(binding.level_name, period_key)
            except (KeyError, TypeError, ValueError) as exc:
                raise ForecastShapeUnsupportedError(
                    message="semantic forecast history period key is outside its certified snapshot",
                    context={
                        "case": "period_key_invalid",
                        "model": model,
                        "period_binding": binding.model_dump(mode="json"),
                        "segment": repr(segment_key),
                        "period_key": period_key,
                        "required_coverage": required_coverage,
                        "repair": "re-observe a scope using the exact certified period keys",
                    },
                ) from exc
            try:
                start = _as_civil_date(row["period_start"])
                end = _as_civil_date(row["period_end"])
            except (TypeError, ValueError) as exc:
                raise ForecastShapeUnsupportedError(
                    message="semantic forecast period bounds are not valid certified dates",
                    context={
                        "case": "period_bounds_invalid",
                        "model": model,
                        "period_binding": binding.model_dump(mode="json"),
                        "segment": repr(segment_key),
                        "period_key": period_key,
                        "received": {
                            "start": repr(row["period_start"]),
                            "end": repr(row["period_end"]),
                        },
                        "repair": "observe a scope containing certified period boundaries",
                        "required_coverage": required_coverage,
                    },
                ) from exc
            if (
                record.global_ordinal != ordinal
                or record.start_date != start
                or record.end_date != end
            ):
                raise ForecastShapeUnsupportedError(
                    message="semantic forecast history period identity does not match its snapshot",
                    context={
                        "case": "period_identity_mismatch",
                        "model": model,
                        "period_binding": binding.model_dump(mode="json"),
                        "segment": repr(segment_key),
                        "received": {
                            "key": period_key,
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "ordinal": ordinal,
                        },
                        "certified": {
                            "key": record.key,
                            "start": record.start_date.isoformat(),
                            "end": record.end_date.isoformat(),
                            "ordinal": record.global_ordinal,
                        },
                        "required_coverage": required_coverage,
                        "repair": "re-observe the history from the exact certified period snapshot",
                    },
                )
            records.append(record)
        ordered = tuple(sorted(records, key=lambda item: item.global_ordinal))
        if len({item.global_ordinal for item in ordered}) != len(ordered):
            raise ForecastShapeUnsupportedError(
                message="semantic forecast history contains duplicate certified periods",
                context={
                    "case": "period_duplicate",
                    "model": model,
                    "period_binding": binding.model_dump(mode="json"),
                    "segment": repr(segment_key),
                    "required_coverage": required_coverage,
                    "repair": "re-observe one row per complete certified period",
                },
            )
        if any(
            current.global_ordinal != previous.global_ordinal + 1
            for previous, current in pairwise(ordered)
        ):
            raise ForecastShapeUnsupportedError(
                message="semantic forecast history contains a period gap",
                context={
                    "case": "period_gap",
                    "model": model,
                    "period_binding": binding.model_dump(mode="json"),
                    "segment": repr(segment_key),
                    "period_ordinals": [item.global_ordinal for item in ordered[:8]],
                    "required_coverage": required_coverage,
                    "repair": "re-observe a scope containing consecutive certified periods",
                },
            )
        sequences.append(ordered)

    first = sequences[0]
    if any(sequence != first for sequence in sequences[1:]):
        raise ForecastShapeUnsupportedError(
            message="semantic forecast panel segments do not share one certified period sequence",
            context={
                "case": "panel_period_sequence_mismatch",
                "model": model,
                "period_binding": binding.model_dump(mode="json"),
                "segment_count": len(sequences),
                "required_coverage": required_coverage,
                "repair": "re-observe all panel segments over one shared certified period scope",
            },
        )
    minimum = _min_points(model, seasonality_period)
    if len(first) < minimum:
        raise ForecastInsufficientHistoryError(
            message="not enough complete certified periods for selected forecast model",
            context={
                "row_count": len(first),
                "minimum": minimum,
                "period_binding": binding.model_dump(mode="json"),
            },
        )
    return first


def _semantic_future_buckets(
    resolver: TemporalResolver,
    *,
    level: str,
    last_ordinal: int,
    horizon: int,
    binding: SemanticPeriodBindingV1,
    display_timezone: str,
    model: str,
) -> tuple[_ForecastBucket, ...]:
    periods = tuple(period for period in resolver.snapshot.periods if period.level_name == level)
    by_ordinal = {period.global_ordinal: period for period in periods}
    future: list[_ForecastBucket] = []
    for ordinal in range(last_ordinal + 1, last_ordinal + horizon + 1):
        period = by_ordinal.get(ordinal)
        if period is None:
            raise ForecastShapeUnsupportedError(
                message="forecast horizon exceeds certified period coverage",
                context={
                    "case": "period_future_out_of_coverage",
                    "model": model,
                    "period_binding": binding.model_dump(mode="json"),
                    "required_coverage": {
                        "last_history_ordinal": last_ordinal,
                        "requested_horizon": horizon,
                        "missing_ordinal": ordinal,
                        "coverage": [
                            resolver.snapshot.coverage[0].isoformat(),
                            resolver.snapshot.coverage[1].isoformat(),
                        ],
                    },
                    "boundary_timezone": display_timezone,
                    "repair": "reduce horizon or certify a snapshot covering the requested periods",
                },
            )
        future.append(
            _ForecastBucket(
                start=period.start_date,
                end=period.end_date,
                key=period.key,
                ordinal=period.global_ordinal,
                semantic=True,
            )
        )
    return tuple(future)


def _builtin_future_buckets(
    series: pd.Series,
    *,
    grain: str,
    horizon: int,
    binding: BuiltinPeriodBindingV1,
    display_timezone: str,
) -> tuple[_ForecastBucket, ...]:
    if grain not in _FREQ:
        raise ForecastShapeUnsupportedError(
            message=f"forecast does not support grain {grain!r}",
            context={
                "case": "builtin_grain_unsupported",
                "grain": grain,
                "period_binding": binding.model_dump(mode="json"),
            },
        )
    future_times = _future_times(series, grain=grain, horizon=horizon)
    offset = pd.tseries.frequencies.to_offset(_FREQ[grain])
    resolver = GregorianIsoResolver(binding.boundary_timezone)
    buckets: list[_ForecastBucket] = []
    for value in future_times:
        start = pd.Timestamp(value)
        end = start + offset
        local_start = start
        if local_start.tzinfo is not None:
            local_start = local_start.tz_convert(display_timezone).tz_localize(None)
        key = resolver.period_on(grain, local_start.date()).key
        buckets.append(
            _ForecastBucket(
                start=start,
                end=end,
                key=key,
                semantic=False,
            )
        )
    return tuple(buckets)


def _forecast_temporal_contract(
    *,
    binding: BuiltinPeriodBindingV1 | SemanticPeriodBindingV1,
    future: Sequence[_ForecastBucket],
    display_timezone: str,
) -> FrameTemporalContractV1:
    start = _python_temporal(future[0].start)
    end = _python_temporal(future[-1].end)
    return FrameTemporalContractV1(
        time_scope=TimeScopeContractV1(kind="absolute", start=start, end=end),
        observation_period=binding,
        actual_start=start,
        actual_end=end,
        output_period_keys=tuple(bucket.key for bucket in future),
        display_timezone=display_timezone,
    )


def _as_civil_date(value: object) -> date:
    if type(value) is date:
        return value
    raise ValueError(f"period boundary must be a civil date, got {type(value).__name__}")


def _native_json_scalar(value: object) -> object:
    """Lower pandas/NumPy scalar wrappers to the exact JSON scalar types."""

    if isinstance(value, np.generic):
        return value.item()
    return value


def _python_temporal(value: date | datetime | pd.Timestamp) -> date | datetime:
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _isoformat(value: date | datetime | pd.Timestamp) -> str:
    return _python_temporal(value).isoformat()


def _time_axis(frame: MetricFrame) -> tuple[str, str]:
    return metric_time_axis(frame)


def _resolve_seasonality(
    *,
    model: str,
    grain: str,
    seasonality_period: int | None,
    semantic: bool = False,
    binding: BuiltinPeriodBindingV1 | SemanticPeriodBindingV1 | None = None,
) -> int | None:
    if model not in _FORECAST_MODELS:
        raise ForecastShapeUnsupportedError(
            message=f"forecast does not support model {model!r}",
            context={
                "case": "unsupported_model",
                "model": model,
                "period_binding": (
                    binding.model_dump(mode="json") if binding is not None else None
                ),
                "supported_models": sorted(_FORECAST_MODELS),
            },
        )
    if not semantic and grain not in _FREQ:
        raise ForecastShapeUnsupportedError(
            message=f"forecast does not support grain {grain!r}",
            context={
                "case": "builtin_grain_unsupported",
                "grain": grain,
                "period_binding": (
                    binding.model_dump(mode="json") if binding is not None else None
                ),
            },
        )
    if seasonality_period is not None and seasonality_period <= 1:
        raise ForecastPolicyError(message="seasonality_period must be > 1")
    if model == "seasonal_naive":
        if semantic and seasonality_period is None:
            raise ForecastShapeUnsupportedError(
                message=(
                    "semantic-period seasonal_naive requires an explicit seasonality_period > 1"
                ),
                context={
                    "case": "seasonality_period_required",
                    "model": model,
                    "period_binding": (
                        binding.model_dump(mode="json") if binding is not None else None
                    ),
                    "required": "seasonality_period > 1",
                },
            )
        return seasonality_period or _DEFAULT_SEASONALITY[grain]
    return seasonality_period


def _ensure_no_time_gap(
    df: pd.DataFrame,
    *,
    time_col: str,
    grain: str,
    segment_dims: Sequence[str] = (),
) -> None:
    values = pd.to_datetime(df[time_col]).drop_duplicates().sort_values()
    expected = pd.date_range(values.iloc[0], values.iloc[-1], freq=_FREQ[grain])
    if values.nunique() != len(expected):
        raise ForecastInputQualityError(message="forecast history has missing time buckets")
    if not segment_dims:
        return

    expected_set = {pd.Timestamp(value) for value in expected}
    invalid_segments: list[dict[str, object]] = []
    group_key: str | list[str] = segment_dims[0] if len(segment_dims) == 1 else list(segment_dims)
    for segment_key, group in df.groupby(group_key, dropna=False):
        values_tuple = segment_key if isinstance(segment_key, tuple) else (segment_key,)
        observed = {pd.Timestamp(value) for value in pd.to_datetime(group[time_col]).unique()}
        missing = sorted(expected_set - observed)
        if not missing:
            continue
        invalid_segments.append(
            {
                "keys": dict(zip(segment_dims, values_tuple, strict=True)),
                "missing_bucket_count": len(missing),
                "missing_buckets": [value.isoformat() for value in missing[:5]],
            }
        )
    if invalid_segments:
        invalid_segments.sort(key=lambda item: repr(item["keys"]))
        raise ForecastInputQualityError(
            message="forecast panel history has missing time buckets within segments",
            context={
                "segment_dimensions": list(segment_dims),
                "invalid_segment_count": len(invalid_segments),
                "invalid_segments": invalid_segments[:5],
            },
        )


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
    sort_column: str,
    value_col: str,
    future_buckets: Sequence[_ForecastBucket],
    model: str,
    seasonality_period: int | None,
    interval_level: float,
    fail_open: bool,
    prefix: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    prefix = prefix or {}
    y = df.sort_values(sort_column)[value_col].astype(float).to_numpy()
    minimum = _min_points(model, seasonality_period)
    if len(y) < minimum:
        if not fail_open:
            raise ForecastInsufficientHistoryError(
                message="not enough history for selected forecast model",
                context={"row_count": len(y), "minimum": minimum},
            )
        return [
            _forecast_row(
                prefix=prefix,
                bucket=bucket,
                predicted=float("nan"),
                lower=float("nan"),
                upper=float("nan"),
                residual_stddev=float("nan"),
                model="insufficient",
                horizon_index=i + 1,
                reason_code="insufficient_history",
            )
            for i, bucket in enumerate(future_buckets)
        ]

    preds, residual = _predict(
        y,
        model=model,
        seasonality_period=seasonality_period,
        horizon=len(future_buckets),
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
    for i, (bucket, predicted) in enumerate(zip(future_buckets, preds, strict=True), start=1):
        margin = z * residual_stddev * sqrt(i)
        rows.append(
            _forecast_row(
                prefix=prefix,
                bucket=bucket,
                predicted=float(predicted),
                lower=float(predicted - margin),
                upper=float(predicted + margin),
                residual_stddev=residual_stddev,
                model=model,
                horizon_index=i,
                reason_code=reason,
            )
        )
    return rows


def _forecast_row(
    *,
    prefix: dict[str, object],
    bucket: _ForecastBucket,
    predicted: float,
    lower: float,
    upper: float,
    residual_stddev: float,
    model: str,
    horizon_index: int,
    reason_code: str,
) -> dict[str, object]:
    row: dict[str, object] = {
        **prefix,
        "time": bucket.start,
        "predicted": predicted,
        "lower": lower,
        "upper": upper,
        "residual_stddev": residual_stddev,
        "model": model,
        "horizon_index": horizon_index,
        "reason_code": reason_code,
    }
    if bucket.semantic:
        row.update(
            {
                "bucket_start": bucket.start,
                "bucket_end": bucket.end,
                "period_key": bucket.key,
                "period_start": bucket.start,
                "period_end": bucket.end,
                "period_ordinal": bucket.ordinal,
                "is_complete": True,
            }
        )
    return row


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
    sort_column: str,
    value_col: str,
    segment_dims: list[str],
    future_buckets: Sequence[_ForecastBucket],
    model: str,
    seasonality_period: int | None,
    interval_level: float,
    fail_open: bool,
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
                sort_column=sort_column,
                value_col=value_col,
                future_buckets=future_buckets,
                model=model,
                seasonality_period=seasonality_period,
                interval_level=interval_level,
                fail_open=fail_open,
                prefix=prefix,
            )
        )
    return rows, counts
