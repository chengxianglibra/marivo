# Timezone and Calendar Alignment

Status: design. This document specifies how `marivo.analysis` interprets time:
the two timezone axes (read tz and report tz), how physical time columns are
classified, how absolute and relative windows and buckets are computed, and how
calendar holidays align to business data. It complements
[`session-state-and-runtime.md`](session-state-and-runtime.md) (where the report
tz is persisted) and the metric-observation rules in
[`operators-and-frames.md`](operators-and-frames.md).

## Two timezones, both auto-derived

Marivo has exactly two timezones. Both default automatically, so the common case
needs zero configuration, and each answers a different question:

| Axis | Controls | Default | Authored override |
| --- | --- | --- | --- |
| **Read tz** (a data property) | how a *naive* wall-clock column becomes an absolute instant | the datasource's engine default tz, falling back to system tz | optional `timezone=` on `ms.datetime(...)` / `ms.timestamp(...)` / time-bearing `ms.strptime(...)` |
| **Report tz** (a session property) | rendering of instant columns; `now`/`today`/relative windows; bucket boundaries | system tz, persisted in session meta and echoed in results | `session.get_or_create(report_timezone=...)` |

Separating the two lets an analysis *read* data in the tz where it is stored and
*present* results in the analyst's tz — the same split every mature engine makes
(Postgres `timestamptz` + `SET TIME ZONE`, Snowflake `TIMESTAMP_LTZ` + session
`TIMEZONE`, DuckDB `TIMESTAMPTZ` + `SET TimeZone`, Looker
`database_time_zone`/`query_time_zone`). The report tz is type-gated to instant
columns; naive and civil-date columns render as stored.

The naive read default is the datasource **engine default tz, not UTC**, by
explicit decision: Marivo then interprets a naive column exactly as the engine or
a hand-written `SELECT` would, so a `preview` through Marivo and raw SQL agree.

## Column classification

Every time field resolves to exactly one physical kind — the single dispatch point
for time handling:

| Physical kind | Members | Read tz | Report tz role |
| --- | --- | --- | --- |
| **Instant** | tz-aware `timestamp` | N/A (already absolute) | rendered / bucketed in report tz |
| **Localizable wall-clock** | naive `datetime`/`timestamp`; sub-day partitions (`%Y%m%d%H`, strptime with a time component) | applies: engine default → system tz, or parse override | localize via read tz → instant → render / bucket in report tz |
| **Civil-date wall-clock** | `date`; day partitions (`%Y%m%d`, date-only strptime) | N/A (no sub-day precision) | not shifted; matched / bucketed as a civil date |

A naive column is not an absolute instant — it stores wall-clock fields with no
offset, so "which tz it means" lives only with whoever wrote the data. The read tz
turns that guess into a declaration: `ms.timestamp(timezone="UTC")` marks a naive
column as actually UTC, and it is then anchored correctly as an instant. Instant
columns are already absolute and ignore the read tz; civil-date columns have no
time-of-day and are never shifted (declaring `timezone=` on a `date`/day-partition
field is an error). `epoch_seconds` has no authoring surface today and is out of
scope; if reintroduced it joins the Instant kind.

## Timezone resolution

**Read tz** (localizable wall-clock only), in order: (1) an explicit `timezone=` on
the parse variant; (2) the datasource's resolved engine default tz; (3) system tz
when the engine exposes none.

**Report tz** is the system tz unless `session.get_or_create(report_timezone=...)`
overrides it. It is resolved once and persisted in session meta;
`resolve_system_timezone()` returns a `ResolvedTimezone(name, tz, resolution,
warning)` by reading, in order, the `TZ` environment variable, the
`/etc/localtime` symlink into `zoneinfo/`, and finally a fixed-offset fallback from
the local clock. A resolvable IANA name yields `resolution="iana"`; otherwise
`resolution="fixed_offset"` plus a warning is recorded, because a fixed offset
drops DST rules and can misplace windows/buckets across a DST boundary. Because the
report tz is persisted, reopening a session with a conflicting `report_timezone`
raises `SessionTimezoneConflict`.

## How windows and buckets are computed

One pipeline replaces per-`data_type` guessing:

```text
user intent (report-tz wall-clock window / today / now)
  -> 1. resolve window bounds to a report-tz half-open interval [start, end)
  -> 2. land per column kind:
       civil-date wall-clock : match directly in civil-date space (no conversion)
       localizable wall-clock: localize via read tz -> instant -> compare
       instant               : convert bounds (report tz) -> UTC instant -> compare
  -> 3. bucket: truncate in report tz
```

