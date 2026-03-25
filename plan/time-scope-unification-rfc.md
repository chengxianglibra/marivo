# RFC: Unified Time-Scope Interface For `aggregate_query` And `compare_metric`

> Status: Draft
> Date: 2026-03-25
> Scope: minimal RFC + implementation todo list

---

## 1. Summary

This RFC proposes a clean-slate redesign of the `aggregate_query` and `compare_metric` step interfaces.

The redesign has one main idea:

- both steps should use the same typed `time_scope` contract
- both steps should use the same internal time-resolution and compare compilation path
- the only real difference between them should be:
  - `compare_metric`: compare a published semantic metric
  - `aggregate_query`: compare user-specified aggregates

This RFC explicitly does **not** preserve backward compatibility. The goal is the best interface, not a least-change migration.

---

## 2. Problem

Current problems in the existing design:

1. `compare_metric` and `aggregate_query(compare_period=true)` both represent "windowed compare", but expose different contracts
2. both implementations are effectively day-granularity only
3. `aggregate_query` mixes free-form `where` with compare-window semantics
4. partition-constrained engines require pruning predicates that may not match the true analysis-time column
5. Factum must support three real table shapes:
   - partition-only time fields such as `log_date` / `log_hour`
   - timestamp-only fields such as `event_time`
   - mixed timestamp + partition fields

The result is duplicated logic, weak hour support, and no consistent way to express "compare any arbitrary time window" in typed step form.

---

## 3. Goals

The new design must:

1. support arbitrary current/baseline windows at `day` and `hour` grain
2. work for partition-only, timestamp-only, and mixed time layouts
3. separate:
   - analysis-time semantics
   - engine pruning semantics
4. keep time comparison out of free-form SQL predicates
5. align `aggregate_query` and `compare_metric` as much as possible
6. preserve typed-step design rules

Non-goals:

- minute/second-grain compare in this phase
- supporting arbitrary user-written SQL as the external compare contract
- keeping old fields such as `compare_period`, `period_start`, `date_column`, `where`

---

## 4. Design Principles

### 4.1 One compare model

Factum should have exactly one window-comparison model:

- a typed `time_scope`
- a typed `scope`
- one shared internal compare compiler

### 4.2 Separate correctness from pruning

Comparison semantics and partition pruning are not the same thing.

The system must compile each compare step into:

1. `analysis_time_expr`
   Determines whether a row belongs to current/baseline.

2. `partition_pruning_predicate`
   Satisfies engine partition constraints and limits scan cost.

Rule:

- `analysis_time_expr` controls correctness
- `partition_pruning_predicate` controls executability and cost

### 4.3 No time predicates inside free-form filters

Time windows must be expressed only through `time_scope`.

This avoids:

- silently truncating baseline/current windows
- duplicating time logic across params
- conflicting semantics between explicit windows and ad-hoc predicates

### 4.4 Same shape, different payload

The two step interfaces should look structurally similar:

- both accept `table`
- both accept `time_scope`
- both accept `scope`
- both can run in `single_window` or `compare` mode

Only the value-definition part differs:

- `compare_metric`: semantic metric name
- `aggregate_query`: aggregate expressions

---

## 5. Proposed New Interfaces

## 5.1 Shared sub-objects

### `time_scope`

```json
{
  "mode": "single_window | compare",
  "grain": "day | hour",
  "current": {
    "start": "2026-03-25T10:00:00",
    "end": "2026-03-25T14:00:00"
  },
  "baseline": {
    "start": "2026-03-25T06:00:00",
    "end": "2026-03-25T10:00:00"
  }
}
```

Rules:

- all windows are half-open: `[start, end)`
- `baseline` is required only when `mode = compare`
- `grain = day` still uses the same shape; values may be date-only strings
- `grain = hour` requires datetime boundaries

### `scope`

```json
{
  "constraints": {
    "cluster": "k8soneservice-oneservice",
    "user": "sys_titan"
  },
  "predicate": "query_state = 'FAILED'"
}
```

