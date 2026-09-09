"""Complete-series descriptive discovery with exact typed Candidate identities."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise

import numpy as np
import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.descriptors import DatasetRowContract, _canonical_digest
from marivo.analysis.datasets.handles import CanonicalValue
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.candidate_contracts import (
    REASON_CODES,
    CandidateDefinition,
    CandidateEvaluationSummary,
    CandidateSpecV1,
)
from marivo.analysis.operators.errors import discovery_error
from marivo.analysis.operators.rollup import bucket_bounds
from marivo.analysis.operators.row import ordered
from marivo.analysis.operators.row_values import _missing, frame_keys, row_key_names


def _coordinate(value: object) -> CanonicalValue:
    if _missing(value):
        return None
    if isinstance(value, np.integer):
        value = int(value)
    elif isinstance(value, np.floating):
        value = float(value)
    elif isinstance(value, np.bool_):
        value = bool(value)
    if isinstance(value, datetime):
        return ("instant", value.isoformat())
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise discovery_error("finite exact candidate coordinates", "non-finite coordinate")
        parts = value.as_tuple()
        assert isinstance(parts.exponent, int)
        digits = "".join(str(digit) for digit in parts.digits)
        trimmed = digits.rstrip("0")
        return (
            "decimal",
            parts.sign if trimmed else 0,
            trimmed or "0",
            parts.exponent + len(digits) - len(trimmed) if trimmed else 0,
        )
    if isinstance(value, float):
        if not math.isfinite(value):
            raise discovery_error("finite exact candidate coordinates", "non-finite coordinate")
        return 0.0 if value == 0 else value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise discovery_error("typed scalar candidate coordinates", "invalid coordinate type")


def candidate_item_id(
    definition: CandidateDefinition, row: DatasetRowContract, values: Mapping[str, object]
) -> str:
    """Bind a complete typed business key to the exact discovery input and parameters."""
    fields = {field.field_id: field for field in row.schema.columns}
    coordinates = tuple(
        (fields[key].logical_type_id, _coordinate(values[fields[key].name]))
        for key in row.key_field_ids
    )
    return "sha256:" + _canonical_digest(
        (
            "candidate_item@v1",
            definition.identity_payload(),
            coordinates,
        )
    )


def _number(value: object, *, unavailable_nonfinite: bool = False) -> float | None:
    if _missing(value):
        return None
    if isinstance(value, np.integer):
        value = int(value)
    elif isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise discovery_error("finite numeric observations", "nonnumeric discovery input")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise discovery_error("representable numeric observations", "numeric overflow") from error
    if not math.isfinite(result):
        if unavailable_nonfinite:
            return None
        raise discovery_error("finite non-null observations", "non-finite discovery input")
    if (isinstance(value, int) and result != value) or (
        isinstance(value, Decimal) and Decimal(str(result)) != value
    ):
        raise discovery_error("representable numeric observations", "observation precision lost")
    return result


@dataclass(frozen=True, slots=True)
class _Baseline:
    mean: float
    stddev: float
    deviations: tuple[float, ...]
    scores: tuple[float, ...]


def _baseline(values: list[float]) -> _Baseline | None:
    """Use population moments; nonconstant underflow is a failure, never a zero fit."""
    if all(value == values[0] for value in values):
        return None
    try:
        mean = math.fsum(value / len(values) for value in values)
        deviations = tuple(value - mean for value in values)
        squares = tuple(value * value for value in deviations)
        variance = math.fsum(squares) / len(values)
        if not all(math.isfinite(value) for value in (mean, variance, *deviations, *squares)):
            raise discovery_error("finite population baseline and deviations", "numeric overflow")
        if variance == 0:
            if any(value != values[0] for value in values):
                raise discovery_error(
                    "representable nonzero population variance", "variance underflow"
                )
            return None
        stddev = math.sqrt(variance)
        scores = tuple(value / stddev for value in deviations)
        if not all(math.isfinite(value) for value in scores):
            raise discovery_error("finite descriptive z-scores", "score overflow")
        return _Baseline(mean, stddev, deviations, scores)
    except (OverflowError, ValueError, ZeroDivisionError) as error:
        raise discovery_error(
            "finite population baseline and scores", "numeric evaluation failure"
        ) from error


def _time(value: object) -> pd.Timestamp:
    if _missing(value) or not isinstance(value, (date, datetime)):
        raise discovery_error("exact non-null temporal coordinates", "invalid discovery time")
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError("missing timestamp")
        return stamp
    except (ValueError, OverflowError) as error:
        raise discovery_error("finite temporal coordinates", "invalid discovery time") from error


def _metric_links(
    times: list[object], spec: CandidateSpecV1, check: Callable[[], None] | None
) -> list[bool]:
    authority = decode_fold_authority(spec.definition.fold_authority)
    grain, snapshot = authority.time_grain(), authority.temporal_snapshot()
    if grain is None:
        raise discovery_error("captured time grain", "missing discovery temporal authority")
    links: list[bool] = []
    previous_end: pd.Timestamp | None = None
    try:
        for index, value in enumerate(times):
            if check is not None and index % 1024 == 0:
                check()
            stamp = _time(value)
            start, end = bucket_bounds(stamp, grain, snapshot)
            if stamp != start or end <= start:
                raise discovery_error("registered time-coordinate labels", "unaligned time bucket")
            if snapshot is not None and (
                start < pd.Timestamp(snapshot.coverage[0])
                or end > pd.Timestamp(snapshot.coverage[1])
            ):
                raise discovery_error(
                    "coordinates inside certified calendar coverage", "uncovered time bucket"
                )
            links.append(previous_end == start)
            previous_end = end
    except (DatasetCompilationError, ValueError, OverflowError, TypeError) as error:
        raise discovery_error(
            "certified time-coordinate sequence", "unavailable period boundary"
        ) from error
    return links


def _runs(scores: list[float | None], links: list[bool], threshold: float) -> list[tuple[int, ...]]:
    runs: list[tuple[int, ...]] = []
    current: list[int] = []
    for index, score in enumerate(scores):
        if current and (not links[index] or score is None or abs(score) < threshold):
            runs.append(tuple(current))
            current = []
        if score is not None and abs(score) >= threshold:
            current.append(index)
    if current:
        runs.append(tuple(current))
    return runs


@dataclass(frozen=True, slots=True)
class _Series:
    records: tuple[dict[str, object], ...]
    status: str
    searched: int
    evaluated: int = 0
    baseline: _Baseline | None = None
    window_size: int | None = None


def _metric_series(
    frame: pd.DataFrame,
    spec: CandidateSpecV1,
    positions: list[int],
    check: Callable[[], None] | None,
) -> _Series:
    positions.sort(key=lambda index: _time(frame[spec.time_name].iloc[index]))
    times = [frame[spec.time_name].iloc[index] for index in positions]
    links = _metric_links(times, spec, check)
    values = [_number(frame[spec.metric_name].iloc[index]) for index in positions]
    available = [index for index, value in enumerate(values) if value is not None]
    complete = [value for value in values if value is not None]
    if len(complete) < 3:
        return _Series((), "insufficient", len(values))
    baseline = _baseline(complete)
    if baseline is None:
        return _Series((), "constant", len(values))
    scores: list[float | None] = [None] * len(values)
    score: float | None
    for index, score in zip(available, baseline.scores, strict=True):
        scores[index] = score
    dimensions = {name: frame[name].iloc[positions[0]] for name in spec.dimensions}
    records: list[dict[str, object]] = []
    if spec.definition.objective == "point_anomalies":
        for index, score in enumerate(scores):
            if score is None or abs(score) < spec.definition.threshold:
                continue
            observed = values[index]
            assert observed is not None
            records.append(
                {
                    **dimensions,
                    "score": abs(score),
                    "time_coordinate": times[index],
                    "observed_value": observed,
                    "baseline_value": baseline.mean,
                    "signed_deviation": observed - baseline.mean,
                    "direction": "high" if score >= 0 else "low",
                }
            )
    else:
        for run in _runs(scores, links, spec.definition.threshold):
            peak = max(run, key=lambda index: abs(scores[index] or 0.0))
            score = scores[peak]
            assert score is not None
            records.append(
                {
                    **dimensions,
                    "score": abs(score),
                    "window_start": times[run[0]],
                    "window_end": times[run[-1]],
                    "point_count": len(run),
                    "peak_absolute_zscore": abs(score),
                    "direction": "high" if score >= 0 else "low",
                    "baseline_start": times[available[0]],
                    "baseline_end": times[available[-1]],
                }
            )
    return _Series(tuple(records), "evaluated", len(values), len(complete), baseline)


def _ordinal(value: object) -> int:
    if isinstance(value, np.integer):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise discovery_error(
            "nonnegative integer comparison ordinals", "invalid comparison coordinate"
        )
    return value


def _period_series(
    frame: pd.DataFrame,
    spec: CandidateSpecV1,
    positions: list[int],
    check: Callable[[], None] | None,
) -> _Series:
    positions.sort(key=lambda index: _ordinal(frame["comparison_ordinal"].iloc[index]))
    ordinals = [_ordinal(frame["comparison_ordinal"].iloc[index]) for index in positions]
    window = max(7, len(positions) // 10)
    searched = max(0, len(positions) - window + 1)
    baseline_time = spec.baseline_time_name
    if baseline_time is None:
        raise discovery_error("paired Delta time authority", "missing baseline time field")
    current_times = [frame[spec.time_name].iloc[index] for index in positions]
    baseline_times = [frame[baseline_time].iloc[index] for index in positions]
    numbers: list[float | None] = []
    for index, current, baseline in zip(positions, current_times, baseline_times, strict=True):
        if check is not None and len(numbers) % 1024 == 0:
            check()
        value = _number(frame[spec.metric_name].iloc[index], unavailable_nonfinite=True)
        if _missing(current) or _missing(baseline):
            value = None
        else:
            _time(current)
            _time(baseline)
        if "calculation_status" in frame and frame["calculation_status"].iloc[index] != "ok":
            value = None
        numbers.append(value)
    if len(positions) < window + 1:
        return _Series((), "insufficient", searched)
    rolling = pd.Series(numbers, dtype="float64").rolling(window, min_periods=window).mean()
    unavailable = [0]
    for number in numbers:
        unavailable.append(unavailable[-1] + (number is None))
    means: list[float | None] = [None] * len(positions)
    for index in range(window - 1, len(positions)):
        if check is not None and index % 1024 == 0:
            check()
        start = index - window + 1
        if (
            ordinals[index] - ordinals[start] != window - 1
            or unavailable[index + 1] != unavailable[start]
        ):
            continue
        mean = float(rolling.iloc[index])
        if not math.isfinite(mean):
            raise discovery_error(
                "finite complete trailing-window means", "numeric window overflow"
            )
        means[index] = mean
    available = [index for index, mean in enumerate(means) if mean is not None]
    complete = [mean for mean in means if mean is not None]
    if len(complete) < 2:
        return _Series((), "unavailable", searched)
    baseline = _baseline(complete)
    if baseline is None:
        return _Series((), "constant", searched)
    scores: list[float | None] = [None] * len(positions)
    score: float | None
    for index, score in zip(available, baseline.scores, strict=True):
        scores[index] = score
    links = [False, *(right == left + 1 for left, right in pairwise(ordinals))]
    dimensions = {name: frame[name].iloc[positions[0]] for name in spec.dimensions}
    records: list[dict[str, object]] = []
    for run in _runs(scores, links, spec.definition.threshold):
        peak = max(run, key=lambda index: abs(scores[index] or 0.0))
        score = scores[peak]
        assert score is not None
        records.append(
            {
                **dimensions,
                "score": abs(score),
                "window_start": current_times[run[0]],
                "window_end": current_times[run[-1]],
                "baseline_start": baseline_times[run[0]],
                "baseline_end": baseline_times[run[-1]],
                "window_size": window,
                "peak_absolute_zscore": abs(score),
                "direction": "high" if score >= 0 else "low",
            }
        )
    return _Series(tuple(records), "evaluated", searched, len(complete), baseline, window)


def _typed_output(
    records: list[dict[str, object]], spec: CandidateSpecV1, source: pd.DataFrame
) -> pd.DataFrame:
    columns: dict[str, pd.Series] = {}
    for field in spec.output_row.schema.columns:
        values = [record[field.name] for record in records]
        if field.name == "reason_codes":
            columns[field.name] = pd.Series(values, dtype=pd.ArrowDtype(pa.list_(pa.string())))
            continue
        if field.role_id == "dimension":
            incoming = source[field.name].dtype
            arrow = (
                incoming.pyarrow_dtype
                if isinstance(incoming, pd.ArrowDtype)
                else pa.array(source[field.name]).type
            )
            if pa.types.is_null(arrow):
                arrow = pa.type_for_alias(field.logical_type_id)
        elif field.logical_type_id == "date":
            arrow = pa.date32()
        elif field.logical_type_id in ("timestamp", "datetime", "instant"):
            incoming = source[spec.time_name].dtype
            arrow = (
                incoming.pyarrow_dtype
                if isinstance(incoming, pd.ArrowDtype)
                else pa.array(source[spec.time_name]).type
            )
        elif field.logical_type_id == "int64":
            arrow = pa.int64()
        elif field.logical_type_id in ("float64", "numeric"):
            arrow = pa.float64()
        elif field.logical_type_id == "string":
            arrow = pa.string()
        else:
            raise discovery_error(
                "exact registered Candidate field types", "unsupported output field type"
            )
        columns[field.name] = pd.Series(values, dtype=pd.ArrowDtype(arrow))
    return pd.DataFrame(columns)


def execute_candidate(
    frame: pd.DataFrame,
    spec: CandidateSpecV1,
    check: Callable[[], None] | None = None,
) -> tuple[pd.DataFrame, CandidateEvaluationSummary]:
    """Evaluate complete admitted input once, validate all identities, then apply limit."""
    if check is not None:
        check()
    keys = frame_keys(frame, row_key_names(spec.input_row))
    if len(keys) != len(set(keys)):
        raise discovery_error("unique complete source business keys", "duplicate input coordinates")
    groups: dict[tuple[object, ...], list[int]] = {}
    for index, key in enumerate(frame_keys(frame, spec.dimensions)):
        groups.setdefault(key, []).append(index)
    results: list[_Series] = []
    for positions in groups.values():
        if check is not None:
            check()
        results.append(
            _period_series(frame, spec, positions, check)
            if spec.definition.objective == "period_shifts"
            else _metric_series(frame, spec, positions, check)
        )
    evaluated = [result for result in results if result.status == "evaluated"]
    if not evaluated:
        raise discovery_error(
            "at least one evaluable series with a nonconstant finite baseline",
            "no evaluable series",
        )
    records = [record for result in results for record in result.records]
    reason = REASON_CODES[spec.definition.objective]
    for index, record in enumerate(records):
        if check is not None and index % 1024 == 0:
            check()
        record["reason_codes"] = (reason,)
        record["item_id"] = candidate_item_id(spec.definition, spec.output_row, record)
    output = _typed_output(records, spec, frame)
    output_keys = frame_keys(output, row_key_names(spec.output_row))
    if len(output_keys) != len(set(output_keys)):
        raise discovery_error(
            "unique candidate business coordinates before limit", "duplicate candidate keys"
        )
    item_ids = output["item_id"].tolist()
    if len(item_ids) != len(set(item_ids)):
        raise discovery_error(
            "unique deterministic candidate digests", "candidate digest contradiction"
        )
    output = ordered(output, spec.output_row, spec.output_rows)
    selected = output.iloc[: spec.definition.limit].copy(deep=True).reset_index(drop=True)
    scores = output["score"].tolist()
    baselines = [result.baseline for result in evaluated if result.baseline is not None]
    means, deviations = [value.mean for value in baselines], [value.stddev for value in baselines]
    windows = [result.window_size for result in evaluated if result.window_size is not None]
    summary = CandidateEvaluationSummary(
        input_row_count=len(frame),
        series_count=len(results),
        evaluated_series_count=len(evaluated),
        insufficient_series_count=sum(result.status == "insufficient" for result in results),
        constant_series_count=sum(result.status == "constant" for result in results),
        unavailable_series_count=sum(result.status == "unavailable" for result in results),
        searched_unit_count=sum(result.searched for result in results),
        evaluated_unit_count=sum(result.evaluated for result in results),
        pre_limit_candidate_count=len(output),
        emitted_candidate_count=len(selected),
        score_range=(min(scores), max(scores)) if scores else None,
        reason_counts=((reason, len(output)),),
        baseline_mean_range=(min(means), max(means)),
        baseline_stddev_range=(min(deviations), max(deviations)),
        window_size_range=(min(windows), max(windows)) if windows else None,
    )
    if check is not None:
        check()
    return selected, summary
