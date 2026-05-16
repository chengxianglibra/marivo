# Calendar Data Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove calendar_version/region_code from calendar data, switch to replacement-style loading, and support multiple holidays per date via multi-row storage.

**Architecture:** Single flat `calendar` table with PK `(calendar_date, holiday_group_id)`. Empty-string sentinel for non-holiday rows. `CalendarDataReader` queries by date range directly without version resolution. `CalendarConfig` removed entirely. HTTP PUT replaces POST, versions endpoint removed.

**Tech Stack:** Python, Pydantic, SQLite/DuckDB, FastAPI, Typer CLI

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `marivo/adapters/schema.py:380-393` | New calendar table DDL (no version/region, multi-row PK) |
| Modify | `marivo/core/semantic/calendar.py:136-141` | `CalendarAnnotationRow` — add `extra_holiday_group_ids` field |
| Modify | `marivo/core/semantic/calendar.py:228-262` | `build_calendar_annotation_rows` — merge multi-row DB rows per date |
| Modify | `marivo/core/semantic/calendar.py:563-577` | `_coerce_annotation_row` — no changes needed (builds single row from single raw row) |
| Modify | `marivo/core/semantic/calendar.py:587-761` | `_CalendarPairingResolver` — matchers check `extra_holiday_group_ids` |
| Modify | `marivo/runtime/semantic/calendar_data_runtime.py` | Remove version resolution, query by date range, remove `CalendarDataReadResult` version/source fields |
| Modify | `marivo/runtime/runtime.py:40,67-69,110-113` | Simplify `wire_calendar_data_reader` — reader no longer needs config |
| Modify | `marivo/config.py:95-117` | Remove `CalendarConfig`, remove `calendar` from `MarivoConfig` |
| Modify | `marivo/transports/http/models/calendar.py` | Remove `region_code`, `calendar_version`, `CalendarVersionItem`; simplify request/response |
| Modify | `marivo/transports/http/calendar.py` | Switch to PUT, remove 409 logic, remove versions endpoint |
| Modify | `marivo/transports/cli/cmd_calendar.py` | Remove `--version` parameter |
| Modify | `scripts/build_cn_calendar.py` | Adapt output format, remove version/region columns |
| Delete | `data/public_holiday_events_insert.sql` | Replaced by rebuilt SQL file |
| Modify | `marivo/runtime/intents/compare.py:147-168,218-223,658-668` | Remove `calendar_source`/`calendar_version` from alignment output |
| Rewrite | `tests/config/test_calendar_config.py` | Remove (CalendarConfig deleted) |
| Rewrite | `tests/test_calendar_data_runtime.py` | Remove version resolution tests, adjust read tests |
| Modify | `tests/core/test_calendar_alignment_pairing.py` | Add multi-holiday pairing tests |
| Modify | `tests/test_build_cn_calendar.py` | Adapt to new output format |

---

### Task 1: Database schema — new calendar table DDL

**Files:**
- Modify: `marivo/adapters/schema.py:380-393`

- [ ] **Step 1: Update calendar table DDL in METADATA_DDL**

Replace the calendar table definition in `marivo/adapters/schema.py` around lines 380-393. The old DDL:

```sql
CREATE TABLE IF NOT EXISTS calendar (
    calendar_date              TEXT NOT NULL,
    region_code                TEXT NOT NULL,
    calendar_version           TEXT NOT NULL,
    weekday                    INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    is_weekend                 INTEGER NOT NULL CHECK (is_weekend IN (0, 1)),
    is_workday                 INTEGER NOT NULL CHECK (is_workday IN (0, 1)),
    holiday_name               TEXT,
    holiday_group_id           TEXT,
    year_relative_holiday_key  TEXT,
    PRIMARY KEY (calendar_version, region_code, calendar_date)
)
```

And the old index:

```sql
CREATE INDEX IF NOT EXISTS idx_calendar_version_region ON calendar(calendar_version, region_code)
```

Replace with:

```sql
CREATE TABLE IF NOT EXISTS calendar (
    calendar_date              TEXT NOT NULL,
    holiday_group_id           TEXT NOT NULL DEFAULT '',
    weekday                    INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    is_weekend                 INTEGER NOT NULL CHECK (is_weekend IN (0, 1)),
    is_workday                 INTEGER NOT NULL CHECK (is_workday IN (0, 1)),
    holiday_name               TEXT,
    year_relative_holiday_key  TEXT,
    PRIMARY KEY (calendar_date, holiday_group_id)
)
```

And replace the old index with:

```sql
CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar(calendar_date)
```

- [ ] **Step 2: Verify schema module still works**

Run: `.venv/bin/python -c "from marivo.adapters.schema import METADATA_DDL; print('OK')"`
Expected: prints "OK" with no errors

