# Time Authority and Source Time Filter Optimization

Status: implementation authorized on 2026-10-22; progress and evidence are tracked
in the [implementation ledger](temporal-timezone-filtering-progress.md).
The original design review was documentation-only. This document records the agreed policy;
it does not replace the implemented [Temporal semantics](temporal-semantics.md).
D01 was revised the same day: constant-bound membership of source local clocks
follows wall-clock order (section 8) instead of inverting each backend's
gap/fold policy.
The same-day hour-prefix decision is to inherit the prefix's resolved timezone;
no new timezone parameter is planned. See the
[implementation plan](temporal-timezone-filtering-implementation-plan.md).

## 1. Correctness and optimization are different requirements

**An analysis must preserve its governed time meaning. A bare-column filter is
an optimization, not a condition for allowing that analysis to execute.**

| Semantic correctness: required on every execution path | Bare-column optimization: apply when proven |
| --- | --- |
| Distinguish civil dates, local clocks and instants | Translate constant bounds to the raw column's representation |
| Use the resolved source timezone and exact analysis boundaries | Remove column-side parsing/conversion only with an equivalent inverse |
| Preserve native gap/fold interpretation of projected values; select source local clocks by wall order (section 8) | Use exact unions where one raw interval is insufficient |
| Preserve endpoints, precision, nulls, bucket membership and owning row domains | Add safe coarse scan guards when an exact inverse is unavailable |
| Preserve transport meaning and frozen result coordinates | Improve partition pruning/index eligibility without promising optimizer choices |

Three execution forms are valid:

| Form | Predicate | Use |
| --- | --- | --- |
| A: exact bare-column filter | Equivalent comparisons on raw columns and typed bounds, possibly an OR of ranges | Preferred when a supported rewrite proves equivalence |
| B: coarse guard plus native filter | A proven safe raw guard AND the original exact converted predicate | Allowed when the guard can reduce scanning without changing semantics |
| C: native filter | The original backend-native converted predicate | Allowed whenever A/B are unavailable or not useful |

Exception: a source local clock with raw lineage always has an A translation
(section 8), and that translation is its governed selection meaning. Native B/C
would select skipped clocks differently, so they are not used for such fields.

No inverse, missing raw lineage, or a non-ordered encoding is an optimization
miss, not a new analysis error. Never remove the exact predicate merely because
a coarse range exists. Do not add a strict-mode switch, user approval step,
mandatory warning or public execution-plan type for B/C. Existing SQL/plan
inspection suffices to distinguish optimization coverage from execution support.

Trust datasource schema and semantic declarations. Marivo does not inspect rows
to establish whether the declared timezone/encoding matches producer intent, or
audit pruned rows for declaration violations. Type compatibility, required
semantic checks and correct adapter decoding still matter. Missing input
meaning after the applicable default policy, invalid DSL, unsupported native
operations or actual precision loss remain correctness errors; B/C cannot repair
them.

Scope is the current TimeScope, CalendarAxis, certified temporal facts, Metric
windows, Population predicates and snapshot/validity consumers. Existing
one-sided `all_history` windows remain legal. There is no new per-row timezone
API, arbitrary temporal join language, or allocation of daily totals to unknown
intraday events. Terminal raw SQL remains outside typed analysis.

## 2. Verified code and DSL baseline

Reviewed HEAD: `202b92421571361e4c41edeb4a7ccff7dec09d53`
(`feat: use backend-native time filtering`), plus the current uncommitted
symbolic-authoring tree. The latter is separate ongoing work; see
[its progress record](semantic/symbolic-authoring-progress.md). Current source,
constructor signatures and focused Live Help were inspected for this revision.
Neither this document nor local probes certify the whole changing tree.

| Current behavior | Evidence / implication |
| --- | --- |
| `time_dimension_column` returns `TimeDimensionSymbol`; no public `ms.finalize` | Compose native symbols in model files; loader binding publishes closed Catalog values. Do not restore the earlier Ref/finalize workflow |
| `session.create(timezone=None)` resolves and freezes the host default | `DatasetRuntime.create` owns this policy. Explicit timezone is recommended for reproducible examples, but is not currently mandatory |
| `session.instant` / `time_scope` accept civil ISO/date or aware datetime inputs | `dsl/temporal.py` rejects naive Python datetimes and unresolved gap/fold boundaries; aware values retain their instant |
| `timestamp` and `strptime` accept an optional parser timezone | `source_time` uses physical awareness, explicit parser authority, or resolved reader authority; it records the chosen origin |
| Live Help has a declaration/default mismatch | `time_dimension.from_column` says naive native fields require an explicit timezone-bearing parse, while parser Help and execution retain reader defaults. Align this disclosure; do not infer a new execution rejection from the Help sentence |
| `hour_prefix(prefix)` has no timezone parameter | `HourPrefixParse` only contains the prefix identity; current compilation combines date and hour, then uses reader authority |
| Source localization/rendering use native functions through Ibis | `_localize_native`, `_render_native` and `lower_temporal` are the existing seams; no cross-backend DST normalization is performed |
| `encoded_time_predicate` replaces some contribution filters | Canonical ordered encodings with suitable authority can use raw bounds; `None` retains native filtering |
| `optimize_time` retains original predicates and adds usable guards | Adding a guard to a converted predicate is B, even if its bounds happen to be exact; no useful guard leaves the original path. UTC cancellation is separate and may still emit a cast when dtypes differ |
| DATE contribution windows require complete civil-midnight boundaries | `validate_date_contribution` rejects subday/clipped non-midnight contribution questions before a business read. Date-bound extraction there is not evidence of a general partial-day rounding bug |
| Calendar bucket assignment compares prepared converted values | `graph_source_execution` and `population_condition` build `ibis.cases` over `temporal_bound`; `query_preparation.selected_time` filters the same way. Under section 8 they must share the filter's raw bounds |
| Sample coverage expects instant-grid points | `binding/sampling.py` builds expected points with `bind_grid` in the source zone, independent of which clocks a naive column can represent |
| `hour_prefix` prefix is only checked to be a time dimension | `validator.py` does not require a civil-date prefix; `query_preparation` passes the prepared prefix value, not its raw column |
| Results already freeze time facts | `SourceTimeAuthority`, calendar/interval directories and existing Store records retain their owning facts. Filter optimization does not by itself require a Store format change |

