# Unified Temporal Semantics

Status: proposed clean-slate design. This document defines the only target
cross-layer contract; it does not describe the current runtime. Fiscal cumulative
reset from
[issue #50](https://git.bilibili.co/ace-lab/marivo/-/issues/50) is one motivating
case, not the scope boundary. The contract covers only temporal capabilities that
answer recurring business questions across civil, fiscal, retail, operational,
holiday, and campaign analysis.

The design assumes one atomic breaking replacement of every affected public,
persistence, help, and artifact surface. Prior symbols, accepted input shapes,
storage formats, and artifacts are not inputs to this contract. Compatibility
aliases, deprecation periods, dual reads, migration utilities, and legacy
artifact recovery are intentionally outside scope. This document owns period
structure and temporal alignment;
[`analysis/timezone-and-calendar-design.md`](analysis/timezone-and-calendar-design.md)
continues to own read and presentation timezone behavior.

## Decision

Marivo will not represent every time-related business concept with one generic
`Calendar`, nor add one alignment enum for every fiscal or campaign convention.
It will use six deliberately separate concepts:

1. **`PeriodCalendar`** is governed semantic data that partitions civil dates
   into ordered non-overlapping period levels. Gregorian months, a
   February-ending fiscal year, and a 4-4-5 retail calendar are all period
   calendars. Certification derives which levels contain and may roll up to
   which others; authors do not repeat those relationships by default.
2. **`TemporalSet`** is a certified collection of named, possibly sparse or
   overlapping intervals or dates. Public holidays, promotions, launches, and
   festivals are temporal sets, not period calendars.
3. **`WorkSchedule`** certifies the final working status of every civil date in
   finite coverage. It is the authority for working-day arithmetic; Marivo does
   not reconstruct that status from weekday rules and holiday exceptions.
4. **`Grain`** is the closed aggregation-period abstraction. Built-in units and
   governed calendar levels share one public value type without pretending that
   every period has a fixed duration.
5. **`TimeScope`** is the closed selection-window abstraction. Absolute windows,
   named fiscal periods, and named occurrences all carry exact `[start, end)`;
   semantic variants additionally retain their certified provenance.
6. **`AlignmentPolicy`** states how observations inside two already selected
   scopes are paired. Window-relative buckets, progress within corresponding
   periods, named period correspondence, occurrence-relative progress, and
   working-day progress are different policies.

The semantic layer owns reusable calendar meaning. The analysis layer owns the
selection and alignment operation. A single dependency-neutral
`TemporalResolver` implements period lookup and correspondence for every
analysis path. Backends may optimize that contract only when parity is proved.

This split is the central design choice. It lets the ordinary natural-calendar
path remain as small as it is today while making non-Gregorian behavior exact,
inspectable, and reusable.

## Problem being replaced

The current design establishes several useful principles:

- `TimeScope` is half-open and explicit;
- `Grain` is typed and distinguishes fixed sub-day widths from variable civil
  periods;
- source read timezone and session report timezone have separate owners;
- cumulative reset intent is a typed `ms.grain_to_date(...)` value;
- holiday files are strict local data rather than silently downloaded provider
  state;
- analysis policies and artifact evidence are typed and inspectable.

The limitation is not one missing fiscal enum. Period authority is split across
several independent implementations and contracts:

- the semantic cumulative anchor persists only a grain string;
- grouping, cumulative seeding, local folding, comparison, and dense-spine
  paths can derive Gregorian boundaries independently;
- analysis `CalendarRef` represents holiday dates but has no semantic ref,
  content digest, period levels, or independent boundary timezone;
- the current `AlignmentPolicy` is one model with optional fields whose meaning
  depends on `kind`;
- artifacts can record the chosen behavior without identifying a governed
  custom period authority.

That approach is reasonable for the present Gregorian-plus-holiday scope, but
extending it with `fiscal_year_start_month`, more enum kinds, and more optional
fields would multiply authorities and agent choices. The target keeps those
principles while defining separate governed period, event, and work-schedule
authorities without merging their meanings.

## Goals

- Represent natural years, fiscal years and quarters, 4-4-5 calendars, leap
  weeks, custom operational periods, holidays, adjusted workdays, and campaign
  intervals without hiding business rules in analysis code.
- Give cumulative metrics, observation buckets, roll-up, and comparison one
  exact period authority; give event and working-day comparison their own exact
  occurrence or schedule authority.
- Make every period boundary, roll-up, and cross-period pairing reproducible
  from certified project-local evidence.
- Keep the low-cost path for built-in day, week, month, quarter, and year
  grains.
- Keep public choices closed, typed, and discoverable through `marivo.help(...)`,
  catalog cards, result contracts, and structured repair guidance.
- Fail before scanning business data when a required period, event, or schedule
  authority is missing, stale, ambiguous, out of coverage, or structurally
  invalid.
- Keep artifacts understandable without requiring the current project checkout
  or current calendar snapshot.

## Capability scope by business value

The target is broader than fiscal cumulative reset, but it is not a general time
framework. A capability belongs in this design only when it answers a recurring
business question and cannot be expressed safely by the target `TimeScope`,
`Grain`, or typed alignment policy.

| Capability | Recurring business question | Decision |
|---|---|---|
| natural day/week/month/quarter/year | trend, period-over-period, MTD/QTD/YTD | keep built in; no authoring cost |
| fiscal and retail periods | fiscal MTD/QTD/YTD, 4-4-5 reporting, period-end roll-up | required `PeriodCalendar` capability |
| exact named period | analyze `FY2026-Q2` without reproducing its dates | required period selector |
| period progress | compare one fiscal quarter or year at equal completion | required with one containing period per side |
| named period correspondence | shifted versus unshifted comparison around leap weeks | required retail comparison capability |
| holiday or campaign occurrence | select and compare Spring Festival, 618, launches, or incidents | required `TemporalSet` capability |
| working-day progress | compare business-day 10 with business-day 10 across holiday patterns | required independent `WorkSchedule` capability |
| custom-period forecast | plan by fiscal week or month | supported only by methods that explicitly admit ordinal variable-duration periods |
| recurrence authoring | generate future schedules | deferred; any later rules must normalize to finite occurrences |
| cross-calendar equivalence | compare independently governed fiscal conventions | unsupported until a business-authored mapping contract exists |

The fiscal and retail capabilities from custom periods through named
correspondence form one period-authority capability group. Temporal sets and
work schedules are separate high-value capability groups. Forecast admission is
consumer-specific; calendar support does not imply that every forecast method
is valid.

## Non-goals

- A general scheduling or recurrence engine.
- A UI calendar editor.
- Inferring fiscal structure from column names, sample values, a metric, the
  current date, or an organization's locale.
- Treating holidays as universally non-working days or campaigns as mutually
  exclusive periods.
- Proving two independently authored calendars semantically equivalent.
- Allowing arbitrary SQL expressions or callbacks as calendar rules.
- Embedding an unbounded day-level calendar spine into every analysis artifact.

## Terms and invariants

### Instant, civil date, and interval

An **instant** is an absolute point on the UTC timeline. A **civil date** is a
calendar date without a time or offset. A **half-open interval** includes its
start and excludes its end: `[start, end)`. Marivo uses half-open intervals at
all layer boundaries; no caller or operator subtracts an epsilon from an end.

### Built-in grain

A built-in grain is the current `Grain` value: a supported sub-day division or
one of `day`, `week`, `month`, `quarter`, or `year`. It is interpreted using
Marivo's built-in calendar authority and the session report timezone, preserving
current behavior. The built-in week is ISO week. V1 custom period-calendar levels are
day-or-coarser; sub-day bucketing remains a built-in `Grain` concern.

### Period calendar

A period calendar maps every civil date in a declared finite coverage interval
to exactly one period at each declared level. Periods at one level are ordered,
gap-free, and non-overlapping. Certification compares the resulting period
bounds and derives a directed containment graph. Levels whose periods cross are
valid independent partitions but have no roll-up edge.

The calendar is an authority, not a formatting preference. Its exact identity is
its semantic ref and normalized snapshot digest; a usable binding adds one level
name. Boundary timezone is covered by the snapshot. Two calendars with
coincidentally equal dates are not interchangeable.

### Temporal set

A temporal set contains explicit named occurrences with `[start, end)` and an
optional business category. Occurrences may overlap, leave gaps, or have
different durations.
These properties make temporal sets suitable for holidays and activities and
unsuitable as an aggregation grain. Whether a date is working is not derived
from a temporal set.

### Calendar level and period

A **calendar level** is an ordered set of periods, such as fiscal year, fiscal
quarter, or fiscal week. A **calendar period** is one concrete member, such as
`FY2026-Q2`. Public semantic grains identify levels and exact `TimeScope` values
identify periods without exposing storage paths or physical join details.

### Correspondence

A correspondence is an authored functional mapping from a period at one level
to zero or one baseline period at the same level. It answers questions whose
business convention cannot be derived safely from arithmetic, such as shifted
and unshifted prior-year mappings around a 53-week retail year.

Correspondence does not assert that two calendars or metrics are equivalent. It
only authorizes a named pairing operation inside one governed calendar.

### Classification guide

| Business phrase | Representation | Reason |
|---|---|---|
| natural month, quarter, year, ISO week | built-in `Grain` / built-in period authority | Stable default, no project authoring. |
| fiscal year or quarter, 4-4-5 month/week | `PeriodCalendar` level | Exhaustive ordered partition; containment is certified. |
| MTD, QTD, YTD under either authority | `ms.grain_to_date(...)` | Metric reset semantics, not a query window alias. |
| last 30 days | absolute `TimeScope` or fixed trailing anchor | Fixed interval, not a calendar period. |
| one named fiscal quarter | calendar-produced `TimeScope` | Named selector carries exact certified bounds. |
| named statutory holiday window | `TemporalSet` | Sparse named occurrence used for selection or event comparison. |
| working, non-working, or makeup-work date | `WorkSchedule` | Final daily status, including business-specific holiday policy. |
| promotion, launch, festival | `TemporalSet` | Occurrences may overlap or leave gaps. |
| activity season covering every day exactly once | `PeriodCalendar` level | It is intentionally an aggregation partition. |
| activity season with gaps or overlaps | `TemporalSet` | It is an event interval, not a grain. |
| same offset in two arbitrary windows | window-bucket alignment | Relative query position. |
| same progress in two fiscal periods | period-progress alignment | Relative position under one period authority. |
| shifted or unshifted prior retail year | named period correspondence | Business-authored mapping around leap weeks. |
| same progress in two campaigns or holidays | occurrence-progress alignment | Exact occurrences define the two event windows. |
| same working-day number across months | working-day-progress alignment | A work schedule, not a holiday label, defines eligible days. |

## Ownership

| Concern | Canonical owner |
|---|---|
| Physical date-spine table, columns, metadata, sampling | `marivo.datasource` |
| Calendar definition, period keys, certified containment, correspondence | `marivo.semantic` |
| Named temporal occurrence and working-schedule definitions | `marivo.semantic` |
| Complete temporal certification and dependency readiness | semantic catalog runtime |
| Closed `Grain`, `TimeScope`, and temporal-binding value protocols | dependency-neutral internal temporal values, publicly exported by `marivo.analysis` |
| Absolute-window construction, query grain choice, and alignment policy | `marivo.analysis` |
| Exact period and occurrence navigation | semantic catalog entries |
| Period lookup and progress/correspondence mechanics | dependency-neutral `TemporalResolver` |
| Read timezone | source time-dimension contract |
| Calendar or schedule boundary timezone | owning temporal semantic contract |
| Result presentation timezone | analysis `Session` |
| Choice of fiscal convention or comparison mapping | business owner, authored explicitly |
| Interpretation of an event as a driver | agent or user judgment, never the temporal set |

`TemporalResolver` is a narrow pure module shared by semantic validation and
analysis. It depends on normalized temporal contracts, not on datasource,
semantic registry, Ibis, pandas, or session state. This prevents either public
layer from becoming the accidental owner of time semantics.

The immutable `Grain`, `TimeScope`, and temporal-binding implementations live
beside that resolver in a dependency-neutral internal module. `marivo.analysis`
is their only public export owner; `marivo.semantic` imports the internal value
protocols to construct and consume the same objects without importing
`marivo.analysis`. There is no public `marivo.temporal` module and no duplicate
semantic-layer type. This preserves the repository's one-way
`datasource -> semantic -> analysis` dependency while allowing catalog methods
and semantic authoring helpers to return the unified public values.

## Public contract and agent cost

The design follows progressive disclosure. Most analyses encounter no new
concept. An agent sees calendar details only when a business definition actually
uses a non-default period authority.

### Path 1: built-in calendar, still minimal

```python
frame = session.observe(
    metrics=[gross_revenue],
    time_scope=mv.time_scope(start="2026-01-01", end="2026-04-01"),
    grain=mv.grain("month"),
)

qtd_revenue = ms.cumulative(
    name="qtd_revenue",
    base=gross_revenue,
    over=order_time,
    anchor=ms.grain_to_date(grain=mv.grain("quarter")),
)
```

Built-in grains use one explicit constructor; bare strings and tuples are not
accepted. There is no required global calendar, fiscal start month, calendar
timezone, or session option.

### Path 2: use an existing business calendar

```python
calendar = catalog.period_calendars.get("commerce.retail_445")
retail_month = calendar.grain("month")

frame = session.observe(
    metrics=[gross_revenue],
    time_scope=mv.time_scope(start="2026-02-01", end="2026-05-01"),
    grain=retail_month,
)
```

An absolute scope is never snapped to custom-period boundaries. If retail
months run from the 15th to the next 15th, this scope emits the intersecting
periods; the first and last are partial. Their canonical period bounds remain
`[01-15, 02-15)` and `[04-15, 05-15)`, while observed bounds are respectively
`[02-01, 02-15)` and `[04-15, 05-01)`. The frame marks both as incomplete.

An analysis agent obtains the grain from the loaded calendar or from the
cumulative metric contract. `ms.calendar_grain(...)` remains an authoring-only
constructor for reusable semantic model code, where a loaded catalog cannot be
assumed. The agent does not traverse a public level-entry hierarchy, repeat a
timezone, or reproduce period math.

### Path 3: select one named period

```python
calendar = catalog.period_calendars.get("commerce.retail_445")
fy2026_q2 = calendar.period("quarter", "FY2026-Q2")
retail_week = calendar.grain("week")

frame = session.observe(
    metrics=[gross_revenue],
    time_scope=fy2026_q2,
    grain=retail_week,
)
```

The catalog call returns `TimeScope` directly. It already carries exact certified
bounds and period provenance; preflight validates and lowers it rather than
sending an opaque fiscal predicate to the backend.

### Path 4: compare corresponding periods

```python
delta = session.compare(
    current=current,
    baseline=baseline,
    alignment=mv.period_correspondence(
        correspondence="prior_year_shifted",
        unmatched="fail",
    ),
)
```

For ordinary same-shape windows, the existing default remains
`mv.window_bucket()`. An agent chooses an explicit correspondence only when the
business question requires it.

### Path 5: select and compare named occurrences

```python
campaigns = catalog.temporal_sets.get("commerce.cn_events")
spring_festival_2026 = campaigns.occurrence("spring-festival-2026")

frame = session.observe(
    metrics=[gross_revenue],
    time_scope=spring_festival_2026,
    grain=mv.grain("day"),
)

delta = session.compare(
    current=current_campaign_frame,
    baseline=baseline_campaign_frame,
    alignment=mv.occurrence_progress(anchor="start", unmatched="fail"),
)
```

The two occurrence scopes make the business pairing explicit. The agent does not
construct relative-day columns, infer holidays from labels, or pass event fields
to ordinary observation calls.

A `TemporalSet` occurrence is a governed time window, not a semantic `Event` and
not an analysis `EventFrame`. Observing a metric over an occurrence still returns
a `MetricFrame`; funnel `EventFrame` comparison keeps its mechanically determined
step-and-axis pairing and never accepts an occurrence alignment policy.

### Public surface budget

Period authority requires:

- the built-in `mv.grain(unit, *, count=1)` constructor and unified `Grain`;
- the `mv.time_scope(start=..., end=...)` constructor and unified `TimeScope`;
- semantic authoring constructors: `ms.period_correspondence(...)` and
  `ms.period_calendar(...)`;
- the authoring-only `ms.calendar_grain(...)` constructor;
- direct `PeriodCalendarEntry.grain(...)` and exact-period lookup;
- analysis alignment helpers: `mv.period_progress(...)` and
  `mv.period_correspondence(...)` alongside `mv.window_bucket()` and
  `mv.day_of_week(...)`;
- the public result types needed to inspect grains, scopes, and errors.

Event and working-day support require only their own concepts:

- `ms.temporal_set(...)` and catalog occurrence navigation;
- `ms.work_schedule(...)`;
- `mv.occurrence_progress(...)` and `mv.working_day_progress(...)`.

They do not add holiday, campaign, or schedule fields to period-calendar or
ordinary observation calls. An agent that consumes an existing metric, calendar,
event set, or schedule sees grains, scopes, and copyable continuations rather
than the authoring constructors behind them.

As registered semantic kinds, the three roots also extend the existing typed ref
namespace without introducing a second lookup mechanism:

```python
ms.ref.period_calendar(path: str) -> Ref[PeriodCalendarKind]
ms.ref.temporal_set(path: str) -> Ref[TemporalSetKind]
ms.ref.work_schedule(path: str) -> Ref[WorkScheduleKind]
```

These factories are for declaration-before-definition or cross-file semantic
authoring and exact `catalog.require(...)` recovery. Normal analysis discovery
starts from the typed catalog collections.

### Complete public API inventory

This is the exhaustive target public surface. A symbol not listed here is
internal or an output-schema name only.

| Owner | Target public symbol | Contract |
|---|---|---|
| `marivo.analysis` | `Grain` | one exported immutable type; constructed only by `mv.grain(...)`, `ms.calendar_grain(...)`, or `calendar.grain(...)` |
| `marivo.analysis` | `mv.grain(...)` | canonical built-in constructor; exact signature under [Unified grain and time scopes](#unified-grain-and-time-scopes) |
| `marivo.analysis` | `TimeScope` | one exported immutable type; constructed only by `mv.time_scope(...)` or exact catalog lookup |
| `marivo.analysis` | `mv.time_scope(...)` | canonical absolute-scope constructor; exact signature under [Time scopes](#time-scopes) |
| `marivo.analysis` | `AlignmentPolicy` | one exported immutable type; never directly constructed |
| `marivo.analysis` | `mv.window_bucket`, `mv.period_progress`, `mv.period_correspondence`, `mv.day_of_week`, `mv.occurrence_progress`, `mv.working_day_progress` | the complete six-helper constructor family; exact signatures and admission matrix under [Alignment policies](#alignment-policies) |
| `marivo.analysis` | `Session.observe`, `frame.transform.rollup`, `Session.compare`, `Session.correlate`, `Session.hypothesis_test`, `Session.forecast` | operators with the exact temporal parameters defined in their owning sections; no alternate temporal overload |
| `marivo.semantic` | `ms.period_calendar`, `ms.period_correspondence`, `ms.calendar_grain`, `ms.temporal_set`, `ms.work_schedule` | authoring helpers with the exact signatures in this document |
| `marivo.semantic` | `PeriodCorrespondence` | frozen return type of `ms.period_correspondence`; not directly constructed or independently registered |
| `marivo.semantic` | `ms.grain_to_date` | canonical helper requiring one `Grain` |
| `marivo.semantic` | `GrainToDate` | frozen return type carrying `grain: Grain` |
| typed refs | `PeriodCalendarKind`, `TemporalSetKind`, `WorkScheduleKind` and their `SemanticKind` members | closed semantic kind tags used by refs, catalog collections, readiness, and dependency graphs |
| `marivo.semantic` | `ms.ref.period_calendar`, `ms.ref.temporal_set`, `ms.ref.work_schedule` | exact typed-ref factories |
| semantic catalog | `SemanticCatalog.period_calendars`, `.temporal_sets`, `.work_schedules` and the same properties on `DomainEntry` | typed collections; exact property types below |
| semantic catalog | `PeriodCalendarEntry`, `TemporalSetEntry`, `WorkScheduleEntry` | catalog entry types; only the methods listed in [Catalog navigation and result types](#catalog-navigation-and-result-types) are public |
| returned results | `CalendarPeriodPage`, `TemporalOccurrencePage`, `PeriodCalendarDetails`, `CalendarLevelDetails`, `TemporalSetDetails`, `WorkScheduleDetails` | public returned values with the exact members defined below; no public constructors or top-level help entries |
| output contracts | `TimeScopeContractV1`, `BuiltinPeriodBindingV1`, `SemanticPeriodBindingV1`, `TemporalSetBindingV1`, `WorkScheduleBindingV1`, `PeriodBindingV1`, `TemporalAuthorityBindingV1`, `FrameTemporalContractV1`, `ComparisonTemporalContractV1` | public output-schema names defined under [Artifacts, persistence, and recovery](#artifacts-persistence-and-recovery); never constructors or accepted dict inputs |
| errors | stage-specific semantic and analysis error families | no generic temporal base error; temporal cases and required context are fixed under [Public error contract](#public-error-contract) |

The new catalog properties have one spelling and one type:

```python
class SemanticCatalog:
    @property
    def period_calendars(self) -> CatalogCollection[PeriodCalendarKind]: ...
    @property
    def temporal_sets(self) -> CatalogCollection[TemporalSetKind]: ...
    @property
    def work_schedules(self) -> CatalogCollection[WorkScheduleKind]: ...


class DomainEntry:
    @property
    def period_calendars(self) -> CatalogCollection[PeriodCalendarKind]: ...
    @property
    def temporal_sets(self) -> CatalogCollection[TemporalSetKind]: ...
    @property
    def work_schedules(self) -> CatalogCollection[WorkScheduleKind]: ...
```

The focused help targets are the owning constructor or operator names,
for example `marivo.help("semantic.period_calendar")` and
`marivo.help("analysis.period_progress")`. Entry, page, details, internal
variant, binding, and snapshot class names do not enlarge the global help index;
their live `repr`, `show()`, or `contract()` supplies the next valid call.

There is no generic `CalendarPolicy`, no `fiscal=True`, no
`fiscal_year_start_month`, and no `calendar=` parameter repeated on every
analysis operator. Calendar authority travels with the semantic `Grain` and with
resulting frames.

An agent never needs to calculate or provide:

- a calendar snapshot digest or storage path;
- joins from facts to the date spine;
- daylight-saving conversions;
- end-of-period epsilon arithmetic;
- 4-4-5 or 53-week rollover arithmetic;
- backend-specific date truncation SQL;
- an inferred prior-year mapping.

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
the same decoration, loading, dependency, verification, preview, readiness, and
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
    def contract(self) -> AuthoringContract: ...
    def show(self) -> None: ...
```

The positional-only `level` and key keep the common call concise and avoid two
spellings. Their order is stable across exact lookup, date lookup, and paging.

| Result type | Public members |
|---|---|
| `PeriodCalendarEntry` | `ref`, `grain(level)`, `period(level, key)`, `period_on(level, date)`, `periods(level, limit=20, cursor=None)`, `details()`, `contract()`, `show()` |
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
    def contract(self) -> AuthoringContract: ...
    def show(self) -> None: ...


class WorkScheduleEntry:
    @property
    def ref(self) -> Ref[WorkScheduleKind]: ...
    def details(self) -> WorkScheduleDetails: ...
    def contract(self) -> AuthoringContract: ...
    def show(self) -> None: ...
```

`TemporalSetEntry` exposes `ref`, exact `occurrence(key)`, bounded
`occurrences(start=None, end=None, category=None, limit=20, cursor=None)`,
`details()`, `contract()`, and `show()`; date/timestamp
arguments must match the set encoding and the interval filter means overlap with
`[start, end)`. `occurrence(key)` returns `TimeScope` directly;
A `WorkScheduleEntry` is an ordinary leaf catalog entry with `ref`, `details()`,
`contract()`, and `show()`. The remaining exact returned shapes are:

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
and deterministic. `contract()` exposes only mechanically valid continuations:
grain or scope acquisition, narrower paging, verify/preview/readiness where applicable,
or use as `time_scope`, grain, or schedule. Member lookup reads the immutable
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
inspect -> explicit complete coverage -> sample(persist_values=True) -> author -> load
        -> catalog.verify -> catalog.preview(using=snapshot) -> readiness
```

There is no new public `prepare`, `sync`, or `calendar registry` API.

For `PeriodCalendar`, `catalog.preview(calendar, using=snapshot)` requires a
bound `DiscoverySnapshot` whose scope is exhaustive rather than truncated and
whose persisted selected values cover the date, level, and correspondence
dependencies. Calendar authoring therefore sets `persist_values=True` explicitly.
The one bounded `inspection.sample(...)` acquisition is the authoritative data
read. Calendar preview loads those exact immutable rows locally, validates every
row in declared coverage, and atomically writes a compact normalized certified
snapshot under the project-local semantic state directory. It never performs a
second datasource read and does not certify from profiles or display samples.
Persisted values are plaintext project-local state, so the explicit flag is
the author's privacy decision rather than a hidden calendar side effect.

The calendar preview's `limit=` controls only rendered example rows. It cannot
reduce certification coverage. The preceding snapshot acquisition uses the
bound scope's timeout and row budget, reads one sentinel row beyond that budget
to detect truncation, and must observe scope exhaustion. Rows outside declared
coverage may exist and are excluded deterministically before validation, but the
acquired physical scope itself must fit the chosen acquisition budget. A larger
shared date dimension therefore needs a bounded datasource view or partition
before it can be a V1 calendar source.

Ordinary preview evidence is advisory to later readiness. A certified
period-calendar snapshot is the intentional exception:
its values are executable semantic dependencies, not merely evidence about a
formula. Consequently:

- the calendar itself is not analysis-ready without a matching certified
  snapshot;
- any metric or analysis binding that depends on the calendar is blocked when
  the snapshot is missing, stale, invalid, or out of coverage;
- readiness is query-free and checks only project-local state;
- analysis never queries a calendar backend at execution time;
- a business-data scan cannot begin until all temporal dependencies pass.

The preview manifest separately binds the decorated definition and dependency
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

## One temporal resolver

`TemporalResolver` is the only implementation authority for period behavior. It
is initialized from a built-in algorithm authority or certified normalized
snapshot and exposes pure operations conceptually equivalent to:

```text
period_containing(level, civil_date) -> Period
period_before(level, exclusive_end) -> Period
period_by_key(level, key) -> Period
period_bounds(level, key) -> [start_date, end_date)
bucket_key(level, instant_or_date) -> key
period_progress(level, instant_or_date) -> ProgressCoordinate
containing_period(from_level, key, to_level) -> Period
ordinal_within(from_level, key, to_level) -> int
can_roll_up(from_level, to_level) -> bool
correspondence(name, level, current_key) -> baseline_key | null
```

`period_before` is explicit because cumulative scalar windows end exclusively.
No caller subtracts a microsecond, millisecond, or day to find the containing
period.

These consumers must use the resolver rather than independently truncating
dates:

- ordinary observation bucketing and dense-spine generation;
- cumulative reset periods and partial-period seed selection;
- cumulative distinct first-seen assignment;
- weighted cumulative component alignment;
- scalar and time-series comparison pairing;
- attribution bridge alignment;
- coverage and missing-period diagnostics;
- rendering of period labels and result contracts.

An admitted custom-period forecast also consumes the resolver, but forecast is
not part of the minimum period-authority implementation. Each forecast method
must first declare that ordinal, variable-duration periods are valid inputs.

Built-in Gregorian/ISO behavior uses the same internal resolver contract with
the reserved authority id `builtin:gregorian-iso/v1`. Its boundary timezone is the session
report timezone. A backend-native truncation or spine is an optimization only:
the compiled path must prove parity with resolver-generated keys and boundaries
for the relevant timezone and range. Unsupported parity falls back to explicit
bounds or fails; dialect convenience never changes semantics.

## Execution lowering

Certified temporal data must be executable without a live join to the calendar
source. During preflight, the resolver selects only periods that overlap the
business scan, including any cumulative seed or consumer-authorized history
extension. It produces a canonical bounded relation of period key, requested
containing keys,
local start, local exclusive end, and certified ordinals.

The analysis compiler lowers that bounded relation through a backend adapter.
Inline relations, range predicates, or native date functions are private
optimizations and require parity with resolver keys and boundaries. If no exact
lowering is available, preflight fails before the business scan; it never joins
the mutable source calendar as a fallback.

For timestamp facts, preflight also derives exact instant boundaries from the
calendar's local midnights. The business-window predicate is pushed before the
period relation, then the fact's calendar-local civil date is matched against
`[start_date, end_date)`. Civil-date facts skip instant conversion. The emitted
grouping contract includes period key, start, end, ordinal, and authority id;
display labels never become join keys.

No ordinary semantic relationship is required between the fact entity and the
calendar date-spine entity. This is temporal resolution over a governed
authority, not a guessed business-dimension join.

## Timezones

Marivo retains the current two caller-visible timezone axes and introduces one
semantic axis:

1. **read timezone** belongs to a time dimension when source wall-clock values
   need localization;
2. **semantic boundary timezone** belongs to a `PeriodCalendar`, `TemporalSet`,
   or `WorkSchedule` and determines how instants map to its civil-date bounds;
3. **report timezone** belongs to the analysis session and controls standard
   built-in buckets and result presentation.

Semantic boundary timezone is normally invisible to analysis callers because it
travels with the grain, scope, or schedule binding. It is not a third session
knob.

The conversion is deterministic:

```text
source instant                -> calendar boundary timezone -> civil date
source wall-clock + read tz   -> instant -> boundary timezone -> civil date
source civil date             -> civil date directly
```

Rendering uses the report timezone after semantic resolution. Changing a
session's report timezone must not redefine custom fiscal membership. Built-in
grains continue to use report timezone as their boundary timezone.

A frame contract persists the read-time interpretation used for each source,
the resolved period authority, and the presentation timezone. Two frames from
one session therefore share presentation policy without pretending that their
source or calendar authorities are the same.

## Time scopes

`TimeScope` is the one public immutable selection-window abstraction. It is a
closed value with three internal variants; callers cannot construct the base or
variant classes directly:

```text
AbsoluteTimeScope:
  kind: "absolute"
  start
  end

CalendarPeriodTimeScope:
  kind: "calendar_period"
  start
  end
  calendar_ref
  snapshot_digest
  boundary_timezone
  level
  key

TemporalOccurrenceTimeScope:
  kind: "temporal_occurrence"
  start
  end
  temporal_set_ref
  snapshot_digest
  boundary_timezone
  key
  occurrence_category | null
```

All variants expose only `kind`, normalized
`start: date | datetime`, normalized exclusive `end: date | datetime`, stable
equality and hashing, bounded `repr`, `show()`, and `contract()` as common public
behavior. Strings are constructor input only and never remain in a normalized
scope. Variant provenance is rendered by `show()` and persisted by `contract()`;
callers do not branch on it to reproduce calendar logic.

```python
class TimeScope:
    @property
    def kind(self) -> Literal["absolute", "calendar_period", "temporal_occurrence"]: ...
    @property
    def start(self) -> date | datetime: ...
    @property
    def end(self) -> date | datetime: ...
    def contract(self) -> TimeScopeContractV1: ...
    def show(self) -> None: ...
```

`TimeScopeContractV1` is a bounded returned contract containing the tagged
identity and exact bounds. Its bounded rendering shows the mechanically valid
`time_scope=` continuation. It is also the serialization embedded in artifacts;
callers do not construct or submit it as input. Its exact tagged shape is fixed
under [Artifacts, persistence, and recovery](#artifacts-persistence-and-recovery).

The ordinary absolute constructor is:

```python
mv.time_scope(
    *,
    start: date | datetime | str,
    end: date | datetime | str,
) -> TimeScope
```

Strings must use strict ISO date or datetime encoding. Offset-aware datetimes are
instants; naive datetimes use the report-timezone wall-clock
interpretation; date bounds remain civil dates. Invalid, mixed date/datetime,
empty, or reversed bounds fail at construction. Direct `TimeScope(...)`, dict
input, and public variant constructors are not accepted. Artifact contracts
persist the resolved execution bounds, so the report-timezone interpretation is
never reconstructed from the raw input alone.

Calendar-period and occurrence variants have no free-form constructor. Exact
catalog lookup returns `TimeScope` directly:

```python
quarter = calendar.period("quarter", "FY2026-Q2")
holiday = campaigns.occurrence("spring-festival-2026")
```

The catalog reads the immutable certified snapshot, resolves exact bounds, and
binds the snapshot digest without querying the datasource. Preflight verifies
that exact binding, converts calendar-local civil-date bounds to instants when
required, and persists both semantic provenance and resolved execution bounds.
It does not replace the bound snapshot with the current one.

There is no relative `current_period` public selector in this design. Agents
resolve a period key from the calendar card using an explicit date and receive
the exact scope. No scope reads the wall clock implicitly, and requests outside
certified coverage fail instead of extrapolating period arithmetic.

## Grains and roll-up

Every grain-bearing public parameter accepts only the unified `Grain` value.
There is no public `GrainSelection` or `GrainInput` union and no normalization of
strings, tuples, dicts, or direct model construction. Analysis never infers a
semantic grain from a metric's cumulative anchor, a domain, or similarly named
dimension values.

The temporal consumer parameters are exactly:

```text
session.observe(..., time_scope: TimeScope | None = None, grain: Grain | None = None, ...)
frame.transform.rollup(..., grain: Grain | None = None, ...)
ms.grain_to_date(*, grain: Grain) -> GrainToDate
```

`None` means that the optional parameter is absent; it is not a `TimeScope`
variant. Semantic scopes are validated before the canonical absolute-window
planner. Ordinary observation, roll-up, and cumulative calls gain no `calendar=`,
`level=`, `period=`, `event=`, or `timezone=` parameter. Cohort/lifecycle
operators consume the same `TimeScope` only where their target contract admits
a time window; they do not gain period- or event-specific parameters.

Grain compatibility is structural rather than rank-only. A query bucket is
legal under a custom cumulative reset when the resolver can prove that every
bucket lies wholly inside one reset period. Built-in hour and day buckets can
therefore sit under a fiscal month. A declared retail week can sit under its
retail month. An ISO week that crosses a retail-month boundary is rejected even
though both names suggest a familiar ordering.

A bare built-in bucket keeps report-timezone boundaries. When those boundaries
would cross custom-calendar periods, repair uses the calendar's reserved `day`
grain or a session whose report timezone matches the calendar; Marivo never
silently reinterprets the bare grain. Sub-day custom levels are not in V1, so a
sub-day request that cannot be proved contained fails with the same structural
repair rather than inventing a fourth timezone option.

An absolute `TimeScope` is also never expanded, contracted, or snapped to the
requested grain. For every intersecting semantic period, the frame contract
records:

```text
period_start
period_end
observed_start = max(period_start, scope.start)
observed_end = min(period_end, scope.end)
is_complete = observed_start == period_start and observed_end == period_end
```

Metric values use only `[observed_start, observed_end)`. Period identity and
labels continue to use the certified full bounds. Ordinary observation admits
partial edge periods and makes them visible; roll-up, forecast, and other
consumers that require completeness reject `is_complete=false`. There is no
public snap or partial-period policy parameter. To request complete periods, the
caller passes the exact `TimeScope` returned by `calendar.period(...)`, a
containing period scope, or an absolute scope built from certified period
`start` and `end` values.

Roll-up is legal only along the certified containment graph in the same calendar
snapshot. A 4-4-5 week can roll up to retail month and quarter when its complete
bounds prove those edges. An ISO week that crosses a Gregorian-month boundary
has no week-to-month edge even though both names are familiar.

Cumulative values are last-value roll-ups only when:

- the certified graph admits source level to target level;
- every emitted source period is complete for the requested scope;
- the selected row is exactly the resolver-defined end of the target period;
- the cumulative metric's reset authority matches the source calendar.

Otherwise the operator returns a typed unsupported-shape or incomplete-period
error. It does not approximate with the last available row.

## Cumulative semantics

`ms.grain_to_date(grain=...)` is the only period-reset anchor. Its resolved
public value and resolved contract are:

```python
class GrainToDate:
    @property
    def kind(self) -> Literal["grain_to_date"]: ...
    @property
    def grain(self) -> Grain: ...

ms.grain_to_date(*, grain: Grain) -> GrainToDate
```

The value is returned only by `ms.grain_to_date(...)` and is not directly
constructed. Its resolved contract is versioned and contains one closed period
binding:

```text
schema: "grain-to-date-anchor/v1"
period_binding:
  BuiltinPeriodBindingV1:
    kind: "builtin_period"
    authority_id: "builtin:gregorian-iso/v1"
    level_name
    boundary_timezone
  | SemanticPeriodBindingV1:
    kind: "semantic_period"
    calendar_ref
    snapshot_digest
    level_name
```

`PeriodBindingV1` is exactly the union of those two variants and is reused
unchanged by cumulative anchors, observed frames, roll-up, comparison, and
forecast artifacts. The built-in authority id versions the algorithm; it does
not invent empty calendar digest fields. Semantic grains lower from their closed
calendar and level identity. The authored metric stores the `Grain`;
materialized and observed contracts store the resolved binding. Agent cards
show the human calendar ref and level;
the snapshot digest appears only in exact diagnostics and recovery.

Every cumulative implementation route uses `TemporalResolver` for the reset
period, the period containing a row, the period immediately before an exclusive
window end, partial-period seed range, and coverage checks. This applies equally
to sum, count, count-distinct first-seen, and weighted components. SQL/Ibis and
local execution cannot retain independent period-truncation logic.

Changing any resolved authority field changes cumulative semantic identity.
Artifact readers accept exactly `grain-to-date-anchor/v1`; every other anchor
schema is outside this contract.

## Alignment policies

`AlignmentPolicy` is a frozen public base type with a closed `kind`
discriminator. It is the one exported annotation and accepted input
type, but is no longer directly constructible. The six concrete implementation
classes are deliberately not exported through `marivo.analysis.__all__` or the
top-level help index. Agents construct every value through one of six helpers:

```python
mv.window_bucket(
    *,
    mode: Literal["ordinal_bucket", "calendar_bucket"] = "ordinal_bucket",
    strict_lengths: bool = False,
) -> AlignmentPolicy

mv.period_progress(
    *,
    unmatched: Literal["fail", "drop"] = "fail",
) -> AlignmentPolicy

mv.period_correspondence(
    *,
    correspondence: str,
    unmatched: Literal["fail", "drop"] = "fail",
) -> AlignmentPolicy

mv.day_of_week(
    *,
    within: Grain = mv.grain("month"),
    unmatched: Literal["fail", "drop"] = "fail",
) -> AlignmentPolicy

mv.occurrence_progress(
    *,
    anchor: Literal["start", "end"] = "start",
    unmatched: Literal["fail", "drop"] = "fail",
) -> AlignmentPolicy

mv.working_day_progress(
    *,
    schedule: Ref[WorkScheduleKind] | WorkScheduleEntry,
    unmatched: Literal["fail", "drop"] = "fail",
) -> AlignmentPolicy
```

The common public member is:

```python
class AlignmentPolicy:
    @property
    def kind(
        self,
    ) -> Literal[
        "window_bucket",
        "period_progress",
        "period_correspondence",
        "day_of_week",
        "occurrence_progress",
        "working_day_progress",
    ]: ...
```

Variant-specific members are exactly the arguments of their helper after
normalization; no unrelated optional field appears on another variant.

The alignment-bearing session methods accept that one public type:

```python
class Session:
    @overload
    def compare(
        self,
        current: MetricFrame,
        baseline: MetricFrame,
        *,
        alignment: AlignmentPolicy | None = None,
        analysis_purpose: str | None = None,
    ) -> DeltaFrame: ...

    @overload
    def compare(
        self,
        current: EventFrame,
        baseline: EventFrame,
        *,
        alignment: None = None,
        analysis_purpose: str | None = None,
    ) -> DeltaFrame: ...

    def correlate(
        self,
        a: MetricFrame,
        b: MetricFrame,
        *,
        measure_a: str | None = None,
        measure_b: str | None = None,
        alignment: AlignmentPolicy | None = None,
        method: Literal["pearson", "spearman", "kendall"] = "pearson",
        lag_range: range | Sequence[int] | None = None,
        analysis_purpose: str | None = None,
    ) -> AssociationResult: ...

    def hypothesis_test(
        self,
        a: MetricFrame,
        b: MetricFrame,
        *,
        hypothesis: Literal["mean_changed"] = "mean_changed",
        value_a: str | None = None,
        value_b: str | None = None,
        alignment: AlignmentPolicy | None = None,
        sampling: SamplingPolicy | None = None,
        alpha: float = 0.05,
        analysis_purpose: str | None = None,
    ) -> HypothesisTestResult: ...
```

For `MetricFrame` operators, `None` means that the caller omitted the choice and
the operator uses `mv.window_bucket()`; it is not an alignment variant. Funnel
`EventFrame` comparison also requires `alignment=None`, but there `None` means no
policy is applied because step-and-axis pairing is mechanically determined.
There is no public `AlignmentPolicyInput`, variant union, string, or dict input.

V1 admission is closed; an unlisted combination raises
`AlignmentPolicyNotApplicableError` before pairing:

| Policy | `MetricFrame.compare` | `correlate` | `hypothesis_test` | `EventFrame.compare` |
|---|---|---|---|---|
| `window_bucket` | scalar, segmented, time-series, or panel when the operator shape contract admits it | admitted under the declared axis and lag contract | admitted under the paired-sample contract | not accepted; omit `alignment` |
| `day_of_week` | day-grain time-series or panel only | unsupported | unsupported | unsupported |
| `period_progress` | cumulative scalar, or day-or-coarser time-series or panel, under one exact target period per side | unsupported | unsupported | unsupported |
| `period_correspondence` | complete time-series or panel rows whose semantic grain equals the correspondence level | unsupported | unsupported | unsupported |
| `occurrence_progress` | day-grain time-series or panel selected by exact occurrence scopes | unsupported | unsupported | unsupported |
| `working_day_progress` | day-grain time-series or panel under the supplied exact schedule | unsupported | unsupported | unsupported |

Panel admission additionally requires identical declared non-time axes and
pairs independently within each axis tuple. No calendar-aware policy is legal
for a segmented frame. `correlate` and `hypothesis_test` deliberately remain
`window_bucket`-only in V1; widening their statistical meaning requires a
separate business case rather than inheriting every comparison policy.

Each returned value has a bounded repr, exact `kind`, and only the fields in its
helper signature. Serialization is a closed tagged object. Supplying another
variant's field, passing a dict, or calling `AlignmentPolicy(...)` is rejected
with `AlignmentPolicyValidationError` and a helper call built from the received
state.

No variant accepts a generic `calendar=` that might mean a period partition,
event set, or work schedule.

### Window bucket

```python
mv.window_bucket(mode="ordinal_bucket", strict_lengths=False)
```

With `mode="ordinal_bucket"`, this pairs each current bucket with the baseline
bucket at the same offset from its selected window start. With
`mode="calendar_bucket"`, it pairs identical resolved bucket keys and therefore
usually compares overlapping absolute periods. `strict_lengths` remains local to
this variant. Neither mode claims fiscal or calendar equivalence.

### Day of week

```python
mv.day_of_week(within=mv.grain("month"), unmatched="fail")
```

`within` supplies the containing-period authority; it is not the observation
grain and it is not a holiday calendar. Each frame must have an
effective day grain and resolve to exactly one containing `within` period. The
pairing coordinate is `(iso_weekday, weekday_occurrence_ordinal)`, where the
ordinal counts that weekday from the containing period start. This pairs the
first Tuesday with the first Tuesday even when the two periods start on
different weekdays. Requiring one row per effective local day makes that
coordinate unique; sub-day and coarser frames fail admission. Built-in periods
use the report timezone; a semantic `Grain` uses its certified boundary
timezone. Mixed authorities, zero or multiple containing periods, duplicate
coordinates, and absent coordinates fail before pairing. `unmatched="drop"` is
the only non-failing choice and remains visible in evidence.

### Period progress

```python
mv.period_progress(unmatched="fail")
```

Pairs observations at the same progress coordinate inside corresponding reset
periods already carried by the frames. V1 rejects sub-day time-series input. For
day-grain time series, the coordinate is the zero-based local-day ordinal from
the target-period start. For a coarser built-in or semantic grain, it is the
source-period ordinal within the certified containing target period. Both sides
must use the same effective source grain. For scalar partial cumulative periods,
the coordinate is calendar-relative completed local days plus local
time-of-day, not elapsed UTC seconds. This distinction makes daylight-saving
transitions and unequal month lengths explicit.

V1 admits this policy only when each frame resolves to exactly one containing
target period. Scalar admission is cumulative only, and its target is the reset
period; non-cumulative scalar comparison uses `window_bucket`. For a
non-cumulative time-series or panel frame, the target must come from an exact
calendar-period `TimeScope`. Both sides must use the same target level, but they
may select different period keys such as `FY2026-Q2` and `FY2025-Q2`. A scope
containing zero or multiple target periods fails before pairing; the
implementation never pairs target periods by window order. Multi-period
comparison operates at the emitted period grain with a named correspondence, or
is split into exact single-period comparisons.

If a baseline period cannot represent the coordinate, as with February 29 or a
53rd week, policy is explicit: `unmatched="fail"` or `unmatched="drop"`. There is
no nearest-date or last-observation fallback.

`period_progress` requires both frames to carry the same exact period authority
for the relevant level: calendar ref, snapshot digest, and target level.
Similar labels or coincident boundaries do not qualify.

### Period correspondence

```python
mv.period_correspondence(
    correspondence="prior_year_shifted",
    unmatched="fail",
)
```

Uses one named correspondence certified in the current frame's calendar. The
current and baseline frames must both be time-series or panel frames at the
exact semantic `Grain` named by that correspondence's level, and every candidate
row must represent a complete period. The baseline frame must contain the mapped
period under the same calendar ref, snapshot digest, and level. Pairing is
therefore `current_period_key -> certified baseline_period_key`; the policy
never combines a coarse correspondence with an implicit finer-grain progress
rule. `unmatched="drop"` is allowed for exploratory work and must remain visible
in evidence.

This policy handles business-defined 53-week comparison conventions. It is not
an enum value such as `fiscal_year`, because fiscal calendars can disagree and
the mapping is data, not a universal algorithm.

### Alignment evidence

Every comparison-like artifact records:

- selected policy and resolved period, occurrence, or schedule authority;
- current and baseline window bounds;
- candidate current points;
- paired points;
- current-only and baseline-only points;
- unmatched policy-coordinate or correspondence count;
- dropped count and reason;
- exact execution path (`backend` or parity-equivalent `local`) and whether a
  backend optimization was used.

Only `unmatched="drop"` may produce a non-zero business-data dropped count. A
parity-equivalent local execution path is implementation evidence, not a
business pairing fallback and never changes the paired coordinates. The
contract and bounded `show()` surface display the pairing counts and execution
path before conclusions.

## Operator admission

| Consumer | Custom period behavior |
|---|---|
| `observe` | Groups by an exact semantic `Grain` and emits period identity and bounds. |
| cumulative observe | Uses the authored reset level for seed, reset, coverage, and scalar cutoff. |
| MetricFrame `compare` | Uses the closed policy admission matrix and exact authority checks. |
| EventFrame `compare` | Uses mechanically determined step-and-axis pairing and accepts no alignment policy. |
| `correlate` / hypothesis tests | V1 admits only `window_bucket`; other variants fail before pairing. |
| `attribute` | Reuses the already paired current/baseline temporal evidence; it cannot realign independently. |
| `forecast` | Accepts complete ordered periods and forecasts on ordinal steps only when the method declares variable-duration-period support. |
| raw export | Preserves period keys and bounds but cannot re-enter typed analysis as a new authority. |

Forecasting a fiscal month is not forecasting a fixed number of seconds. The
frame's period ordinal is the regular model coordinate; period start/end remain
for interpretation. A method requiring fixed-duration spacing rejects variable
custom levels. Forecast horizons are period counts under the same calendar
snapshot and must stay within certified coverage; they are never extrapolated by
averaging earlier period lengths.

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

The target authoring contract is deliberately parallel to `PeriodCalendar` but
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

Occurrence-relative comparison is a separate alignment variant:

```python
mv.occurrence_progress(anchor="start", unmatched="fail")
```

Each input frame must have been selected by exactly one temporal-occurrence
`TimeScope` and must have one row per effective local day. With `anchor="start"`,
the coordinate is the zero-based local-day ordinal from the occurrence start;
with `anchor="end"`, it is the zero-based local-day ordinal counted backward
from the exclusive end. The two occurrences may have different absolute dates
and durations, but their day grains and effective boundary timezones must match.
Sub-day, duplicate-day, and coarser frames fail admission. `unmatched="fail"` or
`"drop"` governs a shorter baseline; there is no label-based holiday match or
nearest-date fallback. Cross-year occurrence choice remains explicit in the two
scopes, so Marivo does not guess that similarly named campaigns are equivalent.

`WorkSchedule` certifies final daily working status. Its target contract is:

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

Working-day comparison consumes an exact schedule binding and pairs the same
working-day ordinal inside two selected scopes:

```python
schedule = catalog.work_schedules.get("commerce.cn_sales_schedule")
mv.working_day_progress(schedule=schedule, unmatched="fail")
```

Each input must have one row per effective local day. Only rows whose
schedule-local civil date is working participate; their coordinate is the
zero-based working-day ordinal from the selected scope start. Policy exclusions
and unmatched ordinals are counted separately in alignment evidence. Sub-day,
duplicate-day, and coarser frames fail admission, and the supplied schedule must
cover both scopes under one exact ref and snapshot digest. Ordinary day-of-week
comparison is a separate analysis-only policy and needs no holiday authority.

## Artifacts, persistence, and recovery

An artifact stores one compact closed temporal contract, not the entire
certified spine. The canonical authority variants are:

```text
BuiltinPeriodBindingV1:
  kind: "builtin_period"
  authority_id: "builtin:gregorian-iso/v1"
  level_name: str
  boundary_timezone: str

SemanticPeriodBindingV1:
  kind: "semantic_period"
  calendar_ref: str
  snapshot_digest: str
  level_name: str

TemporalSetBindingV1:
  kind: "temporal_set"
  temporal_set_ref: str
  snapshot_digest: str

WorkScheduleBindingV1:
  kind: "work_schedule"
  work_schedule_ref: str
  snapshot_digest: str
```

`PeriodBindingV1` is exactly
`BuiltinPeriodBindingV1 | SemanticPeriodBindingV1`; the class names, field names,
and `kind` values are identical in cumulative anchors, frames, comparison, and
forecast. `TemporalAuthorityBindingV1` is the closed union of all four variants.
These are public artifact schema names returned through `contract()` and
persisted metadata; they are not constructors and are not exported from
`marivo.analysis.__all__`.

The scope payload embedded in a frame is also one closed tagged schema:

```text
TimeScopeContractV1:
  schema: "time-scope/v1"
  | kind: "absolute"
    start: date | datetime
    end: date | datetime
  | kind: "calendar_period"
    start: date
    end: date
    calendar_ref: str
    snapshot_digest: str
    boundary_timezone: str
    level: str
    key: canonical JSON scalar
  | kind: "temporal_occurrence"
    start: date | datetime
    end: date | datetime
    temporal_set_ref: str
    snapshot_digest: str
    boundary_timezone: str
    key: canonical JSON scalar
    occurrence_category: str | null
```

The scalar constraints are the same as catalog lookup. This output schema has
no constructor, public variant classes, or dict-input path.

An observed frame carries one exact contract:

```text
FrameTemporalContractV1:
  schema: "frame-temporal/v1"
  time_scope: TimeScopeContractV1 | null
  observation_period: PeriodBindingV1 | null
  cumulative_reset_period: PeriodBindingV1 | null
  actual_start
  actual_end
  output_period_keys[]
  display_timezone
```

`TimeScopeContractV1` is the closed tagged serialization of the three `TimeScope`
variants already defined in this document. An absolute scope stores normalized
bounds; period and occurrence scopes additionally store their exact semantic
provenance. `observation_period` is present when the frame is bucketed;
`cumulative_reset_period` is present when the metric has a grain-to-date anchor.
They may intentionally differ, for example daily observations under a fiscal
month reset, and each must match the corresponding authored `Grain`. A frame
with neither leaves both null. There is no generic list of anonymous
authorities.

A comparison-like artifact retains both source contracts rather than pretending
that two observations have one binding:

```text
ComparisonTemporalContractV1:
  schema: "comparison-temporal/v1"
  current: FrameTemporalContractV1
  baseline: FrameTemporalContractV1
  alignment_policy: closed AlignmentPolicy tagged object | null
  resolved_target_period: PeriodBindingV1 | null
  work_schedule: WorkScheduleBindingV1 | null
  alignment_evidence
```

Occurrence authority remains in each source `TimeScopeContractV1`; a working-day
policy adds its one exact schedule binding. `resolved_target_period` is present
only for day-of-week, period-progress, or period-correspondence admission and
must be mechanically derived from, and authority-compatible with, the source
scope, observation, or cumulative-reset bindings described by that policy.
For every `MetricFrame` comparison, an omitted argument is normalized and
persisted as `mv.window_bucket()`; `alignment_policy` is null only for
`EventFrame` comparison, whose pairing is mechanical. Every variant rejects
another variant's fields. Persisted tagged objects are output contracts, not
accepted public dict inputs.

The semantic period variant contains only calendar ref, snapshot digest, and
level name. A temporal-set or work-schedule variant contains its ref and snapshot
digest. The work-schedule digest binds the final daily status rather than a set
of upstream rule dependencies. This is sufficient to understand exact identity
without duplicating definition and
containment digests that can disagree. The contracts also store resolved bounds,
actual output keys, display timezone, and alignment evidence required to read
them offline; those are observed facts, not additional authority-equality
tokens.

Reading an artifact does not require the current project snapshot.
Replaying, extending, or recomputing it requires the exact certified snapshot
identified by the binding. If unavailable, Marivo reports the missing authority;
it never substitutes the newest snapshot.

Prepared execution writes the resolved temporal contract into the artifact plan
before any business scan. A failed certification or preflight leaves no partial
analysis artifact. Snapshot certification writes a new immutable payload and
atomically updates the current manifest only after validation and hashing
succeed. A failed publication leaves the previous current snapshot readable.

Session indexes treat every closed temporal-contract field as a semantic input
to content identity. Reuse is impossible across any scope, binding, policy, or
resolved-authority change even when the generated backend SQL happens to be
textually equal.

## Discovery and repair

`marivo.help(ms.period_calendar)` owns constructor constraints and the next
mechanical call. `marivo.help("analysis.calendar.period")` owns the exact
snapshot-bound period lookup syntax. Catalog cards own loaded calendar structure
and copyable grains and scopes. Analysis errors own operation-specific repair.
Skills teach the workflow but do not duplicate parameter tables.

Required typed failures include:

| Stage | Failure | Repair surfaced to the agent |
|---|---|---|
| decoration | invalid timezone, coverage, or duplicate level name | exact argument and allowed shape |
| load | cross-entity date/key dependency | offending refs and required date entity |
| verify | unsupported date granularity or nullable required key | exact semantic dependency to repair |
| preview | exhaustive values were not persisted | exact `inspection.sample(..., persist_values=True)` continuation |
| preview | incomplete date coverage | first missing ranges and required full coverage |
| preview | gap, overlap, or discontiguous period key | levels, period keys, and bounded example dates |
| preview | invalid correspondence | mapping name, current key, conflicting or absent baseline keys |
| certification | definition or source content changes during publication | failed atomic publication and the exact retry |
| readiness | missing or stale certified snapshot | exact `catalog.preview(..., using=...)` continuation |
| preflight | requested date or key out of coverage | available bounds or bounded nearest period keys |
| preflight | mixed calendar authorities | both bindings and the grain that must be made explicit |
| preflight or transform | consumer requires complete periods but edge periods are partial | incomplete count, bounded keys, observed/full bounds, and exact full-scope repair |
| execution | backend optimization parity unsupported | exact parity-equivalent local path used or explicit unsupported-path repair |
| alignment | zero or multiple containing periods, missing progress, or correspondence match | counts, keys, and allowed next policies |
| recovery | exact snapshot unavailable | required ref and digest; no newest-snapshot suggestion |

Errors must be bounded. Large gap lists, period lists, and unmatched keys expose a
count, first examples, and a typed pagination/inspection continuation.

### Public error contract

Temporal failures use the canonical stage-specific error owner. A parallel
generic temporal hierarchy would obscure which stage can repair the failure:

| Failure owner | Public error type | Required temporal context |
|---|---|---|
| semantic decoration, load, certification | `SemanticDecoratorError`, `SemanticLoadError`, or `SemanticRuntimeError` | semantic ref, level or occurrence key, `constraint_id`, received value, expected contract, exact repair |
| missing/stale catalog member or bad cursor | `SemanticRuntimeError` with the catalog lookup `ErrorKind` | collection scope, received key/cursor, bounded exact candidates or next page call |
| unresolved readiness dependency | `SemanticProjectNotReadyError` | required ref, snapshot status, exact preview/readiness continuation |
| invalid or uncovered scope | `WindowInvalidError` | requested bounds/key, certified coverage, boundary timezone, exact catalog retry |
| unsupported or crossing grain | `GrainUnsupportedError` | source and requested grain bindings, failed containment fact, legal grains |
| invalid helper arguments | `AlignmentPolicyValidationError` | received fields, accepted helper signature, copyable helper call |
| policy cannot apply to frame shapes or authorities | `AlignmentPolicyNotApplicableError` | both exact bindings, containing-period counts, required shape, alternative legal policy |
| pairing fails after admission | `AlignmentFailedError` | candidate, paired, unmatched and dropped counts plus bounded keys |
| illegal roll-up or incomplete period | `TransformShapeUnsupportedError` | source/target levels, certified graph fact, incomplete keys and observed/full bounds, nearest legal scope or target grain |
| unsupported forecast method or coverage | `ForecastShapeUnsupportedError` | model, period binding, required coverage and supported model repair |

The stage exception class is the coarse programmatic category; each temporal
case has a stable `case`/`constraint_id` discriminator in structured context.
This avoids exposing `TemporalError`, `CalendarError`, and one subclass per
resolver operation while preserving machine-readable repair. Every message says
what was expected, what was received, and the next legal call derived from the
active catalog. Snapshot paths and digests may appear in exact diagnostics but
never as values the caller must construct.

## Agent workflows

### Consume a normal metric

1. Use the metric ref and a built-in `mv.grain(...)` value.
2. Observe and read the typed frame.

No calendar discovery is required.

### Consume a fiscal cumulative metric

1. Inspect the metric card or contract; it already names the resolved reset
   calendar and level.
2. Observe at the metric's natural grain, or follow the offered calendar card
   continuation and call `calendar.grain("<level>")` when another compatible
   fiscal grain is required.
3. For comparison, accept the mechanically available default only when it
   matches the question; choose a named correspondence when the card reports
   leap-period ambiguity.

The agent never re-authors the fiscal convention in the analysis call.

### Author a new calendar

1. Inspect the physical date-spine metadata and choose one explicitly bounded
   source scope and the required columns.
2. Settle the business-owned timezone, coverage, level names, and any named
   comparison correspondences.
3. Acquire those columns once with exhaustive scope and
   `persist_values=True`.
4. Declare dimension refs and one period calendar with a direct `levels`
   mapping.
5. Load and statically verify it.
6. Preview against the exact persisted rows, review structural findings, and
   obtain a certified normalized snapshot digest without another datasource
   read.
7. Check readiness, then obtain exact semantic grains from the calendar entry.

The acquisition is still the ordinary datasource call; the important new
precondition is exhaustive scope:

```python
snapshot = inspection.sample(
    scope=md.unpruned(max_rows=100_000, timeout_seconds=30),
    columns=(
        "calendar_date",
        "fiscal_year_key",
        "fiscal_quarter_key",
        "fiscal_month_key",
        "fiscal_week_key",
        "prior_year_shifted_week_key",
    ),
    persist_values=True,
)
```

If this acquisition reports `scope_exhaustion="truncated"`, the agent must use a
bounded calendar source or choose a larger explicit datasource scope supported
by the environment. Preview never turns a truncated sample into authority.

This is necessarily the expensive path because it creates reusable business
authority. The cost is paid once in semantic authoring rather than repeatedly in
every analysis prompt.

## Cross-layer consistency rules

1. A public period-bearing field uses one `Grain`; no string, tuple, dict, or
   second handle representation is accepted on another operator.
2. A calendar level is never represented by a free-form `(calendar, level)`
   tuple or by parsing a display label.
3. A named period catalog lookup always returns a `TimeScope` with certified
   `[start, end)` and provenance.
4. A semantic period authority is exact. Equality requires calendar ref,
   snapshot digest, and level. Built-in authority equality uses its
   versioned algorithm id, level, and boundary timezone.
5. Standard and custom calendars use the same resolver operations.
6. All persisted temporal contracts are versioned and carry enough identity to
   fail closed.
7. Missing coverage never causes extrapolation.
8. Missing correspondence never causes a guessed shift.
9. Backend-native date functions are optimizations, not semantic authorities.
10. A temporal set cannot be passed as an aggregation grain.

## Custom-period forecast admission

`session.forecast(...)` keeps its current public signature. `horizon` means a
count of periods under the history frame's exact binding; no new calendar or
timezone argument is added. The currently public models have this closed
admission contract:

| `model` | Semantic `Grain` | Additional rule |
|---|---|---|
| `"naive"` | accepted | at least two complete consecutive periods |
| `"drift"` | accepted | at least three complete consecutive periods; slope is per period ordinal, not per elapsed second |
| `"seasonal_naive"` | accepted | caller must provide `seasonality_period > 1`; Marivo does not infer a business season from level names or containment |

History completeness and continuity are checked by certified period ordinal,
not timestamp frequency. Future rows use the next certified period keys and
bounds from the same calendar ref, snapshot digest, and level. `horizon` must fit
entirely inside certified coverage. A missing next period, snapshot change,
partial history period, or unsupported model raises
`ForecastShapeUnsupportedError`; Marivo never averages previous durations to
manufacture future boundaries. Forecast artifact metadata carries the same
`SemanticPeriodBindingV1`, the exact forecast period keys, and `horizon_unit`
equal to the level name.

## Implementation plan by business capability

### Slice 1: governed period authority

- Introduce `PeriodCalendarSnapshotV1`, `TemporalResolver`, the semantic kind,
  unified grain constructors, direct calendar entry lookups, catalog pages, and
  dependency closure.
- Implement Gregorian/ISO behavior through the same resolver adapter contract.
- Add one-read complete certification, readiness blocking, and atomic normalized
  persistence.

### Slice 2: fiscal and retail metric analysis

- Implement `ms.grain_to_date` and cumulative anchor identity for semantic
  grains.
- Route seed, reset, scalar cutoff, dense coverage, distinct, and weighted
  cumulative behavior through the same resolver.
- Accept semantic `Grain` as observation grain and calendar-produced
  `TimeScope` as an exact scope.
- Deliver certified roll-up and persist one closed period binding through cards,
  contracts, artifacts, and recovery.

### Slice 3: fiscal and retail comparison

- Implement the closed alignment variants for window bucket, day-of-week,
  single-period progress, and named period correspondence.
- Enforce exactly one containing target period per side for period progress.
- Add pairing evidence and fail-closed mixed-authority checks.
- Attribution reuses the paired artifact and adds no independent temporal
  selection or alignment policy.

### Slice 4: holidays, campaigns, and incidents

- Introduce semantic `TemporalSet`, one-read certification, exact occurrence
  catalog scopes, and occurrence-progress comparison.
- Keep event identity explicit; do not infer cross-year equivalence from labels.
- Do not change ordinary observation signatures.

### Slice 5: working-day semantics

- Introduce `WorkSchedule` as certified final daily `is_working` status,
  independent of `TemporalSet`.
- Deliver working-day progress and evidence.

### Slice 6: admitted custom-period forecasting

- Admit `naive` and `drift`, and admit `seasonal_naive` only with an explicit
  `seasonality_period`; all three operate on period ordinals.
- Require complete certified history and future coverage.

The following consumers need no temporal-specific behavior: cohort and lifecycle
operators continue to consume ordinary absolute bounds; raw exports remain
terminal; attribution consumes existing paired evidence. A catalog-produced
`TimeScope` may lower to those bounds before such an operator runs, but that does
not create new domain-specific clock semantics.

The slices are internal sequencing only. None defines an independently releasable
public contract. Exports, help, persistence, artifacts, examples, and tests all
activate against the complete target surface in one atomic breaking release.

## Verification matrix

### Semantic construction and certification

- static failure for invalid timezone, empty or reversed coverage, duplicate
  levels, wrong-entity refs, and invalid granularity;
- complete daily coverage, first/last boundary, and one-day period;
- gap, overlap, duplicate date, null key, and discontiguous repeated key within
  one level; crossing levels remain valid independent partitions;
- inferred strict containment, crossing partitions, and coincident-boundary
  ambiguity with no inferred edge;
- functional, null, missing-target, self, and conflicting correspondence cases;
- deterministic normalization and digest across input row ordering;
- changed content produces a deterministic new digest while retaining the prior
  certified snapshot payload under the same schema;
- atomic preservation of the previous snapshot on certification failure;
- one exhaustive acquisition followed by query-free local certification;
- query-free readiness and exact stale-dependency blocking.

### Resolver properties

- every covered date resolves to one period per level;
- `period_before(end).end <= end` without epsilon;
- period bounds round-trip through key lookup;
- certified containment edges and allowed roll-up are equivalent;
- built-in Gregorian and ISO parity across wide randomized ranges;
- Asia/Shanghai, UTC, and DST-observing timezone fixtures;
- leap day, short/long month, year boundary, and 53-week fixtures;
- out-of-coverage operations always fail.

### Analysis behavior

- public construction through `session.observe(...)`, not hand-built metadata;
- scalar and time-series cumulative at full and partial custom periods;
- off-boundary absolute scopes preserve certified `period_start`/`period_end`,
  record clipped `observed_start`/`observed_end`, and mark partial edge periods
  `is_complete=false` without snapping or dropping them;
- distinct and weighted cumulative routes share reset boundaries;
- dense buckets have stable keys and labels;
- legal certified-graph roll-up and illegal crossing/cross-authority roll-up;
- exact calendar-period `TimeScope` persistence and cold-start recovery;
- window-bucket, day-of-week, period-progress, shifted, and unshifted
  correspondence cases;
- closed policy/operator/shape/grain admission, including rejection of
  calendar-aware policies on `correlate`, `hypothesis_test`, segmented frames,
  and funnel `EventFrame` comparison;
- day-of-week pairing for one-row-per-local-day frames inside built-in and
  custom containing periods, including DST and partial-week boundaries, plus
  rejection of sub-day and coarser frames;
- rejection when either period-progress side contains zero or multiple target
  periods;
- period-correspondence rejection when the frame grain differs from the
  correspondence level or any candidate period is partial;
- explicit fail/drop outcomes for February 29 and leap-week mismatches;
- paired, unmatched, and dropped evidence counts plus exact backend/local
  execution path;
- no business scan when temporal preflight fails;
- no cache reuse after a closed temporal binding changes.

### Temporal sets

- unique occurrence id, exclusive positive interval, finite coverage,
  same-entity field validation, and string-or-null category validation;
- valid overlaps and gaps, plus invalid mixed date/timestamp encoding;
- deterministic normalization, prior certified snapshot retention, atomic
  current publication, and readiness blocking;
- exact and paged catalog occurrence lookup with snapshot-bound cursors;
- exact holiday and campaign occurrence `TimeScope` selection to `[start, end)`;
- occurrence-progress start/end anchoring for different absolute dates and
  durations, plus rejection of sub-day, duplicate-day, and coarser frames;
- rejection when a temporal set is supplied as a grain or period
  correspondence authority;
- export, help, signature, and input-validation snapshots proving that only the
  complete target public surface is accepted.

### Work schedules

- civil-date day grain, boolean non-null status, same-entity refs, unique date,
  and complete finite coverage;
- authored weekday, holiday, weekend, and makeup-workday final statuses;
- independent certification when the same physical entity also backs a
  temporal set;
- working-day ordinal pairing, policy exclusions, and unmatched counts, plus
  rejection of sub-day, duplicate-day, and coarser frames;
- exact schedule binding in artifacts, help, and recovery.

### Agent-facing contract

- the complete public API inventory is pinned against exports, focused help,
  concrete annotations, and public result members;
- focused help lists required and optional parameters without a skill dependency;
- calendar, period, temporal-set, occurrence, work-schedule, and metric cards
  expose the exact direct method and grain or scope accepted by the next call;
- `TimeScope` cannot be constructed directly; absolute, period, and occurrence
  paths each return that one type, while dicts and public handle types fail;
- period and occurrence pages are deterministic, bounded, cursor-based, and
  perform no datasource read;
- bounded `repr`, `show`, `contract`, and error examples do not dump a spine;
- ordinary natural-calendar examples require no new parameter;
- fiscal examples require one semantic `Grain`, not repeated authority fields;
- `AlignmentPolicy` cannot be constructed directly and every accepted variant
  has one exported helper with an exact signature;
- every MetricFrame alignment-bearing operator accepts only
  `AlignmentPolicy | None`; `None` means the documented `mv.window_bucket()`
  default, while funnel `EventFrame.compare` requires `None` for its mechanical
  pairing, and no `AlignmentPolicyInput`, string, or dict input exists;
- focused help exposes the closed alignment admission matrix and does not suggest
  unsupported policy/operator/grain combinations;
- every repair path identifies the failed stage and next legal operation.

## Rejected alternatives

| Alternative | Why it is rejected |
|---|---|
| global fiscal start month | Cannot express 4-4-5, leap weeks, historical changes, or multiple calendars; a session global changes metric meaning. |
| reuse analysis `CalendarRef` | Holiday membership is neither a governed period partition nor semantic authority. |
| one calendar/policy mega-object | Mixes partitions, events, work status, scope, and pairing into contradictory optional fields. |
| dynamic `calendar.quarter.get(...)` level attributes | Level names are business-authored and may collide with real entry methods; dynamic attributes weaken typing, help, and completion. `calendar.period(level, key)` is equally direct and works for every custom level. |
| merge `TemporalSet` and `WorkSchedule` | Named intervals may overlap and leave gaps; working status must be one exhaustive boolean per date. A holiday may still be working for one business, so neither meaning can derive the other. |
| derive schedules from weekdays plus event overrides | Moves jurisdiction- and company-specific precedence into Marivo, couples schedules to event naming, and makes agents reconstruct a result the source can state directly. |
| one enum per business convention | Fiscal, retail, campaign, and leap-week conventions are governed data, not a stable closed enum. |
| live runtime join to the calendar table | Makes identity depend on mutable rows and introduces a second source during every analysis. |
| rule-only custom calendars | Simple offsets cannot reproduce leap weeks and historical exceptions; later rule input must normalize to the same finite snapshot. |
| equivalence from matching boundaries | Equal dates do not prove equal business authority or future behavior. |
| manual roll-up authorization | V1 infers strict containment only; crossing or coincident levels get no edge, and business-distinct levels use separate calendars. |
| full daily spine in every artifact | The compact binding, resolved bounds, output keys, and alignment evidence are sufficient for offline reading and exact replay. |

## Industry precedents

This design adopts common ideas without copying another product's complete
surface. Date dimensions, time spines, explicit week settings, and retail
shifted/unshifted mappings are mature patterns; some named vendor custom-calendar
features remain preview functionality. The references support the architectural
pieces, not a claim that one product supplies Marivo's complete contract:

- [dbt MetricFlow time spine](https://docs.getdbt.com/guides/mf-time-spine)
  treats a governed date spine as the basis for metric time behavior.
- [Looker custom calendars](https://docs.cloud.google.com/looker/docs/custom-calendars)
  model custom timeframes with mappings and ordinals rather than only date
  truncation.
- [Power BI calendar-based time intelligence](https://learn.microsoft.com/en-ie/power-bi/transform-model/desktop-time-intelligence)
  distinguishes calendar categories and date-table metadata from query syntax.
- [Snowflake date and time semantics](https://docs.snowflake.com/en/sql-reference/functions-date-time)
  demonstrate why week behavior depends on explicit session or standard
  parameters; Marivo instead binds custom authority into semantic identity.
- [Apache Calcite `TimeFrame`](https://calcite.apache.org/javadocAggregate/org/apache/calcite/rel/type/TimeFrame.html)
  separates named time frames from raw interval units.
- [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545) is relevant to future
  recurrence authoring, but runtime temporal-set occurrences remain explicitly
  certified.
- [Oracle Retail Insights](https://docs.oracle.com/en/industries/retail/retail-insights-cloud/26.1.101.0/rinug/G49333_01.pdf)
  distinguishes shifted and unshifted prior-year mappings around 53-week years,
  motivating named correspondence as authored data rather than hidden
  arithmetic.

The Marivo-specific conclusion is stricter than most BI surfaces: period
authority is a content-addressed semantic dependency, while comparison alignment remains
an explicit analysis policy. That boundary preserves trustworthy artifacts and
keeps the common agent path small.

## Acceptance criteria

This design is complete when all of the following hold in the delivered public
contract:

- a natural-month analysis still needs only one explicit `TimeScope` and one
  built-in `Grain`, with no calendar discovery or authoring;
- one certified custom calendar can drive grouping, named-period selection,
  cumulative reset, roll-up, and comparison without duplicated period logic;
- a 53-week fixture supports two explicit prior-year correspondences and never
  guesses between them;
- missing or stale calendar evidence blocks before querying business data;
- period-calendar, temporal-set, and work-schedule certification each use one
  exhaustive acquisition and no second datasource read;
- result contracts preserve exact temporal authority and can be read offline;
- cumulative anchors, observed frames, comparison, and forecast use the same
  `PeriodBindingV1` variants and discriminator values;
- agents can discover the next valid call from help, cards, or the current typed
  error without opening implementation files;
- every analysis window is one `TimeScope`; absolute windows use
  `mv.time_scope(...)`, while exact period and occurrence lookups return it
  directly without dicts, public handles, conversion properties, or dynamic
  level attributes;
- analysis obtains grains and exact `TimeScope` values from bounded direct
  methods on their catalog root entries; only semantic authoring uses
  `ms.calendar_grain(...)`;
- no public API asks the caller to repeat a calendar, level, timezone, and grain
  as independently disagreeable fields;
- holiday and campaign concepts cannot accidentally be used as aggregation
  grains;
- an exact certified holiday or campaign occurrence can select its governed
  window without adding domain-specific fields to `observe`;
- two explicit occurrences can be compared by relative progress without label
  inference, and working-day comparison requires an exact `WorkSchedule`;
- a work schedule directly certifies final daily status, including makeup
  workdays, without depending on or composing temporal sets;
- an off-boundary absolute scope exposes partial custom periods explicitly and
  no public option silently snaps, expands, drops, or relabels them;
- period-progress comparison rejects scopes containing zero or multiple target
  periods instead of pairing them by order;
- the policy/operator/shape/grain admission matrix is closed, and every
  calendar-aware V1 policy has one unambiguous pairing coordinate;
- custom-period `naive`, `drift`, and explicitly seasonal `seasonal_naive`
  forecasts use certified future period keys and fail beyond coverage;
- every affected SQL/Ibis and local period operation routes through
  `TemporalResolver` or a parity-verified adapter.
