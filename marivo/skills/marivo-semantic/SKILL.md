---
name: marivo-semantic
description: Use for any Marivo datasource definition or semantic-layer authoring task: datasource declarations/refs, entities, dimensions, time dimensions, measures, metrics, relationships, evidence, readiness, and analysis handoff.
---

# marivo-semantic

Use this skill when defining project datasources or building reusable semantic
objects. For metric-centered analysis on an already-ready domain, use
`marivo-analysis`.

## Runtime Assumptions

This skill is written for an ordinary project that depends on Marivo as a
pip-installed Python library; the current workspace is not expected to contain
the Marivo package source. Do not rely on repo fixtures, `make`, or a fixed
`.venv` name. Identify the project Python environment first, then use its
explicit Python path such as `<venv>/bin/python`.

The current project should contain `models/semantic/`. If it does not, create
the project structure before authoring semantic objects.

## Ladder Rules

- Follow the ladder: domain -> entity -> dimension -> time_dimension -> measure -> metric -> relationship -> cross-entity metric -> derived metric.
- Before writing each object, call the matching `ms.prepare_*` API and branch on the returned Brief status.
- Use the datasource-first agreement gate before authoring: inspect Brief facts, datasource metadata, bounded data samples, existing semantic objects, source SQL/provenance, project docs, and decision ledger entries before asking the user.
- Grill the user only after discovery leaves a real semantic decision open. Ask one unresolved semantic decision at a time, include the recommended answer first, and keep every option evidence-derived.
- Do not ask users for schema, column names, data types, partition hints, sample values, join-key viability, or existing object state when Marivo can inspect them.
- Do not invent plausible options. If evidence supports one path, ask for confirmation; if evidence is insufficient, run another bounded discovery query or ask an open clarification.
- Write exactly one semantic object per cycle in `models/semantic/<domain>/_domain.py`.
- Default direct-column semantic authoring is `ms.time_dimension_column(...)`, `ms.dimension_column(...)`, and `ms.measure_column(...)`; verify the measure, then declare `ms.aggregate(name="total_amount", measure=amount, agg="sum")`. Use decorators only for expression-bearing semantic objects. Use `ms.count(name="orders_count", entity=orders, ai_context=ms.ai_context(business_definition="..."))` for entity row counts. Use `@ms.metric(...)` only for expression-body tier-2 metrics.
- Use `ms.from_sql(sql=..., dialect=...)` for SQL provenance, not `source_sql`/`source_dialect` kwargs.
- After writing one object, call `ms.verify_object(ref)` and do not advance while it fails.
- **`verify_object` is enforced:** `ms.prepare_dimension`, `ms.prepare_time_dimension`, `ms.prepare_measure`, `ms.prepare_metric`, `ms.prepare_relationship`, and `ms.prepare_cross_entity_metric` raise `LadderOrderError` if their entity arguments have not passed `ms.verify_object`. You must verify the entity before these calls.
- Use `md.ScanScope()` by default. Passing `partition=None` is allowed only when the answer explicitly accepts an unpruned scan.
- Ask users only for semantic intent, business policy, or unresolved ambiguity that cannot be answered from documented project knowledge or datasource/project discovery.
- Record abandonment with `ms.record_decision(decision_kind="authoring_abandoned", ...)` when a candidate cannot be safely authored.
- Run `ms.readiness(...)` at closeout and do not hand off to analysis while readiness is blocked.

## Inspecting semantic objects

Use `ms.load()` to obtain a `SemanticCatalog`, then browse and inspect:

```python
catalog = ms.load()
catalog.list().show()                         # top-level: domains and datasources
catalog.list("sales").show()                  # entities and metrics under a domain
catalog.list("sales.orders").show()           # dimensions, time dimensions, relationships, filtered metrics
catalog.list(kind="metric").show()            # all metrics across every domain
catalog.list(domain="sales", kind="metric").show()  # metrics in one domain
revenue = catalog.get("sales.revenue")
revenue.details()                             # kind-specific details
revenue.details().show()                      # full bounded object inspection
revenue.children                              # child refs (empty for leaf objects)
```

Use `mv.help(ref)` for a short consumption briefing on a semantic object.
Use `ms.help("<topic>")` for semantic authoring and validation contract help.
Use `catalog.get(ref).details().show()` when you need the full semantic object
metadata, graph refs, source/provenance, and authored AI context.

```python
ms.help("metric")                   # authoring contract help
mv.help(revenue)                    # bounded consumption context
mv.help(revenue, project=project)   # explicit project when not in CWD
```

Read `_domain.py` only when you need to modify the semantic domain, inspect
implementation expressions, or debug authoring behavior.

## Reference Routing

| Need | Read |
| --- | --- |
| End-to-end ladder workflow | `references/workflow.md` |
| Brief status actions and ladder order | `references/object-briefs.md` |
| Datasource inspection and ScanScope | `references/datasource.md` |
| Evidence, ledger, abandon protocol | `references/evidence-and-ledger.md` |
| Readiness closeout | `references/closeout.md` |
| Known failure modes | `references/pitfalls.md` |
