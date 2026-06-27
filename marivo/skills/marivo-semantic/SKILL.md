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
help -> discover -> settle/grill -> author -> verify
```

Layer ownership:

- ms.help(...) owns static authoring contracts: constructors, required and
  optional parameters, allowed values, defaults, omit rules, nested parse
  shapes, and static constraints.
- md.discover_* owns runtime datasource evidence shown through `.show()` /
  `.render()`: physical columns, profiles, detected formats, value ranges,
  primary-key evidence, relationship evidence, deterministic authoring
  warnings, signals, and issues.
- `ms.verify_object(...)`, `ms.readiness(...)`, and load errors own blockers,
  registry state, and validation after authoring.
- This skill owns workflow and routing only. Do not copy constructor parameter
  tables, discovery result schemas, parse recipes, or backend API catalogs into
  skill docs.

## Authoring Ladder

Build datasource-backed semantic objects in dependency order:

```text
domain -> entity -> dimension -> time_dimension -> measure -> metric
       -> relationship -> cross-entity metric -> derived metric
```

Datasource registration is a prerequisite owned by `marivo.datasource`, not a
semantic ladder rung.

Do not author a full domain in one pass. A domain is a container, not the
authoring unit. The default active batch is one entity plus one semantic kind,
for example `entity.sales.orders + dimension`, then
`entity.sales.orders + time_dimension`, then `entity.sales.orders + measure`.
Relationship and cross-entity batches may span two or more entities only when
the batch kind requires that scope.

For each active batch, follow this sequence exactly:

```text
select active batch
  -> inspect ms.help(...)
  -> run matching md.discover_*
  -> inspect current ms.load() catalog state
  -> list candidate objects for this batch
  -> settle candidates from evidence
  -> grill one unresolved decision, if needed
  -> author one object
  -> ms.verify_object(ref)
  -> repeat author/verify for remaining objects in the same batch
  -> close batch
  -> choose next batch
```

Do not skip to another batch while the current authored object has not passed
`ms.verify_object(ref)`.

Every semantic object uses this canonical loop:

```text
ms.help(...) static contract
  -> md.discover_* datasource evidence
  -> settle from evidence, registry, project docs, and prior decisions
  -> grill the user for unresolved semantic decisions
  -> author exactly one semantic object
  -> ms.verify_object(...)
```

Do not write several semantic objects and verify later. The unit of work is
one semantic object.

Concrete per-object actions:

1. Read `ms.help("<constructor-or-object>")`.
2. Run the matching bounded `md.discover_*` call and read `.show()` output.
3. Settle constructor values from discovery evidence, registry facts, project
   docs, source SQL/provenance, prior decisions, and user answers.
4. Grill the user only when a semantic decision remains unresolved.
5. Author exactly one object.
6. Run `ms.verify_object(ref)` and fix failures before advancing.

Object-to-object authoring parameters must use Ref objects, not bare semantic-id
strings. Prefer refs returned by earlier authoring calls or imported from
semantic modules. Use `ms.ref("<kind>.<semantic_id>")` only for explicit
forward/cross-file references, import cycles, or generated code boundaries.

## Grill-Me Gate

Before authoring each semantic object, inspect help, discovery evidence,
current catalog state, project docs, source SQL/provenance when present, prior
decisions, and user answers.

If those sources clearly settle the object, state the evidence basis and author
exactly one object. If a semantic choice remains unresolved, ask one question
at a time and wait for agreement before writing code.

<GRILL-TURN-GATE>
A grill turn MUST ask exactly one unresolved semantic decision.

Do not ask numbered lists of questions or combine multiple decisions in one
message. Do not ask a follow-up decision in the same message.
Do not write or modify semantic code after asking a grill question.

If multiple decisions remain, ask only the highest-blocking decision for the
current semantic object, then stop and wait for the user's answer.
</GRILL-TURN-GATE>

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
sales = catalog.get("domain.sales")
catalog.list(sales.ref).show()
catalog.list(kind=ms.SemanticKind.METRIC).show()
catalog.get("metric.sales.revenue").details().show()
```

Use `mv.help(ref)` for a short consumption briefing on a semantic object.
Use `ms.help("<topic>")` for semantic authoring contracts.

Read `_domain.py` only when you need to modify the semantic domain, inspect
implementation expressions, or debug authoring behavior.

## Reference Routing

| Need | Read |
| --- | --- |
| Evidence, Ref, and handoff notes | `references/workflow.md` |
| Datasource prerequisite flow | `references/datasource.md` |
| Analysis handoff gate | `references/closeout.md` |
| Workflow-level failure modes | `references/pitfalls.md` |
