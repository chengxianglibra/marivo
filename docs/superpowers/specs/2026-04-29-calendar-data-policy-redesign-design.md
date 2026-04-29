# Calendar Data/Policy Redesign

Date: 2026-04-29

## Problem

Current calendar implementation has maintenance issues:

1. **Version duplication**: `resolved_calendar_version` and `holiday_source.calendar_version` are redundant. In single-source scenarios they are identical; in multi-source assembly scenarios the relationship is implicit and manually maintained.
2. **Source coupling**: Calendar config requires pre-registered sources via `source_name`, coupling calendar lifecycle to the source system.
3. **Dead fields**: `CalendarPolicyDefinition.resolved_calendar_source` is hardcoded to `"calendar_data.v1"` and never used at runtime.
4. **Over-complex config**: Snapshot list, dual-level versions, effective date ranges, and source bindings for what is essentially simple lookup data.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Calendar consumption | Internal only | Only compiler/alignment reads calendar data |
| Source dependency | Independent | Calendar is lightweight data reference, not a full source |
| Storage | Single wide table in Marivo DB | No assembly, no multi-table JOIN, fixed schema |
| Version model | Single version per table row | Eliminates resolved vs component version duplication |
| Config shape | All-optional with defaults | Fixed table name + auto-discover latest version + default region |
| Snapshot list | Removed | Single active reference, replace config on version switch |
| Policy count | 7 (from 8) | `calendar_aware` alignment subsumes `holiday_yoy` + `event_yoy` |
| Data maintenance | CLI + API | CLI for dev/ops, API for automation |

## Configuration Model

```yaml
calendar: {}
```

All fields optional with sensible defaults:

| Field | Default | Description |
|-------|---------|-------------|
| `region_code` | `"CN"` | Region filter |
| `calendar_version` | Auto-discovered | Pin to specific version; omit to use latest |

At observation time, the system resolves:
- `resolved_calendar_source` = `"marivo.calendar"` (fixed)
- `resolved_calendar_version` = config value OR `SELECT MAX(calendar_version) FROM marivo.calendar WHERE region_code = ?`

The resolved version is frozen into the observation artifact. The comparability gate checks frozen versions across observations.

### Migration from current config

| Old field | New handling |
|-----------|-------------|
| `snapshots[]` | Removed. Single active reference only. |
| `resolved_calendar_source` | Fixed to `"marivo.calendar"` |
| `resolved_calendar_version` | Auto-discovered or optionally pinned |
| `effective_start/end` | Removed. Version switch = config change. |
| `holiday_source` | Removed. Data in single table. |
| `event_source` | Removed. Event fields nullable in single table. |
| `source_name` | Removed. No source registration dependency. |
| `table_fqn` | Fixed to `marivo.calendar`. |

## Data Model

Table: `marivo.calendar`

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `calendar_date` | DATE | No | Natural day |
| `region_code` | VARCHAR | No | Region code (CN) |
| `calendar_version` | VARCHAR | No | Version anchor |
| `weekday` | TINYINT | No | 1-7 (Mon-Sun) |
| `is_weekend` | BOOLEAN | No | |
| `is_workday` | BOOLEAN | No | Includes makeup workdays |
| `holiday_name` | VARCHAR | Yes | Human-readable label |
| `holiday_group_id` | VARCHAR | Yes | Stable cluster key (e.g., `spring_festival`) |
| `year_relative_holiday_key` | VARCHAR | Yes | Offset within cluster (e.g., `spring_festival_d+0`) |
| `event_group_id` | VARCHAR | Yes | Event window ID (e.g., `618_promo`) |
| `year_relative_event_key` | VARCHAR | Yes | Offset within window (e.g., `618_promo_d-3`) |

Primary key: `(calendar_version, region_code, calendar_date)`

Rows without holiday/event annotations have null values in the corresponding fields.

## Policy Model

7 policies (down from 8):