Rules:

- `constraints` is equality-style typed scoping
- `predicate` is for non-time row predicates only
- time conditions inside `predicate` are invalid

### `time_axis`

```json
{
  "analysis_time": {
    "column": "event_time"
  },
  "partition_pruning": {
    "date_column": "log_date",
    "hour_column": "log_hour"
  }
}
```

Rules:

- optional in the request
- if omitted, Factum resolves it from entity/source metadata or heuristics
- users can override it when metadata is missing or ambiguous

This is an advanced override, not the default user entry point.

---

## 5.2 `compare_metric`

### Request

```json
{
  "table": "iceberg.iceberg_inf.ods_trino_query_info",
  "metric": "avg_queued_time_ms",
  "dimensions": ["resource_group"],
  "time_scope": {
    "mode": "compare",
    "grain": "hour",
    "current": {
      "start": "2026-03-25T10:00:00",
      "end": "2026-03-25T14:00:00"
    },
    "baseline": {
      "start": "2026-03-25T06:00:00",
      "end": "2026-03-25T10:00:00"
    }
  },
  "scope": {
    "constraints": {
      "cluster": "k8soneservice-oneservice",
      "user": "sys_titan"
    },
    "predicate": "query_state = 'FAILED'"
  },
  "time_axis": {
    "analysis_time": {
      "column": "event_time"
    },
    "partition_pruning": {
      "date_column": "log_date",
      "hour_column": "log_hour"
    }
  },
  "order": "delta_pct DESC",
  "limit": 50
}
```

### Semantics

- compares one published semantic metric across one or more dimensions
- uses `time_scope` as the only time comparison contract
- does not accept arbitrary step-level `where` / `filter`

### Required fields

- `table`
- `metric`
- `time_scope`

### Optional fields

- `dimensions`
- `scope`
- `time_axis`
- `order`
- `limit`

---

## 5.3 `aggregate_query`

### Request

```json
{
  "table": "iceberg.iceberg_inf.ods_trino_query_info",
  "group_by": ["resource_group"],
  "measures": [
    {"expr": "COUNT(*)", "as": "query_count"},
    {"expr": "AVG(queued_time_ms)", "as": "avg_queued_time_ms"}
  ],
  "time_scope": {
    "mode": "compare",
    "grain": "hour",
    "current": {
      "start": "2026-03-25T10:00:00",
      "end": "2026-03-25T14:00:00"
    },
    "baseline": {
      "start": "2026-03-25T06:00:00",
      "end": "2026-03-25T10:00:00"
    }
  },
  "scope": {
    "constraints": {
      "cluster": "k8soneservice-oneservice",
      "user": "sys_titan"
    },
    "predicate": "query_state = 'FAILED'"
  },
  "time_axis": {
    "analysis_time": {
      "column": "event_time"
    },
    "partition_pruning": {
      "date_column": "log_date",
      "hour_column": "log_hour"
    }
  },
  "order": "query_count_delta_pct DESC",
  "limit": 50
}
```

### Semantics

- compares one or more aggregate measures across one or more dimensions
- uses the same `time_scope` and `scope` contract as `compare_metric`
- no `compare_period` flag
- no `where`
- no mixed time semantics hidden in free-form SQL

### Required fields

- `table`
- `measures`
- `time_scope`

### Optional fields

- `group_by`
- `scope`
- `time_axis`
- `order`
- `limit`

---

## 5.4 Interface alignment

The two step interfaces should intentionally align like this:

| Concept | `compare_metric` | `aggregate_query` |
|---|---|---|
| Table | `table` | `table` |
| Time window | `time_scope` | `time_scope` |
| Entity/row scope | `scope` | `scope` |
| Time override | `time_axis` | `time_axis` |
| Grouping | `dimensions` | `group_by` |
| Value definition | `metric` | `measures` |
| Ordering | `order` | `order` |
| Limit | `limit` | `limit` |