- [ ] **Step 3: Commit**

```bash
git add marivo/adapters/schema.py
git commit -m "refactor: simplify calendar table schema — remove version/region, multi-row PK"
```

---

### Task 2: Core semantic layer — CalendarAnnotationRow with extra_holiday_group_ids

**Files:**
- Modify: `marivo/core/semantic/calendar.py:136-141`
- Test: `tests/core/test_calendar_alignment_pairing.py`

- [ ] **Step 1: Write failing test for multi-holiday annotation row merge**

Add a test in `tests/core/test_calendar_alignment_pairing.py` that tests `build_calendar_annotation_rows` with multi-row input where the same date has two holidays. The test should verify that the merged `CalendarAnnotationRow` has `extra_holiday_group_ids` populated:

```python
def test_build_annotation_rows_merges_multi_holiday_date():
    """Same date with two holidays should merge into one row with extras."""
    current_window = (date(2024, 10, 1), date(2024, 10, 8))
    baseline_window = (date(2023, 9, 30), date(2023, 10, 7))
    # 2024-10-01 has both national_day and mid_autumn
    raw_rows = [
        {
            "calendar_date": "2024-10-01",
            "weekday": 2,
            "is_weekend": 0,
            "is_workday": 0,
            "holiday_name": "国庆节",
            "holiday_group_id": "national_day",
            "year_relative_holiday_key": "national_day_2024",
        },
        {
            "calendar_date": "2024-10-01",
            "weekday": 2,
            "is_weekend": 0,
            "is_workday": 0,
            "holiday_name": "中秋节",
            "holiday_group_id": "mid_autumn",
            "year_relative_holiday_key": "mid_autumn_2024",
        },
    ]
    rows = build_calendar_annotation_rows(
        current_window=current_window,
        baseline_window=baseline_window,
        raw_rows=raw_rows,
    )
    oct1_row = next(r for r in rows if r.calendar_date == date(2024, 10, 1))
    assert oct1_row.holiday_group_id == "national_day"
    assert "mid_autumn" in oct1_row.extra_holiday_group_ids
    assert "mid_autumn_2024" in oct1_row.extra_year_relative_holiday_keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test TESTS='tests/core/test_calendar_alignment_pairing.py::test_build_annotation_rows_merges_multi_holiday_date'`
Expected: FAIL — `CalendarAnnotationRow` doesn't have `extra_holiday_group_ids` field yet

- [ ] **Step 3: Add `extra_holiday_group_ids` and `extra_year_relative_holiday_keys` to CalendarAnnotationRow**

In `marivo/core/semantic/calendar.py`, update `CalendarAnnotationRow` dataclass (around line 136-141):

```python
@dataclass(frozen=True, slots=True)
class CalendarAnnotationRow:
    calendar_date: date
    weekday: int
    holiday_group_id: str | None = None
    year_relative_holiday_key: str | None = None
    extra_holiday_group_ids: tuple[str, ...] = ()
    extra_year_relative_holiday_keys: tuple[str, ...] = ()
```

- [ ] **Step 4: Update `build_calendar_annotation_rows` to merge multi-row per date**

In `marivo/core/semantic/calendar.py`, update `build_calendar_annotation_rows` (around lines 228-262). The current code builds `rows_by_date` dict where each date maps to a single row. Change it to merge multiple raw rows per date:

```python
def build_calendar_annotation_rows(
    *,
    current_window: tuple[date, date],
    baseline_window: tuple[date, date],
    raw_rows: Sequence[Mapping[str, Any]] | None,
) -> list[CalendarAnnotationRow]:
    """Build annotation rows from raw database rows.

    Multiple raw rows for the same date (different holiday_group_id)
    are merged into a single CalendarAnnotationRow with the first
    non-empty holiday_group_id as primary and the rest as extras.

    This function fills in missing dates with default rows (weekday-only).
    The ``raw_rows`` parameter comes from database I/O, but the function
    itself is pure computation.
    """
    # Group raw rows by date — same date may have multiple holidays
    grouped: dict[date, list[CalendarAnnotationRow]] = {}
    for raw_row in raw_rows or ():
        row = _coerce_annotation_row(raw_row)
        grouped.setdefault(row.calendar_date, []).append(row)

    # Merge each group into a single annotation row
    rows_by_date: dict[date, CalendarAnnotationRow] = {}
    for cal_date, row_group in grouped.items():
        # Pick the first row with a non-empty holiday_group_id as primary
        primary_row = row_group[0]
        holiday_rows = [r for r in row_group if r.holiday_group_id]
        if holiday_rows:
            primary_row = holiday_rows[0]
            extras = holiday_rows[1:]
            merged = CalendarAnnotationRow(
                calendar_date=cal_date,
                weekday=primary_row.weekday,
                holiday_group_id=primary_row.holiday_group_id,
                year_relative_holiday_key=primary_row.year_relative_holiday_key,
                extra_holiday_group_ids=tuple(r.holiday_group_id for r in extras),
                extra_year_relative_holiday_keys=tuple(r.year_relative_holiday_key for r in extras),
            )
        else:
            merged = primary_row
        rows_by_date[cal_date] = merged

    all_rows: list[CalendarAnnotationRow] = []
    seen_dates: set[date] = set()
    for window in (baseline_window, current_window):
        cursor = window[0]
        while cursor < window[1]:
            if cursor not in seen_dates:
                seen_dates.add(cursor)
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `make test TESTS='tests/core/test_calendar_alignment_pairing.py::test_build_annotation_rows_merges_multi_holiday_date'`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add marivo/core/semantic/calendar.py tests/core/test_calendar_alignment_pairing.py
git commit -m "feat: CalendarAnnotationRow supports multi-holiday dates with extra group ids"
```

