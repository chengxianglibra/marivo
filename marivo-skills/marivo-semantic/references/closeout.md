# marivo-semantic closeout

Closeout uses `project.readiness(...)` as the single gate. It reloads project
state, runs required backend previews, runs eligible parity checks, folds
richness gaps into warnings, and reports blockers that prevent analysis handoff.

## Readiness closeout

```python
report = project.readiness(
    refs=("sales.orders", "sales.revenue"),
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        build_purpose="Revenue analysis",
    ),
    preview_limit=20,
    parity_rel_tol=1e-6,
)
print(report.to_dict())
if report.status == "blocked":
    raise RuntimeError([issue.message for issue in report.blockers])
```

`readiness(...)` reports required and completed previews per semantic ref, but it
may execute compatible dataset, field, and time-field previews as one bounded
backend query per dataset. Use the blockers and `parity_summary` refs as the
handoff contract; do not infer one backend query per semantic object.

Blocked readiness prevents analysis handoff. Richness gaps are folded into
readiness warnings; a separate `project.richness(...)` call is optional for
deeper advisory coverage.

## Optional: deeper richness advisory

```python
richness = project.richness(
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last week?",),
        intents=("revenue trend",),
        build_purpose="Revenue analysis",
    )
)
```

`richness(...)` is pure advisory. It never blocks and never mutates readiness.
Use it when you want detailed gap analysis beyond what readiness warnings cover.
