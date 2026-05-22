from __future__ import annotations

import contextlib
import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import commit_aoi_artifact_result
from marivo.runtime.intents.metric_frame import (
    FramePoint,
    is_delta_frame_artifact,
    is_metric_frame_artifact,
    iter_frame_points,
    read_delta_frame_shape,
    read_metric_frame_metric_ref,
    read_metric_frame_shape,
)

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime


_SENSITIVITY_THRESHOLD: dict[str, float] = {
    "conservative": 2.5,
    "balanced": 2.0,
    "aggressive": 1.5,
}

_PERIOD_SHIFT_THRESHOLD: dict[str, float] = {
    "conservative": 0.30,
    "balanced": 0.20,
    "aggressive": 0.10,
}

_MIN_POINTS_FOR_DETECTION = 3
_AOI_PARAM_KEYS: frozenset[str] = frozenset({"source_artifact_id", "sensitivity", "limit"})
_METRIC_FRAME_SCAN_SHAPES: frozenset[str] = frozenset({"time_series", "panel"})
_DELTA_FRAME_SCAN_SHAPES: frozenset[str] = frozenset({"time_series_delta", "panel_delta"})


def _coerce_float(value: Any) -> float | None:
    with contextlib.suppress(TypeError, ValueError):
        if value is not None:
            return float(value)
    return None


def _candidate_item_id(prefix: str, point: FramePoint) -> str:
    return f"{prefix}:series_{point.series_index}:{point.ref['point_key']}"


def _direction_from_delta(delta: float | None) -> str:
    if delta is None:
        return "unknown"
    if delta > 0:
        return "increase"
    if delta < 0:
        return "decrease"
    return "unknown"


def _source_keys(point: FramePoint) -> dict[str, str] | None:
    return dict(point.series_keys) if point.series_keys else None


