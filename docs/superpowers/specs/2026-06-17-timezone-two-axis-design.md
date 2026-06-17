# Timezone: Two-Axis Model (Read tz + Report tz)

Date: 2026-06-17

Status: draft (v2 — revised after design/code review)

Supersedes the single-timezone model in
`docs/specs/analysis/timezone-and-calendar-design.md`. That spec is the current
source of truth; this design reverses three of its deliberate decisions (see
Problem) and must replace it when implemented.

**No backwards compatibility or data migration.** Existing sessions, session
meta, and cached datasource metadata are not migrated. Behavior here applies to
state created under this model; pre-existing on-disk state is replaced, not
upgraded — matching the prior timezone spec's own "不考虑兼容与数据迁移" stance.

v2 changelog (after review against HEAD): corrected the session API name
(`get_or_create`, not `session.start`); defined report-tz persistence and
reopen-conflict semantics; specified a concrete datasource read-tz data model
and executor lookup path; dropped `epoch_seconds` (currently deferred); kept
naive session-local output and the existing fixed-offset bucketing baseline,
moving native per-dialect tz truncation and tz-aware output to a separate future
phase.

## Problem

The current model (`docs/specs/analysis/timezone-and-calendar-design.md`) fuses
everything into **one** timezone: the system tz of the machine running the
Python process. It serves simultaneously as the read tz (how naive columns are
interpreted) and the report tz (how results are presented). Its own §已知取舍
admits the headline cost: **cross-machine non-reproducibility**.

Three concrete weaknesses follow:

