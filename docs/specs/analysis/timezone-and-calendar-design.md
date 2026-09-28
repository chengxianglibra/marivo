# Timezones and Calendars

The Session's persisted report timezone owns built-in temporal interpretation.
A semantic parser may declare a source read timezone, and a certified calendar
owns its boundary timezone. These authorities remain separate and are preserved
through execution, publication and cold recovery.

## Physical time kinds

| Source kind | Interpretation |
| --- | --- |
| Aware timestamp | Absolute instant; localize display and bucket boundaries under report/calendar authority |
| Naive timestamp or time-bearing partition | Local wall clock; apply declared or resolved reader timezone before instant comparison |
| Date or date-only partition | Civil date; preserve its date meaning without an instant shift |

Typed source schema preserves actual timestamp precision. The normalized logical
date/timestamp family does not discard physical precision or parser identity.
Unsupported exact submicrosecond timezone conversion fails explicitly.

## Authority resolution

Session report timezone defaults to system resolution and is persisted once.
An explicit `report_timezone` overrides that default. A conflicting reopen is
rejected. Fixed-offset resolution remains explicit; it does not invent IANA DST
rules. Changing host timezone does not change a recovered Session.

Logical construction captures the persisted report authority without opening a
source connection. Runtime probes the admitted actual reader only when native naive axes lack explicit
parser authority, before setting its UTC execution environment. Probe failures and
invalid engine facts do not fall back to the host timezone. An explicit semantic parser timezone takes precedence
over the reader default; system fallback is recorded when the engine has none.
Conflicting or invalid declarations fail through the owning typed error.

## Scopes and buckets

`mv.time_scope(start=..., end=...)` is literal and half-open. A date-only end is
excluded at that midnight. Explicit timestamp endpoints preserve their precision;
no epsilon subtraction or implicit next-day expansion changes their meaning.

Localizable source values are parsed and localized before scope filtering and
bucketing. Concrete adapter timezone operations preserve report/calendar boundaries
across DST. Native naive gaps/folds fail; known repeated-hour instants share their
report-local civil bucket. Native remote hour/day buckets use count=1 and precision
through microseconds; string parsers and multi-unit extensions remain separately gated. Civil dates compare directly. Integer, string and composite date/hour
partitions follow their governed parser rather than accidental lexical ordering.
An exact-hour upper bucket is excluded; a bucket beginning before a partial-hour
upper bound remains eligible.

Observation windows and Population membership windows are independent. Historical
snapshot/validity coordinates select rows for stable Entity identity K; they do
not become part of K or authorize a fallback to an older snapshot.

## Calendars and cumulative state

Period calendars are finite certified partitions with exact civil boundaries,
ordered periods and a snapshot digest. Their boundary timezone is independent of
source read and Session report time. Construction captures the selected certified
snapshot from project semantic state; no analysis-local calendar file is consulted.

Grain-to-date resets follow the owning civil period. All-history preserves earlier
contributions. Trailing day/week windows retain fixed 86,400/604,800-second lengths.
Retained cumulative coverage compares instants for timestamp axes, preserving
23/25-hour DST days instead of assuming every civil day is 24 hours.

Artifact descriptors retain report authority and adopted per-axis physical kind,
read timezone and source, boundary timezone and parser facts. Retained folds use
these committed contracts. Source-offline cold recovery neither probes a datasource
nor resolves the new host's timezone.

The supported semantic parser and calendar authoring contracts are described in
[temporal semantics](../temporal-semantics.md). Actual source, Runtime and recovery
checks are owned by the lazy temporal test suites; public Session wiring has its
own Runtime tests. Exact Help routes expose the current admitted method contracts.


### Native timestamp timezone-rule agreement (C3a)

Runtime ZoneInfo is the authority for named-zone rules. Before a native timestamp
source produces a primary result, source-side min/max aggregates bound the relevant
intervals. Source-side validation then checks that every non-null naive value has
exactly one ZoneInfo candidate and that source conversion to UTC and the report
boundary agrees with those rules. TZif transition instants and POSIX continuation
rules locate intervals; ZoneInfo supplies their offsets. Gaps, folds, unavailable
rules and engine/runtime disagreement fail with a structured MaterializationError
before publication. Fixed-offset-only paths need no rule-data comparison. SQLite
keeps its connection-local Python temporal functions. This does not transfer source
rows or install remote UDFs. Each range/rule query is an attempted physical
engine_check validation submission. Separate validation and primary queries retain
the existing source-concurrency limitation; this is not a snapshot guarantee.

## R5.1 frozen temporal binding

Status: target frozen; new R5 temporal routes remain unverified. R5 retains the
three authorities above independently: persisted Session report timezone,
resolved source/parser timezone, and certified calendar boundary timezone.
Membership version anchor, attribute read anchor, Metric contribution scope and
output grid are four distinct bindings. None supplies an omitted value for the
others except the explicit member group_by property shorthand owned by Analysis.

`mv.time_grid(*, during: TimeScope, grain: Grain, timezone: str | None = None)
-> TimeGrid` constructs a finite coordinate domain. With no explicit timezone,
built-in grain uses the consuming Session's persisted report timezone; a calendar
grain uses its certified snapshot timezone. The unresolved built-in default is
bound once when a Session consumes the grid, enters its identity, and cannot be
reused with a conflicting authority. It never resolves host timezone itself.
An explicit calendar timezone must agree with the snapshot or reject.

`TimeScope.before_end -> BeforeEndBoundary` is a typed left-limit view;
TimeGrid exposes `window -> GridWindow`, `start/end -> GridEndpoint`, and
`before_end -> GridEndpoint` with a closed before-end interpretation. Grid
handles are bound to the exact grid identity and usable only on its product
receiver. Fixed `during=TimeScope` remains one fixed window, even on each(grid);
`during=grid.window` alone selects the row window. Ordinary datetime endpoints
must be timezone-aware instants; naive source timestamps use the separate
resolved source authority. Existing date/string TimeScope construction retains
its governed normalization and civil-date semantics.

Each grid row retains stable identity, original and clipped half-open boundaries,
partial-cell status, physical time precision, timezone authorities and any
calendar certification digest. A clipped week is not a full week. Empty cells
remain in the bounded time target; only an admitted contribution-coordinate
image may restrict that product. Built-in week uses the existing Grain rule.
A Grain group_by coarsens the unique carried time axis only when each source cell
maps wholly to one target; a week crossing months rejects, even with fine-grained
state. Observe a month grid to obtain those month values.

Before-end version selection is symbolic: exact snapshot left-period selection
or the validity left-limit predicate, never end minus epsilon or one timestamp
tick. Date remains a civil date; aware timestamp remains an instant. Naive
source DST gap/fold or engine/ZoneInfo disagreement rejects before publication;
all validation/range reads must be admitted Ibis expressions. An unqualified
submicrosecond conversion rejects explicitly without truncation. Fixed execution
uses retained instants, parser facts and boundaries without new source probes.

Cumulative endpoint e consumes [anchor(e), e); the display start cannot truncate
all-history or trailing input. Grain-to-date uses the owning reset boundary;
trailing day/week retains fixed 86,400/604,800-second semantics, distinct from
23/25-hour civil days. Removing time needs the declared temporal reduction and
sufficient nonoverlapping state, never a sum of overlapping cumulative values.
Semi-additive evaluation first applies the declared spatial aggregation at each
sample, then its time fold. A later spatial merge requires a commutation proof
or aligned pre-fold state. In particular, summing per-channel finished peaks is
not automatically the peak of the spatial total.
