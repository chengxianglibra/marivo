"""Shared helpers for building unified axes+series metric frame output."""

from __future__ import annotations

import contextlib
from collections import OrderedDict
from typing import Any

from marivo.time_contracts import TimeGrain, bucket_window


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
    """Read the scalar delta point from a v2.0 compare artifact.

    Returns the first point from the first series entry. Falls back to
    top-level backward-compatible aliases if series is absent or empty.
    """
    series_list = artifact.get("series") or []
    if series_list:
        points = series_list[0].get("points") or []
        if points:
            return dict(points[0])
    # v1.0 fallback: assemble from top-level aliases
    return {
        "current_value": artifact.get("current_value"),
        "baseline_value": artifact.get("baseline_value"),
        "delta": artifact.get("absolute_delta"),
        "delta_pct": artifact.get("relative_delta"),
        "direction": artifact.get("direction") or "undefined",
    }


def read_decompose_rows_from_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Read flat contribution rows from a v2.0 decompose artifact.

    Reconstructs the legacy ``rows`` shape from the axes+series format by
    merging each series entry's keys and point fields into a flat dict.
    The ``key`` field is derived from the dimension axes and keys dict.
    Falls back to the backward-compatible ``rows`` alias if series is absent.
    """
    series_list = artifact.get("series") or []
    if series_list:
        axes = read_axes_from_artifact(artifact)
        dim_names = dimension_names_from_axes(axes)
        rows: list[dict[str, Any]] = []
        for entry in series_list:
            keys = entry.get("keys") or {}
            points = entry.get("points") or []
            for point in points:
                row: dict[str, Any] = {}
                # Derive the key field from keys dict using dimension axes
                if dim_names:
                    for dim_name in dim_names:
                        dim_value = keys.get(dim_name)
                        if dim_value is not None and row.get("key") is None:
                            row["key"] = dim_value
                row.update(keys)
                row.update(point)
                rows.append(row)
        return rows
    # v1.0 fallback
    return artifact.get("rows") or []


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
