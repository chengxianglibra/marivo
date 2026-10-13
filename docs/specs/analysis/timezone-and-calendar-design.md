# Timezones and Calendars

Status: current temporal contract, 2026-10-11. Exact physical routes require
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
fallback bypasses exact qualification. Graph snapshots use `graph_dag/v10`; older
versions require source re-execution. Fixed and cold recovery use frozen facts
without reader probes, source connections or host timezone resolution.

The common Ibis temporal normalizer owns parsing and conversion. There is no
automatic whole-source format or timezone-rule audit. A conversion failure
does not switch parser/timezone. Fixed continuations preserve frozen values and
authority without reopening the source. Shared authority values live in
`analysis.core.time_authority`.

## Period correspondence

Period comparison requires an explicit closed time-key descriptor on `mv.exact()`
or `mv.outer()`. The same descriptors are available through time-mapped
`mv.one_to_one(...)` ratios. Each side retains its complete ordered original grid,
calendar, timezone, observation authority and boundary/evaluation bindings.
`CorrespondencePart` v4 owns the mapping capture and immutable
`time_keys.current` / `time_keys.baseline` tables. Construction and frozen-definition
integrity validation use one pure evaluator to check complete coverage, injectivity
and exact evaluation against each frozen grid. Execution and lowering consume the
frozen tables without reloading a Catalog or certification.

| Constructor | Canonical aligned string key | Meaning |
| --- | --- | --- |
| `mv.ordinal()` | `ordinal:1` | Position in each complete original grid, starting at 1 |
| `mv.weekday()` | `weekday:1:1` | ISO weekday 1..7 and that weekday's occurrence within the original grid, starting at 1 |
| `mv.holiday(current=..., baseline=...)` | `holiday:-2` | Signed civil-day offset from the side's occurrence start date; first day is 0 |
| `mv.holiday_then_weekday(current=..., baseline=...)` | `holiday_then_weekday:holiday:0`, `holiday_then_weekday:before:1:1`, `holiday_then_weekday:after:1:1` | Offset inside the occurrence; outside, direction, ISO weekday and outward occurrence count |

Numbers have canonical decimal spelling, without leading zeros, a plus sign or
negative zero. Deterministic key ordering is not calendar order. Ordinal remains
position correspondence and does not establish weekday, fiscal or elapsed-time
equivalence. The other three descriptors require single-day grids, including a
certified calendar's day level. Bucket dates come from `original_start` in each
frozen grid's boundary timezone, including partial cells admitted by the original
grid contract. DST days count as one civil day regardless of elapsed hours.

Holiday inputs must be certified `TemporalSetEntry.occurrence(key)` TimeScopes.
They retain the exact Ref, key, original bounds, boundary timezone and snapshot
digest in the definition fingerprint. Date bounds and aware timestamp bounds at
local midnight are accepted; non-whole-day occurrences reject without truncation.
Timestamp captures preserve their instants in the certification's canonical UTC
representation on construction and decode, keeping definition fingerprints stable
across frozen and cold restoration.
Each occurrence's boundary timezone must equal its side's grid timezone. The
occurrences need not come from one TemporalSet or lie wholly inside the windows.
Hybrid uses the half-open occurrence dates: inside, offset from its first day;
before/after, the closest same weekday outside that boundary has count 1 and each
further week increases it. Dates outside the observation grid still contribute to
this distance, so clipping a window cannot reset the outward count. No descriptor
truncates, pads or fills absent keys. WorkSchedule alignment is outside this family.

The root, both views and a time-mapped one_to_one ratio use the existing ordered
non-time keys with a `string` time position of role `aligned`, for all four
descriptors. The output has no `time_grid`. Correspondence projections retain both
complete original keys with their original types; a missing side's entire original
key is null. `.show()` displays the aligned key and both original time coordinates.
These keys carry correspondence evidence rather than a new observed time axis.

`exact` and time-mapped `one_to_one(...)` require equal aligned-key sets
and checked complete composite-key coverage. `outer(missing="keep")` uses the
composite-key union even when either grid is longer; a missing side yields
`Undefined("missing_side")` independently in both views. `empty_value` rejects
time-key absence at construction: a bucket outside the other observation window
was unobserved, whereas no contributions inside an observed window use the
Metric's empty rule. Use keep or adjust the windows. Ordinary non-time-key absence
continues to use admitted Metric empty finish with retained complete state.

Every non-time group must retain all its original buckets. Filter/limit/rank
cannot renumber a partial grid. Aligned results reject `group_by(TimeGrid/Grain)`,
time rollup, runs, forecast and explicit lag correlation, including selections or
row-statistic results that retain the aligned coordinate. Apply these operations
to original endpoints before comparison. Ordinary selection, current-row
statistics, valid Subject membership and independently admitted exact absolute
attribution remain available; correlation without lag uses its common-domain
contract. There is no public side-selecting time projection operation.

Fixed and cold continuations retain both endpoint coordinates, state and authority
without source or Catalog reload. `ComparisonPart` v2 owns the independent Cell
finishes. This contract grants no elapsed/calendar coercion or blanket backend
support.

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
cannot admit a new time method. Numeric observations and non-period Difference
results carrying an original anchor grid transport this mapping only with their
proven original full-domain correspondence;
score projections keep the same grid/keys. Ordinary row deletion revokes that
proof, even if it retained a contiguous subset. Runs rejects incomplete edge
cells and missing/duplicate physical rows; legal unavailable cells remain explicit.
Ordinal period results carry aligned coordinates without a grid and reject runs.

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
