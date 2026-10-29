# Unified Temporal Semantics

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

Civil dates do not shift; physical instants preserve their meaning. Logical
kind and physical precision are checked independently. Unsupported exact
conversion fails without truncation. Projected source values retain the backend's
native gap/fold interpretation.

Constant-bound membership of a source local clock with raw lineage uses wall
order in its resolved source timezone. An ordinary clock denotes its unique
instant, a repeated clock the later occurrence, and a skipped clock the transition
instant. The compiler translates each exact boundary into the first qualifying
wall clock, then rounds up to the physical representation. Filtering, calendar
assignment, windows, Population and version selection share these boundaries.
Consequently the first occurrence of a repeated reporting hour can be an empty
cell; both instant coordinates remain in the calendar. Skipped source clocks
violate the producer declaration but are placed deterministically without a row
audit. Native projected values can differ from this membership at transitions.
Expressions without admitted raw lineage keep native membership.

Session input boundaries and calendar construction retain their exact validation:
unresolved ambiguous/nonexistent civil boundaries reject; aware instants remain
exact. Reporting grids retain both occurrences of a repeated civil hour.
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

Windows and native TimeField-sample reductions other than mean and weighted_mean require an
explicit observation endpoint in by. Native TimeField-sample mean and weighted_mean can use
represented samples without a time axis; nested sample layers retain their own
endpoint and coverage requirements. TimeField.within(scope) is a source predicate
and field.at(anchor) selects an attribute representation; neither supplies an
observation endpoint. Membership time, attribute time, Metric observation time
and output coordinates stay independent.

Versioned membership requires an exact instant anchor. Field.at selects the
exact attribute instant, including a change at an excluded scope end. Internal
before-endpoint consumers use strict left-limit semantics; they do not subtract
a tick. [Entity versioning](semantic/semantic-object-model.md#versioning-snapshot-and-validity)
owns snapshot and validity selection. Missing exact snapshots are not replaced
with latest or last-known rows.

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
canonical bucket order, nominal bounds, actual clipped bounds, timezone and
required snapshot identity. Calendar authority must cover the requested range.
Builtin grids use report civil boundaries; DST days may span 23 or 25 hours.
A nominal day is not a fixed elapsed duration.

For scope S and nominal bucket N(t), actual observation A(t)=N(t) intersect S.
A clipped edge is partial even if its duration matches some other full period.

`Metric.per(grain)` derives complete inner calendar cells from the nearest
enclosing calendar sample or the observation's finite time coordinate. Each
contribution buckets by its own declared time role. Inner nominal cells must
nest in the enclosing nominal cell; actual bounds clip to the parent's actual
bounds. Each nonempty clipped cell is one sample, including cells without
contributions under the child Metric's empty policy. This adds no duration
weighting or cadence inference. A plain TimeScope permits tiling the whole
interval. See [internal samples](semantic/expressions.md#internal-samples-and-windows)
for builtin and certified nesting rules.

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
hierarchy. Every endpoint is the actual clipped right boundary, never the nominal
end of a partial bucket. trailing_periods rejects partial first/last buckets and
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

mv.ordinal matches compatible full nominal partitions using their saved original
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

Frozen directories retain nominal/actual UTC and civil bounds, original ordinal,
timezone, rule version, required endpoints and finite maps. Cold reads consume
those records without current TZ, Catalog or source reconstruction. Missing or
corrupt authority fails integrity checks; it is not a legitimate absent period.
Calendar certification does not certify business source coverage.

## Encoded event-time predicates

ms.strptime(format) declares canonical widths, padding, literals and valid
calendar values for every encoded field. Format validation does not validate
source rows, including rows pruned out of a query. The supported encoding
contract is owned by [TimeDimension](semantic/semantic-object-model.md#format-specifiers-python-strptime-vs-mysql).

For supported ordered compact/separated date or clock strings and compact
integers, the compiler can invert an exact half-open window into raw-column
comparisons. Each endpoint becomes its first representable point at or after
that boundary: hour encoding over [10:30,11:30) selects >=11 and <12. Aligned
exclusive upper bounds stay excluded. Nulls do not contribute, empty intervals
stay empty, and physical limits/ceiling overflow do not create invalid literals.

Raw ordered clock encodings use the same wall-order boundary translation in
named or fixed source zones. Hour-prefix composition can use lexicographic raw
`(date, hour)` bounds when the date prefix and hour have admitted lineage.
Non-ordered encodings and custom expressions retain native execution. Ordinary
Ibis parser syntax alone does not establish canonical encoding or ordering.

## Physical time execution

Production DuckDB connections use eight threads and UTC. The test suite's
independent one-thread-per-worker cap is not a production connection setting.

The compiler distinguishes three forms per predicate: an exact typed raw-column
comparison (A), a safe coarse scan guard plus the exact native predicate (B), or
native filtering (C). Missing an inverse is an optimization miss. For raw local
clocks, A supplies governed wall-order membership and the native predicate must
not be added back, since it can select skipped clocks differently. Other valid
expressions continue through B/C when exact inversion is unavailable.

Native Instant/CivilDate rewrites preserve backend meaning, endpoints, precision
and nullable truth values. Constants match the raw type; transformed aliases and
implicit column casts do not establish A. Finite sparse calendars retain exact
cell unions, and bucket CASE expressions use the same raw boundaries as filtering.
One-sided windows need no invented lower bound or horizon enumeration.

Compiler-owned UTC cancellation preserves output type and precision. Existing
safe envelopes remain useful for unsupported shapes. No source declaration audit,
production EXPLAIN, generic inverse registry or backend policy replica is added.
Scalar relationship uniqueness, sample completeness, version cardinality and
other independent checks remain. Sampling on raw local clocks uses representable
wall points under the same membership instead of requiring an indistinguishable
first fold occurrence.

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