Owning references:

- [Semantic overview](semantic/overview.md),
  [physical sources](datasource/sources-and-execution.md),
  [analysis architecture](analysis/README.md), and
  [certified temporal authority](semantic/temporal-authority.md).
- [Parser constructors](../../marivo/semantic/_authoring_values.py),
  [time-field constructors](../../marivo/semantic/_authoring_decorators.py),
  [source interpretation](../../marivo/analysis/compiler/source_time.py), and
  [time authority](../../marivo/analysis/core/time_authority.py).
- [Query preparation](../../marivo/analysis/materialization/query_preparation.py),
  [physical filter pass](../../marivo/analysis/materialization/time_filter.py),
  [native lowering](../../marivo/analysis/materialization/temporal_sql.py),
  [calendar assignment](../../marivo/analysis/compiler/observation.py), and
  [version predicates](../../marivo/analysis/compiler/member_version.py).

## 3. Temporal kinds and interpretation

| Kind | Meaning | Rule |
| --- | --- | --- |
| CivilDate | A date label such as `2026-05-01` | No timezone and no inherent instant; preserve the owning date role |
| LocalDateTime | A wall clock such as `2026-05-01 09:00` | Resolve a source interpretation before comparing it with instants |
| Instant | A point such as `2026-05-01T09:00+08:00` | Preserve the instant regardless of source/display representation |

String/integer columns are encodings, not additional temporal kinds. The
existing parser supplies their declared meaning; do not infer it from sample
text or digit count. Native TIME without a date is not an instant. This design
adds neither an epoch parser nor arbitrary offset-text parsing.

`localize(local_clock, source_zone)` interprets a clock. Rendering an instant in
another zone changes its civil representation. They are not inverse identities
at every gap/fold. UTC is a useful comparison/transport representation, not a
requirement to cast every physical column to UTC in SQL or to store every source
as UTC.

### 3.1 Source authority and defaults

The minimum correctness requirement is one resolved interpretation, used
consistently by every consumer. This revision retains the implemented default
policy; changing that policy is not necessary for bare-column optimization:

1. A native instant uses its physical semantics. A parser timezone must not
   relocalize it. Recover omitted native metadata where necessary, rather than
   treating missing Ibis timezone as proof of a naive source.
2. A genuinely local clock uses an explicit `timestamp(timezone=...)` or
   time-bearing `strptime(..., timezone=...)` declaration when supplied.
3. When such a declaration is absent, current execution uses resolved reader
   authority, recording `engine` or `system_fallback`. Preserve that defined
   fallback; do not substitute the Session/report timezone. A failed supported
   reader probe is not permission to invent a zone.
4. CivilDate has no source timezone. An owning snapshot/validity/calendar role
   may separately define which business-day boundaries that date represents.

The reader default is a documented interpretation convention, not evidence that
it matches undeclared business intent. Recommend explicit parser timezone when
that meaning is known. Resolve/freeze the chosen authority through existing
binding, not through a new per-query audit or global business-timezone setting.

Physical semantics and transport are distinct even when the same engine setting
participates in both. For example, UTC transport for MySQL TIMESTAMP must preserve
its instant; it does not establish that a MySQL DATETIME business clock was
produced in UTC. IANA zones also differ from fixed offsets. Keep each public
entry's current accepted syntax: parser declarations currently validate IANA
names, while Session/reader APIs also accept explicit offsets. Do not advertise
a new fixed-offset parser API as part of filter optimization.

### 3.2 Source interpretation versus analysis boundaries

Backend-native gap/fold behavior governs projected source values. Membership of
a source local clock in a constant-bound time selection is defined by Marivo's
wall-order rule (section 8), not by the backend's ambiguity choice. A naive
column cannot distinguish the two occurrences of a repeated clock, and Marivo
does not try to.

Session input validation and calendar construction keep their own existing
contracts: ambiguous or nonexistent civil boundaries reject unless the input
supplies an exact instant; 23/25-hour calendar days and distinct repeated-hour
buckets remain valid. Analysis first captures exact boundary instants; only
then are they translated into each source column's representation.

## 4. Layer responsibilities

