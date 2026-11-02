# Temporal Semantics

Status: implemented current contract.

Civil dates, absolute instants, wall clocks, calendar periods and elapsed
durations have different meaning. Semantic declarations own source time roles
and certified authority; Analysis captures exact boundaries and freezes the
coordinates needed to interpret completed data. Calendar/set authoring
and certification belong to [Temporal authority](semantic/temporal-authority.md).

## Time authority and exact boundaries

Session creation freezes the default report timezone. session.instant(value,
timezone=None) produces an exact UTC InstantAnchor. session.time_scope(start=...,
end=..., timezone=None) captures [start,end) with its boundary timezone.
Date-only upper bounds exclude that midnight. There is no implicit latest period
or relative-date language.

Dates and offset-free ISO strings are local civil inputs. Python naive datetime
objects and ambiguous/nonexistent local clocks reject. Aware inputs and existing
anchors retain their absolute instant even when a parsing timezone is supplied.
Catalog period/occurrence scopes retain their certified boundary timezone and
snapshot rather than adopting a new report timezone.

| Authority | Purpose |
| --- | --- |
| Authored parser/source time role | Interpret source date, wall clock or instant |
| Reader timezone | Interpret source values when no explicit parser authority suffices |
| Session report timezone | Construct default reporting boundaries and builtin grids |
| Certified calendar timezone/snapshot | Govern named period boundaries and membership |
| Frozen result coordinates | Interpret saved values without consulting current rules |

Reader authority is resolved only when required, after the real connection opens.
Explicit parser authority takes precedence. Physical instants need no unnecessary
reader probe; independent adapter transport checks still apply. An absent probe
capability permits a recorded system fallback. A failed/invalid supported probe
fails with its cause, rather than falling back.
Schema preparation captures reader timezone and origin; execution rechecks engine
authority before business reads. Fixed-data operations and cold recovery neither
probe readers nor resolve the current host timezone.

Naive Entity identity is a wall-clock value with no implicit timezone. Identity
retains exact ticks independently of TimeDimension parsing and Session/reader
authority; naive and aware keys cannot be implicitly matched. A TimeDimension
projection over that same column has its own parser/reader interpretation.

ClickHouse reads a zoneless DateTime or DateTime64 in the server timezone, so it
is not a neutral wall clock there. Compiler-owned civil clocks, calendar grid
starts and bucket keys on ClickHouse are carried as `DateTime64(6, 'UTC')`
values, so truncation and interval arithmetic do not depend on the server zone.

Civil dates do not shift; physical instants preserve their meaning. Logical
kind and physical precision are checked independently. Unsupported exact
conversion fails without truncation. Projected source values retain the backend's
native gap/fold interpretation.

Source projection, filtering and calendar assignment use the actual executor's
native interpretation of local clocks. Gap/fold behavior can differ between
backends and pandas; there is no shared wall-order emulation, timezone admission
proof, or retry through another executor. Exact raw-column rewrites are retained
only when equivalent to that interpretation.

Session input boundaries and calendar construction retain their exact validation:
unresolved ambiguous/nonexistent civil boundaries reject; aware instants remain
exact. Output positions are evaluated by the same executor as the time operation.
`hour_prefix(prefix)` combines a civil-date prefix and hour using the prefix's
source context and reader default. A CivilDate has no intrinsic timezone; the
composite does not copy the report timezone or introduce an independent parser
parameter. Predicate literals match the field's civil/instant kind.

## Output axes and observation time

```python
import marivo.analysis as mv

days = session.time_scope(start="2026-07-01", end="2026-08-01").by(mv.grain("day"))
observed = session.observe({"revenue": revenue}, by=(days,)).compute()
observed.show(n=10)
```

The fragment requires a Session and resolved Metric with an admitted time role.
A TimeScope in by contributes an interval axis, even for one interval; by=()
produces a zero-axis observation. CalendarAxis contributes its selected buckets.
Ordinary reductions may omit a time axis and then receive no additional time
constraint. A time role alone does not require a period.

Windows require an explicit observation endpoint in by. All native TimeField
sample reducers can use represented samples without a time axis, regardless of
sample_interval. Calendar Grain samples require finite enclosing coordinates.
TimeField.within(scope) is a source predicate
and field.at(anchor) selects an attribute representation; neither supplies an
observation endpoint. Membership time, attribute time, Metric observation time
and output coordinates stay independent.

