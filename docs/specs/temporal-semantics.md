# Unified Temporal Semantics

Status: implemented current contract.

Temporal meaning is governed semantic authority. A civil date, absolute instant,
localizable wall clock, period-calendar partition, temporal occurrence and working
status are different facts. The analysis API consumes certified authority rather
than inferring calendar meaning from a display label.

## Ownership and invariants

Semantic authoring owns time parsing, finite period partitions, correspondence,
temporal occurrences and daily working status. Certification freezes complete
validated snapshots. The catalog supplies exact scopes and grains; a Session
captures those project snapshots without source I/O while constructing a Dataset.

A TimeScope is a literal half-open interval `[start, end)`. Built-in scopes are
interpreted under the persisted Session report timezone; a certified calendar or
occurrence owns its own boundary timezone. A date-only upper bound is excluded at
that midnight. There is no implicit relative-date language, ambient latest-version
selection or end-bound epsilon subtraction.

Source read timezone is resolved when needed at admitted execution, after the actual reader
connection opens. Physical instants and explicit parser authority require no reader-authority probe;
independent adapter transport checks still apply.
Only absent probe capability permits recorded system fallback; query failures and invalid
engine facts fail with their original cause when reader authority is required. Explicit parser authority takes precedence over engine authority
and explicit system fallback. Civil dates do not shift. Naive time values localize
before instant comparison; absolute instants preserve their meaning. Source
precision and logical temporal kind are validated separately. Unsupported exact
precision conversion fails; no silent truncation is permitted. Native naive gap/fold
values fail before filtering or publication. Known instants in a repeated report hour
share the same civil bucket coordinate. Timestamp predicate literals must match their
field kind: naive civil values or aware instants.

## Dataset construction and continuation

```python
logical = session.observe(
    revenue, time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01")
).with_time_axis(order_time, grain=mv.grain("day")).aggregate()
result = logical.execute()
result.show()
```

The example requires authored Metric and TimeDimension refs. Population membership
and observation scopes are independent. Time coordinates, predicates, cumulative
windows and version selection use the same declared temporal interpretation.
Exact ordered Entity identity K does not include snapshot/validity coordinates.

Metric rollup requires valid current contribution state and a strictly admitted
coordinate transition. Means, ratios, distinct membership, cumulative state and
semantic quantiles retain their own fold authority. Calendar rollup uses certified
boundaries, not string ordering or an assumed Gregorian unit length.

Cumulative all-history, grain-to-date and trailing windows keep exact evaluation
endpoints and selected coverage. Reset windows follow report/calendar civil
boundaries. Trailing days and weeks retain fixed86400/604800-second duration.
Retained cumulative coverage uses instants for timestamp axes, so a DST civil day
may have23 or25 hours without becoming incomplete by a24-hour assumption.

Native `window_bucket` is the closed public alignment value. Comparison uses its
registered pairing contract and retains unavailable/missing coordinates explicitly.
A catalog's correspondence or work-schedule object is not itself an analysis
alignment algorithm. Do not infer an additional comparison policy from a semantic
object's existence. Correlation, Forecast and Candidate methods have separate
native admission and coordinate requirements exposed by their focused Help.

Artifact descriptors retain the adopted physical kind, parser/read source,
report-time authority and boundary timezone. Cold retained continuations use those
facts without resolving a new host or datasource timezone. Native immutable
Parquet scans may execute registered algorithms; no database result destination
or executor fallback is introduced.

## Semantic authoring model

Marivo fixes logical field roles and validation rules, not physical table or
column names. A calendar, temporal-set, or work-schedule source may be an
existing governed table or bounded view. Typed refs map its fields into the contracts below; all
required refs for one object belong to one entity so certification needs no
cross-source join. Natural calendar grains need no source entity. Certified
snapshot schemas are fixed and versioned even though input schemas are flexible.

### `PeriodCalendar`

`PeriodCalendar` is a new domain-scoped semantic kind with refs of the form
`period_calendar:<domain>.<name>`. It is authored in Python and participates in
the same decoration, loading, dependency, preview, readiness, and
fingerprint model as other semantic objects.

V1 calendars are day-based and finite. The constructor is:

