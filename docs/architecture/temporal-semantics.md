# Temporal Semantics

Status: implemented current contract.

Civil dates, absolute instants, wall clocks, natural calendar periods and elapsed
durations have different meaning. Semantic declarations own source time roles;
Analysis captures exact boundaries and freezes completed result coordinates.
Business calendar governance belongs to the harness; see
[Business time data](semantic/temporal-authority.md).

## Time authority and exact boundaries

Session creation freezes the default report timezone. session.instant(value,
timezone=None) produces an exact UTC InstantAnchor. session.time_scope(start=...,
end=...) captures [start,end) in that saved Session timezone.
Date-only upper bounds exclude that midnight. There is no implicit latest period
or relative-date language.

Dates and offset-free ISO strings are local civil inputs. Python naive datetime
objects and ambiguous/nonexistent local clocks reject. Aware inputs and existing
anchors retain their absolute instant even when a parsing timezone is supplied.


Ordinary report TimeScopes and CalendarAxes, including those carried by a
Population, must match the receiving Session timezone at observation capture.
A same-zone scope from another Session is allowed; existing object ownership
checks still apply. A mismatched ordinary domain rejects before source reads;
create a Session in the intended report timezone or rebuild the domain in the
receiving Session. Offset-bearing bounds keep their absolute instants but do
not override the ordinary reporting calendar timezone.

| Authority | Purpose |
| --- | --- |
| Field parser, then Datasource `wall_clock_timezone` | Interpret naive source clocks and business DATE values |
| Physical aware type | Preserve an existing absolute instant |
| Session report timezone | Interpret ordinary reporting ranges, calendar groups and labels |
| Frozen result coordinates and source interpretation | Read saved values without current declarations or host state |

`ms.timestamp(timezone=...)`, `ms.date(timezone=...)` and supported
`ms.strptime(format, timezone=...)` declarations override the Datasource default.
Date-only formats accept business-day timezone. `hour_prefix(prefix)` recursively
inherits the prefix field's effective declaration, including its origin.
Missing declarations for naive values and DATE fail before a business read;
readiness handles statically known encodings, while validation qualifies physical
schema. Aware physical fields ignore the Datasource default and reject an explicit
parser timezone. No reader, report or host timezone fills
in missing source meaning.

Native transport settings remain private: DuckDB, PostgreSQL, MySQL and Trino use
their required UTC sessions. ClickHouse DateTime/DateTime64 represents an instant;
its neutral parser/calendar carriers may retain a UTC label to isolate the server
zone. These representation details do not establish a business source timezone.
Naive Entity primary keys retain exact wall-clock identity independently of their
TimeDimension projection and cannot implicitly match aware keys.

A source DATE D retains its DATE projection. For temporal membership, windows,
sampling, grouping and version control it belongs by its native business-day start
D 00:00 in source timezone Z. A report in timezone X groups that instant in X.
This assigns each complete input row; it does not reconstruct event totals within
a different timezone's day. DATE contributions require complete report days in X
and do not support subday grain or non-integral-day durations. X midnight need
not be midnight in Z. Fixed results retain their captured interpretation.

Source parsing, filtering and calendar assignment follow the actual backend or
local kernel's native gap/fold behavior, including its NULL or error result. No
shared gap/fold policy, extra source audit, Python replay or retry through another
executor is introduced. Session anchors and exact scope inputs retain their strict
civil-boundary validation; captured aware boundaries never get reinterpreted by a
source backend.

Timezone inputs share normalized IANA and fixed-offset identity. UTC aliases
normalize to UTC; `Etc/GMT-8` becomes `UTC+08:00`. Other IANA aliases remain distinct
identities. Fixed offsets use offset arithmetic and are never passed as backend
timezone names. UTC may carry absolute keys, but compilation does not require a
source-to-UTC-to-report conversion chain.

