from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable

from app.analysis_core.calendar_alignment_pairing import CalendarAnnotationRow
from app.config import CalendarConfig, CalendarSnapshotConfig, CalendarSourceBindingConfig
from app.routing import QueryRouter
from app.storage.metadata import MetadataStore


class CalendarDataResolutionError(ValueError):
    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class CalendarSourceBinding:
    source_id: str
    source_name: str
    table_fqn: str
    calendar_version: str


@dataclass(frozen=True, slots=True)
class CalendarSnapshotBinding:
    resolved_calendar_source: str
    resolved_calendar_version: str
    region_code: str
    effective_start: date
    effective_end: date
    holiday_source: CalendarSourceBinding
    event_source: CalendarSourceBinding | None


@dataclass(frozen=True, slots=True)
class CalendarDataReadResult:
    annotation_rows: list[CalendarAnnotationRow]
    resolved_calendar_source: str
    resolved_calendar_version: str
    source_lineage: dict[str, dict[str, str]]


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
        query_router: QueryRouter,
        config: CalendarConfig,
    ) -> None:
        self.metadata = metadata
        self.query_router = query_router
        self.default_region_code = config.default_region_code.strip() or "CN"
        self.snapshots = tuple(
            self._build_snapshot_binding(snapshot) for snapshot in config.snapshots
        )

    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
        region_code: str | None = None,
    ) -> CalendarDataReadResult:
        requested_region = (
            region_code or self.default_region_code
        ).strip() or self.default_region_code
        snapshot = self._resolve_snapshot(
            current_window=current_window,
            baseline_window=baseline_window,
            region_code=requested_region,
        )
        read_start = min(current_window[0], baseline_window[0])
        read_end = max(current_window[1], baseline_window[1])
        holiday_rows = self._read_source_rows(
            binding=snapshot.holiday_source,
            read_start=read_start,
            read_end=read_end,
            region_code=requested_region,
            required_fields=(
                "calendar_date",
                "weekday",
                "is_weekend",
                "is_workday",
                "holiday_group_id",
                "year_relative_holiday_key",
            ),
        )
        event_rows = (
            self._read_source_rows(
                binding=snapshot.event_source,
                read_start=read_start,
                read_end=read_end,
                region_code=requested_region,
                required_fields=(
                    "calendar_date",
                    "weekday",
                    "is_weekend",
                    "is_workday",
                    "event_group_id",
                    "year_relative_event_key",
                ),
            )
            if snapshot.event_source is not None
            else {}
        )
        merged_rows = self._assemble_rows(
            holiday_rows=holiday_rows,
            event_rows=event_rows,
            baseline_window=baseline_window,
            current_window=current_window,
        )
        return CalendarDataReadResult(
            annotation_rows=merged_rows,
            resolved_calendar_source=snapshot.resolved_calendar_source,
            resolved_calendar_version=snapshot.resolved_calendar_version,
            source_lineage=self._build_source_lineage(snapshot),
        )

    def _build_snapshot_binding(self, snapshot: CalendarSnapshotConfig) -> CalendarSnapshotBinding:
        resolved_source = snapshot.resolved_calendar_source.strip()
        resolved_version = snapshot.resolved_calendar_version.strip()
        if not resolved_source:
            raise CalendarDataResolutionError(
                "calendar snapshot requires resolved_calendar_source",
            )
        if not resolved_version or resolved_version.lower() in {"latest", "current"}:
            raise CalendarDataResolutionError(
                "calendar snapshot requires an immutable resolved_calendar_version",
                details={"resolved_calendar_version": snapshot.resolved_calendar_version},
            )
        return CalendarSnapshotBinding(
            resolved_calendar_source=resolved_source,
            resolved_calendar_version=resolved_version,
            region_code=snapshot.region_code.strip() or self.default_region_code,
            effective_start=_parse_date(snapshot.effective_start, field_name="effective_start"),
            effective_end=_parse_date(snapshot.effective_end, field_name="effective_end"),
            holiday_source=self._build_source_binding(snapshot.holiday_source),
            event_source=(
                self._build_source_binding(snapshot.event_source)
                if snapshot.event_source is not None
                else None
            ),
        )

    def _build_source_binding(self, source: CalendarSourceBindingConfig) -> CalendarSourceBinding:
        source_name = source.source_name.strip()
        table_fqn = source.table_fqn.strip()
        calendar_version = source.calendar_version.strip()
        if not source_name or not table_fqn:
            raise CalendarDataResolutionError(
                "calendar source binding requires source_name and table_fqn"
            )
        if not calendar_version or calendar_version.lower() in {"latest", "current"}:
            raise CalendarDataResolutionError(
                "calendar source binding requires an immutable calendar_version",
                details={
                    "source_name": source.source_name,
                    "table_fqn": source.table_fqn,
                    "calendar_version": source.calendar_version,
                },
            )
        source_row = self.metadata.query_one(
            "SELECT source_id FROM sources WHERE display_name = ?",
            [source_name],
        )
        if source_row is None:
            raise CalendarDataResolutionError(
                f"calendar source '{source_name}' is not registered",
                details={"source_name": source_name},
            )
        source_id = str(source_row["source_id"])
        object_row = self.metadata.query_one(
            """
            SELECT object_id
            FROM source_objects
            WHERE source_id = ? AND object_type = 'table' AND fqn = ?
            """,
            [source_id, table_fqn],
        )
        if object_row is None:
            raise CalendarDataResolutionError(
                f"calendar table '{table_fqn}' is not synced for source '{source_name}'",
                details={"source_name": source_name, "table_fqn": table_fqn},
            )
        return CalendarSourceBinding(
            source_id=source_id,
            source_name=source_name,
            table_fqn=table_fqn,
            calendar_version=calendar_version,
        )

    def _resolve_snapshot(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
        region_code: str,
    ) -> CalendarSnapshotBinding:
        if not self.snapshots:
            raise CalendarDataResolutionError("calendar snapshot registry is empty")
        required_start = min(current_window[0], baseline_window[0])
        required_end = max(current_window[1], baseline_window[1])
        matching = [
            snapshot
            for snapshot in self.snapshots
            if snapshot.region_code == region_code
            and snapshot.effective_start <= required_start
            and snapshot.effective_end >= required_end
        ]
        if not matching:
            raise CalendarDataResolutionError(
                "no published calendar snapshot covers the requested alignment window",
                details={
                    "region_code": region_code,
                    "required_start": required_start.isoformat(),
                    "required_end": required_end.isoformat(),
                },
            )
        if len(matching) > 1:
            raise CalendarDataResolutionError(
                "multiple calendar snapshots match the requested alignment window",
                details={
                    "region_code": region_code,
                    "matching_versions": [
                        snapshot.resolved_calendar_version for snapshot in matching
                    ],
                },
            )
        return matching[0]

    def _read_source_rows(
        self,
        *,
        binding: CalendarSourceBinding | None,
        read_start: date,
        read_end: date,
        region_code: str,
        required_fields: Sequence[str],
    ) -> dict[date, dict[str, Any]]:
        if binding is None:
            return {}
        route = self.query_router.resolve_tables([binding.table_fqn])
        qualified_table = route.qualified_names.get(binding.table_fqn, binding.table_fqn)
        safe_qualified_table = _validated_table_identifier(
            qualified_table,
            table_fqn=binding.table_fqn,
            source_name=binding.source_name,
        )
        runtime_engine = (
            route.require_engine() if hasattr(route, "require_engine") else route.engine
        )
        if runtime_engine is None:
            raise CalendarDataResolutionError(
                "calendar route did not provide a runtime engine",
                details={
                    "table_fqn": binding.table_fqn,
                    "source_name": binding.source_name,
                    "calendar_version": binding.calendar_version,
                },
            )
        rows = runtime_engine.query_rows(
            f"""
            SELECT *
            FROM {safe_qualified_table}
            WHERE calendar_date >= ?
              AND calendar_date < ?
              AND region_code = ?
              AND calendar_version = ?
            ORDER BY calendar_date
            """,
            [
                read_start.isoformat(),
                read_end.isoformat(),
                region_code,
                binding.calendar_version,
            ],
        )
        rows_by_date: dict[date, dict[str, Any]] = {}
        for raw_row in rows:
            for field_name in required_fields:
                if field_name not in raw_row:
                    raise CalendarDataResolutionError(
                        f"calendar data row is missing required field '{field_name}'",
                        details={
                            "table_fqn": binding.table_fqn,
                            "source_name": binding.source_name,
                            "calendar_version": binding.calendar_version,
                        },
                    )
            calendar_date = _parse_row_date(raw_row.get("calendar_date"))
            if calendar_date in rows_by_date:
                raise CalendarDataResolutionError(
                    "calendar snapshot contains duplicate calendar_date rows",
                    details={
                        "table_fqn": binding.table_fqn,
                        "source_name": binding.source_name,
                        "calendar_version": binding.calendar_version,
                        "calendar_date": calendar_date.isoformat(),
                    },
                )
            weekday = int(raw_row.get("weekday") or 0)
            if weekday < 1 or weekday > 7:
                raise CalendarDataResolutionError(
                    "calendar snapshot contains invalid weekday",
                    details={
                        "table_fqn": binding.table_fqn,
                        "calendar_date": calendar_date.isoformat(),
                        "weekday": raw_row.get("weekday"),
                    },
                )
            if raw_row.get("is_weekend") is None or raw_row.get("is_workday") is None:
                raise CalendarDataResolutionError(
                    "calendar snapshot must include is_weekend and is_workday",
                    details={
                        "table_fqn": binding.table_fqn,
                        "calendar_date": calendar_date.isoformat(),
                    },
                )
            rows_by_date[calendar_date] = dict(raw_row)
        return rows_by_date

    def _assemble_rows(
        self,
        *,
        holiday_rows: dict[date, dict[str, Any]],
        event_rows: dict[date, dict[str, Any]],
        baseline_window: tuple[date, date],
        current_window: tuple[date, date],
    ) -> list[CalendarAnnotationRow]:
        merged_rows: list[CalendarAnnotationRow] = []
        for cursor in _iter_window_dates(baseline_window, current_window):
            holiday_row = holiday_rows.get(cursor)
            event_row = event_rows.get(cursor)
            base_row = holiday_row or event_row
            if base_row is None:
                missing_sources: list[str] = []
                if not holiday_rows or cursor not in holiday_rows:
                    missing_sources.append("holiday_source")
                if event_rows and cursor not in event_rows:
                    missing_sources.append("event_source")
                raise CalendarDataResolutionError(
                    "calendar snapshot does not cover every requested day",
                    details={
                        "calendar_date": cursor.isoformat(),
                        "missing_sources": missing_sources,
                    },
                )
            if holiday_row is not None and event_row is not None:
                holiday_weekday = int(holiday_row.get("weekday") or 0)
                event_weekday = int(event_row.get("weekday") or 0)
                if holiday_weekday != event_weekday:
                    raise CalendarDataResolutionError(
                        "calendar sources disagree on weekday for the same date",
                        details={
                            "calendar_date": cursor.isoformat(),
                            "holiday_weekday": holiday_row.get("weekday"),
                            "event_weekday": event_row.get("weekday"),
                        },
                    )
            merged_rows.append(
                CalendarAnnotationRow(
                    calendar_date=cursor,
                    weekday=int(base_row.get("weekday") or 0),
                    holiday_group_id=_optional_str(base_row.get("holiday_group_id")),
                    year_relative_holiday_key=_optional_str(
                        base_row.get("year_relative_holiday_key")
                    ),
                    event_group_id=_optional_str(
                        event_row.get("event_group_id") if event_row is not None else None
                    ),
                    year_relative_event_key=_optional_str(
                        event_row.get("year_relative_event_key") if event_row is not None else None
                    ),
                )
            )
        return merged_rows

    @staticmethod
    def _serialize_source_binding(binding: CalendarSourceBinding | None) -> dict[str, str]:
        if binding is None:
            return {}
        return {
            "source_id": binding.source_id,
            "source_name": binding.source_name,
            "table_fqn": binding.table_fqn,
            "calendar_version": binding.calendar_version,
        }

    @classmethod
    def _build_source_lineage(cls, snapshot: CalendarSnapshotBinding) -> dict[str, dict[str, str]]:
        source_lineage = {
            "holiday_source": cls._serialize_source_binding(snapshot.holiday_source),
        }
        if snapshot.event_source is not None:
            source_lineage["event_source"] = cls._serialize_source_binding(snapshot.event_source)
        return source_lineage


def _parse_date(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CalendarDataResolutionError(
            f"calendar config field '{field_name}' must be an ISO date",
            details={field_name: value},
        ) from error


def _parse_row_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError as error:
        raise CalendarDataResolutionError(
            "calendar snapshot contains invalid calendar_date",
            details={"calendar_date": value},
        ) from error


_SAFE_TABLE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")


def _validated_table_identifier(
    identifier: str,
    *,
    table_fqn: str,
    source_name: str,
) -> str:
    normalized = identifier.strip()
    if _SAFE_TABLE_IDENTIFIER_RE.fullmatch(normalized):
        return normalized
    raise CalendarDataResolutionError(
        "calendar source resolved to an unsafe table identifier",
        details={
            "source_name": source_name,
            "table_fqn": table_fqn,
            "qualified_table": identifier,
        },
    )


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