```python
ms.period_calendar(
    *,
    name: str,
    date: Ref[TimeDimensionKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    levels: Mapping[str, Ref[DimensionKind]],
    correspondences: Mapping[str, PeriodCorrespondence] | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: str | None = None,
) -> Ref[PeriodCalendarKind]
```

The `date` ref must be a civil-date `TimeDimension` with day granularity. V1 does
not accept an instant or wall-clock timestamp as a date-spine key; authors must
expose a true date column. This establishes the one source entity for every
level and correspondence column. Authors do not repeat the entity or source.
`coverage` is a half-open civil-date interval. `boundary_timezone` is an IANA
timezone and is part of the certified content.

### Level mapping

`levels` is the complete public level declaration. Each mapping key is a level
name local to the calendar; its value is the non-null dimension whose value
identifies the containing period for each date. The refs must belong to the date
entity. Values must be globally unique within that calendar level; a value such
as `Q1` is therefore insufficient, while `FY2026-Q1` is valid. There is no
public `PeriodLevel` wrapper or `ms.period_level(...)` constructor: it would only
repeat a name and a ref without adding independent semantics.

For two levels `A` and `B`, certification adds `A -> B` when every `A` period is
wholly contained by exactly one `B` period and at least one `B` period contains
multiple `A` periods. It then computes the transitive reduction for direct edges
and retains the transitive closure for admission checks. If periods cross, there
is no edge. If all boundaries coincide, neither direction is inferred. V1 has no
manual roll-up override: authors place coincident but business-distinct levels in
separate calendars or wait for a later business case to justify an authorization
contract.

Every calendar also exposes a reserved derived level named `day`, keyed by the
date ref itself. Authors do not declare it and cannot reuse that name. It gives
agents a calendar-bound daily grain when the session report timezone differs
from the calendar boundary timezone. The same strict-containment rule applies:
`day` receives an edge to a declared level only when at least one target period
contains multiple dates. A declared one-day level whose boundaries coincide
with `day` receives no identity edge. This preserves the no-edge rule for
coincident business meanings while still allowing day-to-week, day-to-month,
and day-to-quarter roll-up.

For every level, certification proves:

- exactly one non-null key exists for every date in coverage;
- equal keys occupy one contiguous date interval;
- distinct key intervals do not overlap or leave a gap;
- normalized period ordinals are strictly increasing;
- every inferred roll-up edge maps each source period to exactly one
  containing target period;
- the resulting containment graph is acyclic.

The normalized period start, end, global ordinal, containment edges, and ordinal
within each containing target are derived. Authors do not provide redundant
boundary, ordinal, or relationship columns.

### Correspondences

```python
ms.period_correspondence(
    *,
    level: str,
    baseline_key: Ref[DimensionKind],
) -> PeriodCorrespondence
```

`PeriodCorrespondence` is a frozen returned authoring value:

```python
class PeriodCorrespondence:
    @property
    def level(self) -> str: ...
    @property
    def baseline_key(self) -> Ref[DimensionKind]: ...
```

It is not directly constructible, has no catalog collection, and is not added
to the top-level help index. Its containing mapping key supplies the
correspondence name.

The nullable `baseline_key` dimension is on the same date entity. Within each
current period it must be constant. The correspondence name is its key in the
calendar's `correspondences` mapping; `level` must name an entry in `levels`.
Every non-null value must identify exactly one period in the declared level and
coverage. The mapping must be functional
and its non-null targets must be injective: two current periods cannot consume
the same baseline period. V1 comparison is one-to-one.

Null means that the authored correspondence has no baseline for that period.
The analysis call, not the semantic declaration, chooses whether unmatched
periods fail or are dropped. This keeps a business mapping reusable across
strict and exploratory analyses without inventing a fallback.

Separate names encode genuinely different conventions, for example
`prior_year_shifted` and `prior_year_unshifted`. Marivo never chooses between
them from a 53-week shape.

### Authoring example