The intent is that an agent or user can learn one mental model and apply it to both steps.

---

## 6. Internal Model

Internally, both requests should normalize into one resolved object:

```json
{
  "table": "iceberg.iceberg_inf.ods_trino_query_info",
  "compare_kind": "semantic_metric | ad_hoc_aggregate",
  "grouping": ["resource_group"],
  "value_spec": {
    "metric": "avg_queued_time_ms",
    "measures": [
      {"expr": "COUNT(*)", "as": "query_count"}
    ]
  },
  "time_scope": {
    "mode": "compare",
    "grain": "hour",
    "current": {"start": "...", "end": "..."},
    "baseline": {"start": "...", "end": "..."}
  },
  "scope": {
    "constraints": {"cluster": "k8soneservice-oneservice"},
    "predicate": "query_state = 'FAILED'"
  },
  "resolved_time_axis": {
    "analysis_time_kind": "timestamp | partition_fields | date_field",
    "analysis_time_expr": "...",
    "partition_pruning_predicate": "...",
    "observation_grain": "hour"
  }
}
```

This is the true shared contract between the service layer and compiler.

---

## 7. Entity Time Layout Cases

## 7.1 Case A: partition-only fields

Example:

- `log_date` = `20260325`
- `log_hour` = `13`
- no timestamp column

Design:

- `analysis_time_expr` is derived from partition fields
- `partition_pruning_predicate` uses the same fields directly

Examples:

- day grain:
  - `analysis_time_expr = log_date`
- hour grain:
  - `analysis_time_expr = combine(log_date, log_hour)` via engine-specific expression

For hour compare:

- pruning must always include `log_date`
- if `hour_column` exists, add bounded hour pruning on edge days

Consequence:

- Factum must not require a native timestamp column for hour compare

## 7.2 Case B: timestamp-only field

Example:

- `event_time`
- no `log_date` / `log_hour`

Design:

- `analysis_time_expr = event_time`
- no explicit partition pruning predicate is required

Consequence:

- correctness comes entirely from timestamp windowing

## 7.3 Case C: timestamp + partition fields

Example:

- `event_time`
- `log_date`
- `log_hour`

Design rule:

- compare semantics use the timestamp field
- partition pruning uses the partition fields

Consequence:

- correctness is preserved
- Trino/Iceberg-style partition requirements can still be satisfied

This is the default policy when both are available.

---

## 8. Resolution Rules

## 8.1 Resolve time axis

When `time_axis` is omitted:

1. prefer metadata-declared timestamp analysis time
2. else prefer metadata-declared partition field pair for hour grain
3. else fall back to heuristics from known names

Default heuristic priority:

- timestamp candidates: `event_time`, `timestamp`, `created_at`, `updated_at`, `time`
- day partition candidates: `log_date`, `event_date`, `dt`, `date`, `day`
- hour partition candidates: `log_hour`, `event_hour`, `hour`, `dt_hour`

## 8.2 Resolve compare windows

Rules:

- `mode = single_window`: only `current` is present
- `mode = compare`: both `current` and `baseline` are required
- all windows normalize to half-open intervals `[start, end)`
- `grain = day` means date buckets and day-window observations
- `grain = hour` means hour buckets and hour-window observations

## 8.3 Resolve observation windows

Observation windows must follow the resolved compare grain:

- compare by day -> emitted observations use `granularity = "day"`
- compare by hour -> emitted observations use `granularity = "hour"`

This applies to both `compare_metric` and compare-style `aggregate_query`.

---

## 9. Validation Rules

The new interface should enforce:

1. `current.start < current.end`
2. `baseline.start < baseline.end` when compare mode is used
3. `grain = hour` requires datetime-compatible boundaries
4. `scope.predicate` must not contain time-axis predicates
5. `aggregate_query.measures` must be aggregate expressions with explicit aliases
6. if both timestamp and partition fields exist, timestamp is the default analysis time unless `time_axis` overrides it
7. if only partition fields exist, hour compare requires both date and hour fields or an engine-supported derived hour expression

