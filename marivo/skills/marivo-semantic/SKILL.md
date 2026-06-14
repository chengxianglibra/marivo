---
name: marivo-semantic
description: Use for any Marivo datasource definition or semantic-layer authoring task: datasource declarations/refs, datasets, fields, time fields, metrics, relationships, evidence, readiness, and analysis handoff.
---

# marivo-semantic

Use this skill when defining project datasources or building reusable semantic
objects. For metric-centered analysis on an already-ready model, use
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

- Follow the ladder: domain -> entity -> dimension -> time_dimension -> metric -> relationship -> cross-entity metric -> derived metric.
- Before writing each object, call the matching `project.prepare_*` API and branch on the returned Brief status.
- Write exactly one semantic object per cycle in `models/semantic/<domain>/_domain.py`.
- After writing one object, call `project.verify_object(ref)` and do not advance while it fails.
- **`verify_object` is enforced:** `prepare_dimensions`, `prepare_time_dimension`, `prepare_metric`, `prepare_relationship`, and `prepare_cross_entity_metric` raise `LadderOrderError` if their entity arguments have not passed `verify_object`. You must verify the entity before these calls.
- Use `md.ScanScope()` by default. Passing `partition=None` is allowed only when the answer explicitly accepts an unpruned scan.
- Ask users only for blocking `AuthoringQuestion`s that cannot be answered from documented project knowledge.
- Record abandonment with `authoring_abandoned` when a candidate cannot be safely authored.
- Run `project.readiness(...)` at closeout and do not hand off to analysis while readiness is blocked.

## Inspecting semantic objects

Use `ms.load()` to obtain a `SemanticCatalog`, then browse and inspect:

```python
catalog = ms.load()
catalog.list().show()                         # top-level: models and datasources
catalog.list("sales").show()                  # datasets and metrics under a model
catalog.list("sales.orders").show()           # fields, time fields, relationships, filtered metrics
revenue = catalog.get("sales.revenue")
revenue.details()                             # kind-specific details
```

Use `ms.help(ref)` for a bounded consumption briefing on any semantic ref.
This is the default path before passing an object to analysis APIs:

```python
mv.help(revenue)                    # bounded consumption context
mv.help(revenue, project=project)   # explicit project when not in CWD
```

Read `_domain.py` only when you need to modify the semantic model, inspect
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
