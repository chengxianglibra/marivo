# marivo-semantic closeout

Closeout decides whether semantic refs are ready for `marivo-analysis`.

## Reload

```python
project = ms.find_project()
assert project is not None
print(project.reload())
```

## Preview and parity

`inspect_source_context(...)` folds source inspection and bounded preview. When
it was called with a row-reading sample policy, `readiness(require_preview=True)`
is already satisfied for that source. For targeted re-preview or closeout,
`collect_source_preview` is still available:

```python
backend_factory = lambda name: mv.datasources.build_backend(name)
project.collect_source_preview(
    datasource="warehouse",
    table="orders",
    backend_factory=backend_factory,
)
project.preview_dataset("sales.orders", backend_factory=backend_factory)
project.preview_metric("sales.revenue", backend_factory=backend_factory)
project.parity_check("sales.revenue", backend_factory=backend_factory)
```

`collect_source_preview()` persists metadata evidence for readiness. The raw
sample rows are not persisted, and the readiness step may run in a later Python
process.

## Readiness gate

```python
report = project.readiness(
    require_preview=True,
    require_evidence_ledger=True,
    strict_enrichment=True,
    backend_factory=backend_factory,
)
```

`readiness()` reports required and completed previews per semantic ref, but it
may execute compatible dataset, field, and time-field previews as one bounded
backend query per dataset. Use the blockers and `preview_summary` refs as the
handoff contract; do not infer one backend query per semantic object.

Blocked readiness prevents analysis handoff. Under `strict_enrichment=True`,
an analyzable handoff ref missing `ai_context.business_definition` blocks, and a
missing `ai_context.guardrails` warns.

## Richness advisory

```python
richness = project.richness(
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        intents=("revenue trend",),
        run_history_refs=("sales.revenue",),
        build_purpose="Revenue analysis",
    )
)
```

Richness gaps are advisory. Report them separately from readiness blockers.