Versioned membership requires an exact instant anchor. `field.at(anchor)` uses
one fixed attribute version for the entire question, including changes exactly at
that instant. `dimension.via((relationship,)).at(fact_time)` instead selects a
version for each original fact in ordinary same-source, single-root observe
classification (`by` and label `where`) on DuckDB and Trino. The control is one
named, unanchored, path-free TimeDimension on an unversioned or snapshot fact root.
A snapshot's partition TimeDimension can supply the control; repeated stable keys
across snapshots retain their separate source contributions. Validity fact roots
require a fixed anchor. Row time does not replace the Metric's observation time
or classify at bucket endpoints.

DATE control selects the target snapshot business-day label and supports only
DATE validity. Absolute timestamp control selects snapshot days in the target's
business timezone or compares exact validity endpoints. Native absolute timestamps
and UTC/fixed-offset local clocks are admitted. New IANA local-clock gap/fold
combinations reject before business reads; existing fixed-anchor membership is
unchanged. Timestamp validity requires timestamp control. Version conditions
remain in the left join, retaining NULL control and missing representations as
NULL attributes, with no latest/last-known fallback. Complete relationship keys
and declared to-one cardinality remain authoritative. No source identity audit
is added. Multi-root, read/classify, numeric reductions, windows and internal
samples require fixed anchors in this release.

