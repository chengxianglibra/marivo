# Calendar Data Sparse Holiday Plan

**Date**: 2026-05-16
**Status**: Implemented plan

## Summary

Simplify calendar data to sparse holiday/adjusted-workday rows. Remove the CN calendar generator and dense SQL seed. Derive weekday and weekend information from concrete dates at runtime.

## Implementation Checklist

- Delete the built-in CN calendar generator, its direct test, and the old dense seed SQL file.
- Update metadata DDL so `calendar` stores `calendar_date`, `day_kind`, `holiday_group_id`, `holiday_name`, and `year_relative_holiday_key`.
- Update `PUT /calendar/data` request handling to accept sparse rows only.
- Update `CalendarDataReader` and core annotation construction so missing dates are filled with runtime-derived weekday rows.
- Update current docs and OpenAPI types to remove version/region/dense-row language.

## Verification

- `make test TESTS='tests/core/test_calendar.py tests/runtime/semantic/test_calendar_data_runtime.py tests/transports/http/test_intent_api.py'`
- Run targeted stale-reference searches across active code and docs.