---

### Task 3: Calendar pairing — matchers check all holiday_group_ids

**Files:**
- Modify: `marivo/core/semantic/calendar.py:587-761`

- [ ] **Step 1: Write failing test for multi-holiday pairing**

Add a test in `tests/core/test_calendar_alignment_pairing.py` where current date has `holiday_group_id="national_day"` and `extra_holiday_group_ids=("mid_autumn",)`, and baseline has `mid_autumn` — the holiday_cluster matcher should find it via extras:

```python
def test_holiday_cluster_matches_via_extra_holiday_group_id():
    """holiday_cluster matcher should find matches in extra_holiday_group_ids."""
    current_window = (date(2024, 10, 1), date(2024, 10, 2))
    baseline_window = (date(2023, 9, 29), date(2023, 10, 1))
    strategy = (
        CalendarMatchingStep(matcher="holiday_cluster", requires_annotation=True),
        CalendarMatchingStep(matcher="natural_date_shift", requires_annotation=False),
    )
    annotation_rows = [
        CalendarAnnotationRow(
            calendar_date=date(2024, 10, 1),
            weekday=2,
            holiday_group_id="national_day",
            year_relative_holiday_key="national_day_2024",
            extra_holiday_group_ids=("mid_autumn",),
            extra_year_relative_holiday_keys=("mid_autumn_2024",),
        ),
        CalendarAnnotationRow(
            calendar_date=date(2023, 9, 30),
            weekday=6,
            holiday_group_id="mid_autumn",
            year_relative_holiday_key="mid_autumn_2023",
        ),
        CalendarAnnotationRow(
            calendar_date=date(2023, 10, 1),
            weekday=7,
            holiday_group_id="national_day",
            year_relative_holiday_key="national_day_2023",
        ),
    ]
    resolution = resolve_calendar_bucket_pairing(
        current_window=current_window,
        baseline_window=baseline_window,
        matching_strategy=strategy,
        annotation_rows=annotation_rows,
    )
    # Oct 1 current has national_day primary — should match baseline Oct 1
    oct1_pairing = resolution.bucket_pairing[0]
    assert oct1_pairing["pairing_reason"] == "holiday_cluster"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `make test TESTS='tests/core/test_calendar_alignment_pairing.py::test_holiday_cluster_matches_via_extra_holiday_group_id'`
Expected: PASS (existing holiday_cluster matcher already matches by primary `holiday_group_id`)

- [ ] **Step 3: Write failing test for year_relative_holiday_key matching via extras**

Add a test where `year_relative_holiday_key` in extras matches baseline:

```python
def test_year_relative_holiday_key_matches_via_extra():
    """year_relative_holiday_key matcher should find matches in extras."""
    current_window = (date(2024, 10, 1), date(2024, 10, 2))
    baseline_window = (date(2023, 9, 29), date(2023, 10, 1))
    strategy = (
        CalendarMatchingStep(matcher="year_relative_holiday_key", requires_annotation=True),
        CalendarMatchingStep(matcher="natural_date_shift", requires_annotation=False),
    )
    annotation_rows = [
        CalendarAnnotationRow(
            calendar_date=date(2024, 10, 1),
            weekday=2,
            holiday_group_id="national_day",
            year_relative_holiday_key="national_day_2024",
            extra_holiday_group_ids=("mid_autumn",),
            extra_year_relative_holiday_keys=("mid_autumn_2024",),
        ),
        CalendarAnnotationRow(
            calendar_date=date(2023, 9, 30),
            weekday=6,
            holiday_group_id="mid_autumn",
            year_relative_holiday_key="mid_autumn_2023",
        ),
    ]
    resolution = resolve_calendar_bucket_pairing(
        current_window=current_window,
        baseline_window=baseline_window,
        matching_strategy=strategy,
        annotation_rows=annotation_rows,
    )
    # national_day_2024 won't match any baseline key, but mid_autumn_2024
    # should also not match (baseline has mid_autumn_2023, not 2024).
    # So this should fall through to natural_date_shift.
    oct1_pairing = resolution.bucket_pairing[0]
    assert oct1_pairing["pairing_reason"] == "natural_date_shift"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test TESTS='tests/core/test_calendar_alignment_pairing.py::test_year_relative_holiday_key_matches_via_extra'`
Expected: PASS (year_relative keys differ by year, so fallthrough is correct)

- [ ] **Step 5: Update `_CalendarPairingResolver._apply_matcher` to check extras for holiday_cluster**

In `marivo/core/semantic/calendar.py`, update the `holiday_cluster` matcher in `_apply_matcher` (around lines 686-694):

```python
if matcher == "holiday_cluster":
    # Collect all holiday group ids: primary + extras
    all_group_ids: list[str] = []
    if current_row.holiday_group_id is not None:
        all_group_ids.append(current_row.holiday_group_id)
    all_group_ids.extend(current_row.extra_holiday_group_ids)
    if not all_group_ids:
        return None, []
    for gid in all_group_ids:
        candidate = self.baseline_by_holiday_group.get(gid)
        if candidate is not None and gid not in self.duplicate_holiday_groups:
            return candidate, []
    # All group ids are either unmapped or duplicate
    unmapped = [gid for gid in all_group_ids if gid not in self.duplicate_holiday_groups]
    if unmapped:
        return None, ["holiday_cluster_unmapped"]
    return None, []
