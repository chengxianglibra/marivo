# DEPRECATED: This module will be migrated to ports in Phase 3d+.
# Calendar data I/O should flow through a port protocol, not the analysis_core layer.
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable

from marivo.config import CalendarConfig
from marivo.core.semantic.calendar import CalendarAnnotationRow
from marivo.storage.metadata import MetadataStore

_RESOLVED_CALENDAR_SOURCE = "calendar"


class CalendarDataResolutionError(ValueError):
    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class CalendarDataReadResult:
    annotation_rows: list[CalendarAnnotationRow]
    resolved_calendar_source: str
    resolved_calendar_version: str
    source_lineage: dict[str, str]


@runtime_checkable
class CalendarDataReaderLike(Protocol):
    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
        region_code: str | None = None,
    ) -> CalendarDataReadResult: ...


class CalendarDataReader:
    def __init__(
        self,
        *,
        metadata: MetadataStore,
        config: CalendarConfig,
    ) -> None:
        self.metadata = metadata
        self.region_code = (config.region_code or "CN").strip() or "CN"
        self.pinned_version = config.calendar_version.strip() if config.calendar_version else None

    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
        region_code: str | None = None,
    ) -> CalendarDataReadResult:
        resolved_region = (region_code or self.region_code).strip() or self.region_code
        resolved_version = self._resolve_calendar_version(region_code=resolved_region)
        read_start = min(current_window[0], baseline_window[0])
        read_end = max(current_window[1], baseline_window[1])
        raw_rows = self._read_rows(
            calendar_version=resolved_version,
            region_code=resolved_region,
            read_start=read_start,
            read_end=read_end,
        )
        annotation_rows = self._build_annotation_rows(
            raw_rows=raw_rows,
            current_window=current_window,
            baseline_window=baseline_window,
        )
        return CalendarDataReadResult(
            annotation_rows=annotation_rows,
            resolved_calendar_source=_RESOLVED_CALENDAR_SOURCE,
            resolved_calendar_version=resolved_version,
            source_lineage={
                "table_fqn": _RESOLVED_CALENDAR_SOURCE,
                "calendar_version": resolved_version,
            },
        )

    def _resolve_calendar_version(self, *, region_code: str) -> str:
        if self.pinned_version:
            return self.pinned_version
        row = self.metadata.query_one(
            "SELECT MAX(calendar_version) AS max_version FROM calendar WHERE region_code = ?",
            [region_code],
        )
        if row is None or row.get("max_version") is None:
            raise CalendarDataResolutionError(
                "no calendar data available for the requested region",
                details={"region_code": region_code},
            )
        return str(row["max_version"])

    def _read_rows(
        self,
        *,
        calendar_version: str,
        region_code: str,
        read_start: date,
        read_end: date,
    ) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            """
            SELECT calendar_date, weekday, is_weekend, is_workday,
                   holiday_group_id, year_relative_holiday_key,
                   event_group_id, year_relative_event_key
            FROM calendar
            WHERE calendar_version = ?
              AND region_code = ?
              AND calendar_date >= ?
              AND calendar_date < ?
            ORDER BY calendar_date
            """,
            [
                calendar_version,
                region_code,
                read_start.isoformat(),
                read_end.isoformat(),
            ],
        )
        if not rows and self.pinned_version:
            raise CalendarDataResolutionError(
                "pinned calendar_version has no data for the requested window",
                details={
                    "calendar_version": calendar_version,
                    "region_code": region_code,
                    "read_start": read_start.isoformat(),
                    "read_end": read_end.isoformat(),
                },
            )
        return rows

    def _build_annotation_rows(
        self,
        *,
        raw_rows: list[dict[str, Any]],
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
    ) -> list[CalendarAnnotationRow]:
        rows_by_date: dict[date, dict[str, Any]] = {}
        for raw_row in raw_rows:
            calendar_date = _parse_row_date(raw_row.get("calendar_date"))
            weekday = int(raw_row.get("weekday") or 0)
            if weekday < 1 or weekday > 7:
                raise CalendarDataResolutionError(
                    "calendar data contains invalid weekday",
                    details={
                        "calendar_date": calendar_date.isoformat(),
                        "weekday": raw_row.get("weekday"),
                    },
                )
            if raw_row.get("is_weekend") is None or raw_row.get("is_workday") is None:
                raise CalendarDataResolutionError(
                    "calendar data must include is_weekend and is_workday",
                    details={"calendar_date": calendar_date.isoformat()},
                )
            rows_by_date[calendar_date] = raw_row

        annotation_rows: list[CalendarAnnotationRow] = []
        for cursor in _iter_window_dates(current_window, baseline_window):
            row = rows_by_date.get(cursor)
            if row is None:
                raise CalendarDataResolutionError(
                    "calendar data does not cover every requested day",
                    details={"calendar_date": cursor.isoformat()},
                )
            annotation_rows.append(
                CalendarAnnotationRow(
                    calendar_date=cursor,
                    weekday=int(row.get("weekday") or 0),
                    holiday_group_id=_optional_str(row.get("holiday_group_id")),
                    year_relative_holiday_key=_optional_str(row.get("year_relative_holiday_key")),
                    event_group_id=_optional_str(row.get("event_group_id")),
                    year_relative_event_key=_optional_str(row.get("year_relative_event_key")),
                )
            )
        return annotation_rows


def _parse_row_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError as error:
        raise CalendarDataResolutionError(
            "calendar data contains invalid calendar_date",
            details={"calendar_date": value},
        ) from error


def _iter_window_dates(*windows: tuple[date, date]) -> list[date]:
    all_dates: list[date] = []
    seen: set[date] = set()
    for window in windows:
        cursor = window[0]
        while cursor < window[1]:
            if cursor not in seen:
                seen.add(cursor)
                all_dates.append(cursor)
            cursor = date.fromordinal(cursor.toordinal() + 1)
    return all_dates


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
