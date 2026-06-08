# marivo-semantic closeout

Closeout decides whether semantic refs are ready for `marivo-analysis`.

## Reload

```python
project = ms.find_project()
assert project is not None
print(project.reload())
```

## Bind Datasource Access

Bind datasource access once before closeout. `readiness(...)` uses the bound
access to run required preview, materialization, parity, and richness checks.

```python
project.bind_datasource_access(
    inspect_source=mv.datasources.inspect_source,
    backend_factory=mv.datasources.build_backend,
)
```

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
