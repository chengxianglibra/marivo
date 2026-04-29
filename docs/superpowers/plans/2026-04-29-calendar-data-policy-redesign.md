# Calendar Data/Policy Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify calendar from snapshot-based multi-source assembly to single-table single-version model in Marivo DB, consolidate 8 policies to 7 with calendar_aware cascading.

**Architecture:** Calendar data lives in a fixed `marivo.calendar` table in the Marivo metadata DB. Config is all-optional with defaults. CalendarDataReader queries this single table directly, eliminating source registration and multi-table assembly. Seven policies replace eight, with `calendar_aware` alignment subsuming `holiday_yoy` + `event_yoy`.

**Tech Stack:** Python 3.12+, Pydantic v2, FastAPI, argparse, SQLite/DuckDB

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `app/config.py` | Replace 3 calendar config classes with 1 simple class |
| Modify | `app/storage/schema.py` | Add `marivo.calendar` DDL |
| Rewrite | `app/analysis_core/calendar_data_runtime.py` | Simplified single-table reader |
| Modify | `app/analysis_core/calendar_policy.py` | 7 policies, remove `resolved_calendar_source` field |
| Modify | `app/service.py` | Update reader creation and calendar binding validation |
| Create | `app/api/calendar.py` | HTTP API for calendar data loading |
| Modify | `app/api/router.py` | Register calendar router |
| Create | `app/api/models/calendar.py` | Request/response models for calendar API |
| Modify | `app/api/models/catalog.py` | Remove `resolved_calendar_source` from catalog entry |
| Create | `app/cli/cmd_calendar.py` | CLI `marivo calendar load` |
| Modify | `app/cli/__init__.py` | Register calendar subcommand |
| Modify | `app/intents/calendar_alignment_metadata.py` | Update source_lineage validation |
| Modify | `scripts/build_cn_calendar.py` | Output CSV instead of direct DB insertion |
| Modify | `marivo.example.yaml` | Update calendar section |
| Rewrite | `tests/test_calendar_data_runtime.py` | Tests for new reader |
| Modify | `tests/test_calendar_policy_registry.py` | Update for 7 policies |

---

### Task 1: Update CalendarConfig model

**Files:**
- Modify: `app/config.py:121-145`

Replace `CalendarSourceBindingConfig`, `CalendarSnapshotConfig`, and `CalendarConfig` with a single simplified `CalendarConfig`.

- [ ] **Step 1: Write the failing test**

Add a test to `tests/test_config.py` (create if needed) that validates the new CalendarConfig accepts empty dict, accepts optional fields, and rejects unknown fields:

```python
from __future__ import annotations

import unittest

from app.config import CalendarConfig


class CalendarConfigTests(unittest.TestCase):
    def test_empty_config_uses_defaults(self) -> None:
        config = CalendarConfig.model_validate({})
        self.assertEqual(config.region_code, "CN")
        self.assertIsNone(config.calendar_version)

    def test_optional_fields_can_be_set(self) -> None:
        config = CalendarConfig.model_validate(
            {"region_code": "US", "calendar_version": "us_2026_v1"}
        )
        self.assertEqual(config.region_code, "US")
        self.assertEqual(config.calendar_version, "us_2026_v1")

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaises(Exception):
            CalendarConfig.model_validate({"snapshots": []})

    def test_rejects_latest_version(self) -> None:
        with self.assertRaises(Exception):
            CalendarConfig.model_validate({"calendar_version": "latest"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL (old CalendarConfig has `snapshots` field, `empty_config_uses_defaults` passes but `rejects_unknown_fields` fails because `snapshots` is accepted)

- [ ] **Step 3: Write minimal implementation**

In `app/config.py`, replace lines 121-145 (the three old classes) with:

```python
class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_code: str = "CN"
    calendar_version: str | None = Field(default=None, pattern=r"^(?!latest$|current$).+")
```

The `pattern` regex rejects "latest" and "current" as calendar_version values, enforcing immutability.

Update the `MarivoConfig.calendar` field default to still use `CalendarConfig` (no change needed since `CalendarConfig()` now has all defaults).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Run existing tests to check breakage**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: Several test failures in `test_calendar_data_runtime.py` and `test_calendar_policy_registry.py` due to the config change. These will be fixed in later tasks.

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "refactor: simplify CalendarConfig to all-optional with defaults"
```

---

### Task 2: Add marivo.calendar table DDL

**Files:**
- Modify: `app/storage/schema.py:1099` (add before closing `]` of METADATA_DDL)

- [ ] **Step 1: Write the failing test**

In `tests/test_schema.py` (create if needed):

```python
from __future__ import annotations

import unittest

from app.storage.schema import METADATA_DDL


class CalendarSchemaTests(unittest.TestCase):
    def test_metadata_ddl_includes_calendar_table(self) -> None:
        calendar_ddl = [stmt for stmt in METADATA_DDL if "marivo.calendar" in stmt]
        self.assertGreaterEqual(len(calendar_ddl), 1, "METADATA_DDL must include marivo.calendar table")

    def test_calendar_ddl_has_required_columns(self) -> None:
        calendar_ddl = [stmt for stmt in METADATA_DDL if "marivo.calendar" in stmt and "CREATE TABLE" in stmt][0]
        required_columns = [
            "calendar_date",
            "region_code",
            "calendar_version",
            "weekday",
            "is_weekend",
            "is_workday",
            "holiday_name",
            "holiday_group_id",
            "year_relative_holiday_key",
            "event_group_id",
            "year_relative_event_key",
        ]
        for column in required_columns:
            self.assertIn(column, calendar_ddl, f"marivo.calendar must have column '{column}'")

    def test_calendar_ddl_has_primary_key(self) -> None:
        calendar_ddl = [stmt for stmt in METADATA_DDL if "marivo.calendar" in stmt and "CREATE TABLE" in stmt][0]
        self.assertIn("PRIMARY KEY", calendar_ddl)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_schema.py -v`
Expected: FAIL (no marivo.calendar table in DDL)

- [ ] **Step 3: Write minimal implementation**

