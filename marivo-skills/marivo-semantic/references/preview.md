# marivo-semantic preview reference

Preview evidence validates physical shape. It does not establish business
meaning by itself.

## Raw datasource preview

Use bounded raw previews before declaring or revising datasets, time-like
columns, amount columns, enum/status columns, and join keys:

```python
import marivo.analysis as mv

preview = mv.datasources.preview("warehouse", table="orders", limit=20)
print(preview.to_dict())
```

For Trino without a default schema:

```python
preview = mv.datasources.preview(
    "warehouse",
    table="orders",
    database="sales_mart",
    limit=20,
)
```

Raw previews help validate time formats, enum values, nulls, amount signs,
amount units, JSON-like strings, and join-key shape. They do not prove that a
column is the correct business concept.

## Semantic preview

After authoring and reload, preview the semantic objects that will be handed to
analysis:

```python
import marivo.analysis as mv

backend_factory = lambda name: mv.datasources.build_backend(name)

project.preview_dataset("sales.orders", backend_factory=backend_factory, limit=20)
project.preview_field("sales.order_date", backend_factory=backend_factory, limit=20)
project.preview_metric("sales.revenue", backend_factory=backend_factory, limit=20)
```

Preview failures are readiness blockers for affected handoff refs. Fix the
semantic declaration, datasource access path, or cast before handing refs to
`marivo-analysis`.

## Sampling and failure handling

Keep preview limits bounded. Prefer representative filters only when they are
needed to expose recent values, non-null examples, or known edge cases.

If raw preview fails, repair the datasource configuration or table access path
before authoring semantics from that source. If semantic preview fails, reload
after edits and rerun preview before closeout. Closeout can mention remaining
preview gaps only when readiness does not require those refs for analysis
handoff.
