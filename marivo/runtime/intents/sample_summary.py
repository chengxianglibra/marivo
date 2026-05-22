from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import commit_aoi_artifact_result
from marivo.runtime.intents.metric_frame import (
    is_metric_frame_artifact,
    read_axes_from_artifact,
    read_metric_frame_metric_ref,
    read_metric_frame_series,
    read_metric_frame_shape,
)

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime

_AOI_PARAM_KEYS = frozenset({"source_artifact_id", "sample_kind"})
_SAMPLE_SUMMARY_MEASURES: list[dict[str, Any]] = [
    {"id": "n", "value_type": "integer", "nullable": False},
    {"id": "mean", "value_type": "number", "nullable": True},
    {"id": "standard_deviation", "value_type": "number", "nullable": True},
]


def _require_params(params: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(params, dict):
        raise ValueError("sample_summary: INVALID_ARGUMENT - params must be an object")

    extra_keys = sorted(set(params) - _AOI_PARAM_KEYS)
    if extra_keys:
        raise ValueError(f"sample_summary: INVALID_ARGUMENT - unsupported field(s): {extra_keys}")

    missing_keys = sorted(_AOI_PARAM_KEYS - set(params))
    if missing_keys:
        raise ValueError(
            f"sample_summary: INVALID_ARGUMENT - missing required field(s): {missing_keys}"
        )

    source_artifact_id = params.get("source_artifact_id")
    if not isinstance(source_artifact_id, str) or not source_artifact_id.strip():
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - source_artifact_id must be a non-empty string"
        )

    sample_kind = params.get("sample_kind")
    if not isinstance(sample_kind, str) or not sample_kind.strip():
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - sample_kind must be a non-empty string"
        )
    if sample_kind != "numeric":
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - only sample_kind='numeric' is supported"
        )

    return source_artifact_id.strip(), sample_kind


def extract_time_sample_axis(artifact: dict[str, Any]) -> dict[str, str]:
    """Return the sample axis derived from a time-series metric_frame."""
    shape = read_metric_frame_shape(artifact)
    if shape != "time_series":
        raise ValueError("sample_summary: INVALID_ARGUMENT - requires a time_series metric_frame")

    axes = read_axes_from_artifact(artifact)
    time_axes = [axis for axis in axes if isinstance(axis, dict) and axis.get("kind") == "time"]
    if len(axes) != 1 or len(time_axes) != 1:
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - time_series metric_frame requires exactly "
            "one time axis with grain"
        )

    grain = time_axes[0].get("grain")
    if not isinstance(grain, str) or not grain.strip():
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - time_series metric_frame requires exactly "
            "one time axis with grain"
        )
    return {"kind": "sample", "source_axis": "time", "grain": grain.strip()}


def _coerce_numeric_value(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def _metric_frame_numeric_values(artifact: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for series in read_metric_frame_series(artifact):
        if not isinstance(series, dict):
            continue
        points = series.get("points")
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            value = _coerce_numeric_value(point.get("value"))
            if value is not None:
                values.append(value)
    return values


def compute_numeric_summary_from_metric_frame(artifact: dict[str, Any]) -> dict[str, Any]:
    """Compute n/mean/sample standard deviation from metric_frame point values."""
    values = _metric_frame_numeric_values(artifact)
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "standard_deviation": None}

    mean = sum(values) / n
    standard_deviation = None
    if n > 1:
        variance = sum((value - mean) ** 2 for value in values) / (n - 1)
        standard_deviation = math.sqrt(variance)

    return {"n": n, "mean": mean, "standard_deviation": standard_deviation}


def _quality_for_summary(summary: dict[str, Any]) -> dict[str, Any]:
    n = summary["n"]
    if n >= 2:
        return {"status": "test_ready", "issues": []}
    if n == 1:
        return {
            "status": "insufficient_data",
            "issues": [
                {
                    "code": "insufficient_sample_size",
                    "message": "At least two numeric points are required for sample standard deviation.",
                }
            ],
        }
    return {
        "status": "insufficient_data",
        "issues": [
            {
                "code": "no_numeric_points",
                "message": "The source metric_frame contains no numeric point values.",
            }
        ],
    }


def build_sample_frame_artifact(
    *,
    artifact_id: str,
    source_artifact_id: str,
    source_artifact: dict[str, Any],
) -> dict[str, Any]:
    metric_ref = read_metric_frame_metric_ref(source_artifact)
    summary = compute_numeric_summary_from_metric_frame(source_artifact)
    return {
        "artifact_id": artifact_id,
        "artifact_family": "sample_frame",
        "shape": "numeric_summary",
        "subject": {
            "kind": "sample_summary",
            "metric_ref": metric_ref,
            "source_artifact_id": source_artifact_id,
        },
        "axes": [extract_time_sample_axis(source_artifact)],
        "measures": [dict(measure) for measure in _SAMPLE_SUMMARY_MEASURES],
        "lineage": {
            "operation": "sample_summary",
            "source_artifact_ids": [source_artifact_id],
        },
        "payload": {
            "summary": summary,
            "quality": _quality_for_summary(summary),
        },
    }


def run_sample_summary_transform(
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    *,
    reasoning: str | None = None,
) -> dict[str, Any]:
    source_artifact_id, _sample_kind = _require_params(params)
    source_artifact = runtime.resolve_artifact_by_id(session_id, source_artifact_id)
    if not isinstance(source_artifact, dict) or not is_metric_frame_artifact(source_artifact):
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - source_artifact_id must point to a metric_frame "
            "artifact with artifact_family='metric_frame'"
        )

    step_id = new_step_id()
    artifact = build_sample_frame_artifact(
        artifact_id="art_placeholder",
        source_artifact_id=source_artifact_id,
        source_artifact=source_artifact,
    )
    metric_ref = artifact["subject"]["metric_ref"]
    summary = artifact["payload"]["summary"]
    provenance = {
        "source_artifact_id": source_artifact_id,
        "source_artifact_family": "metric_frame",
        "source_shape": read_metric_frame_shape(source_artifact),
        "sample_kind": "numeric",
        "executed_at": datetime.now(UTC).isoformat(),
    }
    envelope = commit_aoi_artifact_result(
        runtime,
        session_id,
        step_id,
        "sample_summary",
        "sample_frame",
        f"{metric_ref.removeprefix('metric.')}_sample_summary",
        artifact,
        f"sample_summary {metric_ref} from {source_artifact_id}: n={summary['n']}",
        provenance=provenance,
        reasoning=reasoning,
        semantic_metadata=None,
        sql_texts=[],
    )
    return envelope.model_dump()
