# Calendar Data Simplification Design

**Date**: 2026-05-16
**Status**: Approved

## Motivation

Current calendar data storage uses a `(calendar_version, region_code, calendar_date)` composite primary key with one holiday per row. This introduces unnecessary complexity: version lifecycle management (MAX queries, pinning, 409 conflict detection), region code overhead for a single-region dataset, and inability to represent dates with multiple holidays (e.g., 2024-10-01 being both National Day and Mid-Autumn Festival).

The redesign removes version and region, switches to replacement-style loading, and supports multiple holidays per date via multi-row storage.

## Decisions

1. **Region scope**: Implicit multi-region — no `region_code` field, data structure doesn't restrict region, but current dataset is CN only.
2. **Version model**: Single-replace — each load replaces all existing calendar data, no version history.
3. **Compare types**: All 7 types retained unchanged (normal, yoy, mom, wow, holiday_aligned_yoy, weekday_aligned_yoy, weekday_aligned_mom).
4. **Multi-holiday storage**: Wide multi-row model — each row is `(calendar_date, holiday_group_id)`, same date multiple holidays = multiple rows.

## Database Layer

### New table structure

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

### Changes from current schema

- Removed `calendar_version` and `region_code` columns
- PK simplified from `(calendar_version, region_code, calendar_date)` to `(calendar_date, holiday_group_id)`
- `holiday_group_id` defaults to empty string `''` — non-holiday rows use this sentinel
- Same date with multiple holidays produces multiple rows with different `holiday_group_id` values (e.g., `national_day`, `mid_autumn`)
- Loading semantics changed to PUT: `DELETE FROM calendar` then bulk INSERT, no 409 logic
- Removed `idx_calendar_version_region` index, replaced with `idx_calendar_date` single-column index for date range queries

## Core Semantic Layer (calendar.py)

### Removed / simplified types

- `CalendarAnnotationRow` — remove version/region fields, add `extra_holiday_group_ids: list[str]` for multi-holiday dates
- `CalendarDataReadResult` — remove `resolved_calendar_source`, `resolved_calendar_version`; simplify `source_lineage` to `{table: "calendar"}`

### Unchanged

- All 7 `CompareType` values and `CalendarAlignmentPlan` mappings
- All 6 `CalendarPolicyDefinition` entries
- `CalendarBaselineGenerationRule`, `CalendarMatchingStep`, `CalendarPairingResolution`
- `CalendarPolicyRef` enumeration
- `CalendarPolicyCatalogEntry` metadata

### Adjusted

- `CalendarAnnotationRow` construction from DB rows: same-date multi-row entries (different `holiday_group_id`) merge into a single annotation row. Merge strategy: first non-empty `holiday_group_id` and `year_relative_holiday_key` as primary annotation, remaining `holiday_group_id` values stored in `extra_holiday_group_ids`.
- `holiday_cluster` matcher and `year_relative_holiday_key` matcher must check all `holiday_group_id` values (primary + extras) when pairing.

## Runtime + HTTP + Config + CLI

### CalendarDataReader (runtime)

- Remove version resolution logic (no `MAX(calendar_version)` query)
- Remove `region_code` dependency
- `read_for_alignment()` queries by date range directly
- Return simplified `CalendarDataReadResult`

### CalendarConfig

- Remove `region_code` and `calendar_version` fields
- Remove version validation logic (no "latest"/"current" rejection)
- Remove `CalendarConfig` entirely from `MarivoConfig` — after removing `region_code` and `calendar_version`, the class has no fields left. The `calendar` key on `MarivoConfig` is removed. `CalendarDataReader` no longer receives config-derived parameters.

### HTTP API

- `POST /calendar/data` → `PUT /calendar/data` (replacement semantics)
  - `CalendarDataLoadRequest` removes `calendar_version`
  - `CalendarDataLoadResponse` removes `calendar_version`, retains `status` + `row_count`
  - Remove 409 "version already exists" logic
- `GET /calendar/versions` → **removed entirely**
- `CalendarDataRow` removes `region_code`

### CLI

- `marivo calendar load` removes `--version` parameter
- Retain CSV validation logic (required columns: calendar_date, weekday, is_weekend, is_workday)

### build_cn_calendar.py

- Output adapts to new format: each date may have multiple rows (multiple holidays)
- Remove version/region output columns
- SQL INSERT file adapts to new table structure

## Tests

### Updated tests

- `test_calendar_config.py` — simplify or remove (version validation, region tests no longer relevant)
- `test_calendar_data_runtime.py` — remove pinned/latest version discovery/version missing tests; adjust read logic tests
- `test_calendar_alignment_pairing.py` — adjust annotation row construction; add multi-holiday pairing tests
- `test_build_cn_calendar.py` — adapt to new output format

### Unchanged tests

- `test_calendar_alignment_baseline.py` — baseline logic doesn't touch calendar data reading
- `test_calendar_policy_registry.py` — policy registry doesn't involve version/region

### New tests

- Multi-holiday annotation row merge logic
- PUT replacement-style loading HTTP endpoint test
- Same-date dual-holiday (National Day + Mid-Autumn) pairing behavior

## Approach

Pure flat-table approach (Method A) — single `calendar` table, PK `(calendar_date, holiday_group_id)`, empty-string sentinel for non-holiday rows. Maximum code deletion, simplest queries, no version/region overhead.