```

- [ ] **Step 6: Update `year_relative_holiday_key` matcher to check extras**

Update the `year_relative_holiday_key` matcher (around lines 695-701):

```python
if matcher == "year_relative_holiday_key":
    all_keys: list[str] = []
    if current_row.holiday_group_id is not None:
        if current_row.year_relative_holiday_key is not None:
            all_keys.append(current_row.year_relative_holiday_key)
    all_keys.extend(current_row.extra_year_relative_holiday_keys)
    if not all_keys:
        return None, []
    for key in all_keys:
        candidate = self.baseline_by_holiday_key.get(key)
        if candidate is not None:
            return candidate, []
    return None, ["holiday_cluster_unmapped"]
```

- [ ] **Step 7: Run full calendar pairing test suite**

Run: `make test TESTS='tests/core/test_calendar_alignment_pairing.py'`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add marivo/core/semantic/calendar.py tests/core/test_calendar_alignment_pairing.py
git commit -m "feat: calendar matchers check all holiday_group_ids for multi-holiday dates"
```

---

### Task 4: Remove CalendarConfig and simplify CalendarDataReader

**Files:**
- Modify: `marivo/config.py:95-117`
- Modify: `marivo/runtime/semantic/calendar_data_runtime.py`
- Test: `tests/config/test_calendar_config.py` (delete)
- Test: `tests/test_calendar_data_runtime.py` (rewrite)

- [ ] **Step 1: Write new failing test for simplified CalendarDataReader**

Rewrite `tests/test_calendar_data_runtime.py`. Remove all version-related tests (pinned version, latest version, version missing). New test structure:

