# Timezones and Calendars

Status: current temporal contract, 2026-10-09. Exact physical routes require
independent method qualification; this document defines time meaning.

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

Logical construction captures the persisted report authority. Source schema
preflight captures the actual reader timezone and its `engine` or
`system_fallback` origin without business reads. A naive source adopts explicit
semantic parser authority first, then the reader default, then system authority
only when the reader has no timezone capability. Probe failures and invalid
engine facts do not fall back to the host timezone. The original parser
declaration remains unchanged; resolved defaults are separate frozen facts.
Conflicting or invalid declarations fail through the owning typed error.

## Scopes and buckets

`mv.time_scope(start=..., end=...)` is literal and half-open. A date-only end is
excluded at that midnight. Explicit timestamp endpoints preserve their precision;
no epsilon subtraction or implicit next-day expansion changes their meaning.

Localizable source values follow their declared parser/timezone before scope
filtering and bucketing. Concrete adapter operations retain report/calendar
boundaries across DST. Source values outside the declared wall-time/format
contract are not automatically audited; backend conversion failures propagate.
Known repeated-hour aware instants share their report-local civil bucket. Native remote hour/day buckets use count=1 and precision
through microseconds; string parsers and multi-unit extensions remain separately gated. Civil dates compare directly. Integer, string and composite date/hour encodings follow their governed parser
contract. Qualified static inverse ranges may compare ordered raw encodings as
specified by [Temporal Semantics](../temporal-semantics.md#encoded-event-time-ranges).
An exact-hour upper bucket is excluded; a bucket beginning before a partial-hour
upper bound remains eligible.

Observation windows and member-domain windows are independent. Historical
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
checks have independent temporal test owners; public Session wiring has its
own Runtime tests. Exact Help routes expose the current admitted method contracts.


### Native timestamp timezone authority

Runtime ZoneInfo supplies named-zone rules for declared conversions. Analysis
does not submit source-side range, gap/fold or engine/runtime rule-agreement
audits before every requested query. SQLite retains its connection-local Python
temporal functions. Parsing/conversion still uses the admitted source expression;
backend errors propagate and noncompliant source values may affect results.
An explicit boundary/deadline conversion keeps its method-owned ambiguity checks.

## Temporal binding

Analysis retains the three authorities independently: persisted Session report timezone,
resolved source/parser timezone, and certified calendar boundary timezone.
Membership version anchor, attribute read anchor, Metric contribution scope and
output grid are four distinct bindings. A TimeGrid passed to observe(during=grid)
explicitly binds its output axis and per-cell contribution windows. A
GridEndpoint passed to read/observe(at=...)
explicitly binds its output axis and endpoint interpretation. Neither changes
member version selection or supplies an omitted attribute version. Spatial
classifications enter the first Metric observation through `observe(by=...)`;
only numeric, ratio and statistic results retain `group_by`.

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
`at=` selector that binds every grid cell and reads the state immediately before
its end; `grid.end` reads the exact boundary instant instead.

TimeGrid exposes `start/end -> GridEndpoint` and `before_end -> GridEndpoint`
with a closed before-end interpretation. Observe accepts `during=TimeGrid` and
selects each cell's own half-open window. Read and cumulative observe accept
`at=GridEndpoint` and bind its grid directly. During and at are alternatives;
classification cannot implicitly introduce a grid. Unversioned attributes retain
their stable value in every endpoint cell, while ordinary scalar instants remain
restricted to versioned attributes. Fixed `during=TimeScope` remains one
observation window without an independent output grid. Ordinary datetime endpoints
must be timezone-aware instants; naive source timestamps use the separate
resolved source authority. Existing date/string TimeScope construction retains
its governed normalization and civil-date semantics.
For DATE sources, fixed windows retain their own boundary timezone; cumulative
windows retain their reset authority. Grid row windows use the grid's boundary
timezone. The source, report and boundary authorities remain distinct.

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
tick. Date remains a civil date; aware timestamp remains an instant. Declared
source-time interpretation remains trusted; any required temporal check uses
an admitted expression and its exact invocation scope. An unqualified
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

## Temporal execution and retained folds

The public entries are `members.observe(metric, during=grid)` for partitioned
contributions, `members.read(field, at=grid.start/end/before_end)` for an
independent attribute version, and `members.observe(cumulative, at=grid.end)`
for endpoint accumulation. The member/time product is an internal graph node,
not a separately constructible public result. Domain recovery uses the existing
AnalysisDomain family; no standalone temporal-domain types or compatibility
aliases remain. A fixed TimeScope never supplies an omitted endpoint.
The cumulative anchor is retained per component occurrence, including the two
components of a ratio of cumulative aggregates. Different overlapping cumulative
windows cannot be rolled up by removing time.

DuckDB table/Parquet observations bind native microsecond timestamps (aware or
explicitly localized) and civil dates through the existing Ibis source-time
owner. The declared source interpretation is preserved without a repeated raw/normalized
source audit. Conversion failures remain execution errors. The report,
source and grid/calendar zones remain independent. SQLite's connection-local
temporal functions convert declared or frozen default named-zone and fixed-offset
timestamps to UTC. The resulting exact UTC timestamp/date routes, including
first/last/mean/min/max status folds, still require individual qualification.
SQLite has no reported reader timezone, so absent an explicit declaration it
uses the captured system default. Report and grid timezones never supply source
read authority. Unsupported source forms and conversion precision still reject.

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
Store 9 publication. Fixed continuation uses registered local algorithms and
saved parts without current Semantic, source or DuckDB access. Exact type,
backend and source-form qualification remains independent of this contract.


## Source-time binding and recovery

The graph observation path admits native microsecond timestamps with declared,
physical, reader or system-default authority, and timestamp strings with an
authored `strptime` format. Each observation stores a required `TemporalExecution`
containing report authority and the resolved `SourceTimeAuthority`. Schema
preflight retains the complete reader fact and its origin. Driver authority is
checked again when opening the execution source; a change rejects before a
business batch is submitted. A system default is captured once and reused during
later graph construction and execution even if the host timezone changes.

Method qualification uses the temporal normalizer's UTC input shape with the
actual source precision. Civil dates preserve their calendar interpretation.
Report, window, grid and calendar authorities remain separate frozen parameters
or domain facts and participate in fingerprints and execution identity. The
report timezone never replaces the physical source shape, and no generic
fallback bypasses exact qualification. Graph snapshots use `graph_dag/v5`; older
versions require source re-execution. Fixed and cold recovery use frozen facts
without reader probes, source connections or host timezone resolution.

The common Ibis temporal normalizer owns parsing and conversion. There is no
automatic whole-source format or timezone-rule audit. A conversion failure
does not switch parser/timezone. Fixed continuations preserve frozen values and
authority without reopening the source. Shared authority values live in
`analysis.core.time_authority`.

## Period correspondence

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

Each comparison design keeps its originating observation/grid authority through
grouping and Singleton reduction. Unequal complete bucket counts reject;
equal-length periods may pair without asserting equal civil meaning. Fixed
continuation uses retained instants and durations without calendar/Catalog reload.
This contract grants no elapsed/calendar coercion or blanket backend support.

## Occurrence and relative-window time

This section owns occurrence/Anchor time meaning and calendar refusal. Duration
arithmetic/rounding belongs to the
[operator owner](operators-and-frames.md#domain-methods).

Occurrence time is an absolute aware instant with a frozen parser/timezone
fingerprint. Native source/Ibis/driver conversion may lose precision,
including ns to us; the method does not promise lossless source tick transport.
Preparation records the declared source unit, observed file unit, actual captured
unit and native conversion behavior. When individual losses cannot be determined,
it records **possibly lossy**; it does not claim lossless timestamps. No additional
source-side lossless tick extractor is required. All membership/time bounds,
version selection, order checks, consumption and future durations/windows use
the actual captured representation. Precision-created ties still need business
order unless a closed operator invariant applies; occurrence IDs cannot repair
ambiguity. A civil DATE alone does not supply elapsed-instant authority;
occurrence methods need a resolved captured instant and explicit conversion
contract. Unresolved time authority cannot be repaired from report/host timezone.

The qualified DuckDB occurrence preparation captures timestamp(us, UTC) in
Arrow while preserving original source-unit/report facts in its qualification.
Fixed/cold execution uses the actual captured carrier. The
qualified naive timestamp_ns cast truncates toward zero for positive and negative
values; aware/source conversion may happen earlier and remains possibly lossy.
Parquet writes Arrow seconds as milliseconds in the selected PyArrow version,
which is separately disclosed. Receipt-bound schema metadata, Evidence identity
and fixed/cold recovery retain this disclosure. Domain result `.show()` exposes
the captured precision facts; they do not establish another source profile.

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
actual captured tick unit and reject overflow.

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

## Relative windows

`mv.duration(...)` specifies exactly one integer unit: hours, minutes, seconds,
milliseconds, microseconds or nanoseconds. Hours/minutes become checked int64
seconds; the other units retain s/ms/us/ns ticks. `mv.elapsed(duration)` requires
a positive length and adds elapsed ticks to the captured instant.

`mv.calendar_days(days, ZoneInfo("America/New_York"))` requires a positive integer
and a named IANA zone. It preserves the Anchor's local wall time while adding civil
days, then verifies the deadline by round trip. A spring gap or autumn fold fails
with `r7.calendar_deadline` and an explicit repair to use elapsed time or a different
start/window. It never chooses a fold or shifts a gap silently. Windows include
the Anchor instant and exclude the deadline; the complete Anchor occurrence is
excluded separately. Another same-instant occurrence needs captured business order.

The occurrence capture's accepted instant carrier is UTC microseconds. A deadline that cannot
be represented exactly in that carrier fails with `r7.window_precision`; no tick
truncation is allowed. This does not retract disclosed source ns-to-us loss.
The calendar source envelope is conservative and finite; only actual Anchor
wall-time deadlines determine selection, after all source preparation has ended.

## Captured source timestamp precision

The Parquet adapter retains the timestamp unit from the actual file schema in
source facts, separately from the Ibis/DuckDB expression and emitted Arrow
carrier. An ms file emitted as us remains an ms source qualification; an ns file
retains its ns source qualification. BoundTimeGrid and its retained mappings
continue to use their declared us boundary precision. Observation comparisons
use bounds with at least that precision so a coarse source carrier cannot round
the accepted scope.

The selected PyArrow writer converts timestamp[s] to timestamp[ms] in Parquet.
A fixture written that way proves an ms file, not a seconds-source profile;
the actual file schema determines admission and precision disclosure.

## Statistical grid authority

This is the owner of grid identity, adjacency, precision and future continuation;
statistical methods cannot reinterpret time. The current BoundTimeGrid uses
UTC datetime microsecond boundaries and explicitly declares precision="us".
Source s/ms/us/ns representations and capture conversions are separate physical
profiles; an ns source does not grant ns BoundTimeGrid or Duration precision.
Existing conversion/ambiguity contracts remain authoritative.

A complete retained r8.grid_cells/v1 table binds the grid identity, original and
actual cell boundaries, ordered cell identity/ordinal, partial flag, report and
boundary timezones, grain, certified-calendar snapshot digest, exact coverage
and source/capture authority. Each series has a complete original composite
coordinate->cell mapping. Metadata containing a grid object without the mapping
cannot admit a new time method. Numeric observation and aligned Difference
transport this mapping only with their proven original full-domain correspondence;
score projections keep the same grid/keys. Ordinary row deletion revokes that
proof, even if it retained a contiguous subset. Runs rejects incomplete edge
cells and missing/duplicate physical rows; legal unavailable cells remain explicit.

Adjacent means consecutive captured grid cells whose actual end equals the next
start, within one full non-time tuple. Builtin civil-day DST changes produce
actual 23/25-hour spans. Certified periods may have unequal lengths but use
captured consecutive period coordinates and boundaries. Run count is cell count;
duration is the exact UTC microsecond end-start. TimeRun start/end carry timestamp
values and the same frozen timezone/precision authority; no count-based duration,
wall-clock subtraction or current-calendar lookup is allowed. Left/right scope
boundaries are observation limits, not proof that a condition resolved.

For Association +k pairs the left cell ordinal i with right ordinal i+k in the
same captured series/grid. Coordinate-based boundary loss and Null-pair deletion
are distinct counts; no retained-row shift or cross-series pairing is permitted.
Explicit lag is not admitted by a mere timestamp column or non-time Entity/group
shape. The shared grid and pairing receipt must prove complete correspondence;
independent captures cannot create common observation authority from equal labels.

Forecast training and future cells use one exact captured continuation authority.
Builtin continuation uses the bound grain/timezones and certified civil boundary
rules; semantic calendars require actual certified future periods through the
requested horizon. Capture the future cell table before local forecast execution
and before Artifact publication. Every series has the same complete history and
future sequence, with no partial cells or missing extension. Future horizon
ordinals identify approved cells, not elapsed hours or an inferred calendar.
Ambiguous/nonexistent civil boundaries follow existing rejection; no shifted
local deadline or DST fold guess is introduced. Source-offline recovery decodes
the committed grid and retained cells under Store 9's local trust contract.
A successor enforces its own consumed-grid requirements; recovery never loads
a current Semantic calendar to manufacture continuation.

Physical qualification distinguishes native-table us and Parquet source
s/ms/us/ns, UTC and America/New_York report authority, complete builtin days
(including both DST transitions) and certified unequal periods. Non-time positive
profiles remain separate. Negative requirements include partial edges, removed or
duplicated cells, wrong calendar/zone/grid identity, uncaptured future horizon,
misleading timestamp precision and false completeness receipts. Unsupported or
unqualified exact profiles reject; qualification of another time/source form
does not admit them.

### Elapsed interval output

Run boundaries come from the admitted original grid's frozen UTC instants.
Duration is the exact difference in microseconds between the first cell start
and last cell end. DST days and unequal certified periods retain their actual
boundaries. Runs reject partial edges and never regenerate a grid during
fixed execution or recovery.

### Bounded DST grid consumer

The bounded consumer records spring and fall New York day grids with UTC source
instants and an Asia/Tokyo report timezone. Explicit aware scope endpoints retain
their instant identity; a date-only scope continues to use the Session report
timezone even when the grid specifies a different boundary zone. Fixed grouping
retains the observed grid coordinate and its 23/25-hour boundaries. DuckDB's
additional Tokyo registration is limited to table/string Entity transport,
time product and sum-zero observation, plus retained int64 Entity sum-zero rollup.
It does not admit untested types, units, forms or other timezone keys. Native
development evidence is not full-family or same-candidate formal qualification.