| Layer | Required responsibility | Not required by this design |
| --- | --- | --- |
| Datasource / provider adapter | Supply actual physical type/precision and native meaning needed by the selected field; preserve correct driver transport | Per-row timezone verification, a universal metadata registry, or scanning data to infer business meaning |
| Semantic | Declare encoding, source interpretation and existing time roles; trust conforming data; validate compatible declarations | An execution policy or optimizer eligibility gate on otherwise valid expressions |
| Backend through Ibis | Execute native parsing, localization/rendering and ambiguous-time interpretation of projected values | Identical projected values for ambiguous source clocks across providers |
| Analysis compiler | Translate boundaries with the shared timezone rules already used for DSL input; apply proven A or safe B; otherwise use C | A private transition directory, a replica of each backend's ambiguity policy, a general inverse solver, or automatic rejection on optimization miss |
| Frame / Store | Preserve existing values, logical types and frozen interval/calendar facts | Persisting source expression lineage or new optimizer state in every result |
| Presentation | Format known instants without changing their identity | Reassigning already aggregated rows into new calendar buckets |

Keep value expressions and filter rewrites distinct. Native conversion in a
projection, a calendar assignment or a WHERE clause is legitimate. An A claim
requires raw-column lineage in that particular predicate; a transformed alias
is not evidence of physical pushdown. A/B/C may differ by predicate in one query.

Reuse existing adapter facts and Ibis expression lineage. Add metadata only when
a concrete ambiguity needs it, notably implicit-zone ClickHouse timestamps.
`PhysicalFacts` already carries Arrow schema, and Parquet binding preserves
original timestamp-unit metadata. Do not redesign every provider's metadata or
introduce a custom lossless source decoder to support the optimization.

Completed data keeps its frozen interval endpoints, labels and calendar identity.
A new reporting calendar changes aggregate membership and generally needs a new
observation. Display formatting alone does not. Existing cold reads stay
source-free; no new display-only API or persistence migration is prescribed.

## 5. DSL and limited API changes

### 5.1 Existing source declarations

Inside an `ms.load()` model context with the Entity symbol `orders` available:

```python
import marivo.semantic as ms

occurred_at = ms.time_dimension_column(
    name="occurred_at", entity=orders, column="created_at",
    parse=ms.timestamp(timezone="Asia/Shanghai"),
)
encoded_at = ms.time_dimension_column(
    name="encoded_at", entity=orders, column="created_at_text",
    parse=ms.strptime("%Y-%m-%d %H:%M:%S", timezone="Asia/Shanghai"),
)
business_date = ms.time_dimension_column(
    name="business_date", entity=orders, column="date_key",
    parse=ms.strptime("%Y%m%d"),
)
```

These are declaration fragments. Compose the resulting native symbols directly;
the loader closes the graph and publishes Catalog values. There is no explicit
`finalize` step. `parse=None` remains valid for native DATE and native instants.
Restricted Ibis time-field bodies remain legal: syntax acceptance does not prove
an exact inverse, but absence of an inverse does not invalidate native execution.

Keep the existing `timestamp`/`strptime` signatures and default behavior. The
D03 ordered-format set limits optimization, not the accepted parser language.

### 5.2 Hour-prefix source meaning

Current `ms.hour_prefix(prefix)` takes a TimeField/symbol identifying the date
prefix. A date alone supplies no timezone, so the combined local clock currently
uses reader authority. This is a usable defined default, not a reason to disable
D10 or require a new keyword before optimizing it.

Keep `ms.hour_prefix(prefix)` unchanged. The combined clock inherits the
prefix's resolved timezone through its existing source interpretation context;
do not introduce an independent timezone parameter or policy on `HourPrefixParse`.
For a civil-date prefix with no intrinsic timezone, resolve that context through
the prefix's existing reader/default policy, not the Session/report timezone.
The date itself remains CivilDate with no source timezone. Carry the effective
composition authority alongside the prefix's raw lineage during preparation;
do not infer it from a converted value or copy the reporting boundary timezone.

### 5.3 Session, literals and calendars

Retain `session.create(timezone=None)`: omission resolves and freezes the host
zone once, while resume uses saved facts. Requiring every caller to pass a zone
is an additional breaking API policy, not a correctness requirement of this
optimization, and is removed from the implementation scope. Explicit timezone
remains the recommended reproducible spelling:

```python
import marivo.analysis as mv

session = mv.session.create("monthly-sales", timezone="Asia/Shanghai")
may = session.time_scope(start="2026-05-01", end="2026-06-01")
ny_may = session.time_scope(
    start="2026-05-01", end="2026-06-01", timezone="America/New_York"
)
cutoff = session.instant("2026-05-01T00:30:00+08:00")
days = may.by(mv.grain("day"))

# With bound TimeField and Metric expressions from the owning consumer scope:
selected = occurred_at.within(may)
observed = session.observe({"revenue": revenue}, by=(days,))
```

Constructing `selected` does not implicitly filter `observed`. Membership,
observation time, output coordinates and attribute time remain independent.

| Input / consumer | Time interpretation |
| --- | --- |
| Civil date or offset-free ISO boundary | Explicit method timezone, otherwise frozen Session default |
| Aware ISO/datetime, including an InstantAnchor | Retain the exact instant even when another timezone is supplied |
| Naive Python datetime or unresolved gap/fold boundary | Existing validation rejects it |
| TimeScope | Exact half-open `[start, end)` plus its captured calendar timezone |
| `TimeField.within(scope)` | Half-open `>= start AND < end`; wall-order membership for LocalDateTime fields (section 8); retain field-kind/operand compatibility |
| Certified calendar/temporal scope | Retain its captured authority and finite facts |
| DATE contribution axis | Existing complete business-midnight boundaries; no invented intraday allocation |
| DATE snapshot/validity | Preserve the separate version role's business timezone and endpoint semantics |

