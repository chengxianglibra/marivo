from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

from marivo.core.semantic.calendar import CalendarAnnotationRow
from marivo.runtime.intents.metric_frame import build_metric_frame_artifact
from marivo.runtime.semantic.calendar_data_runtime import CalendarDataReadResult
from marivo.time_scope import ResolvedTimeAxis

_SESSION = "sess_4c2_test"
_FAKE_ARTIFACT_ID = "art_fake4c2001"


def _make_compiled_mock() -> MagicMock:
    m = MagicMock()
    m.sql = "SELECT 1"
    m.params = []
    m.metadata = {}
    return m


def _set_resolved_time_axis(runtime: MagicMock, expr: str, *, kind: str = "date_field") -> None:
    def _resolve_time_axis(resolved: Any, **_: Any) -> ResolvedTimeAxis:
        axis = ResolvedTimeAxis(
            observation_grain=resolved.time_scope.grain,
            analysis_time_kind=kind,
            analysis_time_expr=expr,
        )
        resolved.resolved_time_axis = axis
        return axis

    runtime.resolve_windowed_query_time_axis.side_effect = _resolve_time_axis


def _scalar_observation(metric: str = "m1") -> dict[str, Any]:
    return _scalar_observation_v2(metric)


def _time_series_observation(
    metric: str = "m1",
    *,
    granularity: str = "day",
    series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if series is None:
        series = [
            {
                "window": {"start": "2024-01-01", "end": "2024-01-02"},
                "value": 10.0,
            },
            {
                "window": {"start": "2024-01-02", "end": "2024-01-03"},
                "value": 20.0,
            },
        ]
    return build_metric_frame_artifact(
        artifact_id=f"art_{metric}_time_series",
        shape="time_series",
        metric_ref=metric,
        time_scope={"field": "time", "start": "2024-01-01", "end": "2024-01-03"},
        scope={},
        axes=[{"kind": "time", "grain": granularity}],
        series=[{"keys": {}, "points": series}],
        unit=None,
    )


# --- v2.0 format fixture helpers (axes+series) ---


def _scalar_observation_v2(metric: str = "m1", *, value: float | None = 42.0) -> dict[str, Any]:
    return build_metric_frame_artifact(
        artifact_id=f"art_{metric}_scalar",
        shape="scalar",
        metric_ref=metric,
        time_scope={"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
        scope={},
        axes=[],
        series=[{"keys": {}, "points": [{"value": value}]}],
        unit=None,
    )


def _time_series_observation_v2(
    metric: str = "m1",
    *,
    granularity: str = "day",
    points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if points is None:
        points = [
            {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 10.0},
            {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 20.0},
        ]
    return build_metric_frame_artifact(
        artifact_id=f"art_{metric}_time_series",
        shape="time_series",
        metric_ref=metric,
        time_scope={"field": "time", "start": "2024-01-01", "end": "2024-01-03"},
        scope={},
        axes=[{"kind": "time", "grain": granularity}],
        series=[{"keys": {}, "points": points}],
        unit=None,
    )


def _segmented_observation_v2(
    metric: str = "m1",
    *,
    dimensions: list[str] | None = None,
    series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if dimensions is None:
        dimensions = ["platform"]
    if series is None:
        series = [
            {"keys": {"platform": "web"}, "points": [{"value": 20.0}]},
            {"keys": {"platform": "mobile"}, "points": [{"value": 10.0}]},
        ]
    axes = [{"kind": "dimension", "name": d} for d in dimensions]
    return build_metric_frame_artifact(
        artifact_id=f"art_{metric}_segmented",
        shape="segmented",
        metric_ref=metric,
        time_scope={"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
        scope={},
        axes=axes,
        series=series,
        unit=None,
    )


def _panel_observation_v2(
    metric: str = "m1",
    *,
    granularity: str = "day",
    dimensions: list[str] | None = None,
    series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if dimensions is None:
        dimensions = ["platform"]
    if series is None:
        series = [
            {
                "keys": {"platform": "web"},
                "points": [
                    {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 10.0},
                    {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 12.0},
                ],
            },
            {
                "keys": {"platform": "mobile"},
                "points": [
                    {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 5.0},
                    {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 6.0},
                ],
            },
        ]
    axes = [{"kind": "time", "grain": granularity}] + [
        {"kind": "dimension", "name": d} for d in dimensions
    ]
    return build_metric_frame_artifact(
        artifact_id=f"art_{metric}_panel",
        shape="panel",
        metric_ref=metric,
        time_scope={"field": "time", "start": "2024-01-01", "end": "2024-01-03"},
        scope={},
        axes=axes,
        series=series,
        unit=None,
    )


class _FakeCalendarDataReader:
    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
    ) -> CalendarDataReadResult:
        return CalendarDataReadResult(
            annotation_rows=[
                CalendarAnnotationRow(
                    calendar_date=date(2025, 2, 20),
                    weekday=6,
                    holiday_group_id="spring_festival",
                    year_relative_holiday_key="spring_festival_d+3",
                ),
                CalendarAnnotationRow(
                    calendar_date=date(2026, 2, 20),
                    weekday=5,
                    holiday_group_id="spring_festival",
                    year_relative_holiday_key="spring_festival_d+3",
                ),
            ],
            source_lineage={"table_fqn": "calendar"},
        )
