"""Shared helpers for building unified axes+series metric frame output."""

from __future__ import annotations

import contextlib
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypedDict

from marivo.time_contracts import TimeGrain, bucket_window

MetricFrameShape = str
DeltaFrameShape = str


class FramePointRef(TypedDict):
    artifact_id: str
    series_index: int
    point_index: int
    series_keys: dict[str, str]
    point_key: str


@dataclass(frozen=True)
class FramePoint:
    artifact_id: str
    series_index: int
    point_index: int
    series_keys: dict[str, str]
    point: dict[str, Any]
    ref: FramePointRef

    @property
    def window(self) -> dict[str, Any] | None:
        window = self.point.get("window")
        if not isinstance(window, dict):
            return None
        copied_window: dict[str, Any] = deepcopy(window)
        return copied_window

    def value(self, field: str) -> Any:
        return self.point.get(field)


@dataclass(frozen=True)
class MetricFrameValueSample:
    sample_key: tuple[tuple[str, str], ...]
    value: Any
    window: dict[str, Any] | None
    series_keys: dict[str, str]


def build_metric_frame_artifact(
    *,
    artifact_id: str,
    shape: MetricFrameShape,
    metric_ref: str,
    time_scope: dict[str, Any],
    scope: dict[str, Any],
    axes: list[dict[str, str]],
    series: list[dict[str, Any]],
    unit: str | None,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "metric_frame",
        "shape": shape,
        "subject": {
            "kind": "metric",
            "metric_ref": metric_ref,
            "time_scope": time_scope,
            "scope": scope,
        },
        "axes": axes,
        "measures": [
            {
                "id": "value",
                "value_type": "number",
                "nullable": True,
                "unit": unit,
            }
        ],
        "payload": {"series": series},
    }