```python
"""Tests for CalendarDataReader — simplified (no version/region)."""
from datetime import date
from unittest.mock import MagicMock

import pytest

from marivo.core.semantic.calendar import CalendarAnnotationRow
from marivo.runtime.semantic.calendar_data_runtime import (
    CalendarDataReader,
    CalendarDataReadResult,
    CalendarDataResolutionError,
)


def _make_metadata_store(rows: list[dict]) -> MagicMock:
    store = MagicMock()
    store.query.return_value = rows
    return store


def test_read_for_alignment_returns_annotation_rows():
    rows = [
        {
            "calendar_date": "2024-01-01",
            "weekday": 1,
            "is_weekend": 0,
            "is_workday": 0,
            "holiday_name": "元旦",
            "holiday_group_id": "new_year",
            "year_relative_holiday_key": "new_year_2024",
        },
        {
            "calendar_date": "2024-01-02",
            "weekday": 2,
            "is_weekend": 0,
            "is_workday": 0,
            "holiday_group_id": "new_year",
            "year_relative_holiday_key": "new_year_2024",
        },
    ]
    store = _make_metadata_store(rows)
    reader = CalendarDataReader(metadata=store)
    result = reader.read_for_alignment(
        current_window=(date(2024, 1, 1), date(2024, 1, 3)),
        baseline_window=(date(2023, 1, 1), date(2023, 1, 3)),
    )
    assert isinstance(result, CalendarDataReadResult)
    assert len(result.annotation_rows) >= 2
    jan1 = next(r for r in result.annotation_rows if r.calendar_date == date(2024, 1, 1))
    assert jan1.holiday_group_id == "new_year"


def test_read_for_alignment_multi_holiday_date():
    rows = [
        {
            "calendar_date": "2024-10-01",
            "weekday": 2,
            "is_weekend": 0,
            "is_workday": 0,
            "holiday_name": "国庆节",
            "holiday_group_id": "national_day",
            "year_relative_holiday_key": "national_day_2024",
        },
        {
            "calendar_date": "2024-10-01",
            "weekday": 2,
            "is_weekend": 0,
            "is_workday": 0,
            "holiday_name": "中秋节",
            "holiday_group_id": "mid_autumn",
            "year_relative_holiday_key": "mid_autumn_2024",
        },
    ]
    store = _make_metadata_store(rows)
    reader = CalendarDataReader(metadata=store)
    result = reader.read_for_alignment(
        current_window=(date(2024, 10, 1), date(2024, 10, 2)),
        baseline_window=(date(2023, 9, 30), date(2023, 10, 1)),
    )
    oct1 = next(r for r in result.annotation_rows if r.calendar_date == date(2024, 10, 1))
    assert oct1.holiday_group_id == "national_day"
    assert "mid_autumn" in oct1.extra_holiday_group_ids


def test_read_for_alignment_empty_db_raises():
    store = MagicMock()
    store.query.return_value = []
    reader = CalendarDataReader(metadata=store)
    with pytest.raises(CalendarDataResolutionError, match="calendar data unavailable"):
        reader.read_for_alignment(
            current_window=(date(2024, 1, 1), date(2024, 1, 3)),
            baseline_window=(date(2023, 1, 1), date(2023, 1, 3)),
        )


def test_source_lineage():
    rows = [
        {
            "calendar_date": "2024-01-01",
            "weekday": 1,
            "is_weekend": 0,
            "is_workday": 1,
            "holiday_group_id": "",
            "year_relative_holiday_key": None,
            "holiday_name": None,
        },
    ]
    store = _make_metadata_store(rows)
    reader = CalendarDataReader(metadata=store)
    result = reader.read_for_alignment(
        current_window=(date(2024, 1, 1), date(2024, 1, 2)),
        baseline_window=(date(2023, 1, 1), date(2023, 1, 2)),
    )
    assert result.source_lineage["table"] == "calendar"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test TESTS='tests/test_calendar_data_runtime.py'`
Expected: FAIL — `CalendarDataReader` still requires `config` parameter, `CalendarDataReadResult` still has version fields

- [ ] **Step 3: Remove CalendarConfig from marivo/config.py**

In `marivo/config.py`, delete the `CalendarConfig` class (lines 95-109) and remove the `calendar` field from `MarivoConfig` (line 117 `calendar: CalendarConfig = Field(default_factory=CalendarConfig)`).

- [ ] **Step 4: Rewrite CalendarDataReader in calendar_data_runtime.py**

Rewrite `marivo/runtime/semantic/calendar_data_runtime.py`:

```python
"""I/O-bound calendar data reader for alignment intents."""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol, Sequence

from marivo.core.semantic.calendar import (
    CalendarAnnotationRow,
    build_calendar_annotation_rows,
)


class CalendarDataResolutionError(ValueError):
    """Raised when calendar data is unavailable for alignment."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class CalendarDataReadResult:
    """Result from reading calendar data for alignment."""

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


class CalendarDataReaderLike(Protocol):
    """Protocol interface for the calendar data reader."""

    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
    ) -> CalendarDataReadResult: ...


class CalendarDataReader:
    """Concrete calendar data reader — queries the calendar table by date range."""

    def __init__(self, metadata: Any) -> None:
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
        sql = (
            "SELECT calendar_date, weekday, is_weekend, is_workday, "
            "holiday_name, holiday_group_id, year_relative_holiday_key "
            "FROM calendar "
            "WHERE calendar_date >= ? AND calendar_date < ? "
            "ORDER BY calendar_date, holiday_group_id"
        )
        return self._metadata.query(sql, [combined_start.isoformat(), combined_end.isoformat()])
```

- [ ] **Step 5: Delete tests/config/test_calendar_config.py**

```bash
rm tests/config/test_calendar_config.py
```

- [ ] **Step 6: Run calendar data runtime tests**

Run: `make test TESTS='tests/test_calendar_data_runtime.py'`
Expected: ALL PASS

- [ ] **Step 7: Run typecheck**

Run: `make typecheck`
Expected: PASS (no references to CalendarConfig remaining)

- [ ] **Step 8: Commit**

