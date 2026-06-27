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

The current project should contain `models/datasources/` and
`models/semantic/`. If either directory is missing, create the project
structure before authoring objects.

## Layer Contract

The authoring sequence is:

```text
help -> discover -> settle/grill -> prepare -> author -> verify
```

Layer ownership:

- ms.help(...) owns static authoring contracts: constructors, required and
  optional parameters, allowed values, defaults, omit rules, nested parse
  shapes, and static constraints.
- md.discover_* owns runtime datasource evidence: physical columns, profiles,
  detected formats, value ranges, primary-key evidence, relationship evidence,
  signals, and issues.
- `ms.prepare_*` and `ms.verify_object(...)` own readiness, blockers, matches,
  registry state, and validation.
- This skill owns workflow and routing only. Do not copy constructor parameter
  tables, Brief field tables, discovery result schemas, parse recipes, or
  backend API catalogs into skill docs.

## Authoring Ladder

Build datasource-backed semantic objects in dependency order:

```text
domain -> entity -> dimension -> time_dimension -> measure -> metric
       -> relationship -> cross-entity metric -> derived metric
```

Datasource registration is a prerequisite owned by `marivo.datasource`, not a
semantic ladder rung.

Every semantic object uses the same cycle:

1. Read `ms.help("<constructor-or-object>")`.
2. Run the matching bounded `md.discover_*` call.
3. Settle constructor values from discovery evidence, registry facts, project
   docs, source SQL/provenance, prior decisions, and user answers.
4. Grill the user only when a semantic decision remains unresolved.
5. After agreement, call the matching `ms.prepare_*` API as the
   post-agreement readiness check before authoring.
6. Author exactly one object.
7. Run `ms.verify_object(ref)` and fix failures before advancing.

## Grill-Me Gate

Before authoring each semantic object, inspect help, discovery evidence,
current catalog state, project docs, source SQL/provenance when present, prior
decisions, and user answers.

If those sources clearly settle the object, proceed to the matching
`ms.prepare_*` readiness check and state the evidence basis. Author only when
readiness can proceed. If a semantic choice remains unresolved, ask one
question at a time and wait for agreement before writing code.

After agreement, `ms.prepare_*` is the post-agreement readiness check before
authoring. Use it to catch blockers, matches, and registry state before writing
the object.

Rules:

- Ask only about semantic intent, business policy, or unresolved ambiguity.
- Do not ask for datasource facts Marivo can discover, such as schema, column
  names, data types, sample values, join-key viability, or existing refs.
- Do not invent multiple-choice options. Every option must be grounded in
  metadata comments, column profiles, sample distributions, existing semantic
  objects, source SQL, project docs, or prior decisions.
- If evidence supports one path, ask for confirmation of that path.
- If evidence does not support a finite option list, ask an open clarification
  instead of fabricating options.
- If agreement cannot be reached, record abandonment instead of writing a
  speculative semantic object.

## Inspecting Existing Semantic Objects

Use `ms.load()` to obtain a `SemanticCatalog`, then inspect with the catalog
surface:

```python
catalog = ms.load()
catalog.list().show()
catalog.list("sales").show()
catalog.list(kind="metric").show()
catalog.get("sales.revenue").details().show()
```

Use `mv.help(ref)` for a short consumption briefing on a semantic object.
Use `ms.help("<topic>")` for semantic authoring contracts.

Read `_domain.py` only when you need to modify the semantic domain, inspect
implementation expressions, or debug authoring behavior.

## Reference Routing

| Need | Read |
| --- | --- |
| End-to-end object workflow | `references/workflow.md` |
| Datasource prerequisite flow | `references/datasource.md` |
| Analysis handoff gate | `references/closeout.md` |
| Workflow-level failure modes | `references/pitfalls.md` |