Do not generalize DATE contribution restrictions to all date comparisons, or
invent a new partial-day date policy. `validate_date_contribution` already guards
the contribution path; version selection has its own date/instant rules.

## 6. Backend evidence and differences

The earlier research used Ibis 12.0.0 and actual table/query schemas, pandas and
Arrow reads. These are recorded observations, not a newly run provider matrix.
This revision rechecked the evidence digest and ran only a small local DuckDB
example check described in section 12. Ibis `!` nullability markers are omitted.

| Backend version | Native type | Observed Ibis type | Required physical interpretation |
| --- | --- | --- | --- |
| DuckDB 1.5.3 | TIMESTAMP | `timestamp(6)` | LocalDateTime; resolved source interpretation needed |
| DuckDB 1.5.3 | TIMESTAMPTZ | `timestamp('UTC', 6)` | Instant; original input zone is not recoverable from this dtype |
| DuckDB 1.5.3 | TIMESTAMP_NS | `timestamp(9)` | LocalDateTime with nanosecond precision |
| PostgreSQL 17.11 | TIMESTAMP WITHOUT TIME ZONE | `timestamp(6)` | LocalDateTime |
| PostgreSQL 17.11 | TIMESTAMP WITH TIME ZONE | `timestamp('UTC', 6)` | Instant |
| MySQL 8.4.11 | DATETIME(6) | `timestamp(6)` | LocalDateTime, independent of connection timezone |
| MySQL 8.4.11 | TIMESTAMP(6) | `timestamp('UTC', 6)` | Instant with a required UTC reader transport contract |
| Trino 483 | TIMESTAMP(6) | `timestamp(6)` | LocalDateTime; connector mapping also matters |
| Trino 483 | TIMESTAMP(6) WITH TIME ZONE | `timestamp('UTC', 6)` | Instant; preserve value rather than original zone identity |
| ClickHouse 26.3.33.24 | DateTime / DateTime64(6) | `timestamp(0)` / `timestamp(6)` | Native Instant despite missing Ibis timezone metadata |
| ClickHouse 26.3.33.24 | DateTime64(6, 'Asia/Shanghai') | `timestamp('Asia/Shanghai', 6)` | Native Instant with column timezone metadata |
| All five | DATE / Date | `date` | CivilDate |
| All five | String containing an ISO offset | `string` or `string(n)` | Still an encoded value; offset text is not schema metadata |

DuckDB also had a Parquet round trip. Trino had memory and Iceberg table reads;
this does not qualify Hive or all connector mappings. ClickHouse's server was
UTC; non-UTC server defaults were not independently exercised in that probe.
Pandas represented native DATE as midnight `datetime64`, while Arrow retained
`date32`: pandas dtype must not reclassify the semantic kind.
Recognizing a schema type does not prove every temporal operation is supported:
current `source_time` rejects certain submicrosecond conversion paths. Keep that
precision boundary distinct from an unavailable bare-column inverse.

The MySQL probe deliberately changed the established connection timezone:

```text
Stored instant:       2026-04-30 16:30:00.123456Z
UTC connection:       2026-04-30 16:30:00.123456+00:00
+08:00 connection:    2026-05-01 00:30:00.123456+00:00  (incorrect instant)
```

Ibis continued marking the result UTC after the connection was changed. This
demonstrates a transport precondition, not a failure established on Marivo's
normal checked connection path. Current MySQL connection code already rejects
failure of Ibis UTC initialization.

### 6.1 Consequences for implementation

- DuckDB/PostgreSQL: preserve naive versus instant type and original precision.
- MySQL: retain native TIMESTAMP transport checks; do not relocalize it as
  DATETIME. Explicit DATETIME source meaning remains separate from transport.
- Trino: distinguish instant/local timestamp and connector mappings. A Trino
  predicate does not establish connector-level partition pruning.
- ClickHouse: recover native Instant meaning for implicit-zone DateTime types
  even when Ibis omits the zone. Do not attach a business localization to an
  already-stored epoch. Missing Ibis metadata is a concrete adapter gap, not a
  reason for a general timezone inference framework.
- ClickHouse native parsing of `strptime` encodings and `hour_prefix` date
  prefixes must evaluate in UTC before the declared source zone is applied;
  otherwise the parsed value adopts a server or session zone. Today's
  `native_parse` has not been confirmed to pin it.
- MySQL `CONVERT_TZ` returns NULL for a named zone whose time zone tables are
  not loaded. Connection verification must cover each declared source zone used
  for projection.

These are classification/transport requirements on A, B and C. In contrast,
constant-side SQL syntax, folding and physical index choice are optimization
concerns. Emit native timezone operations via Ibis and existing provider
lowering. Do not assume Ibis automatically derives their inverses.

A typed native constant expression such as
`col >= backend_convert(constant, ...)` keeps the column bare if SQL introduces
no implicit column-side coercion. It may still prune less well than a literal.
Prefer the existing single-query path; this design does not mandate scalar
prequeries, a bound cache or production EXPLAIN. Consider same-backend scalar
bound evaluation only if measured provider behavior justifies that specific
optimization, preserving exact type and connection context. Lack of constant
folding does not block execution.