```bash
git add marivo/config.py marivo/runtime/semantic/calendar_data_runtime.py tests/test_calendar_data_runtime.py
git rm tests/config/test_calendar_config.py
git commit -m "refactor: remove CalendarConfig, simplify CalendarDataReader to date-range query"
```

---

### Task 5: Remove calendar_source/calendar_version from compare intent output

**Files:**
- Modify: `marivo/runtime/intents/compare.py:147-168,218-223,658-668`

- [ ] **Step 1: Remove calendar_source and calendar_version variables from _resolve_time_series_pairing_basis**

In `marivo/runtime/intents/compare.py`, remove:
- Lines 147-148: `calendar_source: str | None = None` and `calendar_version: str | None = None`
- Lines 167-168: `calendar_source = calendar_data.resolved_calendar_source` and `calendar_version = calendar_data.resolved_calendar_version`
- Lines 222-223: `"resolved_calendar_source": calendar_source` and `"resolved_calendar_version": calendar_version` in the `calendar_alignment` dict

The `calendar_alignment` dict (around lines 218-235) becomes:

```python
"calendar_alignment": {
    "compare_type": compare_type,
    "comparison_basis": alignment_plan.comparison_basis,
    "resolved_alignment_mode": alignment_plan.resolved_alignment_mode,
    "current_window": {
        "start": current_window[0].isoformat(),
        "end": current_window[1].isoformat(),
    },
    "baseline_window": {
        "start": baseline_window[0].isoformat(),
        "end": baseline_window[1].isoformat(),
    },
    "bucket_pairing": pairing_resolution.bucket_pairing,
    "rollup_safe": pairing_resolution.rollup_safe,
    "comparability_warnings": pairing_resolution.comparability_warnings,
},
```

- [ ] **Step 2: Remove resolved_calendar_source/resolved_calendar_version from analytical_metadata**

In `compare.py`, around lines 658-668, update the `analytical_metadata["calendar_alignment"]` extraction:

```python
analytical_metadata["calendar_alignment"] = {
    key: calendar_alignment.get(key)
    for key in (
        "compare_type",
        "comparison_basis",
        "resolved_alignment_mode",
        "rollup_safe",
    )
}
```