In `app/storage/schema.py`, add the following DDL statement to `METADATA_DDL` list (before the `metadata_schema_marker` table, around line 1098):

```python
    """
    CREATE TABLE IF NOT EXISTS marivo.calendar (
        calendar_date              TEXT NOT NULL,
        region_code                TEXT NOT NULL,
        calendar_version           TEXT NOT NULL,
        weekday                    INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
        is_weekend                 INTEGER NOT NULL CHECK (is_weekend IN (0, 1)),
        is_workday                 INTEGER NOT NULL CHECK (is_workday IN (0, 1)),
        holiday_name               TEXT,
        holiday_group_id           TEXT,
        year_relative_holiday_key  TEXT,
        event_group_id             TEXT,
        year_relative_event_key    TEXT,
        PRIMARY KEY (calendar_version, region_code, calendar_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_calendar_version_region ON marivo.calendar(calendar_version, region_code)",
```

Note: SQLite uses TEXT for dates and INTEGER for booleans, matching the existing schema conventions in this file.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/storage/schema.py tests/test_schema.py
git commit -m "feat: add marivo.calendar table DDL"
```

---

### Task 3: Rewrite CalendarDataReader

**Files:**
- Rewrite: `app/analysis_core/calendar_data_runtime.py`

This is the core change. The new reader queries `marivo.calendar` directly from the MetadataStore, no source registration or multi-table assembly needed.

- [ ] **Step 1: Write the failing test**

Replace `tests/test_calendar_data_runtime.py` entirely:

```python
from __future__ import annotations

import unittest
from datetime import date
from typing import Any, cast

from app.analysis_core.calendar_data_runtime import (
    CalendarDataReader,
    CalendarDataResolutionError,
    _RESOLVED_CALENDAR_SOURCE,
)
from app.config import CalendarConfig
from app.storage.metadata import MetadataStore


def _calendar_row(
    day: str,
    *,
    holiday_group_id: str | None = None,
    year_relative_holiday_key: str | None = None,
    event_group_id: str | None = None,
    year_relative_event_key: str | None = None,
    calendar_version: str = "cn_2026q2_v1",
) -> dict[str, Any]:
    day_value = date.fromisoformat(day)
    return {
        "calendar_date": day,
        "region_code": "CN",
        "calendar_version": calendar_version,
        "weekday": day_value.weekday() + 1,
        "is_weekend": 1 if day_value.weekday() >= 5 else 0,
        "is_workday": 1 if day_value.weekday() < 5 else 0,
        "holiday_name": None,
        "holiday_group_id": holiday_group_id,
        "year_relative_holiday_key": year_relative_holiday_key,
        "event_group_id": event_group_id,
        "year_relative_event_key": year_relative_event_key,
    }


class _FakeMetadata:
    def __init__(self, calendar_rows: list[dict[str, Any]] | None = None) -> None:
        self._calendar_rows = calendar_rows or []
        self._versions: list[str] = sorted(
            {row["calendar_version"] for row in self._calendar_rows}
        )

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        params = params or []
        if "FROM marivo.calendar" in sql and "MAX(calendar_version)" in sql:
            if not self._versions:
                return []
            return [{"max_version": self._versions[-1]}]
        if "FROM marivo.calendar" in sql:
            calendar_version = params[0] if params else ""
            region_code = params[1] if len(params) > 1 else "CN"
            read_start = params[2] if len(params) > 2 else ""
            read_end = params[3] if len(params) > 3 else ""
            return [
                row
                for row in self._calendar_rows
                if row["calendar_version"] == calendar_version
                and row["region_code"] == region_code
                and read_start <= row["calendar_date"] < read_end
            ]
        raise AssertionError(f"Unexpected query: {sql}")

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        params = params or []
        if "MAX(calendar_version)" in sql:
            rows = self.query_rows(sql, params)
            return rows[0] if rows else None
        raise AssertionError(f"Unexpected query: {sql}")


def _metadata_store(
    calendar_rows: list[dict[str, Any]] | None = None,
) -> MetadataStore:
    return cast("MetadataStore", _FakeMetadata(calendar_rows))


