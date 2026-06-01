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
def order_date(table):
    return table.order_time.cast("date")
```

Parse through timestamp first:

```python
def order_date(table):
    return table.order_time.cast("timestamp").cast("date")
```

## Trino fully qualified name mistake

For Trino semantic datasets, keep schema selection in the datasource call rather
than embedding a multi-part table string:

```python
@ms.dataset(name="orders", datasource=warehouse)
def orders(backend):
    return backend.table("orders", database="sales_mart")
```

Use `backend.list_schemas()` to discover schemas and
`backend.list_tables(database="sales_mart")` to verify table reachability.

## Multi-file sprawl

Avoid spreading one model change across many small files when a focused dataset,
field, metric, or relationship edit would do. Sprawl makes reload, review, and
readiness harder because related definitions become difficult to inspect
together.

## Missing raw preview evidence

If readiness reports `missing_raw_preview`, run a bounded raw datasource preview
and pass the completed raw preview ref to readiness. Metadata and comments do
not replace raw samples for validating shape-sensitive columns.

## Incomplete decision records

Do not call `project.record_decision(...)` with invented internal fields. Use
`project.answer(...)` for user confirmations, or build a `DecisionRecord` from a
real `OpenQuestion`, evidence fingerprint, cited table, and qualifying sources.

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
