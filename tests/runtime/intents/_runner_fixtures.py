from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

from marivo.core.semantic.calendar import CalendarAnnotationRow
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
    return {
        "observation_type": "scalar",
        "metric": metric,
        "schema_version": "1.0",
        "unit": None,
        "value": 42.0,
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "additive_dimensions": ["country", "device", "date"],
            "row_count": 10,
        },
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        "scope": {},
    }


def _time_series_observation(
    metric: str = "m1",
    *,
    granularity: str = "day",
    series: list[dict[str, Any]] | None = None,
    aligned_baseline_series: list[dict[str, Any]] | None = None,
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
    result = {
        "observation_type": "time_series",
        "metric": metric,
        "schema_version": "1.0",
        "unit": None,
        "granularity": granularity,
        "series": series,
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "additive_dimensions": ["country", "device", "date"],
            "row_count": len(series),
        },
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-03"},
        "scope": {},
    }
    if aligned_baseline_series is not None:
        result["aligned_baseline_series"] = aligned_baseline_series
    return result


class _FakeCalendarDataReader:
    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
        region_code: str | None = None,
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
            resolved_calendar_source="calendar",
            resolved_calendar_version="cn_2026_v1",
            source_lineage={
                "table_fqn": "calendar",
                "calendar_version": "cn_2026_v1",
            },
        )