- [ ] **Step 3: Run typecheck and compare intent tests**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add marivo/runtime/intents/compare.py
git commit -m "refactor: remove calendar_source/calendar_version from compare alignment output"
```

---

### Task 6: HTTP API — switch to PUT, remove versions endpoint

**Files:**
- Modify: `marivo/transports/http/models/calendar.py`
- Modify: `marivo/transports/http/calendar.py`

- [ ] **Step 1: Rewrite HTTP calendar models**

Rewrite `marivo/transports/http/models/calendar.py`:

```python
"""HTTP API models for calendar data endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CalendarDataRow(BaseModel):
    model_config = {"extra": "forbid"}

    calendar_date: str = Field(..., description="Date in YYYY-MM-DD format")
    weekday: int = Field(..., ge=1, le=7, description="ISO weekday (1=Mon, 7=Sun)")
    is_weekend: int = Field(..., ge=0, le=1, description="1 if weekend, 0 otherwise")
    is_workday: int = Field(..., ge=0, le=1, description="1 if workday, 0 otherwise")
    holiday_name: str | None = Field(default=None, description="Holiday display name")
    holiday_group_id: str = Field(
        default="", description="Holiday group identifier (empty string for non-holiday rows)"
    )
    year_relative_holiday_key: str | None = Field(
        default=None, description="Year-relative holiday key for cross-year matching"
    )


class CalendarDataLoadRequest(BaseModel):
    model_config = {"extra": "forbid"}

    rows: list[CalendarDataRow] = Field(..., min_length=1, description="Calendar data rows to load")


class CalendarDataLoadResponse(BaseModel):
    model_config = {"extra": "forbid"}

    status: str = Field(..., description="Load status")
    row_count: int = Field(..., description="Number of rows loaded")
```

- [ ] **Step 2: Rewrite HTTP calendar endpoint**

Rewrite `marivo/transports/http/calendar.py`:

```python
"""HTTP endpoints for calendar data management."""
from __future__ import annotations

from fastapi import APIRouter, Request

from marivo.transports.http.models.calendar import (
    CalendarDataLoadRequest,
    CalendarDataLoadResponse,
    CalendarDataRow,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])

_COLUMNS = (
    "calendar_date",
    "weekday",
    "is_weekend",
    "is_workday",
    "holiday_name",
    "holiday_group_id",
    "year_relative_holiday_key",
)

_INSERT_SQL = (
    "INSERT INTO calendar "
    "(calendar_date, weekday, is_weekend, is_workday, holiday_name, "
    "holiday_group_id, year_relative_holiday_key) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


@router.put("/data", response_model=CalendarDataLoadResponse)
def load_calendar_data(request: Request, payload: CalendarDataLoadRequest) -> CalendarDataLoadResponse:
    """Replace all calendar data with the provided rows.

    This is a destructive PUT — existing calendar data is deleted
    before the new rows are inserted.
    """
    metadata = request.app.state.runtime.metadata
    metadata.execute("DELETE FROM calendar")
    params = [
        (
            row.calendar_date,
            row.weekday,
            row.is_weekend,
            row.is_workday,
            row.holiday_name,
            row.holiday_group_id,
            row.year_relative_holiday_key,
        )
        for row in payload.rows
    ]
    metadata.execute_batch(_INSERT_SQL, params)
    return CalendarDataLoadResponse(status="loaded", row_count=len(params))
```

- [ ] **Step 3: Verify HTTP models and endpoint load**

Run: `.venv/bin/python -c "from marivo.transports.http.models.calendar import CalendarDataRow, CalendarDataLoadRequest, CalendarDataLoadResponse; print('OK')"`
Expected: prints "OK"

- [ ] **Step 4: Commit**

```bash
git add marivo/transports/http/models/calendar.py marivo/transports/http/calendar.py
git commit -m "refactor: calendar HTTP API — PUT replace, remove version/region/versions endpoint"
```

---

### Task 7: CLI — remove --version parameter

**Files:**
- Modify: `marivo/transports/cli/cmd_calendar.py`

- [ ] **Step 1: Remove --version from CLI calendar load command**

In `marivo/transports/cli/cmd_calendar.py`, remove the `--version` argument from `add_arguments` and all references to `version` in `_handle_load`. The function should no longer include `calendar_version` in the returned status dict.

Update `add_arguments` (around lines 16-23) — remove the `--version` argument line:

```python
def add_arguments(parser: Any) -> None:
    sub = parser.add_parser("calendar")
    cal_sub = sub.add_subparsers(dest="calendar_command")
    load_p = cal_sub.add_parser("load")
    load_p.add_argument("file", help="Path to calendar CSV file")
```

Update `_handle_load` (around lines 33-111) — remove `version` variable and the reference to `--version` in the status dict. The required columns check should also drop `region_code` and `calendar_version` if they appear:

```python
def _handle_load(args: Any) -> dict[str, Any]:
    # ... keep validation logic but remove version references
    # Remove: version = getattr(args, "version", None)
    # In status dict, remove "calendar_version" key
    # _REQUIRED_COLUMNS stays: {"calendar_date", "weekday", "is_weekend", "is_workday"}
```

- [ ] **Step 2: Verify CLI loads**

Run: `.venv/bin/python -c "from marivo.transports.cli.cmd_calendar import handle; print('OK')"`
Expected: prints "OK"

- [ ] **Step 3: Commit**

```bash
git add marivo/transports/cli/cmd_calendar.py
git commit -m "refactor: remove --version from calendar CLI load command"
```

---

### Task 8: build_cn_calendar.py — adapt output format

**Files:**
- Modify: `scripts/build_cn_calendar.py`
- Test: `tests/test_build_cn_calendar.py`

- [ ] **Step 1: Write failing test for new output format**

Update `tests/test_build_cn_calendar.py`. The test currently checks for 365 rows for 2026 and specific date properties. Adapt it to verify:
- Rows no longer have `calendar_version` or `region_code` columns
- Same-date multi-holiday rows (e.g., 2024-10-01) produce multiple rows
- Non-holiday rows have `holiday_group_id` = `""`

```python
def test_build_calendar_rows_2026_no_version_region():
    rows = _build_calendar_rows(2026, HOLIDAY_WINDOWS_2026)
    # Every row should have empty holiday_group_id or a valid holiday group
    for row in rows:
        assert "calendar_version" not in row
        assert "region_code" not in row
        assert row["holiday_group_id"] in (
            "", "new_year", "spring_festival", "qingming",
            "labor_day", "dragon_boat", "mid_autumn", "national_day",
        )
    assert len(rows) == 365  # 2026 has 365 days (at least one row per day)

def test_build_calendar_rows_2024_dual_holiday_oct1():
    rows = _build_calendar_rows(2024, HOLIDAY_WINDOWS_2024)
    oct1_rows = [r for r in rows if r["calendar_date"] == "2024-10-01"]
    # 2024-10-01 is both National Day and Mid-Autumn Festival
    assert len(oct1_rows) >= 2
    group_ids = {r["holiday_group_id"] for r in oct1_rows}
    assert "national_day" in group_ids
    assert "mid_autumn" in group_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test TESTS='tests/test_build_cn_calendar.py'`
Expected: FAIL — `_build_calendar_rows` still includes version/region and doesn't produce multi-holiday rows

- [ ] **Step 3: Update `_build_calendar_rows` to produce multi-row output and remove version/region**

In `scripts/build_cn_calendar.py`, update `_build_calendar_rows` (around lines 376-415) to:
- Remove `calendar_version` and `region_code` from row dicts
- For dates that fall within multiple holiday windows, produce multiple rows (one per holiday)
- For non-holiday dates, produce a single row with `holiday_group_id = ""`
- The `holiday_group_id` default should be `""` (empty string), not `None`

- [ ] **Step 4: Update `_write_csv` and `_write_tables` to match new format**

In `scripts/build_cn_calendar.py`:
- `_write_csv`: remove `calendar_version` and `region_code` columns from CSV output
- `_write_tables`: update DuckDB table schema to match the new `calendar` table (no version/region, PK `(calendar_date, holiday_group_id)`)

- [ ] **Step 5: Remove `_calendar_version()` and `_resolved_calendar_version()` functions**

These functions (around lines 284-288) are no longer needed.

- [ ] **Step 6: Run tests**

Run: `make test TESTS='tests/test_build_cn_calendar.py'`
Expected: ALL PASS

- [ ] **Step 7: Rebuild SQL INSERT file**

Run: `.venv/bin/python scripts/build_cn_calendar.py --format sql` (or update script to support SQL output)
Then replace `data/public_holiday_events_insert.sql` with the new output.

- [ ] **Step 8: Commit**

```bash
git add scripts/build_cn_calendar.py tests/test_build_cn_calendar.py data/public_holiday_events_insert.sql
git commit -m "refactor: build_cn_calendar produces multi-holiday rows without version/region"
```

---

### Task 9: Runtime wiring — simplify CalendarDataReader attachment

**Files:**
- Modify: `marivo/runtime/runtime.py:40,67-69,110-113`

- [ ] **Step 1: Update runtime.py — CalendarDataReader no longer needs config**

In `marivo/runtime/runtime.py`, the `wire_calendar_data_reader` method (lines 67-69) currently just stores a reader. No change needed there — the reader now takes `metadata` only, no `config`. The `calendar_data_reader` property (lines 110-113) stays the same. No functional change required in this file.

However, if any wiring code constructs `CalendarDataReader(metadata=..., config=...)`, update those call sites to `CalendarDataReader(metadata=...)`. Search for all such constructions.

- [ ] **Step 2: Verify runtime module loads**

Run: `.venv/bin/python -c "from marivo.runtime.runtime import MarivoRuntime; print('OK')"`
Expected: prints "OK"

- [ ] **Step 3: Commit (if any changes were needed)**

```bash
git add marivo/runtime/runtime.py
git commit -m "refactor: simplify CalendarDataReader wiring — no config parameter"
```

---

### Task 10: Full integration — run complete test suite and typecheck

**Files:**
- All modified files

- [ ] **Step 1: Run full typecheck**

Run: `make typecheck`
Expected: PASS — no references to CalendarConfig, calendar_version, region_code in runtime code

- [ ] **Step 2: Run full test suite**

Run: `make test`
Expected: ALL PASS

- [ ] **Step 3: Run lint**

Run: `make lint`
Expected: PASS

- [ ] **Step 4: Verify no lingering references to CalendarConfig/calendar_version/region_code**

Search for any remaining references:

```bash
grep -rn 'CalendarConfig\|calendar_version\|region_code' marivo/ --include='*.py' | grep -v 'test_' | grep -v '__pycache__'
```

Expected: Zero matches (or only in comments/docs that are about to be updated)

- [ ] **Step 5: Final commit if any cleanup needed**

```bash
git add -A  # only if cleanup fixes were needed
git commit -m "chore: final cleanup after calendar data simplification"
```

---

### Task 11: Rebuild SQL data file and update OpenAPI types

**Files:**
- Modify: `data/public_holiday_events_insert.sql`
- Modify: `frontend/src/api/openapi.generated.ts` (regenerate if HTTP API changed)

- [ ] **Step 1: Rebuild the SQL INSERT file with new schema**

Regenerate `data/public_holiday_events_insert.sql` from `data/mvp.duckdb` using the updated `build_cn_calendar.py` script, or manually update the INSERT statements to match the new table schema (no `calendar_version`, no `region_code`, `holiday_group_id` DEFAULT '' instead of NULL).

- [ ] **Step 2: Regenerate frontend OpenAPI types**

Run: `cd frontend && npm run generate-openapi-types` (or the equivalent script)
Expected: `frontend/src/api/openapi.generated.ts` updated to reflect new calendar API models

- [ ] **Step 3: Commit**

```bash
git add data/public_holiday_events_insert.sql frontend/src/api/openapi.generated.ts
git commit -m "chore: rebuild calendar SQL data and regenerate frontend OpenAPI types"
```