```python
retail_calendar = ms.period_calendar(
    name="retail_445",
    date=calendar_date,
    boundary_timezone="Asia/Shanghai",
    coverage=(date(2023, 1, 30), date(2028, 2, 1)),
    levels={
        "year": fiscal_year_key,
        "quarter": fiscal_quarter_key,
        "month": fiscal_month_key,
        "week": fiscal_week_key,
    },
    correspondences={
        "prior_year_shifted": ms.period_correspondence(
            level="week",
            baseline_key=prior_year_shifted_week_key,
        ),
        "prior_year_unshifted": ms.period_correspondence(
            level="week",
            baseline_key=prior_year_unshifted_week_key,
        ),
    },
    domain=commerce,
    ai_context=(
        "Retail reporting calendar. Weeks start on Monday. Use the named "
        "correspondence required by the business report around leap weeks."
    ),
)

retail_month = ms.calendar_grain(calendar=retail_calendar, level="month")

mtd_revenue = ms.cumulative(
    name="mtd_revenue",
    base=gross_revenue,
    over=order_time,
    anchor=ms.grain_to_date(grain=retail_month),
)
```

The ordinary 4-4-5 declaration contains no manual relationship wiring.
Certification derives `week -> month -> quarter -> year` from the rows. Authors
who do not intend two levels to share roll-up semantics declare them in separate
period calendars instead of adding an execution policy to a level definition.

`ms.grain_to_date(...)` keeps one `grain: Grain` parameter. It does not grow
independent calendar, level, fiscal, and timezone parameters that can disagree.

### Unified grain and time scopes

`Grain` is the one public immutable aggregation-period abstraction. It is a
closed value with two internal variants; the concrete variant classes are not
top-level exports:

```text
BuiltinGrain:
  kind: "builtin"
  unit: second | minute | hour | day | week | month | quarter | year
  count: int

SemanticGrain:
  kind: "semantic"
  calendar: Ref[PeriodCalendarKind]
  level: str
```

The only built-in constructor requires `unit` and makes `count` keyword-only:

```python
mv.grain(
    unit: Literal[
        "second", "minute", "hour", "day",
        "week", "month", "quarter", "year",
    ],
    *,
    count: int = 1,
) -> Grain
```

Examples are `mv.grain("month")` and `mv.grain("minute", count=5)`.
`mv.grain()` without a unit, an unknown unit, `count < 1`, or a count unsupported
by that unit fails at construction. Bare strings, token strings such as
`"5minute"`, aliases, tuples, dicts, and direct `Grain(...)` construction are not
accepted by any public grain-bearing parameter.

Reusable semantic model code cannot assume a loaded catalog, so semantic
authoring has one constructor for the semantic variant:

```python
ms.calendar_grain(
    *,
    calendar: Ref[PeriodCalendarKind],
    level: str,
) -> Grain
```

Both constructors return the same public `Grain` contract. The common contract
exposes only `kind`, stable identity, equality, hashing, and bounded `repr`.
`count`, `unit`, fixed width, and duration/rank comparisons are builtin-variant
facts, not common `Grain` behavior. Semantic-grain compatibility is resolved
structurally from the certified calendar graph.

```python
class Grain:
    @property
    def kind(self) -> Literal["builtin", "semantic"]: ...
```

`unit` and `count` are rendered for a builtin value; calendar ref and level are
rendered for a semantic value. They are variant contract fields, not optional
common attributes that callers probe with `None` checks.

