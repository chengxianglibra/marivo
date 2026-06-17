# marivo-semantic pitfalls

Known failure modes for stepwise semantic authoring.

## Treating sample facts as full-table truth

Sample-derived values (`top_values`, `distinct_count`, `min_value`/`max_value`)
are facts about the bounded sample only (`ScanScope`-controlled, approximate).
Never treat them as full-column cardinality, complete enums, or global ranges.
For exhaustive value sets, request user confirmation or inspect the full table
outside the authoring workflow.

## Expecting a candidate worklist

The project does not return a candidate worklist. Rank columns yourself from
`EntityBrief` facts (type, comments, nullable, partition hints, sampled values).
Deep-dive a small set with `md.inspect_columns(...)`, then author directly.

## Skipping prepare before authoring

Every semantic object must be preceded by the matching `ms.prepare_*` call.
The prepare step collects datasource evidence and registry context, returning a
Brief with `status`, `issues`, and `questions`. Skipping prepare means the agent
authors without knowing whether the prerequisites are met, and `verify_object`
will surface the same failures later at higher cost.

## Writing multiple objects before verifying

The per-object cycle requires: prepare -> write one object -> verify -> advance.
Writing several objects before verifying defers the first error, making it
harder to trace which object caused it. One object per cycle keeps the feedback
loop tight.

## Advancing past a failed verify_object

After authoring, call `ms.verify_object(ref)`. If it returns
`status == "failed"`, fix the object and re-verify. The skill forbids advancing
to the next ladder rung while a previous object fails verification.

## Using readiness/richness to compensate for missing authoring evidence

Readiness and richness are closeout diagnostics, not evidence collection tools.
If authoring evidence is incomplete (missing source SQL, no column deep-dives
for ambiguous fields), fix the evidence gaps before running closeout. A
readiness pass on thin evidence masks real semantic issues.

## Business meaning inferred from names

Names are candidate signals only. Use comments, source SQL, knowledge, preview
evidence, or user confirmation before authoring business definitions. Name-only
semantics usually look plausible but fail when analysis users ask for exact
metric meaning, filters, or dimensions.

## Wrong time axis

Do not choose a time dimension only because its name looks common. Confirm the
business event represented by the column, such as order creation, payment,
shipment, cancellation, or ledger posting. Preview values and cite metadata
before making the dimension the default time axis for metrics or entities.

## strptime vs hour_prefix on hour-only time dimensions

`parse=ms.strptime(...)` and `parse=ms.hour_prefix(...)` are mutually exclusive
on string/integer hour-granularity time dimensions. You must choose one.

Choose by column shape — single column with date+hour (e.g. `"2025061403"`)
uses `parse=ms.strptime("%Y%m%d%H", data_type="string")`; separate day and
hour columns use `parse=ms.hour_prefix(...)` on the hour dimension and no
date-bearing strptime. When `hour_prefix` is set, Marivo concatenates the
prefix value with the hour column at query time, so a strptime format would
create conflicting interpretation paths. See authoring-patterns for the full
code examples.

## Parsed partition dimension default

Do not make cast/parse expressions the partition dimension default when a day/hour
partition column already stores a sortable encoded value. Prefer
`parse=ms.strptime(...)` with `data_type="string"` or `data_type="integer"`
for day partition columns, and use `parse=ms.hour_prefix(...)` for hour-only
fields such as `HH`. This preserves simple partition predicates for engine-side
predicate pushdown. Use cast/parse expressions only when the established
business time axis is not the raw partition value.

## Trino VARCHAR datetime cast

Do not cast a Trino VARCHAR datetime directly to DATE:

```python
@ms.time_dimension(entity=orders, granularity="day", parse=ms.date())
def order_date(table):
    return table.order_time.cast("date")
```

Parse through timestamp first:

```python
@ms.time_dimension(entity=orders, granularity="day", parse=ms.date())
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

Note: both examples use `parse=ms.date()` because the body produces a
DateColumn. Using `parse=ms.datetime(...)` with a `.cast("date")` body
causes a TypeError at execution.

## parse variant vs body dtype mismatch

The `parse` variant on `ms.time_dimension(...)` determines the physical
column type. It must match the ibis dtype produced by the body function:

- `.cast("date")` or a raw date column -> `parse=ms.date()`
- `.cast("timestamp")` or a raw timestamp column -> `parse=ms.datetime(...)` or `parse=ms.timestamp(...)`

When the parse variant implies a different type than the body's ibis dtype,
the executor dispatches to the wrong code path and raises TypeError
(e.g., DateColumn + interval).

```python
# WRONG - ms.datetime() parse with .cast("date") body
@ms.time_dimension(entity=orders, granularity="day", parse=ms.datetime(timezone="UTC"))
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")

# CORRECT - ms.date() parse with .cast("date") body
@ms.time_dimension(entity=orders, granularity="day", parse=ms.date())
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

## Trino fully qualified name mistake

For Trino semantic entities, keep schema selection in the structured source rather
than embedding a multi-part table string:

```python
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders", database="sales_mart"),
)
```

Use `backend.list_databases(catalog="hive")` to discover schemas and
`backend.list_tables(database="sales_mart")` to verify table reachability.

In `provenance=ms.from_sql(...)`, table references can be bare names or fully qualified.
Marivo automatically qualifies bare table references before executing provenance
SQL for parity checks, using the entity `source.database` when set, or falling
back to the datasource's `database` field. Already-qualified table references
are left unchanged.

```python
# Both are valid — choose whichever matches the original SQL
provenance=ms.from_sql(sql="SELECT SUM(amount) FROM orders", dialect="duckdb")               # bare; auto-qualified
provenance=ms.from_sql(sql="SELECT SUM(amount) FROM sales_mart.orders", dialect="duckdb")     # fully qualified; unchanged
```

## Federated backend chosen by habit

Do not create a Trino datasource just because a Trino catalog could reach the
table. Pick the native backend named by the source: ClickHouse tables use
`backend_type="clickhouse"`, MySQL tables use `backend_type="mysql"`, and DuckDB
files or supported local files use `backend_type="duckdb"`. Use Trino for
Hive/Iceberg lakehouse tables or when the user explicitly requires Trino
federation.

## Multi-file sprawl

Avoid spreading one domain change across many small files when a focused entity,
dimension, metric, or relationship edit would do. Default to one
`_domain.py` per domain. Sprawl makes load, review, and readiness harder
because related definitions become difficult to inspect together.

## Analysis handoff before readiness

Do not hand refs to `marivo-analysis` until readiness has no blockers for those
refs. Preview failures, missing raw preview evidence, unresolved ambiguity, and
broken semantic declarations are blockers for affected objects.

## Unverified metric and source SQL parity drift

When a metric is intended to match existing source SQL or a known report,
compare the semantic expression to the cited source and verify parity before
closeout. A renamed dimension, changed filter, different time axis, or missing
status condition can silently drift from the source definition.

## Readiness warnings confused with blockers

Readiness may report advisory richness and parity warnings alongside blockers.
Do not treat warning-only gaps as handoff blockers. Fix readiness blockers
first, then report advisory gaps as recommended follow-up work.

## Unpruned scan without justification

Use `md.ScanScope()` by default, which resolves to the latest partition. Passing
`partition=None` produces an unpruned scan that may be slow and may sample stale
data. Only pass `partition=None` when the answer explicitly accepts an unpruned
scan and the reason is documented.