### Absolute window bounds

Given a report-tz interval `[start, end)` (a date-only `end` takes the next-day
report-tz midnight, so the interval is half-open with a closed lower bound):

- civil-date `date` / day partitions — compared directly in the report-tz date
  space; the upper bound is the next civil date.
- hour/string partitions — bounds are formatted into the partition literal in the
  report tz and compared as strings/integers; no UTC conversion (they carry no
  offset).
- localizable naive `timestamp`/`datetime` — localized via the read tz to an
  instant; report-tz bounds convert to UTC instants for `col >= T_start & col < T_end`.
- instant tz-aware `timestamp` — report-tz bounds convert to UTC instants and
  compare directly.

### Relative windows and `now`

`today` / `yesterday` / `this X` / `last N X` / `xtd` first take the local date of
`now` in the report tz, then expand purely by date; `as_of` defaults to the report-
tz `now`. The public operator surface accepts explicit `time_scope` / absolute
windows — a free-form relative string is rejected with `WindowInvalidError`, so the
agent resolves "last 7 days" to absolute bounds before submitting.

### Bucketing

All grains bucket in the report tz. Civil-date columns are already report-tz civil
dates and truncate directly. Localizable columns localize via the read tz first.
Instant columns are truncated in the report tz; the current baseline uses a
single-offset approximation and records `bucket_tz_strategy="fixed_offset"` in
frame meta (with a DST-risk warning) rather than silently mis-bucketing across a
DST boundary — native per-dialect tz truncation is a forward direction, not a
silent behavior. Timezone conversion for instant columns is pushed down to backend
SQL where possible; a backend without tz functions surfaces a structured
capability error, distinct from an ordinary compile failure.

## Calendar alignment

Calendar holiday times are treated like civil dates in the report tz:

- A holiday ISO date is interpreted as a report-tz local calendar day.
- To align, the data time column is first reduced to a report-tz local date
  (instant columns via report-tz conversion, civil-date columns directly), then
  matched against holidays.
- The `Calendar` model carries no `timezone` field: `.marivo/calendar/*.json` with
  a `timezone` key is rejected as an unknown field (`extra="forbid"`), and
  `CalendarInfo` carries no `calendar_timezone`. With no "calendar tz vs report tz"
  pair, the silent off-by-one-day conflict cannot arise — a holiday has exactly one
  interpretation.

Calendar-based alignment policies (`mv.holiday_aligned()`,
`mv.holiday_and_dow_aligned()`, etc.) are specified in
[`operators-and-frames.md`](operators-and-frames.md#typed-policies); this document
governs only how their holiday dates are interpreted.

## Failure semantics

Timezone errors follow the structured `AnalysisError` style. Because read tz always
has a default (engine → system), there is no "undeclared tz" error class; the
declaration conflicts are bounded:

| Trigger | Error / behavior |
| --- | --- |
| Invalid ISO date/time in a window bound | `WindowBoundInvalid` |
| Free-form relative expression | `WindowInvalidError` (only explicit `time_scope` is accepted) |
| Invalid IANA name in a `timezone=` declaration or `report_timezone` | `TimezoneInvalidError` |
| `timezone=` conflicts with a tz-aware column's own tz | `TimezoneInvalidError` (details note declared vs actual) |
| `timezone=` declared on a civil-date / partition field | `TimezoneInvalidError` (hint: kind unsupported) |
| Reopening a session with a conflicting `report_timezone` | `SessionTimezoneConflict` |
| System tz not resolvable to an IANA name | fixed-offset fallback + `resolution="fixed_offset"` warning (not an error) |
| Instant/naive bucket across DST with only a fixed-offset approximation | `bucket_tz_strategy="fixed_offset"` frame-meta warning (not an error) |

## Known tradeoffs

- **Report-tz reproducibility.** Presentation, `now`/`today`, and bucket boundaries
  follow the persisted report tz; the same analysis presents consistently once the
  report tz is fixed, independent of the machine, but a fixed-offset fallback (no
  resolvable IANA name) can misplace DST-crossing buckets.
- **Declare naive read tz.** Many warehouses store naive `timestamp`s that are
  really UTC. The read-tz default (engine → system) matches the engine's own
  reading, but where the source column's meaning differs from the datasource
  default, declare it once with `timezone=` on the time field for cross-environment
  agreement.
- **CI/test stability.** Fix `TZ` (e.g. `TZ=UTC` or a team convention) in CI so
  report-tz-dependent assertions do not drift with the runner's timezone.
