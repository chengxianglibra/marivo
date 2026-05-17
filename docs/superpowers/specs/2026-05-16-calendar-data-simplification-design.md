# Calendar Data Simplification Design

**Date**: 2026-05-16
**Status**: Approved

## Motivation

Calendar alignment only needs holiday annotations. Weekday and weekend facts are deterministic functions of `calendar_date`, so storing dense daily rows adds redundant data and makes every generated dataset larger than necessary.

## Decisions

- Calendar data is sparse: rows represent `holiday` or `adjusted_workday` dates only.
- Weekday and weekend facts are derived at runtime from `calendar_date`.
- The metadata `calendar` table has no version, region, weekday, weekend, or workday columns.
- The repository no longer ships or maintains a built-in CN calendar generator; callers provide holiday data through `PUT /calendar/data`.

## Data Shape

```sql
CREATE TABLE IF NOT EXISTS calendar (
    calendar_date              TEXT NOT NULL,
    holiday_group_id           TEXT NOT NULL DEFAULT '',
    day_kind                   TEXT NOT NULL CHECK (day_kind IN ('holiday', 'adjusted_workday')),
    holiday_name               TEXT,
    year_relative_holiday_key  TEXT,
    PRIMARY KEY (calendar_date, day_kind, holiday_group_id)
)
```

Multiple holidays on one date are represented as multiple `holiday` rows with different `holiday_group_id` values. Ordinary non-holiday dates are not stored.

## Runtime Behavior

- `CalendarDataReader` reads sparse rows by date range and returns `{table: "calendar"}` lineage.
- `build_calendar_annotation_rows()` fills missing dates and derives `weekday = calendar_date.weekday() + 1`.
- `weekday_aligned` never reads calendar data.
- `holiday_aligned` and `holiday_and_weekday_aligned` read sparse holiday rows, then fall back according to the existing compare policy when holiday keys do not match.
