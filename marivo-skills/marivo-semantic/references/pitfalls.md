# marivo-semantic pitfalls

Known failure modes for evidence-driven semantic authoring.

## Treating sample facts as full-table truth

Sample-derived values (`top_values`, `distinct_count`, `min_value`/`max_value`)
are facts about the bounded sample only (`sample_scope="bounded_sample"`,
`approximate=True`). Never treat them as full-column cardinality, complete
enums, or global ranges. For exhaustive value sets, request user confirmation
or inspect the full table outside the authoring workflow.

## Expecting a candidate worklist

The project does not return a candidate worklist. Rank columns yourself from
pack facts (type, comments, nullable, partition hints, sampled values). Use
`project.inspect_column_context(...)` to deep-dive a small set of columns, then
author directly.

## Passing ledger evidence as source inputs

`project.assess_authoring(...)` checks whether the named semantic object has
enough source context to author. It accepts bounded source roles through
`sources=(ms.AuthoringSourceInput(...),)` and semantic dependencies through
`semantic_refs=...`; it does not take ledger ids or ai_context fields as check
inputs. Assess the object with the source/role shape directly.

## Forgetting to load before inspect_authored_object

After authoring `.marivo/semantic/<model>/*.py`, always call `project.load()`
before `project.inspect_authored_object(ref)`. Without load, the registry is
stale and the inspection reflects the pre-authoring state. This also ensures
auto-recorded `metric_decomposition` and `time_dimension_identity` decisions are
present in the ledger.

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

## Parsed partition dimension default

Do not make cast/parse expressions the partition dimension default when a day/hour
partition column already stores a sortable encoded value. Prefer raw
`data_type="string"` or `data_type="integer"` with `date_format`, and use
`required_prefix` for hour-only fields such as `HH`. This preserves simple
partition predicates for engine-side predicate pushdown. Use cast/parse
expressions only when the established business time axis is not the raw
partition value.

## Trino VARCHAR datetime cast

Do not cast a Trino VARCHAR datetime directly to DATE:

```python
@ms.time_dimension(entity=orders, data_type="date", granularity="day")
def order_date(table):
    return table.order_time.cast("date")
```

Parse through timestamp first:

```python
@ms.time_dimension(entity=orders, data_type="date", granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

Note: both examples declare `data_type="date"` because the body produces a
DateColumn. Declaring `data_type="datetime"` with a `.cast("date")` body
causes a TypeError at execution.

## data_type vs body dtype mismatch

The declared `data_type` must match the ibis dtype produced by the body
function:

- `.cast("date")` or a raw date column -> `data_type="date"`
- `.cast("timestamp")` or a raw timestamp column -> `data_type="datetime"` or `data_type="timestamp"`

When `data_type` does not match the body's ibis dtype, the executor dispatches
to the wrong code path and raises TypeError (e.g., DateColumn + interval).

```python
# WRONG - data_type="datetime" with .cast("date") body
@ms.time_dimension(entity=orders, data_type="datetime", granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")

# CORRECT - data_type="date" with .cast("date") body
@ms.time_dimension(entity=orders, data_type="date", granularity="day")
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

## Federated backend chosen by habit

Do not create a Trino datasource just because a Trino catalog could reach the
table. Pick the native backend named by the source: ClickHouse tables use
`backend_type="clickhouse"`, MySQL tables use `backend_type="mysql"`, and DuckDB
files or supported local files use `backend_type="duckdb"`. Use Trino for
Hive/Iceberg lakehouse tables or when the user explicitly requires Trino
federation.

## Multi-file sprawl

Avoid spreading one domain change across many small files when a focused entity,
dimension, metric, or relationship edit would do. Sprawl makes load, review, and
readiness harder because related definitions become difficult to inspect
together.

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
