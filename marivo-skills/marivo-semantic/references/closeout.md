# marivo-semantic closeout

Closeout decides whether semantic refs are ready for `marivo-analysis`.

## Reload

```python
project.load()
catalog = ms.load()
```

## Readiness gate

`readiness(...)` is a pure structural check — no datasource connection
required. It verifies load errors, unknown refs, evidence ledger blockers,
cross-datasource unfederated metrics, raw SQL requirements, strict
enrichment issues (missing `business_definition` / `guardrails`), and load
warnings forwarding.

```python
report = project.readiness(
    refs=("sales.orders", "sales.revenue"),
)
report.show()
if report.status == "blocked":
    raise SystemExit("Semantic project is not ready for analysis handoff.")
```

For runtime validation that requires datasource connectivity, use the
dedicated APIs separately:

- **Previews**: `md.preview(...)` for raw sources, `catalog.preview(...)`
  for semantic refs
- **Parity**: `parity_check()`
- **Richness**: `richness()`

## Abandoned candidates

`ReadinessReport.abandoned` lists candidates recorded with
`authoring_abandoned` during the authoring ladder. These are informational;
abandonment is not a permanent block.

## Blocked readiness

Blocked readiness prevents analysis handoff. Missing
`ai_context.business_definition` is a blocker; missing
`ai_context.guardrails` is a warning.

## Debugging helpers

For targeted inspection outside the normal closeout path:

- `md.preview(...)` — bounded raw preview
- `catalog.preview(ref, ...)` — bounded semantic preview
- `project.parity_check(ref, ...)` — SQL parity detail
- `project.richness()` — demand-ranked richness gaps