`TimeScope` is the one public immutable selection-window abstraction. Absolute
windows come from `mv.time_scope(...)`; exact calendar periods and temporal
occurrences come directly from the loaded catalog. The three internal variants
are defined under [Time scopes](#time-scopes), but their concrete classes are not
top-level exports. There is no public handle type, input union, dict input,
`ms.calendar_period(...)`, or `ms.temporal_occurrence(...)`.

Period and occurrence keys are non-null canonical JSON scalars. Date-like
business keys are normalized to ISO strings; NaN, infinity, containers, and
backend-native scalar wrappers are rejected. A catalog-produced scope is frozen,
structurally comparable, hashable, and already bound to the exact certified
snapshot and bounds. Its bounded single-line `repr` exposes business identity
and points to `show()` for detail; it never exposes a project path.

### Catalog navigation and result types

`SemanticCatalog` adds the registered-object collections
`catalog.period_calendars`, `catalog.temporal_sets`, and
`catalog.work_schedules`. The owning domain exposes the same three scoped
collections. Levels, periods, and occurrences are owned members rather than
independently registered semantic objects, so they do not get global catalog
collections, `ms.ref.<kind>` factories, or nested collection chains in the
ordinary consumption path.

The canonical period path is:

```python
calendar = catalog.period_calendars.get("commerce.retail_445")
quarter = calendar.grain("quarter")
fy2026_q2 = calendar.period("quarter", "FY2026-Q2")
current_quarter = calendar.period_on("quarter", date(2026, 5, 15))
page = calendar.periods("quarter", limit=20, cursor=None)
```

The public returned types and members are fixed:

```python
class PeriodCalendarEntry:
    @property
    def ref(self) -> Ref[PeriodCalendarKind]: ...
    def grain(self, level: str, /) -> Grain: ...
    def period(
        self,
        level: str,
        key: str | int | float | bool,
        /,
    ) -> TimeScope: ...
    def period_on(self, level: str, value: date, /) -> TimeScope: ...
    def periods(
        self,
        level: str,
        /,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> CalendarPeriodPage: ...
    def details(self) -> PeriodCalendarDetails: ...
    def show(self) -> None: ...
```

The positional-only `level` and key keep the common call concise and avoid two
spellings. Their order is stable across exact lookup, date lookup, and paging.

| Result type | Public members |
|---|---|
| `PeriodCalendarEntry` | `ref`, `grain(level)`, `period(level, key)`, `period_on(level, date)`, `periods(level, limit=20, cursor=None)`, `details()`, `show()` |
| `CalendarPeriodPage` | `items`, `next_cursor`, `show()` |

`period(...)` and `period_on(...)` return `TimeScope` directly.
`CalendarPeriodPage.items` is a tuple of the same `TimeScope` values, ready for
analysis without another conversion property. The exact returned shapes are:

```python
class CalendarPeriodPage:
    @property
    def items(self) -> tuple[TimeScope, ...]: ...
    @property
    def next_cursor(self) -> str | None: ...
    def show(self) -> None: ...


class CalendarLevelDetails:
    name: str
    key_ref: Ref[DimensionKind] | Ref[TimeDimensionKind]
    period_count: int | None
    direct_finer_levels: tuple[str, ...] | None
    direct_coarser_levels: tuple[str, ...] | None
    rollup_targets: tuple[str, ...] | None

    def show(self) -> None: ...


class PeriodCalendarDetails:
    ref: Ref[PeriodCalendarKind]
    boundary_timezone: str
    coverage: tuple[date, date]
    source_date: Ref[TimeDimensionKind]
    levels: tuple[CalendarLevelDetails, ...]
    correspondences: Mapping[str, str]
    snapshot_status: Literal["missing", "current", "stale", "invalid"]
    parents: tuple[Ref[SemanticKindTag], ...]
    children: tuple[Ref[SemanticKindTag], ...]
    dependents: tuple[Ref[SemanticKindTag], ...]

    def show(self) -> None: ...
```

`correspondences` maps correspondence name to its declared level. The three
certification-derived level fields are non-null exactly when
`snapshot_status == "current"`; otherwise all three are `None`, so an empty
certified graph is never confused with unavailable certification. These detail
types follow the existing catalog details protocol and are returned by
`calendar.details()`; they are not constructors, catalog entries, or top-level
help targets. A calendar-period `TimeScope.show()` adds its key, exact bounds,
global ordinal, containing period keys, and available named correspondence
targets without creating another public entry type.

`calendar.period_on(level, value)` accepts one civil `date` and returns the
unique containing period. `calendar.periods(...)` is ordered by global period
ordinal. `cursor` is an opaque token emitted by the previous page. `limit` is an integer
in `[1, 100]`. It does not accept an offset, unbounded `items`, fuzzy labels, or
relative phrases such as `"current quarter"`. A cursor is bound to the exact
collection snapshot and filters; reuse after the current snapshot changes or
with different filters fails with a fresh first-page continuation.

The canonical occurrence path is:

```python
campaigns = catalog.temporal_sets.get("commerce.cn_events")
scope = campaigns.occurrence("spring-festival-2026")

page = campaigns.occurrences(
    start=date(2026, 1, 1),
    end=date(2027, 1, 1),
    category="statutory_holiday",
    limit=20,
    cursor=None,
)
```

Its exact public methods are:

```python
class TemporalSetEntry:
    @property
    def ref(self) -> Ref[TemporalSetKind]: ...
    def occurrence(
        self,
        key: str | int | float | bool,
        /,
    ) -> TimeScope: ...
    def occurrences(
        self,
        *,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
        category: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> TemporalOccurrencePage: ...
    def details(self) -> TemporalSetDetails: ...
    def show(self) -> None: ...


class WorkScheduleEntry:
    @property
    def ref(self) -> Ref[WorkScheduleKind]: ...
    def details(self) -> WorkScheduleDetails: ...
    def show(self) -> None: ...
```

`TemporalSetEntry` exposes `ref`, exact `occurrence(key)`, bounded
`occurrences(start=None, end=None, category=None, limit=20, cursor=None)`,
`details()`, and `show()`; date/timestamp
arguments must match the set encoding and the interval filter means overlap with
`[start, end)`. `occurrence(key)` returns `TimeScope` directly;
A `WorkScheduleEntry` is an ordinary leaf catalog entry with `ref`, `details()`,
and `show()`. The remaining exact returned shapes are:

```python
class TemporalOccurrencePage:
    @property
    def items(self) -> tuple[TimeScope, ...]: ...
    @property
    def next_cursor(self) -> str | None: ...
    def show(self) -> None: ...


class TemporalSetDetails:
    ref: Ref[TemporalSetKind]
    boundary_timezone: str
    coverage: tuple[date, date]
    occurrence_id: Ref[DimensionKind]
    start: Ref[TimeDimensionKind]
    end: Ref[TimeDimensionKind]
    category: Ref[DimensionKind] | None
    occurrence_count: int | None
    snapshot_status: Literal["missing", "current", "stale", "invalid"]
    parents: tuple[Ref[SemanticKindTag], ...]
    children: tuple[Ref[SemanticKindTag], ...]
    dependents: tuple[Ref[SemanticKindTag], ...]

    def show(self) -> None: ...


class WorkScheduleDetails:
    ref: Ref[WorkScheduleKind]
    boundary_timezone: str
    coverage: tuple[date, date]
    date: Ref[TimeDimensionKind]
    is_working: Ref[DimensionKind]
    snapshot_status: Literal["missing", "current", "stale", "invalid"]
    parents: tuple[Ref[SemanticKindTag], ...]
    children: tuple[Ref[SemanticKindTag], ...]
    dependents: tuple[Ref[SemanticKindTag], ...]

    def show(self) -> None: ...
```

`occurrence_count` is non-null exactly when the temporal-set snapshot is
current. An occurrence `TimeScope.show()` adds its exact key, bounds, and
category. These detail types are public returned values but are not constructors
or top-level help targets.

All entry and page `repr` values are bounded and one-line. `show()` is bounded
and deterministic. Entry methods expose grain or scope acquisition and narrower
paging directly; live help owns preview, readiness, and analysis consumers.
Member lookup reads the immutable
certified snapshot and never queries the datasource. Missing, ambiguous, stale,
out-of-coverage, and cursor errors reuse the existing structured semantic catalog
error family and include a real bounded retry; no new generic temporal lookup
exception is added.

## Certification and readiness

Python declarations are the source of business intent, but a custom calendar's
row values literally define its period boundaries. Sampling a few rows cannot
prove those semantics. `PeriodCalendar` therefore has a stronger preview rule
than ordinary semantic objects.

The canonical workflow is:

```text
inspect -> author -> load -> catalog.require
        -> catalog.preview(scope=explicit_complete_coverage) -> readiness
```

There is no new public `prepare`, `sync`, or `calendar registry` API.

For `PeriodCalendar`, `catalog.preview(calendar, scope=...)` reads the current
datasource directly. The scope must cover the complete declared calendar range,
and its row budget must admit the full set of required date, level, and
correspondence columns plus one sentinel row. Preview validates those current
rows and atomically publishes a compact normalized certified artifact under the
project-local semantic state directory. It never certifies from discovery
profiles, historical samples, or the returned display slice.

The calendar preview's `limit=` controls only rendered example rows. It cannot
reduce certification coverage. The certification read uses the supplied
scope's timeout and row budget, reads one sentinel row beyond that budget
to detect truncation, and must observe scope exhaustion. Rows outside declared
coverage may exist and are excluded deterministically before validation, but the
acquired physical scope itself must fit the chosen acquisition budget. A larger
shared date dimension therefore needs a bounded datasource view or partition
before it can be a V1 calendar source.

Ordinary preview is not a readiness input. A certified period-calendar artifact
is the intentional exception:
its values are executable semantic dependencies, not merely evidence about a
formula. Consequently:

- the calendar itself is not analysis-ready without a matching certified
  artifact;
- any metric or analysis binding that depends on the calendar is blocked when
  the snapshot is missing, stale, invalid, or out of coverage;
- readiness is query-free and checks only project-local state;
- analysis never queries a calendar backend at execution time;
- a business-data scan cannot begin until all temporal dependencies pass.

The artifact manifest separately binds the decorated definition and dependency
digests for stale-readiness checks. The normalized `snapshot_digest` binds the
executable coverage, timezone, levels, periods, containments, and
correspondences. Exact semantic identity is calendar ref and snapshot digest; a
level binding adds its level name. Certified payloads are immutable and
content-addressed. Successful certification atomically advances the calendar's
current manifest to the new digest, while prior certified payloads under this
same contract remain available for artifacts that reference them. Authors do
not invent or synchronize a second human revision identifier.

V1 requires exactly one row per covered civil date.
Acquisition remains bounded by the ordinary datasource scope and timeout; the
semantic API does not add a second hard-coded row-limit policy.

## Normalized snapshot contract

The internal persisted contract is `PeriodCalendarSnapshotV1`:

```text
schema: "period-calendar-snapshot/v1"
calendar_ref
snapshot_digest
boundary_timezone
coverage: [start_date, end_date)
levels[]:
  name
periods[]:
  level_name
  key
  start_date
  end_date
  global_ordinal
containments[]:
  source_level
  target_level
  source_key
  target_key
  ordinal_in_target
correspondences[]:
  name
  level_name
  current_key
  baseline_key | null
```

Records are canonically ordered before hashing. Dates use ISO civil-date text;
keys use the dimension's canonical scalar encoding. `snapshot_digest` covers all
normalized executable fields but excludes storage paths, acquisition timestamps,
and display-only context. `definition_digest` remains manifest evidence for
staleness and is not a second calendar-equality token. Acquisition metadata and
source evidence lineage are stored beside, not inside, semantic identity.

The normalized snapshot may discard daily membership rows after validation
because contiguous period bounds reproduce membership exactly. It retains
periods, the certified containment graph, and correspondence mappings, which are
bounded and sufficient for runtime resolution. The reserved `day` level is
synthesized directly from coverage and is not expanded into persisted period
records. Storage encoding, indexing, and compression are private implementation
details.


## Temporal sets and work schedules

`PeriodCalendar` must remain a partition. The following high-value event concepts
use a separate `TemporalSet` semantic kind delivered after period authority:

- statutory and company holidays;
- adjusted working days when they need a named event window, not as work-status
  authority;
- promotions and shopping festivals;
- launches, incidents, and operational freezes;
- seasons whose intervals overlap or leave uncovered dates.

An activity season may be modeled as a period-calendar level only if the business
definition intentionally makes it exhaustive, gap-free, non-overlapping, and
ordered over the declared coverage. Otherwise it is a temporal set.

The current authoring contract is deliberately parallel to `PeriodCalendar` but
does not expose levels or roll-up:

```python
ms.temporal_set(
    *,
    name: str,
    occurrence_id: Ref[DimensionKind],
    start: Ref[TimeDimensionKind],
    end: Ref[TimeDimensionKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    category: Ref[DimensionKind] | None = None,
    domain: Ref[DomainKind] | None = None,
    ai_context: str | None = None,
) -> Ref[TemporalSetKind]
```

All field refs belong to one entity. `occurrence_id` is unique; `end` is
exclusive and strictly after `start`; intervals must be wholly inside finite
coverage. Overlap and gaps are valid. Civil-date and timestamp sets cannot mix
in one object. A one-day holiday is authored explicitly as `[date, date + 1)`;
Marivo does not infer duration from a holiday label.

`coverage` is always a half-open pair of local civil dates in
`boundary_timezone`. For a timestamp set, those dates resolve to local-midnight
instant bounds and every occurrence must lie wholly inside the resulting
instant interval. For a civil-date set, comparison stays in civil-date space.
This makes the single coverage type exact for both encodings without accepting a
mixed date/datetime tuple.

When supplied, `category` values must be non-empty canonical strings or null;
other dimension scalar types fail certification. This keeps the authoring field,
catalog filter, occurrence scope, and serialized contract on one exact type and
avoids overloading the closed-value `kind` discriminator with business data.

Certification writes `TemporalSetSnapshotV1` with exact occurrence id, start,
end, category, boundary timezone, coverage, and snapshot digest. The same one-read
persisted-value acquisition, immutable payload, atomic current-manifest update,
and readiness rules apply. A recurrence rule such as RFC 5545 may be accepted as
authoring input later, but certification must expand it to explicit occurrences
over finite coverage. Runtime semantics never depend on the version or interpretation
choices of a recurrence library.

`catalog.temporal_sets.get(...).occurrence(key)` produces an exact `TimeScope`
bound to the occurrence's `[start, end)`. It may be used directly as
`time_scope`. This is sufficient for a statutory-holiday or campaign-season
window without adding
holiday and campaign parameters to `observe` or a second string-based selector.

`WorkSchedule` certifies final daily working status. Its current contract is:

```python
ms.work_schedule(
    *,
    name: str,
    date: Ref[TimeDimensionKind],
    is_working: Ref[DimensionKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    domain: Ref[DomainKind] | None = None,
    ai_context: str | None = None,
) -> Ref[WorkScheduleKind]
```

`date` is a civil-date, day-grain time dimension. `is_working` is a non-null
boolean dimension on the same entity. Certification requires exactly one row for
every date in finite `coverage`, with no duplicates or gaps. A normal weekday, a
statutory holiday, a weekend, and a makeup Saturday all use the same final
boolean field. The business source owns precedence among those rules; Marivo
does not expose a second rule engine that could disagree with it.

`WorkScheduleSnapshotV1` stores the ref, boundary timezone, coverage, normalized
daily boolean sequence, and snapshot digest. The physical encoding may use a
compact bitset, but that is private. The same table or date entity may back a
`PeriodCalendar`, a `TemporalSet`, and a `WorkSchedule`; each semantic object is
certified independently and has its own ref and digest. In particular,
`WorkSchedule` has no semantic dependency on `TemporalSet`. A company may use
the holiday names for event analysis while applying a different final working
policy.

```python
cn_sales_schedule = ms.work_schedule(
    name="cn_sales_schedule",
    date=calendar_date,
    is_working=sales_is_working,
    boundary_timezone="Asia/Shanghai",
    coverage=(date(2025, 1, 1), date(2027, 1, 1)),
    domain=commerce,
)
```

For this source, an ordinary Monday may be `True`, a statutory-holiday Monday
`False`, and a makeup-work Saturday `True`. Those are authored facts, not rules
that an analysis agent must combine. Certification uses the same exhaustive
one-read acquisition and atomic immutable-snapshot publication as the other two
temporal kinds.


## Current verification ownership

`tests/test_lazy_temporal_source.py` verifies actual native source parsing,
physical precision, read precedence, report identity, DST, calendar boundaries,
validity selection and exact84/85-hour comparison endpoints. Runtime tests cover
atomic publication, failure cleanup,23/25-hour cumulative coverage and independent
source-offline recovery. Source/fold suites retain independent numerical
expectations for cumulative, first/last, weighted and distinct behavior.

`tests/test_lazy_temporal_public_runtime.py` verifies the public Session wiring for
declared UTC, engine-default native timestamps and string parsing. Tests for
semantic certification remain with their authoring owner. Public Help, bounded
state protocols and executable examples are independently checked during cutover.

See [timezone and calendar design](analysis/timezone-and-calendar-design.md),
[observation contract](../superpowers/specs/2026-09-01-lazy-analysis-observation-model-design.md)
and [materialization contract](../superpowers/specs/2026-09-01-lazy-analysis-materialization-runtime-design.md)
for detailed owning rules.


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
