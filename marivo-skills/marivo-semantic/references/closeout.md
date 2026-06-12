# marivo-semantic closeout

Closeout decides whether semantic refs are ready for `marivo-analysis`.

## Reload

```python
project.load()
catalog = ms.load()
```

## Readiness gate

`readiness(...)` is the single closeout API. It reloads, checks refs, runs
required backend previews, runs eligible parity checks, and folds richness
findings into one `ReadinessReport`. Datasource backends are resolved
internally via the connection service.

```python
report = project.readiness(
    refs=("sales.orders", "sales.revenue"),
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        intents=("revenue trend",),
        run_history_refs=("sales.revenue",),
        build_purpose="Revenue analysis",
    ),
    scope=md.ScanScope(),
)
report.show()
if report.status == "blocked":
    raise SystemExit("Semantic project is not ready for analysis handoff.")
```

`readiness()` reports required and completed previews per semantic ref, but it
may execute compatible dataset, field, and time-field previews as one bounded
backend query per dataset. Use the blockers and `preview_summary` refs as the
handoff contract; do not infer one backend query per semantic object.

## Abandoned candidates

`ReadinessReport.abandoned` lists candidates recorded with
`authoring_abandoned` during the authoring ladder. These are informational;
abandonment is not a permanent block.

## Blocked readiness

Blocked readiness prevents analysis handoff. Richness gaps, including missing
`ai_context.business_definition` or `ai_context.guardrails`, are readiness
warnings and are summarized in `richness_summary`.

## Debugging helpers

For targeted inspection outside the normal closeout path:

- `project.preview_dataset(ref, ...)` — bounded row glimpse
- `project.preview_field(ref, ...)` — field-level preview
- `project.preview_metric(ref, ...)` — metric execution preview
- `project.parity_check(ref, ...)` — SQL parity detail

These debugging helpers use the internal connection service. No
separate connection parameter is required.
