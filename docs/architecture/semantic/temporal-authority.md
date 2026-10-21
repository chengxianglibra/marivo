# Certified Temporal Authority

PeriodCalendar and TemporalSet define finite governed time facts.
Their Python declarations state intent; exhaustive certification freezes the
source values that execute that intent. Analysis consumes these snapshots
without querying their sources at business execution time.
[Temporal semantics](../temporal-semantics.md) owns consuming boundaries, windows,
report timezones and frozen Frame correspondence.

## Two independent semantic kinds

| Kind | Source fields | Certified meaning |
| --- | --- | --- |
| PeriodCalendar | Civil date plus named level keys | Finite exhaustive ordered partition and containment graph |
| TemporalSet | Unique occurrence ID, start/end and optional category | Exact finite occurrences; overlap and gaps permitted |

All field refs for an object belong to one Entity. Constructors use explicit
boundary_timezone and half-open civil-date coverage. These declarations do not
require physical table/column names or redundant author-maintained ordinals.
The same source Entity may back both kinds, each with its own ref/digest.

## Period calendars

ms.period_calendar declares name, date, boundary_timezone, coverage and a levels
mapping. date must be a civil-date TimeDimension. A timestamp-bearing parse is rejected;
parse=None retains a premise until source schema preparation proves native DATE.
Midnight timestamp values cannot substitute for a DATE type. Empty coverage does
not certify a calendar.

Each level maps a local name to a non-null Dimension on that Entity. Keys must
be globally unique within the level, so FY2026-Q1 can identify a quarter while
Q1 repeated across years cannot. The reserved day level derives from coverage;
authors do not declare it.

Certification requires exactly one row per covered date and proves:

- Every date has exactly one key for each level.
- Equal keys form a contiguous interval; different periods neither overlap nor
  leave gaps.
- Canonical period ordinals increase strictly.
- A finer period belongs to exactly one containing coarser period on each
  inferred edge, and the graph is acyclic.

An edge A -> B requires every A period to fit one B and at least one B to
contain multiple A periods. Crossing partitions and coincident boundaries gain
no edge. The system stores the direct transitive reduction and admissible
closure; authors do not add manual roll-up overrides. Derived starts, ends,
ordinals and containment positions come from the validated rows.

For already declared field Refs and domain, a calendar can be authored as:

```python
from datetime import date
import marivo.semantic as ms

retail_calendar = ms.period_calendar(
    name="retail_445", date=calendar_date,
    boundary_timezone="Asia/Shanghai",
    coverage=(date(2025, 1, 1), date(2027, 1, 1)),
    levels={"year": fiscal_year_key, "month": fiscal_month_key},
    domain=commerce,
    ai_context=ms.ai_context(business_definition="Governed retail reporting periods"),
)
retail_month = ms.calendar_grain(calendar=retail_calendar, level="month")
```

ms.calendar_grain captures a source-free lookup. A Catalog entry's grain(level)
binds the already certified snapshot. Both return the common Grain family, with
distinct lookup and bound stages.

## Temporal sets

ms.temporal_set declares occurrence_id, start, end, boundary_timezone, coverage
and optional category. IDs are unique. End is exclusive and strictly after start;
occurrences lie wholly within finite coverage. Civil dates and timestamps cannot
mix in one set. Timestamp coverage converts its civil dates to local-midnight
instants in the declared boundary timezone.

Overlapping seasons, incidents, promotions and holidays belong here when they
are not a complete partition. A one-day holiday is explicitly [date, date+1),
without inferring duration from a label. Category is a nonempty canonical string
or null. Certification stores exact IDs, boundaries, categories and digest;
there is no runtime recurrence expansion.

## Certification, staleness and storage

catalog.preview(ref, scope=...) reads the current datasource over complete
declared coverage. The positive acquisition budget must admit all required
columns and rows, with a sentinel to detect truncation and observed exhaustion.
The display limit does not reduce certification coverage. A larger physical
source may need a bounded upstream view/partition; sampling cannot certify it.
Requested native query timeouts retain datasource support limits.

Successful preview validates and atomically publishes an immutable normalized
snapshot under project-local semantic state. Failed reads/validation do not
replace the current certificate. Readiness is query-free and blocks missing,
stale, invalid or insufficiently covered required temporal authority before a
business scan.

The manifest binds definition/dependency digests for staleness. The snapshot
digest binds canonical executable content and excludes acquisition time, paths
and display context. Prior content-addressed payloads remain available for
already captured dependencies. Calendar identity is its ref and snapshot digest,
plus the level when bound as a Grain; textual keys alone cannot establish equality.

| Snapshot | Normalized executable fields |
| --- | --- |
| period-calendar-snapshot/v2 | Ref, timezone, coverage, level periods/bounds/ordinals, containments |
| TemporalSetSnapshotV1 | Ref, timezone, coverage, occurrence IDs/bounds/categories |

Calendar day rows may be discarded after validation because contiguous bounds
reproduce membership. The reserved day level derives from coverage. Encoding
and indexing remain private; no public sync/prepare or second calendar store exists.

## Catalog navigation

```python
calendar = catalog.period_calendars.get("commerce.retail_445")
month = calendar.grain("month")
period = calendar.period_on("month", date(2026, 5, 15))
page = calendar.periods("month", limit=20)

campaigns = catalog.temporal_sets.get("commerce.campaigns")
occurrence = campaigns.occurrence("launch-2026")
occurrences = campaigns.occurrences(
    start=date(2026, 1, 1), end=date(2027, 1, 1), limit=20,
)
```

period(level, key), period_on(level, date) and occurrence(key) return exact
TimeScope values directly. Period/occurrence pages contain those same scopes;
there are no independent level/occurrence collections or public scope factories.
Periods are ordered by global ordinal. Occurrence filters select overlap with
[start,end) and must match the set's civil-date/timestamp kind.

Keys are non-null canonical JSON scalars; date-like business keys normalize to
ISO strings. Bounded pages use limit 1..100 and opaque cursors bound to the
snapshot and filters. Changing either requires a fresh first page. Lookups read
certified local data, never a datasource. Missing/stale/out-of-coverage authority
fails with concrete semantic repairs.

Entry details expose snapshot_status and definition/dependency facts. Derived
period counts/graphs or occurrence counts are available only with a current
certificate; unavailable and an empty certified graph remain distinct.
Exact members are discoverable through native type Help.

## Implementation map

[Semantic temporal constructors](../../../marivo/semantic/_authoring_temporal.py)
own declarations; [catalog.py](../../../marivo/semantic/catalog.py) owns certification
dispatch and entries. Normalized snapshots and validation live with shared scope/Grain values in
[_temporal.py](../../../marivo/_temporal.py) and grid construction in
[_temporal_grid.py](../../../marivo/_temporal_grid.py). Analysis freezes the consumed
coordinates through [core/time_authority.py](../../../marivo/analysis/core/time_authority.py)
and [core/frozen_calendar.py](../../../marivo/analysis/core/frozen_calendar.py).