Internal before-endpoint consumers retain strict left-limit semantics without
subtracting a tick. [Entity versioning](semantic/semantic-object-model.md#versioning-snapshot-and-validity)
owns snapshot and validity selection.

## Grain and bucket structure

mv.grain(unit, count=1) constructs builtin second/minute/hour/day/week/month/
quarter/year grains. Count greater than one is a sub-day width and must tile
one nominal day at construction; calendar-variable units require one.
Grain has no ordering operators. Duplicate subday spellings such as 60second,
120second and 60minute reject; 24hour is outside the subday contract.
90second and 90minute remain legal. There are no
string aliases, guessed tuple grains or direct public Grain constructors.
Named grains come from ms.calendar_grain or a certified calendar entry. Equality
and hashing include captured snapshot identity; a lookup Grain differs from a
captured Grain. Scope.by requires a captured calendar Grain because it has no
Session resolution authority.

TimeScope.by(grain) constructs a CalendarAxis without business reads. It captures
selection intent, timezone, grain and required snapshot identity. During result
evaluation, the source backend generates its required output positions; original
local time data uses pandas. Builtin day/week/month/quarter/year use native civil
truncation. Subday widths align to local midnight and must divide a nominal day.
A nominal day is not a fixed elapsed duration.

Selected nominal starts determine the output candidates; an exclusive end that
repeats an earlier civil clock does not erase the selection. The executor resolves
each candidate and candidates sharing one native key describe one output position.
Window parameters are evaluated from that native position, while explicit absolute
selection bounds retain their instants. A native key need not precede the selected
window endpoint. pandas ambiguity and nonexistent-time errors retain their defaults.

A calendar key is the executor's actual UTC microsecond bucket identity. It is
not a promise that all members form one contiguous UTC interval. The completed
attachment retains key, civil label, selected order, nominal ordinal and selection
progress (`full`, `prefix`, `partial`, or `unavailable`, with a civil cutoff for a
prefix). Labels and nominal ordinals may repeat for distinct actual keys. Genuine
TimeScope, source-read and window ranges have independent owners.

Ordinary output completes the union of generated positions S and observed keys O.
Only missing S keys receive empty-result rows; additional O keys remain present.
Window output instead uses its explicit endpoint domain. Calendar output never
creates new category keys. Certified custom output supports exactly one complete
period; partial, sparse and multiple-period selections reject before business
reads. Certification remains necessary but does not create an execution map.

`Metric.per(grain)` reduces actual internal time samples within its enclosing
finite coordinate. Presence includes all-NULL samples; absent time buckets are
not invented. Each source computes the complete external, parent and child key
from the same fact time. Static nesting rules apply at every layer: subday widths
divide the parent width, day nests in week/month/quarter/year, month in
quarter/year, and quarter in year. Equal builtin grains are valid; week does not
nest in month/quarter/year. Custom hierarchies have no implicit containment rule;
one complete custom period may be sampled under an interval axis. Final output
completion never changes an internal sample mean or count. See
[internal samples](semantic/expressions.md#internal-samples-and-windows).

Semantic sample Grains resolve against the Session's frozen current certified
snapshot and become declared calendar dependencies. Calendar sample layers do
not support inner windows or wrapping windows; both raise a typed error before
execution rather than discarding the window rule.

The axis preserves these distinctions. An original scope is available through
axis.scope; a shifted sparse axis may have scope=None. An envelope does not
reconstruct its selected domain or authorize filling missing periods.

## Metric windows

Windows attach to the original Metric equation with source-free mv rules:

| Rule | Contribution interval |
| --- | --- |
| all_history | No lower bound, exclusive evaluation end |
| since | Explicit lower anchor to exclusive end |
| to_date | Containing reset period start to exclusive end |
| trailing_duration | Exact elapsed duration before end |
| trailing_periods | Declared count of nominal output calendar periods |

Each output endpoint reevaluates the complete Metric on its own contribution
window. A seven-day duration is exactly 168 hours; seven calendar days can differ
across DST. Scalar to_date chooses the unique reset period with left < end <=
right, so a month boundary endpoint uses the preceding complete month without
epsilon arithmetic. Calendar output additionally requires compatible reset
hierarchy. The owning executor evaluates output and reset endpoints before contribution
windows are expanded. Each selected endpoint uses its captured clipping progress. trailing_periods rejects partial first/last buckets and
uses complete calendar predecessors rather than positions in a sparse axis.
Nested windows and missing required endpoints reject.

Output selection, source scope and contribution windows are separate facts.
Completed daily means, distinct counts, ratios or quantiles cannot generally be
summed into a larger-window Metric. A new source grain/window requires a new
observe; current-row reduce is only a statistic of represented cells.

## Period shifts and fixed-data matching

mv.offset requires exactly one non-boolean integer keyword: years or periods.
Periods means nominal steps of the consuming axis's grain, including its step:
day -7 is seven days, week -1 is one week, week -7 is seven weeks and month -1
is one month. Negative offsets seek an earlier baseline; they do not count visible
rows or fixed days. Years means the same civil period, with ISO
week-year rules for weeks. Neither means a fixed 365-day/52-week duration.
Public builtin calendar grains currently require count=1; subday offset is unsupported.

CalendarAxis.shift supports unit-step builtin week/month/year domains consisting
of full buckets or bucket-start prefixes. It freezes the exact target selection
and immediate current-to-baseline map. Missing ISO week 53 or civil cutoff is
legal absence. It never clamps a date, shortens the current domain or fills
sparse gaps. Nonzero offsets into ambiguous/nonexistent target clocks reject;
zero copies the exact instant. Chained shifts retain only their direct origin.

Data-side offset matching supports full builtin day/week/month/quarter/year
buckets, plus week/month/year prefixes with equal saved civil progress. Partial
day/quarter and middle slices reject. Full months can differ in elapsed days.
MTD/YTD progress uses civil positions, rather than equal seconds or ordinal day.

mv.ordinal matches compatible full logical selections using their saved original
ordinals, without compressing positions after where/top. It also pairs two
single-record interval directories with the same boundary timezone. Named
calendars support ordinal, without builtin offset semantics. Shift does not
accept ordinal. Multiple calendar axes and already paired inputs cannot acquire
an arbitrary automatic matching route.

align requires explicit match or equality. compare may consume a compatible
immediate frozen shift relation; wrong current anchors or conflicting explicit
rules reject. A manually prepared baseline can use an explicit alias-keyed match.
Exact checks actual complete joint rows under that relation; unused directory
buckets alone neither create rows nor cause failure. Compare outer requires
actual baseline time rows to map into the current declared domain; general
two-ended time unions belong to align. The complete correspondence and presence
rules are owned by [AnalysisFrame](analysis/analysis-frame.md#correspondence-and-comparison).

Frozen calendar directories retain actual keys, civil labels, order and selection
progress, plus explicit finite shift correspondences. Equality compares actual
keys under compatible axis/grain/timezone contracts. Offset and ordinal consume
saved civil identity and progress without relocalizing old keys; ambiguous
one-to-many civil matches reject only the affected comparison. Cold reads consume
saved values without current timezone, Catalog or source reconstruction. Missing
or corrupt required metadata fails integrity checks. Calendar certification does
not certify business source coverage.

## Encoded event-time predicates

`ms.strptime(format)` parses encoded input before membership. SQL NULL remains
NULL. DuckDB, Trino, ClickHouse and MySQL/MariaDB return NULL for rejected
parsed data; PostgreSQL uses native `to_timestamp` and can fail the entire query
on rejected data. There is no error-policy option. Invalid
format, timezone, type and backend capability remain configuration/preparation
errors. No trimming, guessing, retry or automatic quality scan is performed.

DuckDB uses `try_strptime`; Trino wraps parsing and conversion in `TRY`;
ClickHouse uses `parseDateTimeOrNull`. PostgreSQL retains the native
`to_timestamp` acceptance rules, including noncanonical separators and partial
fields. MySQL-family SELECT parsing is followed by native zero-day arithmetic
to reject impossible calendar carriers.
Native parsers have different acceptance rules. Trino TRY can also suppress
recoverable errors evaluating the parser input; other engines can propagate
argument evaluation errors. Configuration and whole-query failures are not caught.

Encoded raw ranges cannot replace parsing or serve as extra necessary
conditions without an equivalence proof. This includes calendar assignment,
version coordinates and `hour_prefix` date dependencies. Parsed local clocks
and output projections retain native gap/fold interpretation. Independent physical
filters remain eligible for pushdown. Parsing can increase scan and CPU costs;
no source preflight is added. NULL event time has no period membership, while
a NULL attribute control time preserves the fact under a NULL label.

## Physical time execution

Production DuckDB connections use eight threads and UTC. The test suite's
independent one-thread-per-worker cap is not a production connection setting.

The compiler distinguishes exact typed raw-column comparisons, a safe coarse
scan guard plus the exact native predicate, and native filtering. An unavailable
inverse is an optimization miss. Rewrites preserve kind, precision, endpoints and
nullable truth values. Real TimeScope and contribution ranges remain half-open;
one-sided windows need no invented lower bound.

Calendar assignment emits native truncation or local-midnight subday arithmetic.
It has no calendar directory JOIN, per-bucket CASE/UNION, global UTC lattice,
parent/child execution map, or internal completion grid. Source-produced keys
passed to pandas participate in exact-key alignment and arithmetic without being
parsed or bucketed again. Different sources retain their own time execution.

Required final output indexes use a compact native scalar range expression; this
is result evaluation with O(N) output cost, not a timezone qualification query or
a business-fact scan. Custom single-period output uses its certified range and a
native constant key. No source calendar date directory is uploaded or reread.

Remaining finite mixed-key parameters use invocation-owned typed SQL relations.
ClickHouse uses JSONExtract/arrayJoin inside SQL, including Distributed requests;
it never turns these parameters into HTTP external data or multipart uploads.
Timestamp wire values are exact integer microseconds, dates are decoded from
strings, and nullable, decimal and special floating values retain their types.
DuckDB retains invocation-owned Arrow registration; other providers retain their
native JSON relation carriers. Complete request bytes, including FORMAT and
repeated payload, count toward the existing SQL limit. Exceeding it fails without
splitting, retrying, raising the limit, or falling back to multipart.

Compiler-owned UTC cancellation and safe envelopes preserve native meaning.
Existing ownership, scalar relationship, version cardinality, NULL and output
integrity checks remain. No source declaration audit, generic inverse registry,
or backend timezone policy replica is added. Native operation errors propagate
through the existing materialization error boundary.

Predicate shape alone does not establish physical pruning or index usage.
Runtime evidence identifies the actual provider, connector and table; local SQL
compilation does not qualify an unrun remote backend. Frozen output coordinates
and source-free cold reads retain their owning contracts without optimizer-state
persistence.

## Implementation map

[_temporal.py](../../marivo/_temporal.py) owns shared time values;
[time_grid.py](../../marivo/analysis/core/time_grid.py) and
[time_authority.py](../../marivo/analysis/core/time_authority.py) own grids/authority.
[temporal.py](../../marivo/analysis/dsl/temporal.py) owns CalendarAxis operations;
[frozen_calendar.py](../../marivo/analysis/core/frozen_calendar.py) and
[frame_calendar.py](../../marivo/analysis/core/frame_calendar.py) own saved rules.
[source_time.py](../../marivo/analysis/compiler/source_time.py) owns source time
predicates. Reader authority and native conversion remain datasource-owned.