Filtering prefers original aware columns and matching absolute constants.
Naive/DATE cutoff rewriting is qualified separately for each endpoint: a fixed
offset is stable; an IANA zone must retain the same offset at hourly samples
through the endpoint's absolute ±48-hour window. MySQL/MariaDB named-zone
rewrites additionally use the common qualified CONVERT_TZ range (1971–2037).
Named-zone raw cutoff qualification uses the host timezone database. Its rules
for the selected dates must agree with the source backend's timezone database;
deployments must keep them aligned. Marivo does not compare database versions
or probe for rule differences before applying these optimizations.
Unqualified endpoints retain their native exact predicates. Qualified physical
columns, including declared ordered encodings, compare directly to same-type
source-domain constants. Columns are not cast, parsed or localized for those
range comparisons. Constants round up to the next represented date/clock tick,
preserving half-open bounds, precision and NULL. Same-zone elimination has the
same native-equivalence requirement. SQL shape alone does not establish index
use or partition pruning.

Ordinary TimeField comparisons also recover a retained physical DATE through
the compiler's business-day-start projection. Each qualified endpoint compares
the original DATE to a DATE constant; boolean structure and unqualified native
predicates remain intact. A builtin day calendar in the same source timezone
reuses that prepared day-start instant when every selected day's boundaries
qualify, including days inside the range. Its key remains a UTC microsecond
timestamp. Trino returns localized instants with a UTC zone label so client
timezone rules preserve the instant selected by the server. Other grains, cross-zone groups and transition-adjacent days retain
native civil truncation.

Ordinary observations over a physical DATE or canonical day encoding may group
by the original value before restoring each aggregate group's business-day start.
Encoded keys admit `%Y%m%d` and `%Y-%m-%d` strings, or `%Y%m%d` integers.
Their native parsing, integer-to-string cast and timezone conversion run on the
aggregate results using the captured source-time authority. This shared
optimization applies to DuckDB, PostgreSQL, MySQL/MariaDB, Trino and ClickHouse;
each backend retains its own native conversion, NULL and error behavior. This requires
the qualified same-zone builtin-day mapping above, direct unanchored source time,
and no window, internal sample or population membership binding. Qualification
also checks the already captured native calendar for consecutive civil midnights
and 24-hour elapsed spans, independently of the local timezone database. This
only admits an optimization; 23/25-hour native days retain the existing path.
The raw day value stays private to the source aggregate; predicate/check summaries use the
same grouping, and UTC microsecond keys are restored before result combination,
calendar completion or persistence. Unused source projections may remain in SQL;
native execution plans establish whether conversion actually runs after grouping.
ClickHouse uses direct Date/Date32-to-DateTime64 conversion only for these
qualified keys. Transition-adjacent dates retain the original native clock
interpretation because direct conversion can choose a different gap result.

Source TimeField comparisons freeze their scalar boundaries when the receiving
Session captures the question, including observe/members conditions and authored
Metric or source-reduction filters. ISO dates, Python dates and offset-free ISO
timestamps use the Session's frozen report timezone, independently of any output
scope or calendar timezone. Aware datetimes, offset-bearing ISO timestamps and
InstantAnchors retain their instant. These inputs share Session scope parsing:
naive Python datetimes, invalid ISO and ambiguous/nonexistent civil clocks reject
before source reads. Internal UTC carriers never interpret user boundaries.

All six comparisons retain their ordinary operators and three-valued NULL logic;
equality selects an exact instant rather than an entire day. DATE operands compare
their source business-day-start instant even without an observation time role.
This comparison read is distinct from the original DATE grouping/projection.
Time-valued branches used by the comparison follow the same boundary rule.
Use `session.instant(value, timezone=...)` for explicit boundary timezones.
Session scopes have exact instant bounds and a frozen report timezone. Ordinary string
Dimensions, physical identity values and fixed Frame Column comparisons retain
their existing types. No DATE subday totals are inferred.

## Output axes and observation time

```python
import marivo.analysis as mv

scope = session.time_scope(start="2026-07-01", end="2026-08-01")
observed = session.observe({"revenue": revenue}, by=(scope.by(mv.grain("day")),)).compute()
observed.show(n=10)
```

