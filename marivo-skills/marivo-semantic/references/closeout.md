# marivo-semantic closeout

Closeout decides whether semantic refs are ready for `marivo-analysis`.

## Reload

```python
project = ms.find_project()
assert project is not None
print(project.reload())
```

## Preview and parity

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

## Audit

```python
stale_questions = project.audit(inspect_source=mv.datasources.inspect_source)
```

If audit returns questions, re-enter the `open_questions` path. Do not silently
keep using stale ledger decisions.

## Readiness gate

```python
report = project.readiness(
    require_preview=True,
    require_evidence_ledger=True,
    strict_enrichment=True,
    backend_factory=backend_factory,
)
```

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