1. **Read and report are conflated.** A real analysis often needs to *read* data
   in one tz (where it is stored) and *present* results in another (the
   analyst's local tz). Every mature system separates these — Postgres
   `timestamptz` + `SET TIME ZONE`, Snowflake `TIMESTAMP_LTZ` + session
   `TIMEZONE`, DuckDB `TIMESTAMPTZ` + `SET TimeZone`, Looker
   `database_time_zone` + `query_time_zone`, Metabase "Report Timezone". The
   report tz is type-gated to instant types in all of them; naive/date types
   render as stored.

2. **The naive read default is wrong and non-reproducible.** Today an undeclared
   naive `datetime`/`timestamp` is read in the process system tz
   (`_column_timezone`, `marivo/analysis/executor/runner.py:452`). That depends
   on the analysis machine and disagrees with how the *engine itself* reads the
   same column. Engines (ClickHouse, Trino, DuckDB, Postgres, …) all carry a
   default tz; reading a naive column in any other tz means a value means one
   thing through Marivo and a different thing through the engine's own client.

3. **No report-tz axis exists.** The session resolves a single system-derived
   `tz` (`marivo/analysis/session/core.py:176`), re-resolved on every attach and
   never persisted (`marivo/analysis/session/_runtime.py:181`), with no way to
   choose the presentation tz; the prior design removed it on purpose.

## Decision

Marivo has **exactly two** timezones. Both are auto-derived so the common case
needs zero configuration.

| Axis | What it controls | Default | Authored override |
| --- | --- | --- | --- |
| **Read tz** (data property) | How a *naive* wall-clock column becomes an absolute instant | The datasource's **engine default tz** (probed), falling back to **system tz** | optional `timezone=` on `ms.datetime(...)`, `ms.timestamp(...)`, or time-bearing `ms.strptime(...)` |
| **Report tz** (session property) | Analysis-result rendering of instant columns; `now`/`today`/relative windows; bucket boundaries | **System tz**, persisted in session meta and echoed in results | `session.get_or_create(report_timezone=)` |

Instant columns (tz-aware `timestamp`) are absolute and **ignore read tz**; they
are only ever rendered in report tz. Civil-date columns (`date`, `%Y%m%d` day
partitions) have no time-of-day and are **never shifted**.

The naive read default is the engine default tz (**not UTC**) by explicit
decision: it makes Marivo interpret a naive column exactly as the engine / raw
SQL would, so `preview` through Marivo and a hand-written `SELECT` agree.

## Column classification

Every time field resolves to exactly one physical kind. Marivo already carries
most of this split; this formalizes it as the single dispatch point.

| Physical kind | Members | Read tz | Report tz role |
| --- | --- | --- | --- |
| **Instant** | tz-aware `timestamp` | N/A (already an instant) | rendered / bucketed in report tz |
| **Localizable wall-clock** | naive `datetime`/`timestamp`; sub-day partitions (`%Y%m%d%H`, strptime with a time component) | applies: engine default → system tz, parse override | localize via read tz → instant → render / bucket in report tz |
| **Civil-date wall-clock** | `date`; day partitions (`%Y%m%d`, date-only strptime) | N/A (no sub-day precision; declaring `timezone=` is an error) | not shifted; matched / bucketed as a civil date |

`epoch_seconds` is intentionally **out of scope**: it is currently deferred
(`docs/superpowers/specs/2026-06-08-semantic-time-field-format-design.md:102`)
and has no authoring surface. If/when reintroduced it joins the **Instant** kind
(absolute UTC, ignores read tz); that requires its own semantic proposal and is
not assumed here.

### Authoring contract

This design makes timezone declarations optional for localizable wall-clock
fields:

- `ms.datetime(timezone=...)` and `ms.timestamp(timezone=...)` change from
  required `timezone` to optional `timezone: str | None = None`.
- Time-bearing `ms.strptime(..., timezone=None)` remains authorable and no
  longer triggers the current `naive_timezone_undetermined` readiness blocker.
- No declaration means "use datasource engine default tz, with system fallback".
  This is the zero-config path, not an error. A declaration is only needed when
  the source column's wall-clock meaning differs from the datasource default.

Civil-date fields remain different: `ms.date()` and date-only strptime formats
must not accept `timezone=`.

## Timezone resolution

**Read tz** (localizable wall-clock only), in order:

1. Explicit `timezone=` on the parse variant (`ms.datetime(...)`,
   `ms.timestamp(...)`, or time-bearing `ms.strptime(...)`).
2. The datasource's resolved **engine default tz**.
3. System tz (when the engine exposes none).

### Datasource read-tz data model and lookup path

This is net-new; no current object holds it (`DatasourceIR`,
`marivo/datasource/ir.py:48`, is authored config only; `TableMetadata`,
`marivo/datasource/metadata.py:80`, is table-inspection output; the executor's
`_column_timezone(time_meta, session_tz)` has no datasource handle).

- **Storage:** a datasource-scoped *resolved-runtime* value (not authored
  `DatasourceIR` config — the user requirement is zero-config). Add an
  `engine_timezone: ResolvedTimezone` to the datasource's resolved runtime
  metadata (the inspection-output layer, peer to `TableMetadata`, reachable from
  the connection runtime).
- **Population:** probed **once** when the datasource connection runtime is first
  established (or metadata refreshed), via one query through the existing ibis
  connection, by dialect:

  | Engine | Probe |
  | --- | --- |
  | DuckDB | `current_setting('TimeZone')` |
  | ClickHouse | `timezone()` |
  | Trino / Presto | `current_timezone()` |
  | Postgres / Redshift | `current_setting('TimeZone')` |
  | Snowflake | `CURRENT_TIMEZONE()` |
  | BigQuery | none → falls back to system tz |

  A failed/absent probe records `read_tz_resolution="system_fallback"`.
- **Lookup path:** the executor resolves an entity's datasource via
  `EntityIR.datasource` (`marivo/semantic/ir.py:235`), reads the cached
  `engine_timezone` from the connection runtime, and passes it to the read-tz
  resolver. `_column_timezone` is changed from `(time_meta, session_tz)` to
  `(time_meta, *, datasource_read_tz)`: `declared → datasource_read_tz`. The
  `system tz` fallback lives in the datasource probe, so `_column_timezone` no
  longer falls back to the session tz.

**Report tz**, in order:

1. `session.get_or_create(report_timezone=)`.
2. System tz (`resolve_system_timezone()`).

### Report-tz persistence and reopen conflict

This changes today's "resolve every attach, never persist" behavior
(`_runtime.py:181`; `__init__.py:153` rewrites tz audit fields on every attach).

- **Authoritative store:** report tz is **persisted** in session meta as
  `report_tz` + `report_tz_resolution` (`iana` / `fixed_offset`) +
  `report_tz_warning`, written at session **creation** and never silently
  rewritten thereafter.
- **Reopen with no `report_timezone`:** use the persisted value (do not
  re-resolve from the current machine).
- **Reopen with a matching `report_timezone`:** no-op (idempotent
  `get_or_create`).
- **Reopen with a *different* `report_timezone`:** fail-closed
  `SessionTimezoneConflict` (new error), stating the persisted vs requested tz
  and the next step ("create a new session, or delete and recreate to re-bucket
  under a new report tz"). Silently re-bucketing prior results under a new tz is
  the trap this prevents.
- **Build path:** `_session_from_row` (`_runtime.py:173`) reads `report_tz` from
  meta instead of calling `resolve_system_timezone()`; the executor's
  `session_tz` parameter is renamed `report_tz` and sourced from this value.
- **No migration:** the current re-resolve-on-attach logic and the `previous_tz`
  meta field (`__init__.py:151`) are removed wholesale; sessions created before
  this change are not upgraded.

## Query-time semantics

**Core implementation principle: never mutate the engine session tz.** All
timezones are passed explicitly inside the ibis expression. This keeps read tz
(engine default) and report tz (possibly different) independent, and lets the
BigQuery path (explicit conversion) and the has-session-tz-engine path share one
code path.

**Filtering (preserve pushdown — transform the bound, not the column).** Window
bounds are authored as report-tz wall-clock.

- Instant column: bound → UTC instant → `col >= T_start & col < T_end`.
- Localizable wall-clock: bound → instant → project into **read-tz** wall-clock,
  strip tzinfo, compare against the bare column. This is exactly today's
  `_timestamp_bounds_for_column` (`runner.py:427`); the only change is
  `column_tz` resolves through the datasource engine default instead of
  `session_tz`.
- Civil-date: bound → report-tz civil date → compare civil-to-civil.

**Bucketing / rendering (keep the existing baseline).**

- Bucketing keeps today's behavior: a fixed offset computed at the window-start
  anchor (`_timestamp_expr_in_session_timezone`, `runner.py:885`) plus generic
  `truncate`/`cast` (`bucket_start_expr`, `runner.py:873`), now parameterized by
  **report tz** for instants and by **read tz → report tz** for localizable
  wall-clock. The known DST limitation (one offset per window) is documented, not
  fixed here.
- Output is **naive session-local** values (as in `ensure_bucket_start_timestamp`,
  `runner.py:1123`), localized to **report tz**. This is a design choice, not a
  compatibility constraint: a recorded `report_tz` makes the implied offset
  recoverable, and naive-local is the conventional report-tz presentation. The
  tz-aware-output alternative is deferred (see Scope).

**Preview.** Preview is not session-bound. It uses the current
`resolve_system_timezone()` value as its report tz, independent of any persisted
analysis session. Wall-clock columns are shown interpreted in their read tz,
**not silently shifted**; instant columns are rendered in this system report tz;
every time column carries a tz label. Preview answers "what is stored,
interpreted under read tz, displayed under the current system report tz";
analysis results answer "the persisted session report-tz view". The label keeps
the difference legible.

## Failure semantics & observability

| Trigger | Behavior |
| --- | --- |
| reopen session with a different `report_timezone` | fail-closed `SessionTimezoneConflict` (new), persisted vs requested + next step |
| `timezone=` on a civil-date / day-partition field | fail-closed at **authoring** time (mirror of `_validate_time_field_timezone`, `runner.py:457`), with execution-time backstop |
| `timezone=` on an instant column conflicting with the engine-surfaced column offset | fail-closed `TimezoneDeclarationConflict` (existing); doc-note this assertion is usually vacuous |
| `timezone=` not a valid IANA name | `TimezoneInvalidError` (existing) |
| engine tz probe fails | record `read_tz_resolution="system_fallback"`; no error |

Authoring-time validation needs the column dtype from cached datasource
metadata. When metadata is unavailable, authoring validates IANA legality only;
the declared-vs-actual conflict is caught at execution time.

Observability: session repr and result frames carry `report_tz` +
`report_tz_resolution`; each time column carries its resolved `read_tz`.

## Scope

**In scope (this change):**

1. Datasource engine-tz probe + cached `engine_timezone` runtime slot + executor
   lookup via `EntityIR.datasource`.
2. `get_or_create(report_timezone=)`; persist report tz as authoritative; reopen
   conflict; stop re-resolving every attach.
3. Make localizable wall-clock authoring timezone optional: update
   `ms.datetime`, `ms.timestamp`, time-bearing `ms.strptime`, semantic help,
   readiness, and tests so the datasource-default read-tz path is reachable.
4. Split the executor's single `session_tz` into `read tz` (declared → datasource
   → system, for naive localization) and `report_tz` (bounds, `now`/`today`,
   bucketing). Rename `session_tz` → `report_tz`.
5. Move the date/partition `timezone=` rejection and the declared-vs-actual
   conflict check to authoring time, keeping execution-time backstops.
6. Keep naive session-local output and the fixed-offset bucketing baseline, now
   parameterized by report tz.

**Out of scope (separate future proposal):** native per-dialect, DST-correct tz
truncation (replacing the window-start fixed offset); a tz-aware output option;
`BackendTimezoneUnsupported` + `bucket_tz_strategy` capability surface;
`epoch_seconds` reintroduction.

## Mapping to the original four rules

| Rule | This design |
| --- | --- |
| 1. tz-aware read by own tz; parse tz must match else error | instant kind ignores read tz; parse-tz conflict fails at authoring ✓ |
| 2. naive: parse tz, else datasource default | parse tz → engine default tz → system tz (not UTC) ✓ |
| 3. session optional tz, default system, drives output | `report_timezone=`, default system, persisted + echoed, reopen-conflict guarded ✓ |
| 4. preview follows read tz, results follow report tz | preview read-tz-labeled and displayed in system report tz; analysis results use persisted report tz (naive-local, recorded) ✓ |

## Known tradeoff

Defaulting read tz to the engine tz means an ETL that writes UTC into an engine
whose default tz is, say, `Asia/Shanghai` has that naive column read as
`Asia/Shanghai`. The escape hatch is parse-level `timezone="UTC"`. This is the
cost of "zero-config + consistent-with-engine"; it is strictly more controllable
than the current "analysis-machine system tz" (stable across analysis machines,
matches the engine's own reads), and it is explicitly overridable.

## Testing

Run under a controlled `TZ` and an explicit `report_timezone`:

- naive column read in engine default tz vs `timezone="UTC"` override: same data,
  same window, off-by-one-offset hit difference.
- read tz (engine default) ≠ report tz: instant rendered in report tz, naive
  localized via read tz, in one analysis.
- instant vs localizable wall-clock vs civil-date window boundary landing.
- `as_of` / `today` switching at report-tz local midnight.
- `timezone=` on a date column → authoring fail-closed.
- `ms.datetime()` / `ms.timestamp()` / time-bearing `ms.strptime(...)` without
  `timezone=` are accepted and use datasource engine tz.
- reopen session with a different `report_timezone` → `SessionTimezoneConflict`;
  reopen with the same or none → uses persisted value.
- engine without tz-probe → `system_fallback` recorded, no error.
- preview without a session uses system tz as report tz and labels read/report
  timezone interpretation.
- output bucket columns are naive session-local in report tz (chosen behavior;
  update `test_analysis_window_encoding.py:625` to assert report-tz localization).