### 6.2 Local-clock and literal observations

Local probes with DuckDB 1.5.3 and Ibis 12.0.0 only:

- DuckDB `timezone(zone, timestamp)` maps a New York gap clock with the
  pre-transition offset (`2026-03-08 02:15` and `03:15` both become `07:15Z`)
  and a fold clock with the post-transition offset (`2026-11-01 01:15` becomes
  `06:15Z`). PostgreSQL 17 documents the same rule. MySQL 8.4 documentation is
  silent on `CONVERT_TZ` gaps and folds; Trino and ClickHouse were not probed.
- Python `ZoneInfo` defaults to `fold=0`, the earlier occurrence (`05:15Z` for
  the same clock). Neither Python nor any backend is the membership authority
  for ambiguous source clocks; section 8 is.
- Ibis renders an aware non-UTC `datetime` literal as wall clock plus zone name
  (`MAKE_TIMESTAMPTZ(..., 'America/New_York')`). `fold=0` and `fold=1` produce
  identical SQL that the backend reinterprets.
- Comparing a naive TIMESTAMP column with a UTC TIMESTAMPTZ constant inserts
  `CAST(column AS TIMESTAMPTZ)` and interprets the column in the session
  TimeZone; under a UTC session it missed the intended New York row.

Bound constants therefore use the raw column's own type: naive wall-clock
literals for LocalDateTime and UTC instants for native Instant, never an aware
non-UTC literal. The backend cannot be handed a UTC bound for a naive column.

## 7. Safe filter optimization

### 7.1 Proof obligations

Let `F(c)` be the governed meaning of raw value `c`, and `P` the requested
predicate. Replacing it with raw predicate `Q` (A) requires:

```text
P(F(c)) = Q(c)
```

on values compliant with the trusted declaration. For native Instant and
CivilDate values, `F` is the backend's native meaning. For a source local clock
with raw lineage, `F` is the wall-order interpretation of section 8; it agrees
with native projection except on skipped clocks and, on backends choosing the
earlier occurrence, repeated clocks. A removes the original time predicate only
after this proof. For boolean values
used under NOT/OR or projected as values, preserve full nullable truth semantics;
WHERE row-selection equivalence alone is not enough in those contexts.

A coarse guard `G` (B) instead requires that it cannot exclude a row selected by
`P(F_backend(c))`. Retain the full native predicate and preserve required null
and error semantics. If the guard cannot be established, C is the correct path.
There is no obligation to preserve errors from malformed rows that violate the
trusted encoding contract by auditing otherwise unselected data.

Use the same semantic interpretation in contribution filters, Population,
calendar membership and version selection. Optimization coverage may differ;
it need not be implemented in every consumer at once. Never push a contribution
condition into an independent membership domain or ahead of an owning check.
Preserve complete keys, outer/empty coordinates, multiplicity and version
cardinality regardless of SQL form.

### 7.2 Bounds, precision and raw lineage

For Shanghai May over a native local timestamp explicitly interpreted as UTC:

```sql
WHERE occurred_at >= TIMESTAMP '2026-04-30 16:00:00'
  AND occurred_at <  TIMESTAMP '2026-05-31 16:00:00'
```

For a native Instant, use instant-typed constants; for a Shanghai local source,
the equivalent qualified bounds are local May 1 and June 1 midnights. SQL literal
syntax is provider-specific. Differing source/report zones never prevent A for a
raw local-clock column: section 8 defines its bounds. B/C remain for values
without raw lineage.

For a discrete ordered physical domain, translate the semantic boundary before
quantizing it. Let `ceil(b)` be the first representable value at/after `b`, and
`next(b)` the first strictly after it:

| Logical comparison | Exact physical comparison |
| --- | --- |
| `c >= b` | `c >= ceil(b)` |
| `c > b` | `c >= next(b)` |
| `c < b` | `c < ceil(b)` |
| `c <= b` | `c < next(b)` |

For second precision, `c >= 10:00:00.500` becomes `c >= 10:00:01`.
Equality also needs representability; preserve NULL behavior and handle domain
ends without an overflowing sentinel. Never truncate source precision just to
make a rewrite work. If only the optimization lacks a representable formulation,
retain the native predicate. If the native operation itself loses required
precision, that remains a real execution limitation.

Constants may be cast/constructed to match the raw type. A cast hidden inside
an alias does not become a raw column. Follow existing Ibis lineage through
transparent projections; do not build a universal view-expression inversion
system. Event-time filtering alone does not prove pruning on another partition
key without a declared relationship to that key.

### 7.3 Encodings, calendars and composite time

Use current `_ORDERED_FORMATS` for D03 exact encoding rewrites:

- `%Y%m%d`, `%Y-%m-%d`;
- `%Y%m%d%H`, `%Y-%m-%d %H`;
- `%Y%m%d%H%M`, `%Y-%m-%d %H:%M`;
- `%Y%m%d%H%M%S`, `%Y-%m-%d %H:%M:%S`.

Require compatible raw ordering, canonical width/separators and valid calendar
values under the trusted declaration. Compact forms can use compatible integer
columns and integer bounds; string forms use matching string bounds/collation.
Do not cast a raw integer column to string to claim A. `%d/%m/%Y` may parse
correctly but is not chronologically ordered; keep native filtering instead of
enumerating dates. Successful parsing alone does not prove inverse eligibility.

