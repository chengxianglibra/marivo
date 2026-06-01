# marivo-semantic richness reference

The richness report is a **pure advisory** companion to readiness. It never blocks
handoff and never changes `readiness`. Use it at authoring closeout to see the
highest-value gaps first.

## Standard API

```python
import marivo.semantic as ms

project = ms.find_project()
assert project is not None
project.load()

report = project.richness(
    demand=ms.DemandSignal(
        example_questions=("What was revenue by region last quarter?",),
        intents=("revenue trend",),
        run_history_refs=("sales.revenue",),
        build_purpose="Revenue analysis dashboard",
    ),
)

for gap in report.gaps:  # ranked by demand_weight, descending
    print(gap.kind, gap.subkind, gap.refs, gap.demand_weight, gap.suggested_action)
```

`demand` is optional. With `demand=None` the report lists every detected gap at
weight 0.0 as a cold inventory. With a `DemandSignal`, coverage gaps that no demand
points at are dropped, and remaining gaps are ranked by demand weight.

## Gap kinds

- **coverage** (breadth): `fact_table_no_metric`, `dataset_shares_keys_no_relationship`.
- **depth** (per-object quality): `missing_business_definition`, `missing_guardrails`,
  `missing_synonyms`, `missing_examples`.

## Demand signal

- `example_questions` — natural-language questions the layer should answer.
- `intents` — analysis intents.
- `run_history_refs` — semantic ids that have actually been queried (strongest signal).
- `build_purpose` — cold-start seed when there is no history yet.

## Relationship to readiness

Richness is advisory only. The one correctness-adjacent case — a handoff ref with no
`business_definition` — is enforced by `readiness(strict_enrichment=True)`, not by this
report. Richness still lists `missing_business_definition` as advice, but it never blocks.