class CalendarDataReaderTests(unittest.TestCase):
    def _make_reader(
        self,
        calendar_rows: list[dict[str, Any]] | None = None,
        config: CalendarConfig | None = None,
    ) -> CalendarDataReader:
        return CalendarDataReader(
            metadata=_metadata_store(calendar_rows),
            config=config or CalendarConfig(),
        )

    def test_read_for_alignment_reads_single_table(self) -> None:
        rows = [
            _calendar_row(
                "2025-04-01",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_d-3",
                event_group_id="member_day",
                year_relative_event_key="member_day_d-1",
            ),
            _calendar_row(
                "2025-04-02",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_d-2",
                event_group_id="member_day",
                year_relative_event_key="member_day_d+0",
            ),
            _calendar_row(
                "2026-04-01",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_d-3",
                event_group_id="member_day",
                year_relative_event_key="member_day_d-1",
            ),
            _calendar_row(
                "2026-04-02",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_d-2",
                event_group_id="member_day",
                year_relative_event_key="member_day_d+0",
            ),
        ]
        reader = self._make_reader(rows)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 3)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 3)),
        )

        self.assertEqual(result.resolved_calendar_source, _RESOLVED_CALENDAR_SOURCE)
        self.assertEqual(result.resolved_calendar_version, "cn_2026q2_v1")
        self.assertEqual(len(result.annotation_rows), 4)
        self.assertEqual(result.annotation_rows[0].holiday_group_id, "qingming")
        self.assertEqual(result.annotation_rows[0].event_group_id, "member_day")

    def test_read_for_alignment_with_pinned_version(self) -> None:
        rows = [
            _calendar_row("2026-04-01", calendar_version="cn_2026q1_v1"),
            _calendar_row("2026-04-01", calendar_version="cn_2026q2_v1"),
        ]
        config = CalendarConfig(calendar_version="cn_2026q1_v1")
        reader = self._make_reader(rows, config=config)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 2)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
        )

        self.assertEqual(result.resolved_calendar_version, "cn_2026q1_v1")

    def test_read_for_alignment_discovers_latest_version(self) -> None:
        rows = [
            _calendar_row("2026-04-01", calendar_version="cn_2026q1_v1"),
            _calendar_row("2026-04-01", calendar_version="cn_2026q2_v1"),
        ]
        reader = self._make_reader(rows)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 2)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
        )

        self.assertEqual(result.resolved_calendar_version, "cn_2026q2_v1")

    def test_read_for_alignment_raises_when_no_data(self) -> None:
        reader = self._make_reader([])

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

        self.assertIn("no calendar data", str(ctx.exception).lower())

    def test_read_for_alignment_raises_when_pinned_version_missing(self) -> None:
        rows = [_calendar_row("2026-04-01", calendar_version="cn_2026q2_v1")]
        config = CalendarConfig(calendar_version="cn_2026q1_v1")
        reader = self._make_reader(rows, config=config)

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

    def test_read_for_alignment_rejects_invalid_weekday(self) -> None:
        rows = [
            {
                "calendar_date": "2026-04-01",
                "region_code": "CN",
                "calendar_version": "cn_2026q2_v1",
                "weekday": 8,
                "is_weekend": 0,
                "is_workday": 1,
                "holiday_name": None,
                "holiday_group_id": None,
                "year_relative_holiday_key": None,
                "event_group_id": None,
                "year_relative_event_key": None,
            },
        ]
        reader = self._make_reader(rows)

        with self.assertRaises(CalendarDataResolutionError):
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

    def test_read_for_alignment_rejects_missing_weekend_workday(self) -> None:
        rows = [
            {
                "calendar_date": "2026-04-01",
                "region_code": "CN",
                "calendar_version": "cn_2026q2_v1",
                "weekday": 2,
                "is_weekend": None,
                "is_workday": 1,
                "holiday_name": None,
                "holiday_group_id": None,
                "year_relative_holiday_key": None,
                "event_group_id": None,
                "year_relative_event_key": None,
            },
        ]
        reader = self._make_reader(rows)

        with self.assertRaises(CalendarDataResolutionError):
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

    def test_source_lineage_contains_table_and_version(self) -> None:
        rows = [
            _calendar_row("2026-04-01"),
        ]
        reader = self._make_reader(rows)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 2)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
        )

        self.assertIn("table_fqn", result.source_lineage)
        self.assertEqual(result.source_lineage["table_fqn"], "marivo.calendar")
        self.assertIn("calendar_version", result.source_lineage)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_calendar_data_runtime.py -v`
Expected: FAIL (import errors, old API)

- [ ] **Step 3: Write minimal implementation**

Replace `app/analysis_core/calendar_data_runtime.py` entirely:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable

from app.analysis_core.calendar_alignment_pairing import CalendarAnnotationRow
from app.config import CalendarConfig
from app.storage.metadata import MetadataStore

_RESOLVED_CALENDAR_SOURCE = "marivo.calendar"


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
        self.pinned_version = (
            config.calendar_version.strip() if config.calendar_version else None
        )

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
            "SELECT MAX(calendar_version) AS max_version FROM marivo.calendar WHERE region_code = ?",
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
            FROM marivo.calendar
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
                    year_relative_holiday_key=_optional_str(
                        row.get("year_relative_holiday_key")
                    ),
                    event_group_id=_optional_str(row.get("event_group_id")),
                    year_relative_event_key=_optional_str(
                        row.get("year_relative_event_key")
                    ),
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
```

Key differences from the old implementation:
- No `CalendarSourceBinding` / `CalendarSnapshotBinding` dataclasses
- No `QueryRouter` dependency — reads directly from `MetadataStore`
- No multi-table assembly — single `marivo.calendar` query
- `source_lineage` is now a flat dict with `table_fqn` + `calendar_version`
- `_resolve_calendar_version()` auto-discovers latest version when not pinned
- `CalendarDataReadResult.source_lineage` type changed from `dict[str, dict[str, str]]` to `dict[str, str]`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_calendar_data_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/analysis_core/calendar_data_runtime.py tests/test_calendar_data_runtime.py
git commit -m "refactor: simplify CalendarDataReader to single-table model"
```

---

### Task 4: Update CalendarPolicyDefinition (8 -> 7 policies)

**Files:**
- Modify: `app/analysis_core/calendar_policy.py`

- [ ] **Step 1: Write the failing test**

Replace `tests/test_calendar_policy_registry.py` with updated tests for 7 policies:

```python
from __future__ import annotations

import unittest

from app.analysis_core.calendar_policy import (
    CalendarPolicyResolutionError,
    get_calendar_policy,
    list_calendar_policies,
    policy_registry_summary,
    resolve_calendar_policy,
    validate_calendar_policy_ref,
)