---

## 10. Shared SQL Compilation Pattern

Both step types should compile through one periodization pattern:

```sql
WITH scoped AS (
  SELECT
    CASE
      WHEN {analysis_time_expr} >= ? AND {analysis_time_expr} < ? THEN 'current'
      WHEN {analysis_time_expr} >= ? AND {analysis_time_expr} < ? THEN 'baseline'
    END AS _period,
    *
  FROM {table}
  WHERE
    (
      ({analysis_time_expr} >= ? AND {analysis_time_expr} < ?)
      OR
      ({analysis_time_expr} >= ? AND {analysis_time_expr} < ?)
    )
    AND {partition_pruning_predicate_if_any}
    AND {scope_constraints_if_any}
    AND {scope_predicate_if_any}
)
```

Then:

- `compare_metric` runs semantic metric aggregation over `scoped`
- `aggregate_query` runs ad-hoc grouped aggregate expressions over `scoped`

For single-window mode, the same compilation skeleton can be simplified to a single window without `_period`.

---

## 11. Required SQL Examples

## 11.1 Partition-only, hour compare

Entity fields:

- `log_date` (`YYYYMMDD`)
- `log_hour` (`HH`)

Window:

- current: `[2026-03-25T10:00:00, 2026-03-25T14:00:00)`
- baseline: `[2026-03-25T06:00:00, 2026-03-25T10:00:00)`

Compiler must produce:

- analysis expression derived from `log_date + log_hour`
- pruning including `log_date = '20260325'`
- hour pruning using `log_hour >= '06' AND log_hour < '14'`

## 11.2 Timestamp-only, hour compare

Entity fields:

- `event_time`

Compiler must use:

- `event_time >= ? AND event_time < ?`

No partition predicate required.

## 11.3 Timestamp + partition fields

Entity fields:

- `event_time`
- `log_date`
- `log_hour`

Compiler must use:

- compare semantics on `event_time`
- pruning on `log_date` / `log_hour`

---

## 12. Metadata Requirements

To avoid over-relying on field-name heuristics, metadata should describe time capability explicitly.

Minimal metadata target:

```json
{
  "time_capabilities": {
    "analysis_time": {
      "timestamp_column": "event_time",
      "fallback_date_column": "log_date",
      "fallback_hour_column": "log_hour"
    },
    "partition_time": {
      "date_column": "log_date",
      "date_format": "yyyymmdd",
      "hour_column": "log_hour",
      "hour_format": "hh"
    },
    "default_compare_grain": "day"
  }
}
```

Preferred resolution order:

1. metadata
2. heuristic fallback

---

## 13. Open Questions

1. Should `time_axis` be exposed immediately or only as an advanced override after metadata is stable?
2. Should `scope.predicate` stay as raw SQL, or be replaced later by a typed predicate AST?
3. Where should `time_capabilities` live first:
   - semantic entity properties
   - source object properties
   - both
4. Should timezone be explicit per analysis-time column in phase 1, or do we require session-consistent naive timestamps only?

---

## 14. Recommended Implementation Order

1. define the new request/response contract
2. add shared `TimeScope`, `Scope`, and `TimeAxis` models
3. implement shared time-axis resolution
4. implement shared compare compilation
5. refactor `aggregate_query` to the new interface
6. refactor `compare_metric` to the new interface
7. update tests and docs

---

## 15. TODO List

## P0: API and models

- [ ] Redefine `aggregate_query` request model around:
  - [ ] `table`
  - [ ] `group_by`
  - [ ] `measures`
  - [ ] `time_scope`
  - [ ] `scope`
  - [ ] `time_axis`
- [ ] Redefine `compare_metric` request model around:
  - [ ] `table`
  - [ ] `metric`
  - [ ] `dimensions`
  - [ ] `time_scope`
  - [ ] `scope`
  - [ ] `time_axis`
