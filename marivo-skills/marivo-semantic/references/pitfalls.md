# marivo-semantic pitfalls

Known failure modes for semantic authoring agents.

## Business meaning inferred from names

Names are candidate signals only. Use comments, source SQL, knowledge, preview
evidence, or user confirmation before authoring business definitions. Name-only
semantics usually look plausible but fail when analysis users ask for exact
metric meaning, filters, or dimensions.

## Wrong time axis

Do not choose a time field only because its name looks common. Confirm the
business event represented by the column, such as order creation, payment,
shipment, cancellation, or ledger posting. Preview values and cite metadata
before making the field the default time axis for metrics or datasets.

## Parsed partition field default

Do not make cast/parse expressions the partition field default when a day/hour
partition column already stores a sortable encoded value. Prefer raw
`data_type="string"` or `data_type="integer"` with `date_format`, and use
`required_prefix` for hour-only fields such as `HH`. This preserves simple
partition predicates for engine-side predicate pushdown. Use cast/parse
expressions only when the established business time axis is not the raw
partition value.

## Trino VARCHAR datetime cast

Do not cast a Trino VARCHAR datetime directly to DATE:

```python
@ms.time_field(dataset=orders, data_type="date", granularity="day")
def order_date(table):
    return table.order_time.cast("date")
```

Parse through timestamp first:

```python
@ms.time_field(dataset=orders, data_type="date", granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

Note: both examples declare `data_type="date"` because the body produces a
DateColumn. Declaring `data_type="datetime"` with a `.cast("date")` body
causes a TypeError at execution.

## data_type vs body dtype mismatch

The declared `data_type` must match the ibis dtype produced by the body
function:

- `.cast("date")` or a raw date column → `data_type="date"`
- `.cast("timestamp")` or a raw timestamp column → `data_type="datetime"` or `data_type="timestamp"`

When `data_type` does not match the body's ibis dtype, the executor dispatches
to the wrong code path and raises TypeError (e.g., DateColumn + interval).

```python
# WRONG — data_type="datetime" with .cast("date") body
@ms.time_field(dataset=orders, data_type="datetime", granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")

# CORRECT — data_type="date" with .cast("date") body
@ms.time_field(dataset=orders, data_type="date", granularity="day")
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

## Trino fully qualified name mistake

For Trino semantic datasets, keep schema selection in the structured source rather
than embedding a multi-part table string:

```python
orders = ms.dataset(
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

Avoid spreading one model change across many small files when a focused dataset,
field, metric, or relationship edit would do. Sprawl makes reload, review, and
readiness harder because related definitions become difficult to inspect
together.

## Missing raw preview evidence

If readiness reports `missing_raw_preview`, run
`project.collect_source_preview(datasource="warehouse", table="orders", backend_factory=backend_factory)`
before readiness. Metadata and comments do not replace raw samples for validating
shape-sensitive columns.

## Incomplete decision records

Do not call `project.record_decision(semantic_id, record)` without the required
`semantic_id` first argument (`question.subject_refs[0]`), or with invented
internal fields. Use `project.answer(...)` for user confirmations, or build a
`DecisionRecord` from a real `OpenQuestion`, evidence fingerprint, cited table,
and qualifying sources.

Do not use `project.answer(...)` for readiness-only blockers that have no
`OpenQuestion`, and do not write `DecisionRecord(chosen=None, ...)`. If a
declared metric or time field lacks a readiness decision, reload after the
declaration so Marivo auto-records it, or record a complete object-level
`DecisionRecord`.

## Analysis handoff before readiness

Do not hand refs to `marivo-analysis` until readiness has no blockers for those
refs. Preview failures, missing raw preview evidence, unresolved ambiguity, and
broken semantic declarations are blockers for affected objects.

## Unverified metric and source SQL parity drift

When a metric is intended to match existing source SQL or a known report,
compare the semantic expression to the cited source and verify parity before
closeout. A renamed field, changed filter, different time axis, or missing
status condition can silently drift from the source definition.

## Richness confused with readiness

`project.richness(...)` is advisory. It does not block handoff. Fix readiness
blockers first, then report richness gaps as recommended follow-up work.