D08 retains finite calendar cells, sparse scopes and each window's actual
endpoint. January plus March is an exact OR of those months, not the January-to-
April envelope. The envelope is useful only as B with exact membership retained.
Each cell and window endpoint is translated independently (section 8 for raw
local clocks) and adjacent results merged. When the selection uses A, bucket
assignment must compare the raw column with the same constants; a converted
bucket expression could leave a selected row in no cell at a transition. Today
`graph_source_execution`, `population_condition` and `selected_time` all build
cells from converted values. No generic dynamic calendar join or huge
expression expansion is mandated to avoid C.

D10 retains the existing date/hour point encoding. Under a resolved UTC source
interpretation, native DATE `d` and integer hour `h` in 0..23:

```text
[2026-05-01 10:30Z, 2026-05-02 01:30Z)
    -> (d = 2026-05-01 AND h >= 11)
       OR (d = 2026-05-02 AND h < 2)
```

This is a possible A over two bare columns, not a claim of current compiler
coverage. Any declared zone uses the same route: translate each bound with
section 8, quantize the wall clock up to a whole hour (hour 24 becomes the next
day's hour 0), then compare `(d, h)` lexicographically:

```text
lower (D1, H1), upper (D2, H2), D1 < D2:
    (d = D1 AND h >= H1) OR (d > D1 AND d < D2) OR (d = D2 AND h < H2)
same day: d = D1 AND h >= H1 AND h < H2
```

New York `[2026-11-01 06:00Z, 07:00Z)` becomes `d = 2026-11-01 AND h >= 1 AND
h < 2`; the preceding first-occurrence hour is an empty cell. The prefix needs
raw civil-date lineage (native DATE or a D03 date-only encoding); the hour is an
integer or a canonical two-digit string with matching typed bounds. Otherwise
native composition and filtering remain valid. This selects represented points,
not prorated hourly aggregates. Trust the component domain rather than scanning
for invalid hours; a skipped hour such as `02` on the New York spring day is a
producer error (D09).

Validity predicates similarly retain independent start/end authorities, null or
sentinel open ends and exact strict/non-strict operators. Preserve symbolic
before-end selection; do not subtract a timestamp tick.

### 7.4 Minimal compiler integration

Build the correct native predicate first. Reuse `source_time`,
`encoded_time_predicate`, `cancel_utc`, `optimize_time` and `lower_temporal`:

1. Bind the field's real type, parser, authority and owning row domain.
2. Try recognized exact rewrites using existing raw lineage and typed bounds;
   raw local clocks first translate each bound with section 8.
3. Otherwise keep the native predicate, adding only a proven useful guard.
4. Inspect final emitted SQL in validation before claiming A or physical pruning.

The existing `None` result for no inverse remains sufficient. Do not create a
public predicate-plan hierarchy, a provider-wide inverse registry, automatic
query probes or a new error family for an optimization miss. Extend helpers only
where a concrete consumer needs them. Existing B/C behavior is a valid baseline,
not an implementation defect merely because it retains conversion.

## 8. D01: wall-order membership of source local clocks

A naive source column cannot tell the two occurrences of a repeated clock apart,
and Marivo does not try to. For a source local clock with raw lineage (native
LocalDateTime, a D03 time-bearing encoding, or D10 date/hour components),
Marivo defines constant-bound membership itself instead of inverting each
backend's gap/fold policy. This translation is the governed selection meaning
for that field, not an optional rewrite: selection, calendar bucket assignment,
window endpoints, Population predicates and version anchors on the same field
all use it. Values without raw lineage (D05 exclusions) keep native C filtering.

**Interpretation.** For declared zone `z`, a wall clock `w` denotes instant
`I(w)`:

- an ordinary clock denotes its unique instant;
- a repeated clock denotes its later occurrence (the post-transition offset);
- a skipped clock denotes the transition instant.

`I` is non-decreasing in wall-clock order, so every half-open instant range has
one contiguous wall-clock preimage. No OR of pieces is needed.

**Bound translation.** `g(b)` is the smallest wall clock with `I(w) >= b`:

| Boundary instant `b` | `g(b)` |
| --- | --- |
| Ordinary instant, including the second occurrence of a repeated clock | `b` rendered in `z` |
| Inside the first occurrence of a repeated range | Start of the repeated wall range |
| Exactly the transition instant after a skipped range | Start of the skipped wall range |

Then `I(c) >= b` is `c >= g(b)` and `I(c) < b` is `c < g(b)`. Quantize `g(b)`
to the physical domain with section 7.2 (for encodings, to the format's
precision), and encode it in the raw column's own type. A one-sided window such
as `all_history` simply omits the absent bound; no horizon or enumeration is
needed. Rules come from the shared timezone parser already used for DSL
boundaries. Fixed offsets reduce to subtraction.

New York examples (verified against a brute-force minute scan):

```text
2026-11-01, repeated 01:00-01:59
    [05:30Z, 06:30Z) -> c >= '01:00' AND c < '01:30'
    [05:00Z, 06:00Z) -> c >= '01:00' AND c < '01:00'  (empty cell)
2026-03-08, skipped 02:00-02:59
    [06:30Z, 07:30Z) -> c >= '01:30' AND c < '03:30'  (includes 02:xx)
    [07:00Z, 08:00Z) -> c >= '02:00' AND c < '04:00'
```

Converting both endpoints with plain rendering would give `01:30` twice for the
first range and lose `01:00`-`01:29`.

**Accepted consequences.**

- Agreement with native projection: on DuckDB 1.5.3, `I` matched native
  localization for every repeated clock and differed only on skipped clocks
  (native maps `02:30` to `07:30Z`, `I` to `07:00Z`). PostgreSQL documents the
  same repeated-clock choice. A backend choosing the earlier occurrence would
  project a repeated-hour value one hour before the cell selecting it.
- The first occurrence of a repeated hour is an empty cell at hour or finer
  grain, and rows from both occurrences fall into the second.
- Skipped clocks are producer errors under D09; `I` only places them
  deterministically so cells still partition rows.
- Marivo and backend timezone data may differ in version. This affects only
  agreement between projected values and cells, never selection/bucket
  consistency, because both use Marivo's bounds.

Day or coarser cells in a different report zone rarely have boundaries inside a
source transition hour: Shanghai midnight is 11:00 or 12:00 in New York. Zones
with modern fixed offsets such as `Asia/Shanghai` need the rule only for their
historical transitions (1986-1991).

## 9. Final disposition of the eleven scenarios

These IDs retain the earlier decision mapping without carrying forward the
superseded prohibition on B/C. Restrictions below concern new features or exact
optimization; existing native execution remains valid under its own contracts.

| ID | Disposition | Correctness versus optimization |
| --- | --- | --- |
| D01 | Wall-order membership for source local clocks with raw lineage (section 8); no per-backend gap/fold inversion | Projected values keep native interpretation; selection and bucket membership of raw local clocks follow section 8. Values without raw lineage keep C |
| D02 | No new mixed-offset text parser | Native aware columns remain instants. The probed `%Y-%m-%dT%H:%M:%S%z` is rejected by current format validation; custom expressions gain neither new parser support nor automatic A |
| D03 | Optimize only canonical ordered encodings | Valid non-ordered formats such as `%d/%m/%Y` retain native parsing/filtering; no date enumeration |
| D04 | No per-row-zone declaration or branch generator | Existing field-level source authority remains the model |
| D05 | Restrict exact rewrites to raw columns, existing admitted parsing and proven simple elimination | Restricted Ibis bodies such as `coalesce(a,b)` are not rejected merely for lacking an inverse. An optional materialized field may improve performance |
| D06 | No new dynamic temporal join capability | Existing comparisons remain governed by their current contract; row-dependent bounds gain no constant-bound inverse guarantee |
| D07 | No independent horizon feature or cutoff | Existing long/one-sided windows execute; optimization follows the supported rule rather than an arbitrary year limit |
| D08 | Preserve existing finite calendars, scopes and windows | Correct cells, sparse membership and window endpoints are mandatory. When selection uses raw bounds, bucket assignment uses the same bounds |
| D09 | Exclude data/declaration auditing | Trust source timezone, canonical encoding and component domains; retain existing independent semantic validation |
| D10 | Preserve date/hour composition; inherit the prefix timezone; lexicographic raw bounds via section 8 | Requires raw civil-date prefix lineage; otherwise native composition/filtering remains executable. Skipped hours are producer errors (D09). Keep the existing public signature |
| D11 | No reconstruction of unavailable event detail | Keep current DATE contribution and version contracts; daily totals cannot answer unobserved intraday allocation or arbitrary timezone rebucketing |

For D05, transparent aliases and type/precision-preserving UTC cancellation are
initial simple cases. A current `cancel_utc` branch that inserts a cast is not
proof of A. General arithmetic, truncation or custom parser inversion is not
included merely because some examples could be inverted mathematically.

## 10. Changes by owner, without an implementation mandate

| Owner | Correctness / disclosure work | Optional optimization work |
| --- | --- | --- |
| Datasource adapters, concrete engine profiles | Resolve concrete native-type ambiguity such as implicit-zone ClickHouse Instant; retain existing transport checks and original precision | Supply only facts actually needed by a rewrite; no broad metadata redesign |
| `semantic/_authoring_values.py`, `semantic/ir.py`, capture/binding and Help | Align parser/reader-default disclosure; make hour-prefix timezone inheritance explicit without adding a public parameter | No D03/D05 admission gate on otherwise executable expressions |
| `analysis/core/time_authority.py`, `compiler/source_time.py` | Preserve resolved authority and native classification consistently; no unconditional removal of existing defaults | Reuse authority and raw expression facts for exact bound translation |
| `materialization/query_preparation.py`, `time_filter.py`, `temporal_sql.py` | Keep the correct native path for values without raw lineage; emit typed constants matching the raw column (section 6.2) | Implement the section 8 bound translation once and reuse it in encoders; distinguish A from retained B/C |
| `compiler/observation.py`, `member_version.py`, `graph_source_execution.py`, `population_condition.py` | Preserve calendar/version/membership domains, nulls, cardinality and endpoint operators; bucket CASE uses the selection's raw bounds | Use the shared translation where raw lineage exists |
| `binding/sampling.py` | Expected sample points for a raw local-clock field come from representable wall clocks under section 8, so empty repeated-hour cells and skipped hours do not report missing coverage | — |
| `semantic/validator.py` (`hour_prefix`) | Require a civil-date prefix; carry its raw lineage to the D10 translation | — |
| Session / calendar / Frame / Store | Preserve current defaults, boundary validation and frozen facts | No required signature change, source-lineage persistence or Store migration for filtering |
| Help, docstrings and current EN/ZH examples | Reconcile actual default policies, native source interpretation and real errors when implementation changes | Describe exact optimization coverage without presenting it as analysis eligibility |

Use existing error families for real type, timezone, precision and provider
limitations. Do not emit an unsupported-analysis error because an inverse is
missing, or recommend changing the report timezone to reinterpret a source.
Packaged skill edits remain outside this documentation task and require their
own explicit authorization if later implementation affects their guidance.

## 11. Validation and completion criteria

Validate semantics and performance claims separately:

1. On small controlled fixtures, compare native Instant and CivilDate rewrites
   to an independently built native predicate on the **same backend**,
   bypassing the rewrite under test; unambiguous instants can also use literal
   or epoch checks. For raw local clocks, compare against an independent
   wall-order oracle (a brute-force scan of section 8's interpretation) across
   repeated hours, skipped hours, bounds cutting a transition and one-sided
   windows, and check that selection and bucket assignment place each row in
   exactly one selected cell. Include nulls, exact endpoints and precision.
   Divergence of projected values on skipped clocks is expected, not a failure.
2. Cover the changed consumer seams: observations and equivalent Population
   predicates, actual calendar cells/windows, and affected version endpoints.
   Reuse fixtures; do not build a full backend-by-consumer Cartesian matrix.
3. Inspect SQL/lineage for optimization claims. A must have exact raw predicates;
   B retains its exact native predicate; C executes normally. Use representative
   Runtime EXPLAIN only to substantiate specific pruning/index claims, not as a
   production query or universal correctness gate.
4. Verify valid non-ordered/custom expressions still execute through native
   paths. A missing inverse is not a negative execution test. Keep actual
   DSL/type/provider errors separate.
5. Check cold recovery only when a change touches consumed persisted facts;
   unchanged filter plans do not justify a new persistence/recovery project.

The following table retains the original design-review status. Current implementation
and acceptance status is owned by the [progress ledger](temporal-timezone-filtering-progress.md).

| Work item | Completion criterion | Original design-review status |
| --- | --- | --- |
| Design review | Current DSL/code checked; correctness/default-policy choices separated from A/B/C optimization | Completed in this revision |
| Source interpretation alignment | Concrete native-kind gaps and Help/default inconsistencies addressed without data auditing | Identified, not implemented |
| Hour-prefix timezone inheritance | Composite clocks use the prefix's resolved timezone consistently across consumers; the existing signature and CivilDate kind remain unchanged | Planned, not implemented |
| D01 wall-order membership | One shared bound translation used by selection, buckets, windows, Population, version anchors, D03 encoders and D10; sample coverage aligned | Designed, not implemented |
| D03/D05/D08/D10 optimization | Each added rule proves its bounded equivalence and reports its actual coverage; B/C remain valid elsewhere | Planned, not implemented |
| Provider validation | Relevant real-provider results and physical-plan claims verified for changed paths | Not run by this documentation revision |

The original design-only revision changed no code/test files. Its documentation validation covered links,
examples and internal consistency; implementation later uses focused checks and
the repository's required broad gate for shared behavior changes. Runtime and
release acceptance remain separate evidence claims.

## 12. Evidence and limits of this revision

Prior research artifacts are outside the repository:

- `/tmp/marivo-timezone-research-20261022/probe.py`
- `/tmp/marivo-timezone-research-20261022/probe_iceberg.py`
- `/tmp/marivo-timezone-research-20261022/evidence.json`
- Verified evidence SHA-256:
  `3828be581a8251f37877becd939c2103d2f2857c19b3188b6035dfd8129a66bb`

The backend table in section 6 preserves those observations without depending on
temporary-file survival. This revision checked current signatures and five
focused Help targets, confirmed the current format acceptance difference, and
ran local Ibis 12.0.0 / DuckDB 1.5.3 probes. Section 6.2 records the native
gap/fold, literal-rendering and implicit-cast observations. For section 8, a
minute-level brute-force scan over the 2026 New York fold and gap days and the
1988 Shanghai transitions confirmed that the interpretation is non-decreasing,
that the closed-form `g(b)` matches the brute-force minimum for every
15-minute boundary, and that consecutive cells are disjoint. Compared with
DuckDB native localization, it differed on no repeated clock and on each of the
59 skipped minutes `02:01`-`02:59` (`02:00` coincides). PostgreSQL and MySQL
behavior comes from their documentation only. This verifies the rule and
examples, not Marivo end-to-end or provider acceptance. No remote server matrix
was rerun, and no committed implementation tests were added or modified.

References consulted during the earlier backend research:

- [Ibis temporal datatypes](https://ibis-project.org/reference/datatypes).
- [PostgreSQL date/time types](https://www.postgresql.org/docs/17/datatype-datetime.html).
- [MySQL TIMESTAMP and DATETIME](https://dev.mysql.com/doc/refman/8.4/en/datetime.html).
- [Trino temporal types](https://trino.io/docs/current/language/types.html).
- [ClickHouse DateTime](https://clickhouse.com/docs/reference/data-types/datetime).

Current online documentation may differ from the recorded versions. These links
are provenance, not new release or provider-qualification claims.
