from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.analysis_core.calendar_policy import CalendarMatchingStep


@dataclass(frozen=True, slots=True)
class CalendarAnnotationRow:
    calendar_date: date
    weekday: int
    holiday_group_id: str | None = None
    year_relative_holiday_key: str | None = None
    event_group_id: str | None = None
    year_relative_event_key: str | None = None


@dataclass(frozen=True, slots=True)
class CalendarPairingResolution:
    bucket_pairing: list[dict[str, Any]]
    comparability_warnings: list[str]


_WARNING_ISSUE_CODES = frozenset(
    {
        "holiday_cluster_unmapped",
        "event_cluster_unmapped",
        "fallback_applied",
    }
)


def resolve_calendar_bucket_pairing(
    *,
    current_window: tuple[date, date],
    baseline_window: tuple[date, date],
    matching_strategy: tuple[CalendarMatchingStep, ...],
    fallback_strategy: tuple[str, ...],
    annotation_rows: Sequence[CalendarAnnotationRow],
) -> CalendarPairingResolution:
    resolver = _CalendarPairingResolver(
        current_window=current_window,
        baseline_window=baseline_window,
        matching_strategy=matching_strategy,
        fallback_strategy=fallback_strategy,
        annotation_rows=annotation_rows,
    )
    return resolver.resolve()


def build_calendar_annotation_rows(
    *,
    current_window: tuple[date, date],
    baseline_window: tuple[date, date],
    raw_rows: Sequence[Mapping[str, Any]] | None,
) -> list[CalendarAnnotationRow]:
    rows_by_date: dict[date, CalendarAnnotationRow] = {}
    for raw_row in raw_rows or ():
        row = _coerce_annotation_row(raw_row)
        rows_by_date[row.calendar_date] = row

    all_rows: list[CalendarAnnotationRow] = []
    for window in (baseline_window, current_window):
        cursor = window[0]
        while cursor < window[1]:
            all_rows.append(
                rows_by_date.get(
                    cursor,
                    CalendarAnnotationRow(
                        calendar_date=cursor,
                        weekday=cursor.weekday() + 1,
                    ),
                )
            )
            cursor += timedelta(days=1)
    return all_rows