class CalendarPolicyRegistryTests(unittest.TestCase):
    def test_registry_has_seven_policies(self) -> None:
        policies = list_calendar_policies()
        self.assertEqual(len(policies), 7)

    def test_registry_summary_has_seven_entries(self) -> None:
        summary = policy_registry_summary()
        self.assertEqual(len(summary), 7)

    def test_old_policy_refs_are_removed(self) -> None:
        removed_refs = [
            "calendar_policy.holiday_yoy",
            "calendar_policy.event_yoy",
            "calendar_policy.event_mom",
        ]
        for ref in removed_refs:
            with self.assertRaises(CalendarPolicyResolutionError):
                get_calendar_policy(ref)

    def test_new_calendar_aware_policies_exist(self) -> None:
        new_refs = [
            "calendar_policy.calendar_yoy",
            "calendar_policy.calendar_mom",
        ]
        for ref in new_refs:
            policy = get_calendar_policy(ref)
            self.assertIn("calendar_aware", policy.resolved_alignment_mode)

    def test_calendar_yoy_matching_strategy_cascades(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_yoy")
        matchers = [step.matcher for step in policy.matching_strategy]
        self.assertEqual(
            matchers,
            [
                "event_cluster",
                "year_relative_event_key",
                "holiday_cluster",
                "year_relative_holiday_key",
                "same_weekday_nearest",
                "natural_date_shift",
            ],
        )

    def test_calendar_mom_matching_strategy_cascades(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_mom")
        matchers = [step.matcher for step in policy.matching_strategy]
        self.assertEqual(
            matchers,
            [
                "event_cluster",
                "year_relative_event_key",
                "holiday_cluster",
                "year_relative_holiday_key",
                "same_weekday_nearest",
                "natural_date_shift",
            ],
        )

    def test_registry_summary_filters_by_comparison_basis(self) -> None:
        yoy_summary = policy_registry_summary(comparison_basis="yoy")
        mom_summary = policy_registry_summary(comparison_basis="mom")
        wow_summary = policy_registry_summary(comparison_basis="wow")

        self.assertEqual(
            [item["policy_ref"] for item in yoy_summary],
            [
                "calendar_policy.natural_yoy",
                "calendar_policy.weekday_yoy",
                "calendar_policy.calendar_yoy",
            ],
        )
        self.assertEqual(
            [item["policy_ref"] for item in mom_summary],
            [
                "calendar_policy.natural_mom",
                "calendar_policy.weekday_mom",
                "calendar_policy.calendar_mom",
            ],
        )
        self.assertEqual(
            [item["policy_ref"] for item in wow_summary],
            ["calendar_policy.weekday_wow"],
        )

    def test_policy_definition_has_no_resolved_calendar_source(self) -> None:
        policy = get_calendar_policy("calendar_policy.calendar_yoy")
        self.assertFalse(
            hasattr(policy, "resolved_calendar_source"),
            "CalendarPolicyDefinition should not have resolved_calendar_source field",
        )

    def test_validate_calendar_policy_ref_rejects_unknown_ref(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            validate_calendar_policy_ref("calendar_policy.unknown")
        self.assertEqual(ctx.exception.code, "calendar_policy_unknown")

    def test_validate_calendar_policy_ref_rejects_basis_mismatch(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            validate_calendar_policy_ref(
                "calendar_policy.weekday_wow",
                comparison_basis="yoy",
            )
        self.assertEqual(ctx.exception.code, "calendar_policy_basis_mismatch")

    def test_resolve_calendar_policy_prefers_explicit_then_injected(self) -> None:
        resolved = resolve_calendar_policy(
            explicit_policy_ref="calendar_policy.weekday_yoy",
            injected_policy_ref="calendar_policy.calendar_yoy",
            planner_candidate_refs=["calendar_policy.natural_yoy"],
            comparison_basis="yoy",
        )
        assert resolved is not None
        self.assertEqual(resolved.policy.policy_ref, "calendar_policy.weekday_yoy")
        self.assertEqual(resolved.resolution_source, "explicit_request")

    def test_resolve_calendar_policy_accepts_injected_when_explicit_missing(self) -> None:
        resolved = resolve_calendar_policy(
            injected_policy_ref="calendar_policy.calendar_mom",
            planner_candidate_refs=["calendar_policy.natural_mom"],
            comparison_basis="mom",
        )
        assert resolved is not None
        self.assertEqual(resolved.policy.policy_ref, "calendar_policy.calendar_mom")
        self.assertEqual(resolved.resolution_source, "injected_binding")

    def test_resolve_calendar_policy_rejects_ambiguous_planner_candidates(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            resolve_calendar_policy(
                planner_candidate_refs=[
                    "calendar_policy.weekday_yoy",
                    "calendar_policy.calendar_yoy",
                ],
                comparison_basis="yoy",
            )
        self.assertEqual(ctx.exception.code, "calendar_policy_ambiguous")

    def test_resolve_calendar_policy_can_require_policy(self) -> None:
        with self.assertRaises(CalendarPolicyResolutionError) as ctx:
            resolve_calendar_policy(comparison_basis="wow", required=True)
        self.assertEqual(ctx.exception.code, "calendar_policy_missing")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_calendar_policy_registry.py -v`
Expected: FAIL (old 8 policies, old refs, `resolved_calendar_source` exists)

- [ ] **Step 3: Write minimal implementation**

In `app/analysis_core/calendar_policy.py`, make these changes:

1. Update `CalendarPolicyRef` type alias (line 7-16):

```python
CalendarPolicyRef = Literal[
    "calendar_policy.natural_yoy",
    "calendar_policy.weekday_yoy",
    "calendar_policy.calendar_yoy",
    "calendar_policy.natural_mom",
    "calendar_policy.weekday_mom",
    "calendar_policy.calendar_mom",
    "calendar_policy.weekday_wow",
]
```

2. Remove `resolved_calendar_source` field from `CalendarPolicyDefinition` (line 50):

```python
@dataclass(frozen=True, slots=True)
class CalendarPolicyDefinition:
    policy_ref: CalendarPolicyRef
    comparison_basis: CalendarComparisonBasis
    window_tags: tuple[str, ...]
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    resolved_alignment_mode: str
    resolved_baseline_generation_rule: CalendarBaselineGenerationRule
    matching_strategy: tuple[CalendarMatchingStep, ...]
    fallback_strategy: tuple[str, ...]
    coverage_behavior: str
```

3. Remove `resolved_calendar_source` field from `CalendarPolicyCatalogEntry` (line 73):

The `CalendarPolicyCatalogEntry` class should no longer have `resolved_calendar_source: str`.

4. Replace `_POLICIES` tuple (lines 102-286) with 7 policies. Keep `natural_yoy`, `weekday_yoy`, `natural_mom`, `weekday_mom`, `weekday_wow` unchanged (but remove `resolved_calendar_source` field). Replace `holiday_yoy` + `event_yoy` with `calendar_yoy` and `event_mom` with `calendar_mom`:

```python
_POLICIES: tuple[CalendarPolicyDefinition, ...] = (
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.natural_yoy",
        comparison_basis="yoy",
        window_tags=("natural_date",),
        use_when=("普通同比", "未提节假日", "未提活动窗口"),
        avoid_when=("明确要求周几对周几", "明确要求节假日口径", "明确要求活动口径"),
        resolved_alignment_mode="natural_date",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_year",
            offset_value=1,
            offset_unit="year",
        ),
        matching_strategy=(CalendarMatchingStep("natural_date_shift", requires_annotation=False),),
        fallback_strategy=(),
        coverage_behavior="require_full_natural_date_pairing",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.weekday_yoy",
        comparison_basis="yoy",
        window_tags=("same_weekday",),
        use_when=("工作日效应强", "周一对周一", "周末对周末"),
        avoid_when=("明确要求节假日窗口", "明确要求活动窗口"),
        resolved_alignment_mode="same_weekday",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_year",
            offset_value=1,
            offset_unit="year",
        ),
        matching_strategy=(
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
            CalendarMatchingStep("natural_date_shift", requires_annotation=False),
        ),
        fallback_strategy=("natural_date_shift",),
        coverage_behavior="warn_when_weekday_fallback_used",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.calendar_yoy",
        comparison_basis="yoy",
        window_tags=("calendar_aware", "event_cluster", "holiday_cluster", "same_weekday_fallback"),
        use_when=("节假日", "活动窗口", "春节", "618", "双11", "同比需日历对齐"),
        avoid_when=(),
        resolved_alignment_mode="calendar_aware",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_year",
            offset_value=1,
            offset_unit="year",
        ),
        matching_strategy=(
            CalendarMatchingStep("event_cluster", requires_annotation=True),
            CalendarMatchingStep("year_relative_event_key", requires_annotation=True),
            CalendarMatchingStep("holiday_cluster", requires_annotation=True),
            CalendarMatchingStep("year_relative_holiday_key", requires_annotation=True),
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
            CalendarMatchingStep("natural_date_shift", requires_annotation=False),
        ),
        fallback_strategy=("same_weekday_nearest", "natural_date_shift"),
        coverage_behavior="warn_when_calendar_annotation_missing_or_fallback_used",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.natural_mom",
        comparison_basis="mom",
        window_tags=("natural_date",),
        use_when=("普通月环比", "上月对本月", "未提活动窗口"),
        avoid_when=("明确要求周几对齐", "明确要求活动窗口"),
        resolved_alignment_mode="natural_date",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_period",
        ),
        matching_strategy=(CalendarMatchingStep("natural_date_shift", requires_annotation=False),),
        fallback_strategy=(),
        coverage_behavior="require_full_natural_date_pairing",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.weekday_mom",
        comparison_basis="mom",
        window_tags=("same_weekday",),
        use_when=("周几对齐月环比", "工作日效应强"),
        avoid_when=("明确要求活动窗口",),
        resolved_alignment_mode="same_weekday",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_period",
        ),
        matching_strategy=(
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
            CalendarMatchingStep("natural_date_shift", requires_annotation=False),
        ),
        fallback_strategy=("natural_date_shift",),
        coverage_behavior="warn_when_weekday_fallback_used",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.calendar_mom",
        comparison_basis="mom",
        window_tags=("calendar_aware", "event_cluster", "holiday_cluster"),
        use_when=("活动期月环比", "节假日月环比", "活动窗口对活动窗口"),
        avoid_when=("普通自然月环比"),
        resolved_alignment_mode="calendar_aware",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_period",
        ),
        matching_strategy=(
            CalendarMatchingStep("event_cluster", requires_annotation=True),
            CalendarMatchingStep("year_relative_event_key", requires_annotation=True),
            CalendarMatchingStep("holiday_cluster", requires_annotation=True),
            CalendarMatchingStep("year_relative_holiday_key", requires_annotation=True),
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
            CalendarMatchingStep("natural_date_shift", requires_annotation=False),
        ),
        fallback_strategy=("same_weekday_nearest", "natural_date_shift"),
        coverage_behavior="warn_when_calendar_annotation_missing_or_fallback_used",
    ),
    CalendarPolicyDefinition(
        policy_ref="calendar_policy.weekday_wow",
        comparison_basis="wow",
        window_tags=("same_weekday", "weekly_period"),
        use_when=("周环比", "上周同周几", "工作日/周末结构需稳定"),
        avoid_when=("月环比", "同比", "活动窗口优先"),
        resolved_alignment_mode="same_weekday",
        resolved_baseline_generation_rule=CalendarBaselineGenerationRule(
            strategy="previous_period",
            offset_value=1,
            offset_unit="week",
        ),
        matching_strategy=(
            CalendarMatchingStep(
                "same_weekday_nearest",
                requires_annotation=False,
                tie_breaker="prefer_backward",
                max_shift_days=3,
            ),
        ),
        fallback_strategy=(),
        coverage_behavior="require_same_weekday_pairing",
    ),
)
```

5. Update `calendar_policy_catalog_entry()` function (line 326-347) to remove `resolved_calendar_source` from the constructed entry:

```python
def calendar_policy_catalog_entry(policy_ref: str) -> CalendarPolicyCatalogEntry:
    policy = get_calendar_policy(policy_ref)
    return CalendarPolicyCatalogEntry(
        policy_ref=policy.policy_ref,
        object_id=policy.policy_ref,
        name=_policy_name(policy),
        display_name=_policy_display_name(policy),
        description=_policy_description(policy),
        comparison_basis=policy.comparison_basis,
        resolved_alignment_mode=policy.resolved_alignment_mode,
        window_tags=policy.window_tags,
        use_when=policy.use_when,
        avoid_when=policy.avoid_when,
        matching_strategy_summary=tuple(
            _matching_step_summary(step) for step in policy.matching_strategy
        ),
        fallback_strategy=policy.fallback_strategy,
        coverage_behavior=policy.coverage_behavior,
        detail_path=f"/catalog/objects/calendar_policy/{policy.policy_ref}",
        resolve_path=f"/semantic/resolve/{policy.policy_ref}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_calendar_policy_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/analysis_core/calendar_policy.py tests/test_calendar_policy_registry.py
git commit -m "refactor: consolidate calendar policies from 8 to 7 with calendar_aware"
```

---

### Task 5: Update service.py integration

**Files:**
- Modify: `app/service.py:17,255,301,311,313-325,1371-1372`

- [ ] **Step 1: Write the failing test**

No separate test file needed — the integration is tested through existing observe/compare intent tests. The changes are small enough to verify by running the full test suite after.

- [ ] **Step 2: Implement changes**

In `app/service.py`:

1. Update import (line 17): Remove `CalendarDataResolutionError`, keep `CalendarDataReader`:

```python
from app.analysis_core.calendar_data_runtime import CalendarDataReader, CalendarDataResolutionError
```

This stays the same, but the reader constructor signature changed.

2. Update `_refresh_calendar_data_reader` (lines 313-325). The new CalendarDataReader doesn't need `query_router`, only `metadata` and `config`:

```python
def _refresh_calendar_data_reader(self) -> None:
    try:
        self.calendar_data_reader = CalendarDataReader(
            metadata=self.metadata,
            config=self.config.calendar,
        )
    except CalendarDataResolutionError as error:
        logger.warning("Calendar data reader unavailable: %s", error)
        self.calendar_data_reader = None
```

This removes the `if self._query_router is None or not self.config.calendar.snapshots:` guard. The reader is always created (it reads from the metadata DB, which always exists). If no calendar data is loaded yet, the error surfaces at read time, not at construction time.

3. Remove the `_refresh_calendar_data_reader()` call from the `query_router` setter (line 311). Calendar data reader no longer depends on query_router:

```python
@query_router.setter
def query_router(self, router: QueryRouter | None) -> None:
    self._query_router = router
    self.routing_runtime.query_router = router
```

4. Keep the `_refresh_calendar_data_reader()` call in `__init__` (line 301).

- [ ] **Step 3: Run existing tests**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short -x`
Expected: Failures in tests that reference old `source_lineage` structure. These are fixed in Task 6.

- [ ] **Step 4: Commit**

```bash
git add app/service.py
git commit -m "refactor: update service to use simplified CalendarDataReader"
```

---

### Task 6: Update _build_calendar_policy_binding and calendar_alignment_metadata

**Files:**
- Modify: `app/service.py:3210-3302`
- Modify: `app/intents/calendar_alignment_metadata.py`

The `source_lineage` structure changed from `dict[str, dict[str, str]]` (with `holiday_source`/`event_source` keys) to `dict[str, str]` (with `table_fqn`/`calendar_version` keys). The validation code needs to match.

- [ ] **Step 1: Update _build_calendar_policy_binding in service.py**

Replace the `_build_calendar_policy_binding` static method (lines 3210-3302) with simplified version that validates the new `source_lineage` structure:

```python
@staticmethod
def _build_calendar_policy_binding(
    resolved_calendar_alignments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not resolved_calendar_alignments:
        return None

    def require_string(alignment: dict[str, Any], field: str) -> str:
        value = alignment.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"resolved_calendar_alignment missing {field}")
        return value

    def require_source_lineage(alignment: dict[str, Any]) -> dict[str, str]:
        source_lineage = alignment.get("source_lineage")
        if not isinstance(source_lineage, dict) or not source_lineage:
            raise ValueError("resolved_calendar_alignment missing source_lineage metadata")
        normalized: dict[str, str] = {}
        for field in ("table_fqn", "calendar_version"):
            value = source_lineage.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"resolved_calendar_alignment source_lineage missing {field}")
            normalized[field] = value
        return normalized

    bindings: list[dict[str, Any]] = []
    for alignment in resolved_calendar_alignments:
        bindings.append(
            {
                "policy_ref": require_string(alignment, "policy_ref"),
                "comparison_basis": require_string(alignment, "comparison_basis"),
                "resolved_calendar_source": require_string(
                    alignment, "resolved_calendar_source"
                ),
                "resolved_calendar_version": require_string(
                    alignment, "resolved_calendar_version"
                ),
                "source_lineage": require_source_lineage(alignment),
            }
        )

    first_binding = bindings[0]
    for binding in bindings[1:]:
        if binding != first_binding:
            raise ValueError("conflicting calendar policy bindings in compiled step metadata")
    return first_binding
```

- [ ] **Step 2: Update calendar_alignment_metadata.py**

No changes needed to `calendar_alignment_metadata.py` itself. The `_calendar_alignment_mismatch` function checks `resolved_calendar_source` and `resolved_calendar_version` fields, which are still present in the resolved policy summary. The `source_lineage` field is not checked for structure in this file — it's passed through as-is.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short -x`
Expected: More tests pass now. Remaining failures should be in catalog models and any tests referencing old policy refs.

- [ ] **Step 4: Commit**

```bash
git add app/service.py
git commit -m "refactor: update calendar policy binding validation for new source_lineage"
```

---

### Task 7: Update catalog API models

**Files:**
- Modify: `app/api/models/catalog.py:51-65,107-109`

- [ ] **Step 1: Remove resolved_calendar_source from CatalogCalendarPolicySearchResult**

In `app/api/models/catalog.py`, update `CatalogCalendarPolicySearchResult` (line 51-65) to remove `resolved_calendar_source` field:

```python
class CatalogCalendarPolicySearchResult(CatalogSearchResultBase):
    object_kind: Literal["calendar_policy"]
    lifecycle_status: LifecycleStatus
    readiness_status: ReadinessStatus
    blocker_count: int = 0
    blocking_requirements_preview: list[BlockingRequirement] = Field(default_factory=list)
    capabilities_summary: dict[str, object] = Field(default_factory=dict)
    revision: int
    created_at: str
    updated_at: str
    resolve_path: str
    comparison_basis: str
    resolved_alignment_mode: str
    system_managed: bool = True
    catalog_source: str
```

(Remove the `resolved_calendar_source` line — it was never actually used by consumers and was always the hardcoded placeholder.)

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short -x`
Expected: Fewer failures. Catalog API tests may need updating if they reference `resolved_calendar_source`.

- [ ] **Step 3: Commit**

```bash
git add app/api/models/catalog.py
git commit -m "refactor: remove resolved_calendar_source from calendar policy catalog model"
```

---

### Task 8: Add calendar data API endpoint

**Files:**
- Create: `app/api/models/calendar.py`
- Create: `app/api/calendar.py`
- Modify: `app/api/router.py`
- Modify: `app/api/models/__init__.py` (add exports if needed)

- [ ] **Step 1: Write the API models**

Create `app/api/models/calendar.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class CalendarDataLoadRequest(BaseModel):
    calendar_version: str = Field(..., min_length=1, pattern=r"^(?!latest$|current$).+")
    rows: list[CalendarDataRow]


class CalendarDataRow(BaseModel):
    calendar_date: str
    region_code: str = "CN"
    weekday: int = Field(..., ge=1, le=7)
    is_weekend: bool
    is_workday: bool
    holiday_name: str | None = None
    holiday_group_id: str | None = None
    year_relative_holiday_key: str | None = None
    event_group_id: str | None = None
    year_relative_event_key: str | None = None


class CalendarDataLoadResponse(BaseModel):
    status: str
    calendar_version: str
    row_count: int
```

- [ ] **Step 2: Write the API router**

Create `app/api/calendar.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import get_services
from app.api.models.calendar import CalendarDataLoadRequest, CalendarDataLoadResponse

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.post("/data", response_model=CalendarDataLoadResponse)
def load_calendar_data(payload: CalendarDataLoadRequest, request: Request) -> CalendarDataLoadResponse:
    services = get_services(request)
    metadata = services.metadata_store

    existing = metadata.query_one(
        "SELECT 1 FROM marivo.calendar WHERE calendar_version = ? LIMIT 1",
        [payload.calendar_version],
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"calendar_version '{payload.calendar_version}' already exists",
        )

    rows_to_insert = []
    for row in payload.rows:
        rows_to_insert.append(
            (
                row.calendar_date,
                row.region_code,
                payload.calendar_version,
                row.weekday,
                int(row.is_weekend),
                int(row.is_workday),
                row.holiday_name,
                row.holiday_group_id,
                row.year_relative_holiday_key,
                row.event_group_id,
                row.year_relative_event_key,
            )
        )

    metadata.execute(
        """
        INSERT INTO marivo.calendar
            (calendar_date, region_code, calendar_version, weekday,
             is_weekend, is_workday, holiday_name, holiday_group_id,
             year_relative_holiday_key, event_group_id, year_relative_event_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )

    return CalendarDataLoadResponse(
        status="loaded",
        calendar_version=payload.calendar_version,
        row_count=len(rows_to_insert),
    )


@router.get("/versions")
def list_calendar_versions(request: Request) -> list[dict[str, object]]:
    services = get_services(request)
    rows = services.metadata_store.query_rows(
        "SELECT DISTINCT calendar_version, region_code FROM marivo.calendar ORDER BY calendar_version"
    )
    return [dict(row) for row in rows]
```

- [ ] **Step 3: Register the router**

In `app/api/router.py`, add import and router:

```python
from app.api import (
    approvals,
    calendar,
    catalog,
    # ... existing imports ...
)
```

And add `calendar.router` to the router tuple:

```python
def include_api_routers(app: FastAPI) -> None:
    for router in (
        health.router,
        openapi_fragments.router,
        sessions.router,
        sources.router,
        engines.router,
        mappings.router,
        routing.router,
        semantic.router,
        catalog.router,
        governance.router,
        jobs.router,
        approvals.router,
        metrics.router,
        calendar.router,
    ):
        app.include_router(router)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short -x`
Expected: No new failures from API additions.

- [ ] **Step 5: Commit**

```bash
git add app/api/models/calendar.py app/api/calendar.py app/api/router.py app/api/models/__init__.py
git commit -m "feat: add calendar data API endpoint (POST /calendar/data, GET /calendar/versions)"
```

---

### Task 9: Add calendar CLI command

**Files:**
- Create: `app/cli/cmd_calendar.py`
- Modify: `app/cli/__init__.py`

- [ ] **Step 1: Write the CLI command**

Create `app/cli/cmd_calendar.py`:

```python
from __future__ import annotations

import csv
import sys
from typing import Any

from app.cli._exitcodes import EXIT_FAILURE, EXIT_SUCCESS
from app.cli._output import CliError


def add_arguments(parser: Any) -> None:
    subparsers = parser.add_subparsers(dest="calendar_command")

    load_parser = subparsers.add_parser("load", help="Load calendar data from CSV")
    load_parser.add_argument("file", help="CSV file with calendar data")
    load_parser.add_argument(
        "--version", required=True, help="Calendar version (must be unique)"
    )


def handle(args: Any) -> dict[str, Any]:
    if not hasattr(args, "calendar_command") or args.calendar_command is None:
        raise CliError(EXIT_FAILURE, "Usage: marivo calendar load <file> --version <version>")

    if args.calendar_command == "load":
        return _handle_load(args)

    raise CliError(EXIT_FAILURE, f"Unknown calendar command: {args.calendar_command}")


def _handle_load(args: Any) -> dict[str, Any]:
    file_path = args.file
    calendar_version = args.version.strip()

    if not calendar_version or calendar_version.lower() in {"latest", "current"}:
        raise CliError(EXIT_FAILURE, "calendar_version must be an immutable version string")

    try:
        with open(file_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            rows = list(reader)
    except FileNotFoundError:
        raise CliError(EXIT_FAILURE, f"File not found: {file_path}")
    except csv.Error as e:
        raise CliError(EXIT_FAILURE, f"CSV parse error: {e}")

    required_columns = {
        "calendar_date",
        "weekday",
        "is_weekend",
        "is_workday",
    }
    if not rows:
        raise CliError(EXIT_FAILURE, "CSV file is empty")
    missing_columns = required_columns - set(rows[0].keys())
    if missing_columns:
        raise CliError(EXIT_FAILURE, f"CSV missing required columns: {missing_columns}")

    validated_rows = []
    for i, row in enumerate(rows, start=2):
        weekday = int(row.get("weekday", 0))
        if weekday < 1 or weekday > 7:
            raise CliError(EXIT_FAILURE, f"Row {i}: invalid weekday '{weekday}' (must be 1-7)")
        validated_rows.append(
            {
                "calendar_date": row["calendar_date"],
                "region_code": row.get("region_code", "CN"),
                "calendar_version": calendar_version,
                "weekday": weekday,
                "is_weekend": row["is_weekend"],
                "is_workday": row["is_workday"],
                "holiday_name": row.get("holiday_name") or None,
                "holiday_group_id": row.get("holiday_group_id") or None,
                "year_relative_holiday_key": row.get("year_relative_holiday_key") or None,
                "event_group_id": row.get("event_group_id") or None,
                "year_relative_event_key": row.get("year_relative_event_key") or None,
            }
        )

    return {
        "status": "validated",
        "calendar_version": calendar_version,
        "row_count": len(validated_rows),
        "note": "Data validated. Use API POST /calendar/data to load into Marivo DB.",
    }
```

- [ ] **Step 2: Register the CLI subcommand**

In `app/cli/__init__.py`, add import (after existing imports around line 17):

```python
from app.cli.cmd_calendar import add_arguments as calendar_add_arguments
from app.cli.cmd_calendar import handle as calendar_handle
```

And add the subcommand in `_build_parser()` (after the runtime parser, around line 96):

```python
    # marivo calendar (subcommand group)
    calendar_parser = subparsers.add_parser("calendar", help="Manage calendar data")
    calendar_add_arguments(calendar_parser)
    calendar_parser.set_defaults(handler=calendar_handle)
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short -x`
Expected: No new failures.

- [ ] **Step 4: Commit**

```bash
git add app/cli/cmd_calendar.py app/cli/__init__.py
git commit -m "feat: add marivo calendar load CLI command"
```

---

### Task 10: Update build_cn_calendar.py to output CSV

**Files:**
- Modify: `scripts/build_cn_calendar.py`

- [ ] **Step 1: Add CSV output mode**

Add a `--format` argument to the argparse CLI with choices `["duckdb", "csv"]`, default `"csv"`. Add a `--csv-output` argument for the output file path (default: `calendar_data.csv`).

When `--format csv`, write the calendar rows to a CSV file instead of DuckDB. The CSV has these columns:

```
calendar_date,region_code,weekday,is_weekend,is_workday,holiday_name,holiday_group_id,year_relative_holiday_key,event_group_id,year_relative_event_key
```

Note: `calendar_version` is NOT in the CSV — it's provided at load time via `--version` on the CLI or in the API request.

In the `_build_calendar_rows` function and `_write_tables` function, add a branch for CSV output:

```python
def _write_csv(output_path: Path, calendar_rows: list[dict[str, Any]]) -> None:
    import csv

    fieldnames = [
        "calendar_date",
        "region_code",
        "weekday",
        "is_weekend",
        "is_workday",
        "holiday_name",
        "holiday_group_id",
        "year_relative_holiday_key",
        "event_group_id",
        "year_relative_event_key",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in calendar_rows:
            csv_row = {k: row.get(k, "") for k in fieldnames}
            writer.writerow(csv_row)
```

Update `main()` to call `_write_csv` when `--format csv` is selected.

- [ ] **Step 2: Test the CSV output**

Run: `.venv/bin/python scripts/build_cn_calendar.py --format csv --csv-output /tmp/test_calendar.csv`
Verify the output CSV has the expected columns and rows.

- [ ] **Step 3: Commit**

```bash
git add scripts/build_cn_calendar.py
git commit -m "feat: add CSV output mode to build_cn_calendar.py"
```

---

### Task 11: Update marivo.example.yaml

**Files:**
- Modify: `marivo.example.yaml`

- [ ] **Step 1: Update the calendar section**

Replace the commented-out calendar section (lines 41-59) with:

```yaml
# calendar:
#   # All fields optional. Defaults: region_code=CN, calendar_version=auto-discover latest.
#   # calendar_version: "cn_2026q2_v1"  # Uncomment to pin a specific version
#   # region_code: "CN"                  # Uncomment to override default region
```

- [ ] **Step 2: Commit**

```bash
git add marivo.example.yaml
git commit -m "docs: update marivo.example.yaml calendar section"
```

---

### Task 12: Fix remaining test failures and run full suite

**Files:**
- Various test files that reference old calendar API

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Note all remaining failures.

- [ ] **Step 2: Fix each failure**

Typical remaining failures will be:
- Tests that reference `holiday_yoy`/`event_yoy`/`event_mom` policy refs — update to `calendar_yoy`/`calendar_mom`
- Tests that check `source_lineage` dict structure with `holiday_source`/`event_source` keys — update to flat `table_fqn`/`calendar_version`
- Tests that construct old `CalendarConfig` with `snapshots` key — update to new format

Fix each one by updating the test to match the new API.

- [ ] **Step 3: Run full test suite again**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: update remaining tests for calendar redesign"
```

---

### Task 13: Update existing spec documents to reflect redesign

**Files:**
- Modify: `spec/semantic/calendar-data-contract.zh.md` — update schema to single table
- Modify: `spec/semantic/calendar-version-freeze-policy.zh.md` — simplify version model
- Modify: `spec/semantic/calendar-data-v1-source-note.zh.md` — remove source dependency
- Modify: `spec/semantic/calendar-alignment-policy.zh.md` — update policy catalog

- [ ] **Step 1: Add deprecation notices to existing specs**

At the top of each affected spec file, add a note:

```markdown
> **Superseded** by `docs/superpowers/specs/2026-04-29-calendar-data-policy-redesign-design.md`.
> This document describes the pre-redesign architecture and is kept for historical reference.
```

- [ ] **Step 2: Commit**

```bash
git add spec/semantic/
git commit -m "docs: add deprecation notices to pre-redesign calendar specs"
```

---

### Task 14: Final verification

- [ ] **Step 1: Run make test**

Run: `make test`
Expected: All tests pass.

- [ ] **Step 2: Run make typecheck**

Run: `make typecheck`
Expected: No type errors.

- [ ] **Step 3: Run make lint**

Run: `make lint`
Expected: No lint errors.

- [ ] **Step 4: Commit any remaining fixes**

```bash
git add -A
git commit -m "fix: resolve final typecheck/lint issues from calendar redesign"
```
