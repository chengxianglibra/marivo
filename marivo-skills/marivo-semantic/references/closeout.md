# marivo-semantic closeout

Closeout decides whether semantic refs are ready for `marivo-analysis`.

## Reload

```python
catalog = ms.load()
```

## Datasource Access

Readiness closeout automatically uses the kernel default factories
(`md.inspect_source` and `md.connect`) when project datasources are
registered. For custom `inspect_source` or `backend_factory`, pass
them explicitly to `inspect_source_context`, `assess_authoring`, etc.

## Readiness gate

```python
report = project.readiness(
    refs=("sales.orders", "sales.revenue"),
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        intents=("revenue trend",),
        run_history_refs=("sales.revenue",),
        build_purpose="Revenue analysis",
    ),
    preview_limit=20,
    parity_rel_tol=1e-6,
    redact=True,
)
```

`readiness()` reports required and completed previews per semantic ref, but it
may execute compatible dataset, field, and time-field previews as one bounded
backend query per dataset. Use the blockers and `preview_summary` refs as the
handoff contract; do not infer one backend query per semantic object.

Blocked readiness prevents analysis handoff. Richness gaps, including missing
`ai_context.business_definition` or `ai_context.guardrails`, are readiness
warnings and are summarized in `richness_summary`.
