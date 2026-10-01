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

`TimeScope.before_end -> BeforeEndBoundary` is a typed left-limit view consumed
by version selection such as `members(..., at=scope.before_end)`. It is not a
`date`/`datetime` and is not accepted by `time_scope(end=...)`; use `scope.end`
when constructing another half-open window. Likewise, `grid.before_end` is an
`at=` selector on the matching product, while `grid.window` selects its interval.

TimeGrid exposes `window -> GridWindow`, `start/end -> GridEndpoint`, and
`before_end -> GridEndpoint` with a closed before-end interpretation. Grid
handles are bound to the exact grid identity and usable only on its product
receiver. Fixed `during=TimeScope` remains one fixed window, even on each(grid);
`during=grid.window` alone selects the row window. Ordinary datetime endpoints
must be timezone-aware instants; naive source timestamps use the separate
resolved source authority. Existing date/string TimeScope construction retains
its governed normalization and civil-date semantics.
For DATE sources, fixed windows retain their own boundary timezone even on a
product whose grid uses another timezone; cumulative windows retain their reset
authority. Only row windows use the grid's boundary timezone.

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

## R5.5 unified temporal execution

The public product is `members.each(grid)`. Use `during=grid.window` for
partitioned contributions, `read(field, at=grid.start/end/before_end)` for an
independent attribute version, and `observe(cumulative, at=grid.end)` for an
endpoint accumulation. A fixed TimeScope never supplies an omitted endpoint.
The cumulative anchor is retained per component occurrence, including the two
components of a ratio of cumulative aggregates. Different overlapping cumulative
windows cannot be rolled up by removing time.

DuckDB table/Parquet observations bind native microsecond timestamps (aware or
explicitly localized) and civil dates through the existing Ibis source-time
owner. Governed raw/normalized pairs are checked against the declared timezone
before publication; gaps, folds and engine disagreement reject. The report,
source and grid/calendar zones remain independent. SQLite qualifies the required
UTC timestamp/date routes, including first/last/mean/min/max status folds.
Other SQLite timezone routes remain closed at admission.

`metric.fold@v1` performs the declared spatial sum per exact sample instant,
then the declared scalar time fold. Its original state retains canonical ordered
UTC sample keys, sums, non-null counts and the bound fold kind. Local
`state_rollup.fold@v1` requires aligned sample sets for spatial merging within
each original time cell, merges those components, and folds again. Distinct time
cells concatenate only under the disjoint temporal policy. Missing samples are
not invented as zeros. First/last order is temporal, never physical row order.
Percentile folds and the full numerical qualification remain outside this slice.

Original/clipped boundaries, partial flags, calendar snapshot digest and named
scope identity are frozen with the grid. Equal bounds of two overlapping named
occurrences do not make them one partition. Calendar publication records checks
for each observation occurrence, with its own origin, scope and digest. Source
and fixed whole-cell coarsening preserve the crossing-week rejection.

