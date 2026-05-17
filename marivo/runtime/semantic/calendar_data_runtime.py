# DEPRECATED: This module will be migrated to ports in Phase 3d+.
# Calendar data I/O should flow through a port protocol, not the analysis_core layer.
from __future__ import annotations

from datetime import date
from typing import Any, Protocol, runtime_checkable

from marivo.adapters.metadata import MetadataStore
from marivo.core.semantic.calendar import (
    CalendarAnnotationRow,
    build_calendar_annotation_rows,
)


class CalendarDataResolutionError(ValueError):
    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@runtime_checkable
class CalendarDataReaderLike(Protocol):
    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
    ) -> CalendarDataReadResult: ...


class CalendarDataReadResult:
    annotation_rows: list[CalendarAnnotationRow]
    source_lineage: dict[str, str]

    def __init__(
        self,
        *,
        annotation_rows: list[CalendarAnnotationRow],
        source_lineage: dict[str, str],
    ) -> None:
        self.annotation_rows = annotation_rows
        self.source_lineage = source_lineage


class CalendarDataReader:
    """Concrete calendar data reader — queries the calendar table by date range."""

    def __init__(self, *, metadata: MetadataStore) -> None:
        self._metadata = metadata

    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
    ) -> CalendarDataReadResult:
        """Read calendar annotation rows covering both windows."""
        combined_start = min(current_window[0], baseline_window[0])
        combined_end = max(current_window[1], baseline_window[1])
        raw_rows = self._read_rows(combined_start, combined_end)
        if not raw_rows:
            raise CalendarDataResolutionError(
                "calendar data unavailable for the requested time range",
                details={
                    "combined_start": combined_start.isoformat(),
                    "combined_end": combined_end.isoformat(),
                },
            )
        annotation_rows = build_calendar_annotation_rows(
            current_window=current_window,
            baseline_window=baseline_window,
            raw_rows=raw_rows,
        )
        return CalendarDataReadResult(
            annotation_rows=annotation_rows,
            source_lineage={"table": "calendar"},
        )

    def _read_rows(
        self,
        combined_start: date,
        combined_end: date,
    ) -> list[dict[str, Any]]:
        """Query the calendar table for rows covering the date range."""
        return self._metadata.query_rows(
            "SELECT calendar_date, weekday, is_weekend, is_workday, "
            "holiday_name, holiday_group_id, year_relative_holiday_key "
            "FROM calendar "
            "WHERE calendar_date >= ? AND calendar_date < ? "
            "ORDER BY calendar_date, holiday_group_id",
            [combined_start.isoformat(), combined_end.isoformat()],
        )
