"""Helpers for sampled semi-additive metric folding."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from marivo.analysis.errors import AnalysisError
from marivo.analysis.intents.observe_errors import raise_observe_planning_error
from marivo.analysis.windows.grain import Grain

FoldKind = Literal["mean", "min", "max", "first", "last", "percentile"]


@dataclass(frozen=True)
class QuantileCapability:
    mode: Literal["exact", "approximate"]
    method: str


QUANTILE_CAPABILITIES: dict[str, QuantileCapability] = {
    "duckdb": QuantileCapability(mode="exact", method="linear_interpolation"),
    "trino": QuantileCapability(mode="approximate", method="qdigest"),
    "clickhouse": QuantileCapability(mode="approximate", method="reservoir_sampling"),
}


def quantile_capability(backend_type: str) -> QuantileCapability:
    capability = QUANTILE_CAPABILITIES.get(backend_type)
    if capability is None:
        raise AnalysisError(
            message=f"quantile sampled fold is not registered for backend_type {backend_type!r}",
            details={
                "backend_type": backend_type,
                "supported_backend_types": sorted(QUANTILE_CAPABILITIES),
            },
        )
    return capability


@dataclass(frozen=True)
class FoldExecutionMetadata:
    time_fold: str
    fold_kind: FoldKind
    fold_q: float | None
    status_time_dimension: str
    sample_interval: str
    reaggregatable: bool
    quantile_mode: str | None = None
    quantile_method: str | None = None


def fold_label(time_fold: Any) -> str:
    result: str = time_fold.label() if hasattr(time_fold, "label") else str(time_fold)
    return result


def fold_is_linear(time_fold: Any) -> bool:
    return getattr(time_fold, "kind", None) == "mean"


def sample_interval_token(sample_interval: Any) -> str:
    result: str = sample_interval.to_token()
    return result


_FIXED_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
}


def _fixed_grain_seconds(count: int, unit: str) -> int:
    if unit not in _FIXED_UNIT_SECONDS:
        raise_observe_planning_error(
            code="sampled-grain-floor-unsupported-unit",
            message="sampled metric grain-floor checks require a fixed-size grain unit.",
            candidates={"unit": unit},
            repair=[],
        )
    return count * _FIXED_UNIT_SECONDS[unit]


def ensure_sampled_grain_supported(
    *,
    requested_grain: Grain | None,
    time_meta: Any,
    sample_interval: Any,
) -> None:
    if requested_grain is None:
        return
    physical_floor_seconds = _fixed_grain_seconds(1, time_meta.granularity)
    sample_floor_seconds = _fixed_grain_seconds(sample_interval.count, sample_interval.unit)
    effective_floor_seconds = max(physical_floor_seconds, sample_floor_seconds)
    requested_seconds = _fixed_grain_seconds(requested_grain.count, requested_grain.unit)
    if requested_seconds < effective_floor_seconds:
        raise_observe_planning_error(
            code="grain-finer-than-sampled-floor",
            message="observe grain is finer than the sampled metric's effective grain floor.",
            candidates={
                "requested_grain": requested_grain.to_token(),
                "physical_granularity": time_meta.granularity,
                "sample_interval": sample_interval.to_token(),
            },
            repair=[],
        )


def ensure_status_time_dimension_matches(
    metric_ir: Any, requested_time_dimension: str | None
) -> None:
    if getattr(metric_ir, "additivity", None) != "semi_additive":
        return
    bound = metric_ir.status_time_dimension
    if bound is None:
        raise_observe_planning_error(
            code="status-time-dimension-unresolved",
            message=f"Metric {metric_ir.semantic_id!r} has no resolved status_time_dimension.",
            candidates={"metric": metric_ir.semantic_id},
            repair=[],
        )
    if requested_time_dimension is not None and requested_time_dimension != bound:
        raise_observe_planning_error(
            code="status-time-dimension-mismatch",
            message=f"Metric {metric_ir.semantic_id!r} is bound to status time axis {bound!r}.",
            candidates={
                "requested_time_dimension": requested_time_dimension,
                "status_time_dimension": bound,
            },
            repair=[],
        )


def sample_set_digest(
    *,
    root_entity: str,
    status_time_dimension: str,
    window: dict[str, Any] | None,
    dimensions: list[str],
    where: dict[str, Any],
    sample_interval: str,
) -> str:
    payload = {
        "root_entity": root_entity,
        "status_time_dimension": status_time_dimension,
        "window": window,
        "dimensions": sorted(dimensions),
        "where": where,
        "sample_interval": sample_interval,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def compile_fold(value: Any, sample_point: Any, time_fold: Any) -> Any:
    kind = time_fold.kind
    if kind == "mean":
        return value.mean()
    if kind == "min":
        return value.min()
    if kind == "max":
        return value.max()
    if kind == "first":
        return value.argmin(sample_point)
    if kind == "last":
        return value.argmax(sample_point)
    if kind == "percentile":
        return value.quantile(time_fold.q)
    raise AnalysisError(message=f"unsupported time_fold kind {kind!r}")


def coverage_status_expr(actual: Any, expected: Any) -> Any:
    """Build an ibis expression that returns 'complete' when actual == expected, else 'partial'."""
    return (actual == expected).ifelse("complete", "partial")


def sample_point_table(
    table: Any,
    *,
    time_field_ir: Any,
    sample_grain: Grain,
    report_tz: Any,
    datasource_read_tz: Any,
    window: Any,
    dataset_ir: Any | None = None,
) -> Any:
    from marivo.analysis.executor.runner import (
        bucket_time_expression,
        combine_prefix_hour_to_timestamp,
    )

    raw = time_field_ir.fn(table)
    time_meta = time_field_ir.time_meta
    if time_meta is None:
        raise_observe_planning_error(
            code="status-time-dimension-missing-metadata",
            message=f"status time dimension {time_field_ir.semantic_id!r} has no time metadata.",
            candidates={"time_dimension": time_field_ir.semantic_id},
            repair=[],
        )
    if (
        time_meta.data_type in {"datetime", "timestamp"}
        or getattr(time_meta, "parse_kind", None) == "strptime"
    ):
        sample_point = bucket_time_expression(
            raw,
            time_meta=time_meta,
            grain=sample_grain,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            window=window,
        )
        return table.mutate(sample_point=sample_point)
    if getattr(time_meta, "parse_kind", None) == "hour_prefix":
        if dataset_ir is None:
            raise_observe_planning_error(
                code="status-time-dimension-unsupported-type",
                message="hour_prefix sampled time dimensions require dataset context.",
                candidates={"time_dimension": time_field_ir.semantic_id},
                repair=[],
            )
        combined_ts = combine_prefix_hour_to_timestamp(
            table,
            hour_field_ir=time_field_ir,
            dataset_ir=dataset_ir,
        )
        import types

        synthetic_meta = types.SimpleNamespace(
            data_type="timestamp",
            granularity=time_meta.granularity,
            timezone=str(report_tz),
            format=None,
        )
        sample_point = bucket_time_expression(
            combined_ts,
            time_meta=synthetic_meta,
            grain=sample_grain,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            window=window,
        )
        return table.mutate(sample_point=sample_point)
    raise_observe_planning_error(
        code="status-time-dimension-unsupported-type",
        message="sampled folds require a datetime, timestamp, strptime, or hour_prefix sampled time dimension.",
        candidates={"time_dimension": time_field_ir.semantic_id, "data_type": time_meta.data_type},
        repair=[],
    )
