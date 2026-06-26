# marivo-semantic preview reference

Preview evidence validates physical shape. It does not establish business
meaning by itself. Bounded preview produces bounded-sample evidence; it
does not infer business meaning for a column or metric.

## Source preview evidence

`prepare_entity` and `prepare_dimension`/`prepare_time_dimension`/`prepare_metric`
collect source evidence internally through the datasource discovery primitives.
Readiness runs required raw and semantic previews live through the internal
datasource connection service. Use bounded raw previews before declaring or
revising datasets, time-like columns, amount columns, enum/status columns, and
join keys.

For debugging or targeted raw table inspection, use the `md.discover_*` family:

```python
import marivo.datasource as md

evidence = md.discover_dimensions(
    md.ref("warehouse"),
    md.table("orders"),
    columns=("status", "amount"),
    scope=md.partition({"dt": "20260611"}),
)
for candidate in evidence.candidates:
    print(candidate.column, candidate.profile.distinct_count, candidate.profile.top_values)
```

For Trino without a default schema:

```python
evidence = md.discover_dimensions(
    md.ref("warehouse"),
    md.table("orders", database="sales_mart"),
    scope=md.latest_partition(),
)
```

Raw previews help validate time formats, enum values, nulls, amount signs,
amount units, JSON-like strings, and join-key shape. They do not prove that a
column is the correct business concept.

Normal closeout uses `ms.readiness(...)`, which reruns the required
previews against the internal connection service. No separate
connection parameter is needed.

## Semantic preview

After authoring and load, preview the semantic objects that will be handed to
analysis. Previews use the internal connection service:

```python
import marivo.semantic as ms

catalog = ms.load()
catalog.preview("sales.orders", limit=20)
catalog.preview("sales.orders.order_date", limit=20)
catalog.preview("sales.orders.amount", limit=20)
catalog.preview("sales.revenue", limit=20)
```

Preview failures are readiness blockers for affected handoff refs. Fix the
semantic declaration, datasource access path, or cast before handing refs to
`marivo-analysis`.

Readiness may fold dataset, field, and time-field semantic previews into a
single bounded backend query per parent dataset. Treat readiness output as
per-ref evidence: `required_previews`, `completed_previews`, `failed_previews`,
and blockers still name the semantic refs that are ready or blocked even when
the backend execution was batched internally.

## Sampling and failure handling

Keep preview limits bounded. Prefer representative filters only when they are
needed to expose recent values, non-null examples, or known edge cases.

If raw preview fails, repair the datasource configuration or table access path
before authoring semantics from that source. If semantic preview fails, load
after edits and rerun preview before closeout. Closeout can mention remaining
preview gaps only when readiness does not require those refs for analysis
handoff.
