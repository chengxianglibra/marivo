---
name: marivo-semantic
description: Use for Marivo datasource setup and semantic-layer authoring through evidence snapshots, explicit Python objects, scoped preview, and readiness.
---

# marivo-semantic

Use this skill to define project datasources and reusable semantic objects. For
metric-centered work on an already-ready project, use `marivo-analysis`.

## Ownership

This skill owns workflow and routing only:

- `md.help(...)` owns datasource contracts, inspection, scope, and snapshot details.
- `ms.help(...)` owns semantic constructor parameters and constraints.
- Result `.show()` output and structured errors own state-specific next calls.
- The agent owns evidence-based drafting and technical handling, including uncommon
  physical formats. The user or business owner owns unresolved business-semantic
  decisions and approves metric meaning before analysis handoff.

Do not copy parameter tables, schemas, backend catalogs, or error catalogs here.

## Canonical route

```text
help/browse -> inspect -> explicit scope -> sample once -> project evidence -> settle/grill -> author one Python object -> load typed object -> static verify -> scoped preview -> readiness -> analysis
```

Start with `md.help("authoring")` and `ms.help("authoring")`. Browse existing
objects with `catalog = ms.load()` and its typed collections. Then:

1. Inspect the source with `md.inspect(...)`; read physical extent, partition
   state, schema, and execution capabilities before any user-data query.
2. Choose `md.partition(...)` or explicitly acknowledge a broad read with
   `md.unpruned(...)`. Both require positive row and timeout guards.
3. Call `inspection.sample(scope=..., columns=(...))` once for the active batch.
4. Reuse query-free `snapshot.entity`, `dimensions`, `values`,
   `time_dimensions`, `measures`, and `relationships` projections.
5. Settle fields from help, snapshot evidence, existing catalog/project facts,
   source provenance, and prior user decisions. If one semantic decision remains,
   ask exactly one evidence-grounded grill question and stop.
6. Write exactly one explicit Python object, reload the catalog, and navigate to
   its typed `CatalogObject`.
7. Run `catalog.verify_object(obj)`, then
   `catalog.preview(obj, using=snapshot)`, then
   `catalog.readiness(refs=[obj])`. Repair the same object before advancing.
8. Close out each new or changed object before handing that change to
   `marivo-analysis`. Routine analysis of unchanged objects does not reacquire
   authoring evidence.

## Hard boundaries

- Use `md.table(...)`, `md.parquet(...)`, `md.csv(...)`, or `md.json(...)` for
  physical sources; there is no duplicate semantic source-builder family.
- One snapshot supports many local projections; a projection never reacquires data.
- A returned-row `LIMIT` is not a byte-scan guarantee. Treat unpruned reads as
  potentially expensive even when `max_rows` is small.
- Values are memory-only by default. Use `persist_values=True` only when the
  plaintext project-local cache is acceptable for the data.
- Observed uniqueness is evidence, not a primary-key or business-key decision.
- Do not infer uncommon date formats, epoch units, timezone, aggregation, unit,
  additivity, relationship cardinality, or business meaning.
- `md.raw_sql(...)` is the sole terminal raw SQL execution path — bounded,
  timeout-enforced, and terminal; results cannot re-enter typed analysis.
  `ms.parity_check(...)` is a potentially unbounded diagnostic outside the
  canonical route. Parity is never readiness-required.
- Semantic links use typed refs, not bare semantic-id strings.

## References

| Need | Read |
| --- | --- |
| Datasource setup and snapshot acquisition | `references/datasource.md` |
| Readiness and analysis handoff | `references/closeout.md` |
| Workflow failure modes | `references/pitfalls.md` |
| Cumulative metrics | `references/cumulative-metrics.md` |
| Cumulative anchors | `references/cumulative-anchors-v2.md` |
| Runnable route | `references/examples/` |