A TimeScope in where selects contributions using each branch's fixed clock,
without adding a report coordinate. A CalendarAxis in by captures both the
finite report selection and its grain; it cannot accompany a TimeScope in where.
Bare Grain inputs in observe.by reject.
An explicit Population can carry an exact interval or a finite calendar
coordinate with its own frozen bounds.

Windows require available report endpoints. Native TimeField sample reducers
can use represented samples without an observation period, regardless of
`sample_interval`. Grain-only sampling needs at least one clocked branch; explicit fields
supply their own sample domain. Independent branches retain their scope at
represented sample keys. Builtin calendar samples use the Session report
timezone and actual sample keys. A fixed source predicate such as
a field comparison remains in force throughout a window and supplies no
report endpoint. Membership time, attribute time, Metric observation time and
output coordinates remain independently specified.

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

DATE control uses its declared source-day start to select the target snapshot
business day and supports only DATE validity; cross-zone labels may differ. Absolute timestamp control selects snapshot days in the target's
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

Business calendar keys use ordinary fields. The harness supplies explicit ranges and comparison mappings.

TimeScope.by(grain) constructs a CalendarAxis for an explicit domain without
business reads. It captures selection intent, report timezone and natural grain. During result
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

With a CalendarAxis, ordinary output contains only actual supported complete
joint keys, without filling missing buckets or category combinations. A window
supports its evaluation endpoint through actual window contributions, including
history; a completely empty window does not create a row. Explicit by=Population
retains its complete keys. Independent branches broadcast only at established keys;
fully independent time grouping requires an explicit output Population or an already
compatible time-coordinate provider.