def _score_metric_frame_series(
    points: list[FramePoint],
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    numeric_points: list[tuple[FramePoint, float]] = []
    for point in points:
        value = _coerce_float(point.value("value"))
        if value is not None:
            numeric_points.append((point, value))

    if len(numeric_points) < _MIN_POINTS_FOR_DETECTION:
        return []

    numeric_values = [value for _, value in numeric_points]
    mean = sum(numeric_values) / len(numeric_values)
    variance = sum((value - mean) ** 2 for value in numeric_values) / len(numeric_values)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0:
        return []

    candidates: list[dict[str, Any]] = []
    for point, value in numeric_points:
        window = point.window
        if window is None:
            continue
        delta_abs = value - mean
        score = abs(delta_abs / std)
        if score <= threshold:
            continue
        candidates.append(
            {
                "item_id": _candidate_item_id("point_anomaly", point),
                "window": window,
                "keys": _source_keys(point),
                "value": value,
                "baseline_value": mean,
                "delta_abs": delta_abs,
                "delta_pct": delta_abs / abs(mean) if mean != 0 else None,
                "score": score,
                "direction": _direction_from_delta(delta_abs),
                "source_point_ref": dict(point.ref),
            }
        )
    return candidates


def _delta_direction(point: FramePoint, delta_abs: float | None) -> str:
    direction_raw = point.value("direction")
    if direction_raw in {"increase", "decrease"}:
        return str(direction_raw)
    return _direction_from_delta(delta_abs)


def _score_delta_frame_points(
    points: Iterable[FramePoint],
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for point in points:
        window = point.window
        if window is None:
            continue
        current_value = _coerce_float(point.value("current_value"))
        baseline_value = _coerce_float(point.value("baseline_value"))
        delta_abs = _coerce_float(point.value("delta_abs"))
        delta_pct = _coerce_float(point.value("delta_pct"))
        if delta_abs is None and current_value is not None and baseline_value is not None:
            delta_abs = current_value - baseline_value
        if (
            delta_pct is None
            and delta_abs is not None
            and baseline_value is not None
            and baseline_value != 0
        ):
            delta_pct = delta_abs / abs(baseline_value)

        if delta_pct is not None:
            score = abs(delta_pct)
        elif delta_abs is not None:
            score = abs(delta_abs)
        else:
            continue
        if score < threshold:
            continue

        candidates.append(
            {
                "item_id": _candidate_item_id("period_shift", point),
                "window": window,
                **(
                    {"baseline_window": dict(point.value("baseline_window"))}
                    if isinstance(point.value("baseline_window"), dict)
                    else {}
                ),
                "keys": _source_keys(point),
                "value": current_value,
                "baseline_value": baseline_value,
                "delta_abs": delta_abs,
                "delta_pct": delta_pct,
                "score": score,
                "direction": _delta_direction(point, delta_abs),
                "source_delta_point_ref": dict(point.ref),
            }
        )
    return candidates


def _read_delta_metric_ref(source_artifact: dict[str, Any]) -> str:
    subject = source_artifact.get("subject")
    metric_ref = subject.get("metric_ref") if isinstance(subject, dict) else None
    if not isinstance(metric_ref, str) or not metric_ref.strip():
        metric_ref = source_artifact.get("metric_ref")
    if not isinstance(metric_ref, str) or not metric_ref.strip():
        raise ValueError("detect: INVALID_ARGUMENT - source delta_frame missing metric_ref")
    return metric_ref.strip()


def _validate_sensitivity(raw_sensitivity: Any) -> str:
    sensitivity = str(raw_sensitivity or "aggressive").lower()
    if sensitivity not in _SENSITIVITY_THRESHOLD:
        raise ValueError(
            f"detect: INVALID_ARGUMENT - sensitivity='{sensitivity}' is not valid. "
            f"Must be one of: {sorted(_SENSITIVITY_THRESHOLD)}"
        )
    return sensitivity


def _validate_limit(raw_limit: Any) -> int | None:
    if raw_limit is None:
        return None
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("detect: INVALID_ARGUMENT - limit must be an integer") from exc
    if limit <= 0:
        raise ValueError("detect: INVALID_ARGUMENT - limit must be > 0")
    return limit


def _scanned_series_count(points: list[FramePoint]) -> int:
    return len({point.series_index for point in points})


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, str, str]:
    window = candidate.get("window")
    window_start = str(window.get("start") or "") if isinstance(window, dict) else ""
    return (-float(candidate["score"]), window_start, str(candidate.get("item_id") or ""))


def _metric_frame_quality(points: list[FramePoint]) -> dict[str, Any]:
    numeric_counts_by_series: dict[int, int] = {}
    for point in points:
        numeric_counts_by_series.setdefault(point.series_index, 0)
        if _coerce_float(point.value("value")) is not None:
            numeric_counts_by_series[point.series_index] += 1

    max_series_numeric_points = max(numeric_counts_by_series.values(), default=0)
    eligible_series_count = sum(
        1 for count in numeric_counts_by_series.values() if count >= _MIN_POINTS_FOR_DETECTION
    )
    if eligible_series_count > 0:
        return {"status": "detectable", "issues": []}

    return {
        "status": "needs_attention",
        "issues": [
            {
                "code": "insufficient_points",
                "severity": "warning",
                "message": (
                    "No scanned series has enough numeric points for point anomaly scanning; "
                    f"maximum per-series numeric points is {max_series_numeric_points}, "
                    f"minimum {_MIN_POINTS_FOR_DETECTION} required."
                ),
            }
        ],
    }


def run_detect_intent(
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    """Execute detect by scanning a committed metric_frame or delta_frame artifact."""
    p = params or {}
    extra_keys = sorted(set(p) - _AOI_PARAM_KEYS)
    if extra_keys:
        raise ValueError(
            "detect: INVALID_ARGUMENT - unsupported parameter(s): "
            f"{extra_keys}; detect accepts only source_artifact_id, sensitivity, and limit"
        )

    source_artifact_id_raw = p.get("source_artifact_id")
    source_artifact_id = (
        source_artifact_id_raw.strip() if isinstance(source_artifact_id_raw, str) else ""
    )
    if not source_artifact_id:
        raise ValueError("detect: INVALID_ARGUMENT - source_artifact_id is required")

    sensitivity = _validate_sensitivity(p.get("sensitivity"))
    limit = _validate_limit(p.get("limit"))

    resolved = runtime.resolve_artifact_with_step_by_id(session_id, source_artifact_id)
    if resolved is None:
        raise ValueError(
            "detect: ARTIFACT_NOT_FOUND - no committed artifact for "
            f"source_artifact_id '{source_artifact_id}'"
        )
    _source_step_id, source_artifact = resolved

    if is_metric_frame_artifact(source_artifact):
        source_family = "metric_frame"
        source_shape = read_metric_frame_shape(source_artifact)
        if source_shape not in _METRIC_FRAME_SCAN_SHAPES:
            raise ValueError(
                f"detect: INVALID_ARGUMENT - metric_frame shape '{source_shape}' is not supported"
            )
        strategy = "point_anomaly"
        output_shape = "point_anomaly_candidates"
        metric_ref = read_metric_frame_metric_ref(source_artifact)
        source_points = iter_frame_points(source_artifact_id, source_artifact)
        by_series: dict[int, list[FramePoint]] = {}
        for point in source_points:
            by_series.setdefault(point.series_index, []).append(point)
        raw_candidates = [
            candidate
            for series_points in by_series.values()
            for candidate in _score_metric_frame_series(
                series_points,
                threshold=_SENSITIVITY_THRESHOLD[sensitivity],
            )
        ]
    elif is_delta_frame_artifact(source_artifact):
        source_family = "delta_frame"
        source_shape = read_delta_frame_shape(source_artifact)
        if source_shape not in _DELTA_FRAME_SCAN_SHAPES:
            raise ValueError(
                f"detect: INVALID_ARGUMENT - delta_frame shape '{source_shape}' is not supported"
            )
        strategy = "period_shift"
        output_shape = "period_shift_candidates"
        metric_ref = _read_delta_metric_ref(source_artifact)
        source_points = iter_frame_points(source_artifact_id, source_artifact)
        raw_candidates = _score_delta_frame_points(
            source_points,
            threshold=_PERIOD_SHIFT_THRESHOLD[sensitivity],
        )
    else:
        family = source_artifact.get("artifact_family") or source_artifact.get("artifact_type")
        raise ValueError(
            f"detect: INVALID_ARGUMENT - source artifact family '{family}' is not supported"
        )

    raw_candidates.sort(key=_candidate_sort_key)
    total_candidate_count = len(raw_candidates)
    returned_candidates = raw_candidates[:limit] if limit is not None else raw_candidates
    returned_candidate_count = len(returned_candidates)
    truncated = returned_candidate_count < total_candidate_count

    if source_family == "metric_frame":
        quality = _metric_frame_quality(source_points)
    else:
        quality = {"status": "detectable", "issues": []}

    step_id = new_step_id()
    artifact: dict[str, Any] = {
        "artifact_id": "art_placeholder",
        "artifact_family": "candidate_set",
        "shape": output_shape,
        "subject": {
            "kind": "candidate_scan",
            "metric_ref": metric_ref,
            "source_artifact_id": source_artifact_id,
            "source_artifact_family": source_family,
            "source_shape": source_shape,
        },
        "axes": source_artifact.get("axes") or [],
        "measures": [
            {"id": "score", "value_type": "number", "nullable": False},
            {"id": "value", "value_type": "number", "nullable": True},
            {"id": "baseline_value", "value_type": "number", "nullable": True},
            {"id": "delta_abs", "value_type": "number", "nullable": True},
            {"id": "delta_pct", "value_type": "number", "nullable": True},
        ],
        "capabilities": ["filterable"],
        "lineage": {
            "operation": "detect",
            "source_artifact_ids": [source_artifact_id],
            "strategy": strategy,
        },
        "payload": {
            "items": returned_candidates,
            "scan_summary": {
                "scanned_series_count": _scanned_series_count(source_points),
                "total_candidate_count": total_candidate_count,
            },
            "truncation": {
                "returned_candidate_count": returned_candidate_count,
                "total_candidate_count": total_candidate_count,
                "truncated": truncated,
            },
            "quality": quality,
        },
    }

    provenance = {
        "source_artifact_id": source_artifact_id,
        "source_artifact_family": source_family,
        "source_shape": source_shape,
        "strategy": strategy,
        "detector_version": "2.0",
        "executed_at": datetime.now(UTC).isoformat(),
    }
    artifact_name = f"{metric_ref.removeprefix('metric.')}_candidate_set"
    summary = f"detect {metric_ref} from {source_artifact_id}: {total_candidate_count} candidate(s)"
    envelope = commit_aoi_artifact_result(
        runtime,
        session_id,
        step_id,
        "detect",
        "candidate_set",
        artifact_name,
        artifact,
        summary,
        provenance=provenance,
        reasoning=reasoning,
        semantic_metadata=None,
        sql_texts=[],
    )
    return envelope.model_dump()