| Policy Ref | Basis | Alignment | Matching Strategy |
|-----------|-------|-----------|-------------------|
| `natural_yoy` | YoY | natural | natural_date_shift |
| `weekday_yoy` | YoY | weekday | same_weekday -> natural |
| `calendar_yoy` | YoY | calendar_aware | event_cluster -> holiday_cluster -> same_weekday -> natural |
| `natural_mom` | MoM | natural | natural_date_shift |
| `weekday_mom` | MoM | weekday | same_weekday -> natural |
| `calendar_mom` | MoM | calendar_aware | event_cluster -> holiday_cluster -> same_weekday -> natural |
| `weekday_wow` | WoW | weekday | same_weekday -> natural |

### calendar_aware cascading logic

The `calendar_aware` alignment mode tries matchers in priority order, skipping any that lack data for the given date:

1. **event_cluster**: Match on `event_group_id` + `year_relative_event_key`. Skip if both dates have null event fields.
2. **holiday_cluster**: Match on `holiday_group_id` + `year_relative_holiday_key`. Skip if both dates have null holiday fields.
3. **same_weekday**: Match on same weekday index. Always available.
4. **natural_date_shift**: Simple date arithmetic. Always available.

This subsumes the old `holiday_yoy` and `event_yoy` policies into a single `calendar_yoy`, and `event_mom` into `calendar_mom`.

### CalendarPolicyDefinition cleanup

- Remove `resolved_calendar_source` field (was hardcoded to `"calendar_data.v1"`, never used at runtime).
- Runtime resolves `resolved_calendar_source` from config automatically.

## Data Maintenance

### CLI

```bash
marivo calendar load <file.csv>
```

Reads CSV, validates schema, inserts into `marivo.calendar`.

### API

```
POST /api/v1/calendar/data
Content-Type: text/csv or application/json
```

Accepts CSV or JSON payload, validates, writes to table.

### Version management

- `calendar_version` is required in the data payload (every row must have it).
- Loading an already-existing version is an error (immutable).
- No delete or update API (freeze policy).

### Schema validation on load

- Field completeness: all required columns present
- Type correctness: date, integer, boolean, string
- PK uniqueness: `(calendar_version, region_code, calendar_date)` unique
- `weekday` range: 1-7
- `is_workday`/`is_weekend` logical consistency (a day can be weekend and workday due to makeup days, but not non-weekend and non-workday unless holiday)

### Build script

`build_cn_calendar.py` continues to generate calendar data. Output format changes from direct DB insertion to CSV, which users then load via CLI or API.

## Runtime Data Flow

```
1. Read config -> calendar: {} -> use defaults
2. Connect to Marivo DB -> query marivo.calendar
   - calendar_version: pinned OR SELECT MAX(calendar_version)
   - region_code: config OR "CN"
3. Read matching rows -> WHERE calendar_version = ? AND region_code = ?
4. Freeze to observation artifact:
   - resolved_calendar_source: "marivo.calendar"
   - resolved_calendar_version: <actual version read>
   - source_lineage: <table + version>
5. Comparability gate checks frozen versions across observations
```

### Removed runtime components

- `_build_source_binding()`: No longer queries metadata DB for source registration
- `_assemble_rows()`: No longer merges holiday + event tables
- Source lifecycle dependency: Calendar reads directly from Marivo DB

### Unchanged components

- Comparability gate: Still checks `resolved_calendar_source` and `resolved_calendar_version` consistency
- Observation artifact versioning: Still freezes resolved version
- Bucket pairing logic: Same matching strategies, just fewer policy entries

## Scope

This redesign covers:
- Config schema simplification
- Data model unification (single table)
- Policy consolidation (8 -> 7)
- Data maintenance API (CLI + HTTP)
- Runtime reader simplification

Out of scope:
- New alignment strategies beyond the existing set
- Calendar data UI
- Multi-region support (future: add `calendars:` list)
- Calendar version lifecycle management beyond load + freeze
