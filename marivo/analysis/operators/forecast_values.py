"""Certified complete-series forecast calculations with normative horizon variance."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from statistics import NormalDist

import numpy as np
import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.errors import forecast_error
from marivo.analysis.operators.forecast_contracts import (
    ForecastSemantics,
    ForecastSpecV1,
    ForecastTrainingSummary,
)
from marivo.analysis.operators.rollup import bucket_bounds
from marivo.analysis.operators.row import frame_keys, ordered


@dataclass(frozen=True, slots=True)
class PreparedHistory:
    frame: pd.DataFrame
    groups: tuple[tuple[int, ...], ...]
    future: tuple[pd.Timestamp, ...]
    history: tuple[pd.Timestamp, ...]


def certified_coordinates(
    history: tuple[pd.Timestamp, ...], s: ForecastSemantics
) -> tuple[pd.Timestamp, ...]:
    """Validate current complete buckets and derive their exact certified continuation."""
    authority = decode_fold_authority(s.fold_authority)
    grain, scope, snapshot = (
        authority.time_grain(),
        authority.time_scope(),
        authority.temporal_snapshot(),
    )
    if grain is None or scope is None or not history:
        raise forecast_error("complete retained temporal authority", "missing history or grain")
    scope_start, scope_end = pd.Timestamp(scope.start), pd.Timestamp(scope.end)
    previous_end: pd.Timestamp | None = None
    try:
        for stamp in history:
            start, end = bucket_bounds(stamp, grain, snapshot)
            if (
                stamp != start
                or start < scope_start
                or end > scope_end
                or (previous_end is not None and start != previous_end)
            ):
                raise forecast_error(
                    "complete consecutive history buckets", "partial, duplicate or missing period"
                )
            if snapshot is not None and (
                start < pd.Timestamp(snapshot.coverage[0])
                or end > pd.Timestamp(snapshot.coverage[1])
            ):
                raise forecast_error(
                    "history inside certified calendar coverage", "uncovered history"
                )
            previous_end = end
        future: list[pd.Timestamp] = []
        assert previous_end is not None
        cursor = previous_end
        for _ in range(s.horizon):
            start, end = bucket_bounds(cursor, grain, snapshot)
            if (
                start != cursor
                or end <= start
                or (
                    snapshot is not None
                    and (
                        start < pd.Timestamp(snapshot.coverage[0])
                        or end > pd.Timestamp(snapshot.coverage[1])
                    )
                )
            ):
                raise forecast_error(
                    "certified complete future coverage", "horizon exceeds calendar coverage"
                )
            future.append(start)
            cursor = end
    except (DatasetCompilationError, ValueError, OverflowError, TypeError) as error:
        raise forecast_error(
            "finite certified consecutive time coordinates", "unavailable period boundary"
        ) from error
    return tuple(future)


def prepare_history(frame: pd.DataFrame, spec: ForecastSpecV1) -> PreparedHistory:
    """Validate every panel before any selected numerical model is evaluated."""
    s = spec.semantics
    if frame.empty:
        raise forecast_error("nonempty complete history", "empty input")
    keys = frame_keys(frame, (*spec.dimensions, spec.time_name))
    if len(keys) != len(set(keys)):
        raise forecast_error("unique series/time coordinates", "duplicate history buckets")
    groups: dict[tuple[object, ...], list[int]] = {}
    series_keys = frame_keys(frame, spec.dimensions)
    for i, key in enumerate(series_keys):
        groups.setdefault(key, []).append(i)
    history: tuple[pd.Timestamp, ...] | None = None
    future: tuple[pd.Timestamp, ...] = ()
    ordered_groups = []
    for positions in groups.values():
        for position in positions:
            value = frame[spec.time_name].iloc[position]
            number = frame[spec.metric_name].iloc[position]
            if not isinstance(value, (date, datetime)) or pd.isna(value):
                raise forecast_error(
                    "finite exact time coordinates", "null or invalid history time"
                )
            _number(number)
        ordered_positions = tuple(
            sorted(positions, key=lambda i: pd.Timestamp(frame[spec.time_name].iloc[i]))
        )
        coordinates = tuple(pd.Timestamp(frame[spec.time_name].iloc[i]) for i in ordered_positions)
        if len(coordinates) < s.minimum_history:
            raise forecast_error(
                f"at least {s.minimum_history} complete periods for {s.model_id}",
                f"received {len(coordinates)} periods",
            )
        if history is None:
            history = coordinates
            future = certified_coordinates(history, s)
        elif history != coordinates:
            raise forecast_error(
                "one shared complete history sequence across series", "panel coordinate mismatch"
            )
        ordered_groups.append(ordered_positions)
    assert history is not None
    return PreparedHistory(frame, tuple(ordered_groups), future, history)


@dataclass(frozen=True, slots=True)
class SeriesPrediction:
    points: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    sigma_squared: float
    residual_count: int
    residual_df: int
    all_zero: bool


def _number(value: object) -> int | float | Decimal:
    if isinstance(value, np.integer):
        value = int(value)
    elif isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise forecast_error("finite numeric history values", "null or nonnumeric observation")
    try:
        finite = math.isfinite(float(value))
    except (OverflowError, ValueError):
        finite = False
    if not finite:
        raise forecast_error("finite numeric history values", "non-finite observation")
    return value


def _difference(a: int | float | Decimal, b: int | float | Decimal) -> float:
    exact = (
        Decimal(str(a)) - Decimal(str(b))
        if isinstance(a, Decimal) or isinstance(b, Decimal)
        else a - b
    )
    result = float(exact)
    if result == 0 and exact != 0:
        raise forecast_error("representable nonzero innovation", "innovation precision exhausted")
    return result


def predict_series(
    values: tuple[int | float | Decimal, ...], s: ForecastSemantics
) -> SeriesPrediction:
    """Evaluate exactly the named model equations; never choose a fallback estimator."""
    n, season = len(values), s.season_length or 1
    if n < s.minimum_history or any(not math.isfinite(v) for v in values):
        raise forecast_error(
            "complete finite history meeting model minimum", "unavailable variance authority"
        )
    try:
        slope = _difference(values[-1], values[0]) / (n - 1) if s.model_id == "drift@v1" else 0.0
        lag = season if s.model_id == "seasonal_naive@v1" else 1
        residuals = tuple(_difference(values[i], values[i - lag]) - slope for i in range(lag, n))
        df = n - 2 if s.model_id == "drift@v1" else n - lag
        squares = tuple(r * r for r in residuals)
        variance = math.fsum(squares) / df
        zero = all(r == 0.0 for r in residuals)
        if (
            df <= 0
            or any(not math.isfinite(v) for v in (slope, *residuals, *squares, variance))
            or variance < 0
            or (variance == 0) != zero
        ):
            raise forecast_error(
                "finite model innovations and positive residual degrees of freedom",
                "unavailable or underflowed variance",
            )
        z = -NormalDist().inv_cdf((1 - s.interval_level) / 2)
        points, lower, upper = [], [], []
        for h in range(1, s.horizon + 1):
            point = (
                float(values[n - season + (h - 1) % season])
                if s.model_id == "seasonal_naive@v1"
                else float(values[-1]) + h * slope
            )
            factor = (
                (h - 1) // season + 1
                if s.model_id == "seasonal_naive@v1"
                else h * (1 + h / (n - 1))
                if s.model_id == "drift@v1"
                else h
            )
            horizon_variance = variance * factor
            margin = z * math.sqrt(horizon_variance)
            lo, hi = point - margin, point + margin
            if (
                any(not math.isfinite(v) for v in (point, horizon_variance, margin, lo, hi))
                or not lo <= point <= hi
            ):
                raise forecast_error(
                    "finite complete forecast and bounds", "non-finite horizon value"
                )
            if variance > 0 and (lo == point or hi == point):
                raise forecast_error(
                    "representable nonzero prediction bounds", "interval precision exhausted"
                )
            points.append(float(point))
            lower.append(float(lo))
            upper.append(float(hi))
    except (OverflowError, ValueError, ZeroDivisionError) as error:
        raise forecast_error(
            "finite innovations and complete horizon", "numerical forecast failure"
        ) from error
    return SeriesPrediction(
        tuple(points), tuple(lower), tuple(upper), variance, len(residuals), df, zero
    )


def execute_forecast(
    prepared: PreparedHistory, spec: ForecastSpecV1
) -> tuple[pd.DataFrame, ForecastTrainingSummary]:
    """Consume validated panels once and return all horizon rows plus bounded fit facts."""
    s = spec.semantics
    records: list[dict[str, object]] = []
    variances = []
    zeros = 0
    residual_count = residual_df = 0
    time_field = next(f for f in spec.output_row.schema.columns if f.role_id == "time_dimension")
    for positions in prepared.groups:
        result = predict_series(
            tuple(_number(prepared.frame[spec.metric_name].iloc[i]) for i in positions), s
        )
        variances.append(result.sigma_squared)
        zeros += int(result.all_zero)
        residual_count, residual_df = result.residual_count, result.residual_df
        dimensions = {name: prepared.frame[name].iloc[positions[0]] for name in spec.dimensions}
        for i, time in enumerate(prepared.future):
            records.append(
                {
                    **dimensions,
                    spec.time_name: time.date()
                    if time_field.logical_type_id == "date"
                    else time.to_pydatetime(),
                    "horizon_ordinal": i + 1,
                    "forecast_value": result.points[i],
                    "interval_lower": result.lower[i],
                    "interval_upper": result.upper[i],
                    "training_row_count": len(positions),
                }
            )
    frame = pd.DataFrame.from_records(
        records, columns=[f.name for f in spec.output_row.schema.columns]
    )
    for field in spec.output_row.schema.columns:
        if field.role_id == "effect_value":
            dtype = pd.ArrowDtype(pa.int64() if field.logical_type_id == "int64" else pa.float64())
        else:
            incoming_dtype = prepared.frame[field.name].dtype
            dtype = (
                incoming_dtype
                if isinstance(incoming_dtype, pd.ArrowDtype)
                else pd.ArrowDtype(pa.array(prepared.frame[field.name]).type)
            )
        frame[field.name] = pd.Series(frame[field.name].tolist(), dtype=dtype)
    summary = ForecastTrainingSummary(
        len(prepared.groups),
        len(prepared.history),
        residual_count,
        residual_df,
        zeros,
        (min(variances), max(variances)),
        prepared.history[0].isoformat(),
        prepared.history[-1].isoformat(),
        tuple(t.isoformat() for t in prepared.future),
    )
    return ordered(frame, spec.output_row, spec.output_rows), summary