- [ ] Remove legacy params from API models and service logic:
  - [ ] `period_start`
  - [ ] `period_end`
  - [ ] `baseline_start`
  - [ ] `baseline_end`
  - [ ] `date_column`
  - [ ] `compare_period`
  - [ ] `where`
  - [ ] `filter`

## P0: shared time resolution

- [ ] Add `TimeScope` / `ResolvedTimeScope` types
- [ ] Add `TimeAxisOverride` / `ResolvedTimeAxis` types
- [ ] Add `TimeScopeResolver`
- [ ] Add `TimeAxisResolver`
- [ ] Normalize all compare windows to `[start, end)`
- [ ] Compute:
  - [ ] `analysis_time_expr`
  - [ ] `partition_pruning_predicate`
  - [ ] `observation_grain`

## P0: shared compare compiler

- [ ] Add one shared compiler path for compare-mode steps
- [ ] Add one shared compiler path for single-window steps
- [ ] Reuse shared scoped/periodized CTE generation across both step types
- [ ] Ensure both step types emit correct observation windows for `day` and `hour`

## P0: `aggregate_query`

- [ ] Replace `select` with typed `measures`
- [ ] Require aliases for all measures
- [ ] Support single-window aggregation via `time_scope.mode = single_window`
- [ ] Support compare aggregation via `time_scope.mode = compare`
- [ ] Reject time predicates inside `scope.predicate`

## P0: `compare_metric`

- [ ] Replace old period/date params with `time_scope`
- [ ] Replace old filter behavior with `scope`
- [ ] Support both `day` and `hour` compare windows
- [ ] Keep semantic metric resolution unchanged apart from the new interface

## P1: entity/source time capability resolution

- [ ] Define minimal metadata schema for time capability hints
- [ ] Prefer metadata over name heuristics
- [ ] Add fallback heuristics for:
  - [ ] partition-only entities
  - [ ] timestamp-only entities
  - [ ] mixed entities

## P1: engine-specific pruning

- [ ] Implement pruning derivation for `log_date` only
- [ ] Implement pruning derivation for `log_date + log_hour`
- [ ] Ensure mixed timestamp/partition entities use timestamp for semantics and partition fields for pruning
- [ ] Add explicit tests for Trino/Iceberg-style mandatory partition-filter behavior

## P1: tests

- [ ] Add unit tests for `TimeScopeResolver`
- [ ] Add unit tests for `TimeAxisResolver`
- [ ] Add compiler tests for all three entity time layouts
- [ ] Add tests for `compare_metric` hour compare on:
  - [ ] partition-only entity
  - [ ] timestamp-only entity
  - [ ] mixed entity
- [ ] Add tests for `aggregate_query` hour compare on:
  - [ ] partition-only entity
  - [ ] timestamp-only entity
  - [ ] mixed entity
- [ ] Add validation tests proving time predicates in `scope.predicate` are rejected
- [ ] Add observation-window tests proving emitted `granularity` is correct

## P2: docs

- [ ] Update [`docs/api/sessions.md`](/Users/lichengxiang/source/oss/factum/docs/api/sessions.md) to the new contract
- [ ] Update [`docs/agent-guide.md`](/Users/lichengxiang/source/oss/factum/docs/agent-guide.md) after behavior changes land
- [ ] Update any plan/skill/docs that describe old compare params

---

## 16. Recommendation

The cleanest design is:

1. make `time_scope` the only time-window contract
2. make `scope` the only non-time scoping contract
3. make `time_axis` an advanced override for analysis-time/pruning selection
4. align `aggregate_query` and `compare_metric` around the same request shape
5. share one internal resolution and compilation path

This design directly solves the real requirements:

- arbitrary compare windows
- hour-level support
- partition-only, timestamp-only, and mixed entities
- typed semantics without hidden SQL contracts