These paths use the common graph, method registry, SourceSession, exchange and
Store 7 publication. Fixed continuation uses registered local algorithms and
receipts without Semantic, source or DuckDB access. Qualification scope, independent
oracles, reproduction commands and remaining release boundaries are recorded in
the [versioned R5.5 summary](../../superpowers/specs/2026-09-28-marivo-full-algebra-dsl-r5-migration-ledger.md#r55-qualification-summary).
Detailed run logs under `docs/superpowers/specs/evidence/r55/` are intentionally
local and ignored; they are not shipped as part of this specification.


## R5.7 source-time recovery qualification

The graph observation path admits native microsecond timestamps with declared,
physical, or driver-reported read timezone, and timestamp strings with an authored
`strptime` format. The source and report zones are independent. Driver authority
is captured during schema preflight without business reads, frozen into the
observation, and checked again when opening the execution source. A changed
reported timezone rejects before a business batch is submitted; a host-system
fallback is not accepted as an inferred event read timezone.

The common Ibis temporal normalizer owns parsing and conversion. Governed check
reads compare raw native/parsed values with the normalized UTC values before
publication. Malformed strings and engine disagreement cannot silently acquire
another parser or timezone. `test_lazy_temporal_public_runtime.py` preserves the
four historical report-day oracles (explicit UTC, native naive, native aware,
and authored string parsing), with exact UTC grid keys and values `[1, 2]`.
Fixed continuations preserve their frozen values and authority without reopening
the source. Shared temporal authority values live in `analysis.core.time_authority`;
the old observation module is removed without a forwarding alias.

## R6.1 period correspondence

Status: frozen PeriodChange target, not a new backend/time qualification.
`PeriodChange(alignment=window_bucket())` pairs the complete ordered buckets
inside each equal non-time coordinate by their retained ordinal within the
original two bound grids. Both sides must retain their exact ordered grid,
calendar/timezone authority, boundary instants or DATE keys, evaluation key and
observation-window bindings. The two complete bucket counts must agree. An
ordinal is correspondence evidence, not a replacement for either original time
coordinate and not a claim of equal weekdays/fiscal meaning.

Filter/limit/rank cannot re-number surviving buckets. If complete original bucket
maps survive, selection is validated against that original correspondence;
otherwise pairing rejects. UnionKeys may union non-time coordinates only after
this complete bucket rule succeeds; it cannot truncate, pad or infer missing
time buckets. A one-to-one ordinary ratio using a PeriodChange consumes the
same exact time correspondence bound to its ordered input nodes.

R6 required qualification includes R5-qualified NoTime/scoped observation,
UTC instant-us windows, DATE grids and aware report-local calendar grids for the
three comparison designs where meaningful; grouping and Singleton must retain
their originating time authority. Include unequal 28/31 bucket rejection,
equal-length different-month pairing, and a DST-local day-grid case preserving
both sides' distinct instants/durations. Fixed continuation uses those retained
bindings with no calendar/Catalog reload. No new elapsed/calendar coercion,
SQLite numeric expansion or six-backend qualification follows from R6.

## R7.1 frozen occurrence and relative-window time

Status: accepted target, 2026-10-01; no Event/History/Anchor Runtime qualification.
This section owns F06/F08/F10 time meaning and calendar refusal. Duration
arithmetic/rounding belongs to the
[operator owner](operators-and-frames.md#r71-frozen-domain-method-rules).

Occurrence time is an absolute aware instant with exact source precision and
parser/timezone fingerprint. Required cells are native UTC/report-local aware
microseconds and Parquet s/ms/us/ns, each preserving int64 ticks on exchange and
fixed recovery. DATE and unresolved naive occurrence time are refused. Required
report-local aware DST cells do not permit ambiguous naive wall clocks. Exact
submicrosecond data never passes through datetime.to_pydatetime() or a float
second representation. If a boundary/zone conversion cannot preserve the admitted
unit, that invocation refuses; it is not a lower-precision passing cell.

Start selection is [cohort_window.start, cohort_window.end). completion_through
is an exclusive absolute bound, independent of membership and start windows.
History window clips reporting, not inception lookup. Checkpoints include end as
an end-left-limit query; triggers at end are excluded without subtracting a tick
or epsilon. Known follow-up boundaries preserve their observed/declared authority;
the last event timestamp proves no absence. Historical axes bind at Journey entry
or each History checkpoint according to the consuming method.

ElapsedWindow adds positive exact Duration ticks to the Anchor instant.
CalendarWindow adds positive whole local calendar days in the supplied named
IANA ZoneInfo while preserving the local wall time, then converts the deadline
to an instant. The candidate local deadline is round-trip checked through UTC
under both folds: zero valid instants is nonexistent and two distinct valid
instants is ambiguous. Both cases reject with a structured r7.calendar_deadline
constraint and repair to an explicit elapsed window or a business-approved
unambiguous local boundary. No shift-forward, inherited fold or host timezone
repairs it. A unique valid instant is used; an already aware Anchor instant in
a repeated hour remains exact. Deadline conversion must also preserve the
declared tick unit and reject overflow.

The window is [anchor, deadline); the Anchor occurrence itself is excluded by
its full Event/occurrence identity. A different occurrence at the same instant
counts as later only with the captured business_order fact. Event/Journey names,
occurrence ID and display order do not establish this. Shared overlap is the
accepted policy: return/contribution occurrences may serve several Anchors, each
use bound separately. Seven New York calendar days across spring/fall DST differ
from 168 elapsed hours and require independent deadline and exclusion oracles.

Candidate preparation for Journey Anchors uses the start-window upper boundary
and requested relative window to derive a bounded envelope, with exact per-Anchor
windows applied locally. Calendar envelopes evaluate relevant IANA offset changes
and both start boundaries; an unproved envelope or ambiguous deadline blocks the
precise route, not a silently widened all-history read. Source time authority,
Anchor time, exact deadline/window and conversion version remain in execution
identity, parts and cold K. Recovery never recomputes them from current host facts.