def build_delta_frame_artifact(
    *,
    artifact_id: str,
    shape: DeltaFrameShape,
    metric_ref: str,
    axes: list[dict[str, str]],
    series: list[dict[str, Any]],
    unit: str | None,
    subject: dict[str, Any] | None = None,
    lineage: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
    current_scope: dict[str, Any] | None = None,
    baseline_scope: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    analytical_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_capabilities = capabilities or ["sliceable", "filterable", "decomposable"]
    if shape == "scalar_delta":
        resolved_capabilities = ["filterable", "decomposable"]
    resolved_subject = subject or {
        "kind": "comparison",
        "metric_ref": metric_ref,
        "current": current_scope or {},
        "baseline": baseline_scope or {},
    }
    return {
        "artifact_id": artifact_id,
        "artifact_family": "delta_frame",
        "shape": shape,
        "subject": resolved_subject,
        "axes": axes,
        "measures": [
            {
                "id": "delta_abs",
                "value_type": "number",
                "nullable": True,
                "unit": unit,
            },
            {
                "id": "delta_pct",
                "value_type": "number",
                "nullable": True,
            },
        ],
        "capabilities": resolved_capabilities,
        "lineage": lineage or {},
        "payload": {"series": series, "scope": scope or {}},
        **({"analytical_metadata": analytical_metadata} if analytical_metadata else {}),
        "metric_ref": metric_ref,
    }


def build_attribution_frame_artifact(
    *,
    artifact_id: str,
    metric_ref: str,
    dimension: str,
    subject: dict[str, Any],
    series: list[dict[str, Any]],
    scope: dict[str, Any],
    quality: dict[str, Any],
    lineage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "attribution_frame",
        "shape": "ranked_contributions",
        "subject": subject,
        "axes": [{"kind": "dimension", "name": dimension}],
        "measures": [
            {"id": "contribution_abs", "value_type": "number", "nullable": False},
            {"id": "contribution_pct", "value_type": "number", "nullable": True},
        ],
        "capabilities": ["filterable"],
        "lineage": lineage,
        "payload": {"series": series, "scope": scope, "quality": quality},
        "metric_ref": metric_ref,
    }


def is_metric_frame_artifact(artifact: dict[str, Any]) -> bool:
    return artifact.get("artifact_family") == "metric_frame"


def is_delta_frame_artifact(artifact: dict[str, Any]) -> bool:
    return artifact.get("artifact_family") == "delta_frame"


def is_attribution_frame_artifact(artifact: dict[str, Any]) -> bool:
    return artifact.get("artifact_family") == "attribution_frame"


def read_metric_frame_shape(artifact: dict[str, Any]) -> str:
    shape = artifact.get("shape")
    if not isinstance(shape, str) or not shape:
        raise ValueError("metric_frame artifact missing shape")
    return shape


def read_delta_frame_shape(artifact: dict[str, Any]) -> str:
    if "artifact_family" in artifact and not is_delta_frame_artifact(artifact):
        raise ValueError("delta_frame artifact expected")
    shape = artifact.get("shape")
    if not isinstance(shape, str) or not shape:
        raise ValueError("delta_frame artifact missing shape")
    return shape


def read_delta_frame_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    if "artifact_family" in artifact and not is_delta_frame_artifact(artifact):
        raise ValueError("delta_frame artifact expected")
    return read_frame_payload_series(artifact)


def read_delta_scalar_point(artifact: dict[str, Any]) -> dict[str, Any]:
    series_list = read_delta_frame_series(artifact)
    if series_list:
        points = series_list[0].get("points") or []
        if points:
            return dict(points[0])
    raise ValueError("delta_frame artifact has no scalar delta point")


def metric_display_name(metric_ref: str) -> str:
    return metric_ref.removeprefix("metric.")


def read_metric_frame_subject(artifact: dict[str, Any]) -> dict[str, Any]:
    subject = artifact.get("subject")
    if not isinstance(subject, dict):
        raise ValueError("metric_frame artifact missing subject")
    return subject


def read_metric_frame_time_scope(artifact: dict[str, Any]) -> dict[str, Any]:
    subject = read_metric_frame_subject(artifact)
    time_scope = subject.get("time_scope")
    if not isinstance(time_scope, dict):
        raise ValueError("metric_frame artifact subject missing time_scope")
    return time_scope


def read_metric_frame_scope(artifact: dict[str, Any]) -> dict[str, Any]:
    subject = read_metric_frame_subject(artifact)
    scope = subject.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("metric_frame artifact subject missing scope")
    return scope


def read_metric_frame_metric_ref(artifact: dict[str, Any]) -> str:
    subject = read_metric_frame_subject(artifact)
    metric_ref = subject.get("metric_ref")
    if not isinstance(metric_ref, str) or not metric_ref:
        raise ValueError("metric_frame artifact subject missing metric_ref")
    return metric_ref


def read_metric_frame_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    payload = artifact.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("metric_frame artifact missing payload")
    series = payload.get("series")
    if not isinstance(series, list):
        raise ValueError("metric_frame artifact payload missing series")
    return series


def read_frame_payload_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    payload = artifact.get("payload")
    if isinstance(payload, dict):
        series = payload.get("series")
        if isinstance(series, list):
            return series
    series = artifact.get("series")
    if isinstance(series, list):
        return series
    raise ValueError("frame artifact payload missing series")


def _string_series_keys(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(raw) for key, raw in value.items()}


def _sample_key_from_series_keys(series_keys: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(series_keys.items()))


def _copy_dict(value: dict[str, Any]) -> dict[str, Any]:
    copied_value: dict[str, Any] = deepcopy(value)
    return copied_value


def _point_key(point: dict[str, Any], point_index: int) -> str:
    window = point.get("window")
    if isinstance(window, dict):
        start = str(window.get("start") or "").strip()
        if start:
            return start
    for key in ("item_id", "row_id", "bucket_start", "start"):
        raw = point.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return f"point_{point_index}"


def iter_frame_points(artifact_id: str, artifact: dict[str, Any]) -> list[FramePoint]:
    series_list = read_frame_payload_series(artifact)
    frame_points: list[FramePoint] = []
    for series_index, series in enumerate(series_list):
        if not isinstance(series, dict):
            continue
        series_keys = _string_series_keys(series.get("keys"))
        raw_points = series.get("points") or []
        for point_index, point in enumerate(raw_points):
            if not isinstance(point, dict):
                continue
            point_key = _point_key(point, point_index)
            point_ref: FramePointRef = {
                "artifact_id": artifact_id,
                "series_index": series_index,
                "point_index": point_index,
                "series_keys": dict(series_keys),
                "point_key": point_key,
            }
            frame_points.append(
                FramePoint(
                    artifact_id=artifact_id,
                    series_index=series_index,
                    point_index=point_index,
                    series_keys=dict(series_keys),
                    point=_copy_dict(point),
                    ref=point_ref,
                )
            )
    return frame_points


def iter_metric_frame_value_samples(artifact: dict[str, Any]) -> list[MetricFrameValueSample]:
    """Flatten a metric_frame into shape-native correlation/forecast samples."""
    shape = read_metric_frame_shape(artifact)
    samples: list[MetricFrameValueSample] = []
    for series in read_metric_frame_series(artifact):
        if not isinstance(series, dict):
            continue
        series_keys = _string_series_keys(series.get("keys"))
        raw_points = series.get("points") or []
        for point in raw_points:
            if not isinstance(point, dict):
                continue
            window_raw = point.get("window")
            window = _copy_dict(window_raw) if isinstance(window_raw, dict) else None
            value = point.get("value")

            if shape == "scalar":
                sample_key: tuple[tuple[str, str], ...] = ()
            elif shape == "time_series":
                start = str((window or {}).get("start") or "").strip()
                if not start:
                    continue
                sample_key = (("time", start),)
            elif shape == "segmented":
                sample_key = _sample_key_from_series_keys(series_keys)
                if not sample_key:
                    continue
            elif shape == "panel":
                start = str((window or {}).get("start") or "").strip()
                if not start:
                    continue
                sample_key = (*_sample_key_from_series_keys(series_keys), ("time", start))
            else:
                raise ValueError(f"Unknown metric_frame shape: {shape}")

            samples.append(
                MetricFrameValueSample(
                    sample_key=sample_key,
                    value=value,
                    window=window,
                    series_keys=dict(series_keys),
                )
            )
    return samples


def _first_present(artifact: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in artifact:
            return artifact[key]
    return None


def read_frame_payload_scope(artifact: dict[str, Any]) -> dict[str, Any]:
    payload = artifact.get("payload")
    if isinstance(payload, dict):
        scope = payload.get("scope")
        if isinstance(scope, dict):
            return scope
    scope = artifact.get("scope")
    if isinstance(scope, dict):
        return scope
    summary = artifact.get("summary")
    if isinstance(summary, dict):
        return summary
    return {
        "current_value": _first_present(artifact, ["summary_current_value", "scope_current_value"]),
        "baseline_value": _first_present(
            artifact, ["summary_baseline_value", "scope_baseline_value"]
        ),
        "delta_abs": _first_present(artifact, ["summary_absolute_delta", "scope_absolute_delta"]),
        "delta_pct": _first_present(artifact, ["summary_relative_delta", "scope_relative_delta"]),
        "direction": _first_present(artifact, ["summary_direction", "scope_direction"])
        or "undefined",
    }


def read_metric_frame_unit(artifact: dict[str, Any]) -> str | None:
    measures = artifact.get("measures")
    if not isinstance(measures, list) or not measures:
        return None
    first_measure = measures[0]
    if not isinstance(first_measure, dict):
        return None
    unit = first_measure.get("unit")
    return str(unit) if unit is not None else None


def read_metric_frame_points(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    series = read_metric_frame_series(artifact)
    if not series:
        return []
    first_series = series[0]
    if not isinstance(first_series, dict):
        raise ValueError("metric_frame artifact series entry must be an object")
    points = first_series.get("points")
    if not isinstance(points, list):
        raise ValueError("metric_frame artifact series entry missing points")
    return points


def build_axes(
    granularity: TimeGrain | None,
    dimensions: list[str] | None,
) -> list[dict[str, str]]:
    axes: list[dict[str, str]] = []
    if granularity is not None:
        axes.append({"kind": "time", "grain": granularity})
    if dimensions is not None:
        for dim in dimensions:
            axes.append({"kind": "dimension", "name": dim})
    return axes


def determine_observation_type(
    granularity: TimeGrain | None,
    dimensions: list[str] | None,
) -> str:
    if granularity is not None and dimensions is not None:
        return "panel"
    if granularity is not None:
        return "time_series"
    if dimensions is not None:
        return "segmented"
    return "scalar"


def _coerce_numeric_or_none(value: Any) -> float | None:
    with contextlib.suppress(TypeError, ValueError):
        if value is not None:
            return float(value)
    return None


def build_scalar_series(value: float | None) -> list[dict[str, Any]]:
    return [{"keys": {}, "points": [{"value": value}]}]


def build_time_series_points(
    sparse_series: list[dict[str, Any]],
    start: str,
    end: str,
    granularity: TimeGrain,
    dense_series_builder: Any = None,
) -> list[dict[str, Any]]:
    """Build time_series points list. Each point has {window, value}."""
    if dense_series_builder is not None:
        dense = dense_series_builder(
            sparse_series=sparse_series,
            start=start,
            end=end,
            granularity=granularity,
        )
        return [
            {"window": p.get("window"), "value": _coerce_numeric_or_none(p.get("value"))}
            for p in dense
        ]
    # Fallback: use sparse directly
    return [
        {"window": p.get("window"), "value": _coerce_numeric_or_none(p.get("value"))}
        for p in sparse_series
    ]


def build_segmented_series(
    rows: list[dict[str, Any]],
    dimensions: list[str],
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for row in rows:
        keys = {dim: row.get(dim) for dim in dimensions if dim in row}
        raw_value = row.get("current_value")
        value = _coerce_numeric_or_none(raw_value)
        series.append({"keys": keys, "points": [{"value": value}]})
    series.sort(
        key=lambda item: (
            -(
                item["points"][0]["value"]
                if item["points"][0]["value"] is not None
                else float("-inf")
            ),
            *[str(item["keys"].get(dim, "")) for dim in dimensions],
        )
    )
    return series


def build_panel_series(
    rows: list[dict[str, Any]],
    dimensions: list[str],
    start: str,
    end: str,
    granularity: TimeGrain,
    dense_series_builder: Any = None,
) -> list[dict[str, Any]]:
    """Group rows by dimension keys, build dense time series per group."""
    groups: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
    for row in rows:
        key_tuple = tuple(str(row.get(dim, "")) for dim in dimensions)
        keys_dict = {dim: row.get(dim) for dim in dimensions if dim in row}
        if key_tuple not in groups:
            groups[key_tuple] = {"keys": keys_dict, "sparse_points": []}
        bucket_start = row.get("bucket_start")
        raw_value = row.get("value")
        value = _coerce_numeric_or_none(raw_value)
        try:
            window = bucket_window(bucket_start, granularity)
        except (ValueError, TypeError):
            window = {"start": str(bucket_start), "end": str(bucket_start)}
        groups[key_tuple]["sparse_points"].append({"window": window, "value": value})

    series: list[dict[str, Any]] = []
    for _key_tuple, group in groups.items():
        if dense_series_builder is not None:
            dense_points = dense_series_builder(
                sparse_series=group["sparse_points"],
                start=start,
                end=end,
                granularity=granularity,
            )
            points = [
                {"window": p.get("window"), "value": _coerce_numeric_or_none(p.get("value"))}
                for p in dense_points
            ]
        else:
            points = group["sparse_points"]
        series.append({"keys": group["keys"], "points": points})

    series.sort(
        key=lambda item: (
            -(sum(1 for p in item["points"] if p.get("value") is not None)),
            *[str(item["keys"].get(dim, "")) for dim in dimensions],
        )
    )
    return series


def read_axes_from_artifact(artifact: dict[str, Any]) -> list[dict[str, str]]:
    """Read axes descriptor from a v2.0 artifact."""
    axes_raw = artifact.get("axes", [])
    return list(axes_raw) if axes_raw else []


def read_compare_scalar_point(artifact: dict[str, Any]) -> dict[str, Any]:
    """Read the scalar delta point from a compare or delta-frame artifact."""
    try:
        series_list = read_frame_payload_series(artifact)
    except ValueError:
        series_list = []
    if series_list:
        points = series_list[0].get("points") or []
        if points:
            point = dict(points[0])
            if "delta" not in point and "delta_abs" in point:
                point["delta"] = point["delta_abs"]
            if "delta_pct" not in point and "relative_delta" in point:
                point["delta_pct"] = point["relative_delta"]
            return point
    # v1.0 fallback: assemble from top-level aliases
    return {
        "current_value": artifact.get("current_value"),
        "baseline_value": artifact.get("baseline_value"),
        "delta_abs": artifact.get("absolute_delta"),
        "delta_pct": artifact.get("relative_delta"),
        "direction": artifact.get("direction") or "undefined",
    }


def read_attribution_rows_from_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    if not is_attribution_frame_artifact(artifact):
        raise ValueError("attribution_frame artifact expected")
    axes = read_axes_from_artifact(artifact)
    dim_names = dimension_names_from_axes(axes)
    rows: list[dict[str, Any]] = []
    for entry in read_frame_payload_series(artifact):
        keys = entry.get("keys") or {}
        points = entry.get("points") or []
        for point in points:
            row: dict[str, Any] = {}
            if dim_names:
                for dim_name in dim_names:
                    dim_value = keys.get(dim_name)
                    if dim_value is not None and row.get("key") is None:
                        row["key"] = dim_value
            row.update(keys)
            row.update(point)
            rows.append(row)
    return rows


def has_time_axis(axes: list[dict[str, str]]) -> bool:
    return any(a.get("kind") == "time" for a in axes)


def has_dimension_axis(axes: list[dict[str, str]]) -> bool:
    return any(a.get("kind") == "dimension" for a in axes)


def dimension_names_from_axes(axes: list[dict[str, str]]) -> list[str]:
    return [a.get("name", "") for a in axes if a.get("kind") == "dimension"]


def time_grain_from_axes(axes: list[dict[str, str]]) -> TimeGrain | None:
    for a in axes:
        if a.get("kind") == "time":
            grain = a.get("grain")
            if grain is not None:
                return grain  # type: ignore[return-value]
    return None
