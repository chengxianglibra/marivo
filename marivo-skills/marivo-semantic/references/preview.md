# marivo-semantic preview reference

Preview evidence validates physical shape. It does not establish business
meaning by itself.

## Source preview evidence

`inspect_table(...)` reads source metadata only. `inspect_columns(...)` reads a
fixed 5-row sample for selected columns and closes its datasource connection.
Readiness runs required raw and semantic previews live through project-bound
datasource access. Use bounded raw previews before declaring or revising
datasets, time-like columns, amount columns, enum/status columns, and join keys.

For debugging or targeted raw table inspection, `collect_source_preview` is still
available:

```python
import marivo.analysis as mv

backend_factory = lambda name: md.connect(name)
preview = project.collect_source_preview(
    datasource="warehouse",
    table="orders",
    backend_factory=backend_factory,
    limit=20,
)
print(preview.rows)
```

For Trino without a default schema:

```python
preview = project.collect_source_preview(
    datasource="warehouse",
    table="orders",
    database="sales_mart",
    backend_factory=backend_factory,
    limit=20,
)
```

Raw previews help validate time formats, enum values, nulls, amount signs,
amount units, JSON-like strings, and join-key shape. They do not prove that a
column is the correct business concept.

Successful source previews record project-local preview metadata under
`.marivo/semantic/.evidence/`. Normal closeout still uses
`project.readiness(...)`, which reruns the required previews against the current
bound datasource access. Marivo persists preview metadata only; raw sample rows
are not written to the evidence file.

## Semantic preview

After authoring and load, preview the semantic objects that will be handed to
analysis:

```python
import marivo.analysis as mv

backend_factory = lambda name: md.connect(name)

project.preview_dataset("sales.orders", backend_factory=backend_factory, limit=20)
project.preview_field("sales.orders.order_date", backend_factory=backend_factory, limit=20)
project.preview_metric("sales.revenue", backend_factory=backend_factory, limit=20)
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