Frozen directories still contain all selected native positions S plus legal extra
observed keys O. Directory presence is not result-row presence. Keep original ordinals,
progress and direct shift origins even when the main table is sparse or empty.
All window branches must define endpoints for each final time key; an extra ordinary
key without an endpoint rejects. See
[observation domains](analysis/populations-and-observation.md#observation-domain-contributions-and-counts).

Builtin `Metric.per(grain)` follows each source branch's contribution time.
`Metric.per(field, grain=grain)` fixes an explicit native sample field.
Both reduce actual internal samples in the Session's report timezone. Grain-only
sampling needs at least one clocked leaf; independent leaves reuse values at the
actual sample keys. Explicit fields and Populations supply their own sample domain.
Presence includes all-NULL samples. Every sample retains complete external,
parent and child keys. Static nesting rules apply to layers on the same time
field and require a common timezone. Subday widths
divide the parent width, day nests in week/month/quarter/year, month in
quarter/year, and quarter in year. Equal builtin grains are valid; week does not
nest in month/quarter/year. Business calendar keys are ordinary dimensions, not natural grains.
Final output completion never changes an internal sample mean or count. See
[internal samples](semantic/expressions.md#internal-samples-and-windows).

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
window for clocked leaves. Independent leaves retain their contribution scope and
combine once at each endpoint; a fixed denominator is not multiplied by window length.
Fully independent windows retain their value in range totals or at keys supplied by
an explicit output Population or another coordinate provider, while still validating
endpoints and rules. A seven-day duration is exactly 168 hours; seven calendar days can differ
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
Builtin offsets apply to unit-step day-or-coarser calendar grains. The grain
factory also admits supported subday widths for bucketing. Subday offsets
are unsupported.

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
single-record interval directories with the same boundary timezone. Historical named result descriptors retain fixed-data ordinal matching only. Shift does not
accept ordinal. Multiple calendar axes and already paired inputs cannot acquire
an arbitrary automatic matching route.

align requires explicit match or equality. compare may consume a compatible
immediate frozen shift relation. A different current origin needs an explicit match
between the saved endpoints; an invalid saved map always rejects. When consuming
the same direct origin, explicit rules must agree with that relation.
Unused directory buckets alone do not create rows. Compare is always outer and requires
actual baseline time rows to map into the current declared domain; general
two-ended time unions belong to align. The complete correspondence and presence
rules are owned by [AnalysisFrame](analysis/analysis-frame.md#correspondence-and-comparison).

Native execution must preserve complete direct-map coverage through calendar
merging and output completion. A baseline target directory containing an extra
unmapped key, a target collision or conflicting executor relations rejects
before the Frame completes or publishes. A legal missing period/cutoff remains
an explicit absence, including an empty target directory with complete origin
records. Automatic compare additionally requires the actual current directory
to equal that saved origin; independently valid current results need not qualify
for inferred matching. Explicit match cannot repair an invalid saved map.

Frozen calendar directories retain actual keys, civil labels, order and selection
progress, plus explicit finite shift correspondences. Equality compares actual
keys under compatible axis/grain/timezone contracts. Offset and ordinal consume
saved civil identity and progress without relocalizing old keys; ambiguous
one-to-many civil matches reject only the affected comparison. Cold reads consume
saved values without current timezone, Catalog or source reconstruction. Missing
or corrupt required metadata fails integrity checks. Calendar coordinates do not certify business source coverage.

## Encoded event-time predicates

`ms.strptime(format)` declares an encoded source time. The ordered formats
`%Y%m%d`, `%Y-%m-%d`, `%Y%m%d%H`, `%Y-%m-%d %H`, `%Y%m%d%H%M`,
`%Y-%m-%d %H:%M`, `%Y%m%d%H%M%S` and `%Y-%m-%d %H:%M:%S` require valid,
fixed-width, zero-padded values with exactly the declared separators. Integers
support the compact formats within their actual physical range. Native string
order must preserve the declared encoding order. Noncanonical values violate
the declaration; permissive parser acceptance is not a membership guarantee.
No extra flag, partition enumeration, source validation query or quality scan
is added. SQL NULL remains NULL and has no period membership.

For a direct physical field or transparent projection, qualified endpoints use
the original column and same-type encoded constants. Each endpoint rounds up
to the first represented source tick: an hourly encoding filtered by
`[10:30,12:30)` becomes `[11:00,13:00)`. Civil dates retain their business-day
start semantics in cross-zone ranges. Date/hour composites compare the original
prefix and hour columns. All consumers of source time ranges share this rule.
Derived expressions, nonordered formats, unsupported carriers and ambiguous
endpoints retain native predicates. Projection and calendar assignment use
native parsing and timezone operations.
Qualified same-zone builtin-day observations can defer these operations until
after grouping by a canonical day encoding, as described above.

Hour-prefix values use integers 0..23 or canonical strings `00`..`23`.
Hour 24 and other noncanonical non-NULL values violate the declaration; native
parser acceptance does not establish supported membership. SQL NULL retains
native semantics. No source scan or uniform parse-error policy is introduced.
For positive contribution filters and required direct/top-level-AND explicit time-boundary
conditions, qualified physical date/hour pairs can use a same-day date equality
and hour lower/upper bounds. MySQL string dates use equivalent ordered date
bounds to avoid the backend compiler's binary cast on string equality.
Both endpoints must pass timezone qualification and round to hours on the same
source day, including a midnight lower endpoint. An empty rounded range selects
no rows. For qualified whole-day, cross-day and one-sided ranges, each eligible
endpoint may additionally expose a comparison on the original date column;
the general time predicate remains in place to select the exact hours and preserve
NULL behavior. Endpoint qualification and source-time rounding are independent;
unsupported expressions and unqualified endpoints retain the general path.
The optimizations preserve the rows for which the native predicate is true;
they are not replacements for nullable Boolean values. Negation, NULL inspection,
Boolean projection and other compositions retain their exact expressions.
Required explicit boundary conditions may add raw scan guards while retaining their
prepared Boolean values. No non-null declaration, source audit or partition
enumeration is required. Actual partition pruning requires native plan evidence.

ClickHouse date cutoffs use `toDate` when the rounded boundary is between
`1970-01-01` and `2149-06-06`, inclusive. Boundaries outside that range use
`toDate32` to preserve historical and extended-range Date32 queries. Ibis maps
native Date and Date32 to the same logical date type, so the literal choice
depends on the rounded boundary. Cutoffs within Date's range remain usable on
executing nodes without `toDate32` support.

Native parsing on DuckDB, Trino, ClickHouse and MySQL/MariaDB returns NULL for
rejected parsed data; PostgreSQL uses native `to_timestamp` and can fail the
entire query. There is no error-policy option. Invalid format, timezone, type
and backend capability remain configuration/preparation errors. No trimming,
guessing or retry is performed.

DuckDB uses `try_strptime`; Trino wraps parsing and conversion in `TRY`;
ClickHouse converts ordered formats to explicit standard date/time text and
uses `toDate32OrNull` or `toDateTime64OrNull(..., 6, 'UTC')` as a neutral
carrier before applying source timezone authority. DATE-to-clock conversions
retain DateTime64 precision and range. This path supports ClickHouse 22.3.
Other formats use `parseDateTimeOrNull`, requiring ClickHouse 23.3 or newer.
The provider qualifies parsers remaining in final executable SQL before issuing
a business read; raw-only ranges do not require an unused parser.
PostgreSQL retains the native
`to_timestamp` acceptance rules, including noncanonical separators and partial
fields. MySQL-family SELECT parsing is followed by native zero-day arithmetic
to reject impossible calendar carriers.
Native parsers have different acceptance rules. Trino TRY can also suppress
recoverable errors evaluating the parser input; other engines can propagate
argument evaluation errors. Configuration and whole-query failures are not caught.

Only declared ordered physical encodings authorize raw range replacement;
arbitrary parser expressions do not acquire that source contract. Unqualified
parsed local clocks and output projections retain native gap/fold interpretation.
Independent physical filters remain eligible for pushdown. Remaining parsing
can increase scan and CPU costs; no source preflight is added. A NULL attribute
control time preserves the fact under a NULL label.

## Physical time execution

Production DuckDB connections use eight threads and UTC. The test suite's
independent one-thread-per-worker cap is not a production connection setting.

The compiler distinguishes exact typed raw-column comparisons, a safe coarse
scan guard plus the exact native predicate, and native filtering. An unavailable
inverse is an optimization miss. Rewrites preserve kind, precision, endpoints and
nullable truth values. Real TimeScope and contribution ranges remain half-open;
one-sided windows need no invented lower bound.

Calendar assignment emits native truncation or local-midnight subday arithmetic.
Single-unit subday buckets can truncate the civil clock directly when the
executing backend is known and qualified. Unbound compiler expressions retain
the general midnight arithmetic. Builtin samples in named zones still distinguish
the two instants in a repeated civil hour. UTC and explicit fixed offsets have
no such ambiguity.
Trino constructs dynamic whole-second intervals by integer multiplication with
a native second interval. Negative timezone offsets and NULL remain native
values rather than passing through duration-string parsing.
It has no calendar directory JOIN, per-bucket CASE/UNION, global UTC lattice,
parent/child execution map, or internal completion grid. Source-produced keys
passed to pandas participate in exact-key alignment and arithmetic without being
parsed or bucketed again. Different sources retain their own time execution.

Required final output indexes use a compact native scalar range expression; this
is result evaluation with O(N) output cost, not a timezone qualification query or
a business-fact scan. MySQL and MariaDB generate positions with products of fixed
0–9 UNION ALL digit tables; SQL size grows with the number of decimal digits,
without recursion or a JSON_TABLE dependency.  No source calendar date directory
is uploaded or reread.

Remaining finite mixed-key parameters use invocation-owned typed SQL relations.
ClickHouse uses JSONExtract/arrayJoin inside SQL, including Distributed requests;
it never turns these parameters into HTTP external data or multipart uploads.
Timestamp wire values are exact integer microseconds, dates are decoded from
strings, and nullable, decimal and special floating values retain their types.
DuckDB retains invocation-owned Arrow registration. MySQL and MariaDB expand one
JSON payload through the same position range and JSON_EXTRACT/JSON_UNQUOTE scalar
functions, including on MariaDB 10.3. Typed projections preserve JSON nulls
separately from the string "null". Derived LIMIT barriers retain parameter
materialization. Other providers retain their native JSON relation carriers.
Complete request bytes, including FORMAT and repeated payload, count toward
the existing SQL limit. Exceeding it fails without
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