def _coerce_annotation_row(raw_row: Mapping[str, Any]) -> CalendarAnnotationRow:
    raw_date = raw_row.get("calendar_date")
    if isinstance(raw_date, date):
        calendar_date = raw_date
    else:
        calendar_date = date.fromisoformat(str(raw_date or "")[:10])
    weekday = int(raw_row.get("weekday") or 0)
    if weekday < 1 or weekday > 7:
        raise ValueError("calendar annotation weekday must be in 1..7")
    return CalendarAnnotationRow(
        calendar_date=calendar_date,
        weekday=weekday,
        holiday_group_id=_optional_str(raw_row.get("holiday_group_id")),
        year_relative_holiday_key=_optional_str(raw_row.get("year_relative_holiday_key")),
        event_group_id=_optional_str(raw_row.get("event_group_id")),
        year_relative_event_key=_optional_str(raw_row.get("year_relative_event_key")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class _CalendarPairingResolver:
    def __init__(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
        matching_strategy: tuple[CalendarMatchingStep, ...],
        fallback_strategy: tuple[str, ...],
        annotation_rows: Sequence[CalendarAnnotationRow],
    ) -> None:
        self.current_window = current_window
        self.baseline_window = baseline_window
        self.matching_strategy = matching_strategy
        self.fallback_strategy = fallback_strategy
        self.rows_by_date = {row.calendar_date: row for row in annotation_rows}
        self.baseline_rows = self._window_rows(baseline_window)
        (
            self.baseline_by_holiday_group,
            self.duplicate_holiday_groups,
        ) = self._index_unique(
            self.baseline_rows,
            key_fn=lambda row: row.holiday_group_id,
        )
        self.baseline_by_holiday_key, _ = self._index_unique(
            self.baseline_rows,
            key_fn=lambda row: row.year_relative_holiday_key,
        )
        self.baseline_by_event_group, self.duplicate_event_groups = self._index_unique(
            self.baseline_rows,
            key_fn=lambda row: row.event_group_id,
        )
        self.baseline_by_event_key, _ = self._index_unique(
            self.baseline_rows,
            key_fn=lambda row: row.year_relative_event_key,
        )

    def resolve(self) -> CalendarPairingResolution:
        pairings: list[dict[str, Any]] = []
        comparability_warnings: list[str] = []
        cursor = self.current_window[0]
        index = 0
        while cursor < self.current_window[1]:
            pairing = self._resolve_one_bucket(current_day=cursor, offset=index)
            pairings.append(pairing)
            for issue_code in pairing["issues"]:
                if issue_code in _WARNING_ISSUE_CODES and issue_code not in comparability_warnings:
                    comparability_warnings.append(issue_code)
            cursor += timedelta(days=1)
            index += 1
        return CalendarPairingResolution(
            bucket_pairing=pairings,
            comparability_warnings=comparability_warnings,
        )

    def _resolve_one_bucket(self, *, current_day: date, offset: int) -> dict[str, Any]:
        current_row = self.rows_by_date.get(
            current_day,
            CalendarAnnotationRow(calendar_date=current_day, weekday=current_day.weekday() + 1),
        )
        issues: list[str] = []
        fallback_started = False
        baseline_day: date | None = None
        pairing_reason: str | None = None

        for step_index, step in enumerate(self.matching_strategy):
            baseline_day, step_issues = self._apply_matcher(
                matcher=step.matcher,
                current_row=current_row,
                offset=offset,
            )
            for issue in step_issues:
                if issue not in issues:
                    issues.append(issue)
            if baseline_day is None:
                continue
            if self._is_fallback_match(step_index) and not fallback_started:
                fallback_started = True
                if "fallback_applied" not in issues:
                    issues.append("fallback_applied")
            pairing_reason = step.matcher
            break

        if baseline_day is None:
            issues.append("alignment_coverage_insufficient")

        return {
            "current_bucket_start": current_day.isoformat(),
            "baseline_bucket_start": baseline_day.isoformat() if baseline_day is not None else None,
            "pairing_reason": pairing_reason,
            "shift_days": (current_day - baseline_day).days if baseline_day is not None else None,
            "issues": issues,
        }

    def _apply_matcher(
        self,
        *,
        matcher: str,
        current_row: CalendarAnnotationRow,
        offset: int,
    ) -> tuple[date | None, list[str]]:
        if matcher == "holiday_cluster":
            if current_row.holiday_group_id is None:
                return None, []
            candidate = self.baseline_by_holiday_group.get(current_row.holiday_group_id)
            if candidate is not None:
                return candidate, []
            if current_row.holiday_group_id in self.duplicate_holiday_groups:
                return None, []
            return None, ["holiday_cluster_unmapped"]
        if matcher == "year_relative_holiday_key":
            if current_row.holiday_group_id is None:
                return None, []
            if current_row.year_relative_holiday_key is None:
                return None, ["holiday_cluster_unmapped"]
            candidate = self.baseline_by_holiday_key.get(current_row.year_relative_holiday_key)
            return candidate, [] if candidate is not None else ["holiday_cluster_unmapped"]
        if matcher == "event_cluster":
            if current_row.event_group_id is None:
                return None, []
            candidate = self.baseline_by_event_group.get(current_row.event_group_id)
            if candidate is not None:
                return candidate, []
            if current_row.event_group_id in self.duplicate_event_groups:
                return None, []
            return None, ["event_cluster_unmapped"]
        if matcher == "year_relative_event_key":
            if current_row.event_group_id is None:
                return None, []
            if current_row.year_relative_event_key is None:
                return None, ["event_cluster_unmapped"]
            candidate = self.baseline_by_event_key.get(current_row.year_relative_event_key)
            return candidate, [] if candidate is not None else ["event_cluster_unmapped"]
        if matcher == "same_weekday_nearest":
            target_day = self.baseline_window[0] + timedelta(days=offset)
            candidate = _nearest_same_weekday(
                target_day=target_day,
                baseline_window=self.baseline_window,
                weekday=current_row.weekday,
            )
            return candidate, []
        if matcher == "natural_date_shift":
            baseline_day = self.baseline_window[0] + timedelta(days=offset)
            if baseline_day >= self.baseline_window[1]:
                return None, []
            return baseline_day, []
        raise ValueError(f"Unsupported calendar matcher '{matcher}'")

    def _is_fallback_match(self, step_index: int) -> bool:
        if step_index == 0:
            return False
        prior_matchers = self.matching_strategy[:step_index]
        current_matcher = self.matching_strategy[step_index].matcher
        return any(step.requires_annotation for step in prior_matchers) and (
            current_matcher in self.fallback_strategy
            or not self.matching_strategy[step_index].requires_annotation
        )

    def _window_rows(self, window: tuple[date, date]) -> list[CalendarAnnotationRow]:
        rows: list[CalendarAnnotationRow] = []
        cursor = window[0]
        while cursor < window[1]:
            rows.append(
                self.rows_by_date.get(
                    cursor,
                    CalendarAnnotationRow(calendar_date=cursor, weekday=cursor.weekday() + 1),
                )
            )
            cursor += timedelta(days=1)
        return rows

    @staticmethod
    def _index_unique(
        rows: Sequence[CalendarAnnotationRow],
        *,
        key_fn: Any,
    ) -> tuple[dict[str, date], set[str]]:
        indexed: dict[str, date] = {}
        duplicates: set[str] = set()
        for row in rows:
            key = key_fn(row)
            if key is None:
                continue
            if key in indexed:
                duplicates.add(key)
                continue
            indexed[key] = row.calendar_date
        for duplicate in duplicates:
            indexed.pop(duplicate, None)
        return indexed, duplicates


def _nearest_same_weekday(
    *,
    target_day: date,
    baseline_window: tuple[date, date],
    weekday: int,
) -> date | None:
    candidates: list[date] = []
    cursor = baseline_window[0]
    while cursor < baseline_window[1]:
        if cursor.weekday() + 1 == weekday:
            candidates.append(cursor)
        cursor += timedelta(days=1)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: (
            abs((candidate - target_day).days),
            candidate > target_day,
            candidate,
        ),
    )
